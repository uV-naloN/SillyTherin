"""Vendor-neutral reply timing snapshots."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Protocol
from uuid import UUID

from core.best_effort import best_effort
from observability.dtos.reply_trace import (
    ModelExchange,
    RecordingLoss,
    ReplyContent,
    ReplySpan,
    ReplyTrace,
    SpanKind,
    SpanOutcome,
)

MAX_CHILD_SPANS = 256
REPLY_NAME = "reply.turn"
MODEL_NAME = "model.call"
OTHER_TOOL_LABEL = "tool.other"
SAFE_TOOL_LABELS = frozenset({
    "tool.web_search", "tool.read_page", "tool.dataset_search", "tool.notebook_read", "tool.notebook_run",
    "tool.notebook_edit", "tool.dataset_download", "tool.dataset_list", "tool.dataset_delete",
    "tool.dataset_describe", OTHER_TOOL_LABEL,
})


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
        tick = None
        with best_effort(lambda error: self.invalidate()):
            tick = self._clock.monotonic_ns()
        return tick

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
        from observability.model_capture import finish_model_exchange

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
                from observability.model_capture import (
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
