"""Validate streamed model arguments before they reach native tools."""

import json
from dataclasses import dataclass, field
from uuid import UUID

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage, AIMessageChunk, ToolCall
from langchain_core.outputs import ChatGenerationChunk
from langgraph.runtime import Runtime

from core.agent.middleware.round_limit import tool_rounds_this_turn

INVALID_ARGUMENTS_FIELD = "__invalid_arguments__"


@dataclass
class ToolArgumentFragments:
    call_id: str | None = None
    fragments: list[str] = field(default_factory=list)


class ToolArgumentStream(AsyncCallbackHandler):
    """Remember only which streamed tool calls arrived with invalid JSON."""

    def __init__(self) -> None:
        self.pending: dict[UUID, dict[int | str, ToolArgumentFragments]] = {}
        self.invalid_call_ids: set[str] = set()

    async def on_llm_new_token(self, token: str, *, chunk: object = None, run_id: UUID, **kwargs: object) -> None:
        if not isinstance(chunk, ChatGenerationChunk) or not isinstance(chunk.message, AIMessageChunk):
            return
        calls = self.pending.setdefault(run_id, {})
        for tool_chunk in chunk.message.tool_call_chunks:
            index = tool_chunk.get("index")
            call_id = tool_chunk.get("id")
            key = call_id if index is None else index
            if key is None:
                continue
            arguments = calls.setdefault(key, ToolArgumentFragments())
            arguments.call_id = arguments.call_id or call_id
            arguments.fragments.append(tool_chunk.get("args") or "")

    async def on_llm_end(self, response: object, *, run_id: UUID, **kwargs: object) -> None:
        calls = self.pending.pop(run_id, {})
        self.invalid_call_ids = {
            arguments.call_id
            for arguments in calls.values()
            if arguments.call_id and not is_json_object("".join(arguments.fragments))
        }

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: object) -> None:
        self.pending.pop(run_id, None)

    def take_invalid_call_ids(self) -> set[str]:
        invalid_call_ids = self.invalid_call_ids
        self.invalid_call_ids = set()
        return invalid_call_ids


def reject_json_constant(value: str) -> None:
    raise ValueError("Non-JSON numeric constant.")


def is_json_object(arguments: str) -> bool:
    try:
        return isinstance(json.loads(arguments, parse_constant=reject_json_constant), dict)
    except (ValueError, RecursionError):
        return False


class GuardModelToolCalls(AgentMiddleware):
    """Reject recovered fragments before they can become native calls."""

    def __init__(self, arguments: ToolArgumentStream, max_rounds: int) -> None:
        self.arguments = arguments
        self.max_rounds = max_rounds

    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, list[AIMessage]] | None:
        invalid_call_ids = self.arguments.take_invalid_call_ids()
        messages = state["messages"]
        latest = messages[-1]
        if not isinstance(latest, AIMessage):
            return None
        has_round_budget = tool_rounds_this_turn(messages[:-1]) < self.max_rounds
        candidates = [
            reject_recovered_arguments(call, invalid_call_ids)
            for call in latest.tool_calls if has_round_budget and call["id"]
        ]
        if candidates == latest.tool_calls and not latest.invalid_tool_calls:
            return None
        return {"messages": [latest.model_copy(update={"tool_calls": candidates, "invalid_tool_calls": []})]}


def reject_recovered_arguments(call: ToolCall, invalid_call_ids: set[str]) -> ToolCall:
    if call["id"] not in invalid_call_ids:
        return call
    # Native validation rejects this extra field and returns an error correlated to the original call.
    return {**call, "args": {INVALID_ARGUMENTS_FIELD: True}}
