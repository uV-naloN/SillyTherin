"""Capture the answer sent through one turn's answer port."""

from collections.abc import Callable

from core.chat.ports import AnswerView
from core.contracts.chat import ReplyInterruptionReason
from core.shared.best_effort import best_effort
from observability.dtos.reply_trace import (
    MAX_REPLY_CONTENT_BYTES,
    RecordingLoss,
    ReplyContent,
    ReplyState,
    SpanOutcome,
)


def content_size(text: str) -> int:
    if len(text) > MAX_REPLY_CONTENT_BYTES:
        return MAX_REPLY_CONTENT_BYTES + 1
    return len(text.encode("utf-8"))


class AnswerCapture:
    def __init__(self, answer: AnswerView, on_loss: Callable[[RecordingLoss], None]) -> None:
        self._answer = answer
        self._on_loss = on_loss
        self._prefix = ""
        self._prefix_bytes = 0
        self._has_gap = False
        self._terminal: str | None = None
        self._terminal_overflow = False
        self._has_failed = False

    def mark_failed(self, error: Exception) -> None:
        self._has_failed = True

    async def stream(self, text: str) -> bool | None:
        accepted = await self._answer.stream(text)
        with best_effort(self.mark_failed):
            if accepted is False:
                self._has_gap = True
            if not self._has_gap:
                self._prefix_bytes += content_size(text)
                if self._prefix_bytes <= MAX_REPLY_CONTENT_BYTES:
                    self._prefix += text
        return accepted

    async def finish(self, text: str, *, interruption_reason: ReplyInterruptionReason | None = None) -> bool | None:
        if interruption_reason is None:
            accepted = await self._answer.finish(text)
        else:
            accepted = await self._answer.finish(text, interruption_reason=interruption_reason)
        with best_effort(self.mark_failed):
            if accepted is not False:
                self._terminal_overflow = content_size(text) > MAX_REPLY_CONTENT_BYTES
                self._terminal = None if self._terminal_overflow else text
        return accepted

    def seal(self, message: str, outcome: SpanOutcome) -> ReplyContent | None:
        if self._has_failed:
            self._on_loss(RecordingLoss.OBSERVER_FAILED)
            return None
        try:
            return self.select_content(message, outcome)
        except UnicodeError:
            self._on_loss(RecordingLoss.INVALID_TRACE)
            return None

    def select_content(self, message: str, outcome: SpanOutcome) -> ReplyContent | None:
        has_terminal = self._terminal is not None or self._terminal_overflow
        is_complete = outcome is SpanOutcome.SUCCEEDED and has_terminal
        overflow = self._terminal_overflow if is_complete else self._prefix_bytes > MAX_REPLY_CONTENT_BYTES
        if content_size(message) > MAX_REPLY_CONTENT_BYTES or overflow:
            self._on_loss(RecordingLoss.INVALID_TRACE)
            return None
        if is_complete:
            return ReplyContent(message, self._terminal, ReplyState.COMPLETE)
        state = ReplyState.PARTIAL if self._prefix else ReplyState.MISSING
        return ReplyContent(message, self._prefix or None, state)
