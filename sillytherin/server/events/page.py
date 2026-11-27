"""Messages the page sends to the server. They are checked strictly: a wrong type is refused rather than converted."""

from typing import Any, Literal

from pydantic import ConfigDict, Field

from sillytherin.chat.mode import AgentMode
from sillytherin.server.events.base import MAX_ID_CHARS, MAX_MESSAGE_CHARS, PageEvent


class ChatSend(PageEvent):
    """You sent a message, with the pickers' current choices."""

    type: Literal["chat.send"]
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    model: str = Field(max_length=200)
    effort: str = Field(max_length=MAX_ID_CHARS)


class AgentModeSet(PageEvent):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    type: Literal["agent_mode.set"]
    mode: AgentMode


class ChatStop(PageEvent):
    """The Stop button."""

    type: Literal["chat.stop"]


class SettingsUpdate(PageEvent):
    """A Settings switch changed; values apply right away."""

    type: Literal["settings.update"]
    values: dict[str, bool]


class AskAnswer(PageEvent):
    """You clicked a choice under a prompt."""

    type: Literal["ask.answer"]
    ask_id: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    choice: str = Field(min_length=1, max_length=MAX_ID_CHARS)


class PanelAction(PageEvent):
    """A Notebook panel action; `name` and `payload` are checked against the panel's own allow-list."""

    type: Literal["panel.action"]
    request_id: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    name: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    payload: dict[str, Any]
