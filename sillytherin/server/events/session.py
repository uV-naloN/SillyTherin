"""Messages about the chat itself: opening it, prompts, toasts, settings, the notebook panel and errors."""

from typing import Annotated, Any, Literal

from pydantic import Field

from sillytherin.chat.ports import ToastKind
from sillytherin.contracts.chat import (
    DEFAULT_AGENT_MODE,
    AgentMode,
    Choice,
    Picker,
    SettingSwitch,
)
from sillytherin.server.events.base import MAX_ID_CHARS, EventError, ServerEvent
from sillytherin.server.events.reply import ChatLine


class SessionReady(ServerEvent):
    """The chat is open: everything the page draws first."""

    type: Literal["session.ready"] = "session.ready"
    chat_id: str
    messages: list[ChatLine]  # empty for a new chat; the chat so far after a reconnect
    pickers: list[Picker]
    settings: list[SettingSwitch]
    panel: dict[str, Any]  # NotebookProps of the chat's project
    agent_mode: AgentMode = DEFAULT_AGENT_MODE


class AgentModeChanged(ServerEvent):
    type: Literal["agent_mode.changed"] = "agent_mode.changed"
    mode: AgentMode


class ChatClosed(ServerEvent):
    """The chat the page asked to resume no longer exists (the app restarted); a new one follows."""

    type: Literal["chat.closed"] = "chat.closed"


class ChatMessage(ServerEvent):
    """A message from outside the reply, such as a finished download."""

    type: Literal["chat.message"] = "chat.message"
    text: str


class AskOpened(ServerEvent):
    type: Literal["ask.opened"] = "ask.opened"
    ask_id: str
    markdown: str
    choices: list[Choice]


class KaggleDownloadAskOpened(AskOpened):
    """A standalone chat prompt correlated with its still-pending picker action."""

    purpose: Literal["kaggle_download"] = "kaggle_download"
    request_id: Annotated[str, Field(min_length=1, max_length=MAX_ID_CHARS)]


class AskClosed(ServerEvent):
    """The prompt was answered or timed out; `choice` is None on timeout."""

    type: Literal["ask.closed"] = "ask.closed"
    ask_id: str
    choice: str | None


class Toast(ServerEvent):
    type: Literal["toast"] = "toast"
    kind: ToastKind
    text: str


class SettingsChanged(ServerEvent):
    type: Literal["settings.changed"] = "settings.changed"
    switches: list[SettingSwitch]


class PickersChanged(ServerEvent):
    type: Literal["pickers.changed"] = "pickers.changed"
    pickers: list[Picker]


class PanelPropsEvent(ServerEvent):
    """New panel props pushed from another tab or a download."""

    type: Literal["panel.props"] = "panel.props"
    props: dict[str, Any]


class PanelResult(ServerEvent):
    """The reply to one `panel.action`: props, or props with `error`."""

    type: Literal["panel.result"] = "panel.result"
    request_id: str
    result: dict[str, Any] | list[Any]


class KagglePopup(ServerEvent):
    type: Literal["kaggle.popup"] = "kaggle.popup"
    state: Literal["waiting", "connected", "failed"]
    authorize_url: str
    message: str


class ErrorEvent(ServerEvent):
    """A refused event; never a stack trace."""

    type: Literal["error"] = "error"
    code: EventError
    message: str
