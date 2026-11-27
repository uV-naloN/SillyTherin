"""Native agent observation belongs to the optional app layer."""

import logging
from asyncio import CancelledError
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager, nullcontext
from contextvars import ContextVar
from typing import Any
from uuid import UUID

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage, ToolMessage
from langgraph.types import Command

from sillytherin.best_effort import best_effort
from sillytherin.observability.dtos.reply_trace import (
    ModelExchange,
    ModelOutputState,
    SpanKind,
    SpanOutcome,
)
from sillytherin.observability.model_capture import (
    capture_model_chunk,
    encode_model_input,
)
from sillytherin.observability.reply_trace import (
    MODEL_NAME,
    OTHER_TOOL_LABEL,
    SAFE_TOOL_LABELS,
    ApprovalWait,
    ReplyRecorder,
)
from sillytherin.tools import NOTEBOOK_TOOLS, TOOLS

TOOL_LABELS = {
    tool.name: f"tool.{tool.name}" for tool in (*TOOLS, *NOTEBOOK_TOOLS)
    if f"tool.{tool.name}" in SAFE_TOOL_LABELS
}
log = logging.getLogger(__name__)
current_wait: ContextVar[ApprovalWait | None] = ContextVar("tool_approval_wait", default=None)


class ToolWaitMiddleware(AgentMiddleware):
    def __init__(self, recorder: ReplyRecorder) -> None:
        self._recorder = recorder

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        token = current_wait.set(ApprovalWait(self._recorder))
        try:
            return await handler(request)
        finally:
            current_wait.reset(token)


def observe_answer_wait() -> AbstractContextManager[None]:
    wait = current_wait.get()
    return nullcontext() if wait is None else wait.observe()


def failure_outcome(error: BaseException) -> SpanOutcome:
    return SpanOutcome.CANCELLED if isinstance(error, CancelledError) else SpanOutcome.FAILED


class NativeStageObserver(AsyncCallbackHandler):
    run_inline = True

    def __init__(self, recorder: ReplyRecorder) -> None:
        self._recorder = recorder

    async def on_chat_model_start(
        self, serialized: dict[str, Any], messages: list[list[BaseMessage]], *, run_id: UUID, **kwargs: Any
    ) -> None:
        self.start_stage(run_id, SpanKind.MODEL, MODEL_NAME)
        if len(messages) != 1:
            return
        with best_effort(self.invalidate_after_failure("at start")):
            model_messages, = messages
            exchange = ModelExchange(encode_model_input(model_messages), None, ModelOutputState.MISSING)
            self._recorder.attach_model_exchange(run_id, exchange)

    async def on_llm_end(self, response: object, *, run_id: UUID, **kwargs: Any) -> None:
        self.finish_model_exchange(response, run_id, SpanOutcome.SUCCEEDED)

    async def on_llm_new_token(
        self, token: str, *, chunk: object = None, run_id: UUID, **kwargs: Any
    ) -> None:
        """Capture a response fragment for its model run."""
        with best_effort(self.invalidate_after_failure("for chunk")):
            projected = capture_model_chunk(chunk, token)
            if projected is not None:
                self._recorder.record_model_chunk(run_id, projected)

    def invalidate_recording(self, error: Exception) -> None:
        self._recorder.invalidate()

    def invalidate_after_failure(self, moment: str) -> Callable[[Exception], None]:
        def handle(error: Exception) -> None:
            log.warning("Model trace capture failed %s: %s", moment, type(error).__name__)
            self._recorder.invalidate()

        return handle

    def finish_model_exchange(self, response: object | None, run_id: UUID, outcome: SpanOutcome) -> None:
        """Attach one terminal response to its matching model run."""
        with best_effort(self.invalidate_after_failure("at finish")):
            self._recorder.finish_model_stage(run_id, outcome, response)

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self.finish_model_exchange(kwargs.get("response"), run_id, failure_outcome(error))

    async def on_tool_start(
        self, serialized: dict[str, Any], input_str: str, *, run_id: UUID, **kwargs: Any
    ) -> None:
        name = (serialized or {}).get("name")
        label = TOOL_LABELS.get(name, OTHER_TOOL_LABEL) if type(name) is str else OTHER_TOOL_LABEL
        self.start_stage(run_id, SpanKind.TOOL, label, current_wait.get())

    async def on_tool_end(self, output: object, *, run_id: UUID, **kwargs: Any) -> None:
        failed = isinstance(output, ToolMessage) and output.status == "error"
        self.finish_stage(run_id, SpanOutcome.FAILED if failed else SpanOutcome.SUCCEEDED)

    async def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self.finish_stage(run_id, failure_outcome(error))

    def start_stage(
        self, run_id: UUID, kind: SpanKind, label: str, wait: ApprovalWait | None = None
    ) -> None:
        with best_effort(self.invalidate_recording):
            self._recorder.start_stage(run_id, kind, label, approval_wait=wait)

    def finish_stage(self, run_id: UUID, outcome: SpanOutcome) -> None:
        with best_effort(self.invalidate_recording):
            self._recorder.finish_stage(run_id, outcome)
