"""Enforce the agent’s notebook write and restart rules."""

from collections.abc import Awaitable, Callable, Sequence

from langchain.agents.middleware import AgentMiddleware, ModelResponse, ToolCallRequest
from langchain.agents.middleware import ModelRequest as AgentCall
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.types import Command

from core.workspace.service import NotebookRunOutcome

NOTEBOOK_WRITE_TOOLS = frozenset({"notebook_run", "notebook_edit"})
NOTEBOOK_STATE_TOOLS = NOTEBOOK_WRITE_TOOLS | {"notebook_read"}
RESTART_REPLY = "The notebook was restarted, so earlier results are gone. Press Run all in the notebook, then ask me again."


def notebook_restart_required(messages: Sequence[BaseMessage]) -> bool:
    return any(
        isinstance(message, ToolMessage) and message.artifact is NotebookRunOutcome.RESTART_REQUIRED
        for message in messages
    )


class StopOnNotebookRestart(AgentMiddleware):
    async def awrap_model_call(
        self, request: AgentCall, handler: Callable[[AgentCall], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        if notebook_restart_required(request.messages):
            return ModelResponse(result=[AIMessage(content=RESTART_REPLY)])
        return await handler(request)


def is_conflicting_notebook_write(request: ToolCallRequest) -> bool:
    if request.tool_call["name"] not in NOTEBOOK_WRITE_TOOLS:
        return False
    messages = request.state.get("messages", []) if isinstance(request.state, dict) else []
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        calls = message.tool_calls
        if not any(call["id"] == request.tool_call["id"] for call in calls):
            return False
        notebook_calls = sum(call["name"] in NOTEBOOK_STATE_TOOLS for call in calls)
        return notebook_calls > 1
    return False


class NotebookBatchGuard(AgentMiddleware):
    """Reject competing notebook steps before any write reaches the tool."""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if is_conflicting_notebook_write(request):
            return ToolMessage(
                content="Tool failed: Run one notebook cell per round, after any notebook read.",
                tool_call_id=request.tool_call["id"],
                name=request.tool_call["name"],
                status="error",
            )
        return await handler(request)
