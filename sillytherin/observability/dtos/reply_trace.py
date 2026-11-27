"""What one traced reply records: its spans, model exchanges and content."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated

from pydantic import ConfigDict, Field

MAX_REPLY_CONTENT_BYTES = 65_536
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
