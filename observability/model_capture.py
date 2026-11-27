"""Typed entry points for capturing callback-visible model exchanges."""

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    HumanMessageChunk,
)

from observability.dtos.reply_trace import (
    ModelExchange,
    ModelOutputState,
    SpanOutcome,
)

MAX_MODEL_CAPTURE_BYTES = 262_144


def encode_model_input(messages: Sequence[BaseMessage]) -> str:
    """Project the ordered messages supplied to one model call."""
    projected = [project_message(message) for message in messages]
    return encode_json(projected)


def capture_model_chunk(chunk: object, token: str) -> dict[str, Any] | None:
    """Project one observable response fragment without callback metadata."""
    message = getattr(getattr(chunk, "message", None), "message", None)
    if message is None:
        message = getattr(chunk, "message", None)
    if message is None:
        return {"role": "assistant", "content": token} if token else None
    projected = project_message(message)
    if not has_content(projected) and token:
        projected["content"] = token
    return projected if has_content(projected) else None


def finish_model_exchange(
    input_json: str,
    chunks: Sequence[dict[str, Any]],
    response: object | None,
    outcome: SpanOutcome,
) -> ModelExchange:
    """Build the bounded terminal or incomplete model exchange."""
    output_messages = response_messages(response)
    if outcome is SpanOutcome.SUCCEEDED and output_messages:
        output = {"messages": [project_message(message) for message in output_messages]}
        tool_call_fragments = [
            fragment
            for chunk in chunks
            for fragment in chunk.get("tool_call_fragments", [])
        ]
        if tool_call_fragments:
            output["tool_call_fragments"] = tool_call_fragments
        output_json = encode_json(output)
        return ModelExchange(input_json, output_json, ModelOutputState.COMPLETE)
    if output_messages:
        chunks = [project_message(message) for message in output_messages]
    else:
        chunks = list(chunks)
    output_json = encode_json({"chunks": chunks}) if chunks else None
    output_state = ModelOutputState.PARTIAL if chunks else ModelOutputState.MISSING
    return ModelExchange(input_json, output_json, output_state)


def project_message(message: BaseMessage) -> dict[str, Any]:
    role = message.type
    if isinstance(message, (HumanMessage, HumanMessageChunk)):
        role = "user"
    elif isinstance(message, (AIMessage, AIMessageChunk)):
        role = "assistant"
    projected: dict[str, Any] = {"role": role, "content": message.content}
    if message.name is not None:
        projected["name"] = message.name
    if message.type == "tool" and getattr(message, "tool_call_id", None) is not None:
        projected["tool_call_id"] = message.tool_call_id
    if isinstance(message, (AIMessage, AIMessageChunk)):
        calls = [
            {"id": call.get("id"), "name": call.get("name"), "args": call.get("args")}
            for call in message.tool_calls
        ]
        if calls:
            projected["tool_calls"] = calls
        tool_call_fragments = [
            {key: part for key, part in fragment.items() if key in {"index", "id", "name", "args"}}
            for fragment in getattr(message, "tool_call_chunks", []) or []
        ]
        if tool_call_fragments:
            projected["tool_call_fragments"] = tool_call_fragments
        for key in ("reasoning_content", "reasoning_details"):
            value = message.additional_kwargs.get(key)
            if value is not None:
                projected[key] = value
    return projected


def response_messages(response: object | None) -> list[BaseMessage] | None:
    generations = getattr(response, "generations", None)
    if not generations or not generations[0]:
        return None
    messages = [getattr(generation, "message", None) for generation in generations[0]]
    return [message for message in messages if isinstance(message, BaseMessage)]


def has_content(message: dict[str, Any]) -> bool:
    fields = ("content", "tool_call_fragments", "tool_calls", "reasoning_content", "reasoning_details")
    return any(message.get(field) for field in fields)


def encode_json(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_MODEL_CAPTURE_BYTES:
        raise ValueError("Model exchange exceeds capture limit")
    return encoded
