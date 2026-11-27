"""Observe native tool lifecycle events for the turn's existing progress UI."""

from asyncio import CancelledError
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp

from sillytherin.agent.tool_errors import TOOL_FAILURE_PREFIX, UNEXPECTED_TOOL_FAILURE
from sillytherin.best_effort import best_effort
from sillytherin.chat.ports import ToolStep
from sillytherin.progress import Progress
from sillytherin.tools import native_tool_step_title

UNAVAILABLE_TOOL = "This tool is not available for this reply."
MISSING_STEP_OUTPUT = "Tool finished."


class NativeToolProgress(AsyncCallbackHandler):
    """A local observer; it never runs a tool or changes a model message."""

    def __init__(self, progress: Progress) -> None:
        self.progress = progress
        self.steps: dict[UUID, ToolStep] = {}
        self.observed_call_ids: set[str] = set()
        self.observed_result_ids: set[str] = set()

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        inputs: dict[str, Any] | None = None,
        tool_call_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        if tool_call_id:
            self.observed_call_ids.add(tool_call_id)
        with best_effort():
            title = native_tool_step_title(serialized.get("name", ""), inputs or {})
            self.steps[run_id] = await self.progress.start_tool_step(title)

    async def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        step = self.steps.pop(run_id, None)
        if step is None or not isinstance(output, ToolMessage):
            return
        self.observed_call_ids.add(output.tool_call_id)
        await finish_step(step, step_output(output))

    async def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        step = self.steps.pop(run_id, None)
        if step is not None and not isinstance(error, (CancelledError, GraphBubbleUp)):
            await finish_step(step, f"Failed: {UNEXPECTED_TOOL_FAILURE}")

    async def observe_tool_result(self, result: ToolMessage) -> None:
        result_id = result.id or result.tool_call_id
        if not result_id or result_id in self.observed_result_ids:
            return
        self.observed_result_ids.add(result_id)
        if result.tool_call_id in self.observed_call_ids:
            self.observed_call_ids.discard(result.tool_call_id)
            return
        if result.status != "error":
            return
        try:
            step = await self.progress.start_tool_step("Unavailable tool")
        except Exception:  # noqa: BLE001 - a failed UI update has no impact on model output
            return
        await finish_step(step, f"Failed: {UNAVAILABLE_TOOL}")

    def clear_pending(self) -> None:
        self.steps.clear()
        self.observed_call_ids.clear()
        self.observed_result_ids.clear()


def step_output(message: ToolMessage) -> str:
    if message.status == "error":
        safe_reason = UNEXPECTED_TOOL_FAILURE
        if message.text.startswith(TOOL_FAILURE_PREFIX):
            safe_reason = message.text.removeprefix(TOOL_FAILURE_PREFIX)
        return f"Failed: {safe_reason}"
    return message.artifact if isinstance(message.artifact, str) else MISSING_STEP_OUTPUT


async def finish_step(step: ToolStep, output: str) -> None:
    with best_effort():
        await step.finish(output)
