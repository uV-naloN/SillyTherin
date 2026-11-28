"""Track time spent waiting for user approval within one tool call."""

from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager, nullcontext
from contextvars import ContextVar

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from sillytherin.observability.reply_trace import ApprovalWait, ReplyRecorder

_active_approval_wait: ContextVar[ApprovalWait | None] = ContextVar("tool_approval_wait", default=None)


class ToolWaitMiddleware(AgentMiddleware):
    def __init__(self, recorder: ReplyRecorder) -> None:
        self._recorder = recorder

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        token = _active_approval_wait.set(ApprovalWait(self._recorder))
        try:
            return await handler(request)
        finally:
            _active_approval_wait.reset(token)


def get_active_approval_wait() -> ApprovalWait | None:
    return _active_approval_wait.get()


def observe_answer_wait() -> AbstractContextManager[None]:
    wait = get_active_approval_wait()
    return nullcontext() if wait is None else wait.observe()
