"""Vendor-neutral reply timing snapshots."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Annotated, Protocol
from uuid import UUID

from pydantic import ConfigDict, Field

MAX_CHILD_SPANS = 256
MAX_REPLY_CONTENT_BYTES = 65_536
REPLY_NAME = "reply.turn"
MODEL_NAME = "model.call"
OTHER_TOOL_LABEL = "tool.other"
SAFE_TOOL_LABELS = frozenset({
    "tool.web_search", "tool.read_page", "tool.dataset_search", "tool.notebook_read", "tool.notebook_run",
    "tool.notebook_edit", "tool.dataset_download", "tool.dataset_list", "tool.dataset_delete",
    "tool.dataset_describe", OTHER_TOOL_LABEL,
})
UnixNanoseconds = Annotated[int, Field(ge=0, le=(1 << 64) - 1)]
ReplyText = Annotated[str, Field(strict=True, max_length=MAX_REPLY_CONTENT_BYTES)]


class SpanKind(StrEnum):
    REPLY = "span"
    MODEL = "generation"
    TOOL = "tool"


class SpanOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class RecordingLoss(StrEnum):
    STAGE_LIMIT = "stage_limit"
    OBSERVER_FAILED = "observer_failed"
    INVALID_TRACE = "invalid_trace"


class ReplyState(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    MISSING = "missing"


class ModelOutputState(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    MISSING = "missing"


@dataclass(frozen=True)
class ModelExchange:
    input_json: str = field(repr=False)
    output_json: str | None = field(repr=False)
    output_state: ModelOutputState


@dataclass(frozen=True)
class ReplySpan:
    kind: SpanKind
    name: str
    start_time_unix_nano: UnixNanoseconds
    end_time_unix_nano: UnixNanoseconds
    outcome: SpanOutcome
    model_exchange: ModelExchange | None = field(default=None, repr=False)


@dataclass(frozen=True)
class ReplyContent:
    """The submitted message and the answer text captured for this turn."""

    __pydantic_config__ = ConfigDict(extra="forbid", hide_input_in_errors=True)

    # Preserve the submitted text exactly for the trace input.
    message: ReplyText = field(repr=False)
    # A missing reply omits output in the exported span.
    reply: ReplyText | None = field(repr=False)
    reply_state: ReplyState


@dataclass(frozen=True)
class ReplyTrace:
    root: ReplySpan
    children: tuple[ReplySpan, ...]
    content: ReplyContent | None = None


@dataclass(frozen=True)
class TraceClock:
    epoch_ns: Callable[[], int]
    monotonic_ns: Callable[[], int]


class ReplyTraceSink(Protocol):
    def offer(self, trace: ReplyTrace) -> None:
        """Submit a completed snapshot without waiting for delivery."""
        ...


class ApprovalWait:
    def __init__(self, recorder: "ReplyRecorder") -> None:
        self._recorder = recorder
        self._started: int | None = None
        self._elapsed_ns = 0
        self._active_waits = 0

    @contextmanager
    def observe(self) -> Iterator[None]:
        if not self._active_waits:
            self._started = self._recorder.read_tick()
        self._active_waits += 1
        try:
            yield
        finally:
            self.close_wait()

    def close_wait(self) -> None:
        self._active_waits -= 1
        if self._active_waits or self._started is None:
            return
        end = self._recorder.read_tick()
        if end is not None:
            self._elapsed_ns += max(0, end - self._started)
        self._started = None

    def elapsed_at(self, end: int) -> int:
        pending_ns = 0 if self._started is None else max(0, end - self._started)
        return self._elapsed_ns + pending_ns


@dataclass(frozen=True)
class StartedStage:
    kind: SpanKind
    name: str
    started: int
    approval_wait: ApprovalWait | None

    def finish(self, end: int, outcome: SpanOutcome, epoch_offset: int) -> ReplySpan:
        waiting_ns = self.approval_wait.elapsed_at(end) if self.approval_wait is not None else 0
        active_end = max(self.started, end - waiting_ns)
        return ReplySpan(self.kind, self.name, self.started + epoch_offset, active_end + epoch_offset, outcome)


class ReplyRecorder:
    def __init__(self, clock: TraceClock) -> None:
        self._clock = clock
        self._started = 0
        self._epoch_offset = 0
        self._stages: dict[UUID, StartedStage] = {}
        self._finished: dict[UUID, ReplySpan] = {}
        self._model_inputs: dict[UUID, str] = {}
        self._model_chunks: dict[UUID, list[dict[str, object]]] = {}
        self._sealed = False
        self.loss: RecordingLoss | None = None

    def invalidate(self, loss: RecordingLoss = RecordingLoss.OBSERVER_FAILED) -> None:
        if not self._sealed and self.loss is None:
            self.loss = loss

    def read_tick(self) -> int | None:
        try:
            return self._clock.monotonic_ns()
        except Exception:  # noqa: BLE001 - timing cannot change chat or approval behaviour
            self.invalidate()
            return None

    def start_reply(self) -> None:
        self._started = self._clock.monotonic_ns()
        self._epoch_offset = self._clock.epoch_ns() - self._started

    def start_stage(
        self, run_id: UUID, kind: SpanKind, name: str, *, approval_wait: ApprovalWait | None = None
    ) -> None:
        if self._sealed or self.loss is not None or run_id in self._stages or run_id in self._finished:
            return
        if len(self._stages) + len(self._finished) >= MAX_CHILD_SPANS:
            self.invalidate(RecordingLoss.STAGE_LIMIT)
            return
        started = self._clock.monotonic_ns()
        self._stages[run_id] = StartedStage(kind, name, started, approval_wait)

    def finish_stage(self, run_id: UUID, outcome: SpanOutcome) -> None:
        if self._sealed or self.loss is not None:
            return
        stage = self._stages.pop(run_id, None)
        if stage is not None:
            self._finished[run_id] = stage.finish(self._clock.monotonic_ns(), outcome, self._epoch_offset)

    def attach_model_exchange(self, run_id: UUID, exchange: ModelExchange) -> None:
        """Attach callback content to the matching model span."""
        stage = self._stages.get(run_id)
        if stage is not None and stage.kind is SpanKind.MODEL:
            self._model_inputs[run_id] = exchange.input_json
            self._model_chunks[run_id] = []
            return
        span = self._finished.get(run_id)
        if span is not None and span.kind is SpanKind.MODEL:
            self._finished[run_id] = replace(span, model_exchange=exchange)

    def record_model_chunk(self, run_id: UUID, chunk: dict[str, object]) -> None:
        if run_id in self._model_inputs and not self._sealed:
            self._model_chunks.setdefault(run_id, []).append(chunk)

    def finish_model_stage(self, run_id: UUID, outcome: SpanOutcome, response: object | None) -> None:
        from sillytherin.observability.model_capture import finish_model_exchange

        input_json = self._model_inputs.get(run_id)
        chunks = self._model_chunks.get(run_id, [])
        if input_json is not None:
            exchange = finish_model_exchange(input_json, chunks, response, outcome)
            self.finish_stage(run_id, outcome)
            self.attach_model_exchange(run_id, exchange)
            self._model_inputs.pop(run_id, None)
            self._model_chunks.pop(run_id, None)
            return
        self.finish_stage(run_id, outcome)

    def finish_reply(self, outcome: SpanOutcome, *, content: ReplyContent | None = None) -> ReplyTrace | None:
        if self._sealed or self.loss is not None:
            return None
        end = self._clock.monotonic_ns()
        pending_outcome = SpanOutcome.CANCELLED if outcome is SpanOutcome.CANCELLED else SpanOutcome.INTERRUPTED
        for run_id, stage in self._stages.items():
            self._finished[run_id] = stage.finish(end, pending_outcome, self._epoch_offset)
            input_json = self._model_inputs.get(run_id)
            if input_json is not None:
                from sillytherin.observability.model_capture import (
                    finish_model_exchange,
                )

                exchange = finish_model_exchange(input_json, self._model_chunks.get(run_id, []), None, pending_outcome)
                self._finished[run_id] = replace(self._finished[run_id], model_exchange=exchange)
        self._stages.clear()
        self._sealed = True
        root = ReplySpan(SpanKind.REPLY, REPLY_NAME, self._started + self._epoch_offset, end + self._epoch_offset, outcome)
        return ReplyTrace(root, tuple(sorted(self._finished.values(), key=span_start)), content=content)


def span_start(span: ReplySpan) -> int:
    return span.start_time_unix_nano
