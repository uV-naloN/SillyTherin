"""Messages that carry one reply: its start, its text, its work group and its end."""

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
)

from sillytherin.chat.ports import ReplyInterruptionReason
from sillytherin.server.events.base import ServerEvent


class ReplyContentFields(BaseModel):
    text: str
    interruption_reason: ReplyInterruptionReason | None = Field(
        default=None, description="Marks a partial answer that ended with a provider relay error.",
    )

    @model_serializer(mode="wrap")
    def serialize_reply_content(self, serialize: SerializerFunctionWrapHandler) -> dict[str, Any]:
        fields = serialize(self)
        if self.interruption_reason is None:
            fields.pop("interruption_reason", None)
        return fields


class ChatLine(ReplyContentFields):
    """One message already in the chat, so a reconnected page can redraw it."""

    model_config = ConfigDict(frozen=True)

    role: Literal["user", "assistant"]


class ReplyStarted(ServerEvent):
    type: Literal["reply.started"] = "reply.started"
    message_id: str


class ReplyDelta(ServerEvent):
    type: Literal["reply.delta"] = "reply.delta"
    message_id: str
    text: str


class ReplyEnded(ServerEvent, ReplyContentFields):
    """The reply's final text, including a partial answer after Stop or an "Error: …" text."""

    type: Literal["reply.ended"] = "reply.ended"
    message_id: str


class GroupLabel(ServerEvent):
    """The reply's work group shows what is happening now."""

    type: Literal["group.label"] = "group.label"
    message_id: str
    label: str


class GroupDone(ServerEvent):
    """The work group's summary, such as "4 steps · 12s"."""

    type: Literal["group.done"] = "group.done"
    message_id: str
    label: str


class ThinkingDelta(ServerEvent):
    type: Literal["thinking.delta"] = "thinking.delta"
    message_id: str
    text: str


class StepDone(ServerEvent):
    type: Literal["step.done"] = "step.done"
    message_id: str
    title: str
    output: str
