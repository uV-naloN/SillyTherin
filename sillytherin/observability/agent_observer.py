"""Native agent observation belongs to the optional app layer."""

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

from sillytherin.observability.reply_trace import (
    MODEL_NAME,
    OTHER_TOOL_LABEL,
    SAFE_TOOL_LABELS,
    ApprovalWait,
    ReplyRecorder,
    SpanKind,
    SpanOutcome,
)
from sillytherin.tools import NOTEBOOK_TOOLS, TOOLS

TOOL_LABELS = {
    tool.name: f"tool.{tool.name}" for tool in (*TOOLS, *NOTEBOOK_TOOLS)
    if f"tool.{tool.name}" in SAFE_TOOL_LABELS
}
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

    async def on_llm_end(self, response: object, *, run_id: UUID, **kwargs: Any) -> None:
        self.finish_stage(run_id, SpanOutcome.SUCCEEDED)

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self.finish_stage(run_id, failure_outcome(error))

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
        try:
            self._recorder.start_stage(run_id, kind, label, approval_wait=wait)
        except Exception:  # noqa: BLE001 - LangChain would log raw callback errors otherwise
            self._recorder.invalidate()

    def finish_stage(self, run_id: UUID, outcome: SpanOutcome) -> None:
        try:
            self._recorder.finish_stage(run_id, outcome)
        except Exception:  # noqa: BLE001 - observation must not affect native execution
            self._recorder.invalidate()
