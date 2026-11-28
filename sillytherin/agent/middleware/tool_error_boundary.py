"""Keep unexpected tool failures from exposing internal details."""

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

from sillytherin.agent.tool_errors import TOOL_FAILURE_PREFIX, UNEXPECTED_TOOL_FAILURE


class ToolErrorBoundary(AgentMiddleware):
    """Turns any unexpected tool error into a safe generic result, so no internal detail reaches the chat."""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
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
