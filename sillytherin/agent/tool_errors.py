"""The one place that decides what a failed tool call tells the model."""

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool, ToolException
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command
from pydantic import ValidationError

from sillytherin.agent.model import INVALID_ARGUMENTS_FIELD

TOOL_FAILURE_PREFIX = "Tool failed: "
INVALID_TOOL_ARGUMENTS = f"{TOOL_FAILURE_PREFIX}Invalid tool arguments."
UNEXPECTED_TOOL_FAILURE = "Could not complete the tool call."
MAX_TOOL_ERROR_CHARS = 2000
NOTEBOOK_WRITE_TOOLS = frozenset({"notebook_run", "notebook_edit"})
NOTEBOOK_STATE_TOOLS = NOTEBOOK_WRITE_TOOLS | {"notebook_read"}

ToolCallHandler = Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]]


class ToolErrorBoundary(AgentMiddleware):
    """Turns any unexpected tool error into a safe generic result, so no internal detail reaches the chat."""

    async def awrap_tool_call(self, request: ToolCallRequest, handler: ToolCallHandler) -> ToolMessage | Command:
        try:
            return await handler(request)
        except GraphBubbleUp:
            raise
        except Exception:  # noqa: BLE001 - unexpected service errors must not expose internal details to chat
            return ToolMessage(
                content=f"{TOOL_FAILURE_PREFIX}{UNEXPECTED_TOOL_FAILURE}",
                tool_call_id=request.tool_call["id"],
                name=request.tool_call["name"],
                status="error",
            )


def with_safe_error_text(native_tool: BaseTool) -> BaseTool:
    """The tool answers bad arguments and its own expected failures as error results the model can read."""
    # Set on the tool, not the boundary: LangChain formats these before the boundary sees them,
    # and its default validation text repeats the model's input.
    native_tool.handle_validation_error = explain_invalid_arguments
    native_tool.handle_tool_error = explain_expected_failure
    return native_tool


def explain_invalid_arguments(error: ValidationError) -> str:
    problems = error.errors(include_input=False, include_url=False)
    if any(INVALID_ARGUMENTS_FIELD in problem["loc"] for problem in problems):
        return INVALID_TOOL_ARGUMENTS  # the streamed arguments were not JSON; the marker field means nothing to the model
    reasons = "; ".join(f"{field_path(problem['loc'])}: {problem['msg']}" for problem in problems)
    return f"{TOOL_FAILURE_PREFIX}Invalid tool arguments ({reasons})."


def field_path(location: tuple[int | str, ...]) -> str:
    return ".".join(str(part) for part in location) or "arguments"


def explain_expected_failure(error: ToolException) -> str:
    return f"{TOOL_FAILURE_PREFIX}{str(error)[:MAX_TOOL_ERROR_CHARS]}"


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

    async def awrap_tool_call(self, request: ToolCallRequest, handler: ToolCallHandler) -> ToolMessage | Command:
        if is_conflicting_notebook_write(request):
            return ToolMessage(
                content="Tool failed: Run one notebook cell per round, after any notebook read.",
                tool_call_id=request.tool_call["id"],
                name=request.tool_call["name"],
                status="error",
            )
        return await handler(request)
