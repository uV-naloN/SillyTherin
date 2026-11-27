"""The messages the page and the server send each other over a chat's WebSocket.

Every message is JSON with a `type`. Page events are checked strictly: a wrong type is
refused rather than converted, and fields the page may not set are dropped.
"""

from sillytherin.server.events.base import (
    MAX_FRAME_BYTES,
    MAX_ID_CHARS,
    MAX_MESSAGE_CHARS,
    EventError,
    PageEvent,
    ServerEvent,
)
from sillytherin.server.events.page import (
    AgentModeSet,
    AskAnswer,
    ChatSend,
    ChatStop,
    PanelAction,
    SettingsUpdate,
)
from sillytherin.server.events.parse import AnyPageEvent, parse_page_event
from sillytherin.server.events.reply import (
    ChatLine,
    GroupDone,
    GroupLabel,
    ReplyContentFields,
    ReplyDelta,
    ReplyEnded,
    ReplyStarted,
    StepDone,
    ThinkingDelta,
)
from sillytherin.server.events.session import (
    AgentModeChanged,
    AskClosed,
    AskOpened,
    ChatClosed,
    ChatMessage,
    ErrorEvent,
    KaggleDownloadAskOpened,
    KagglePopup,
    PanelPropsEvent,
    PanelResult,
    PickersChanged,
    SessionReady,
    SettingsChanged,
    Toast,
)

__all__ = [
    "MAX_FRAME_BYTES",
    "MAX_ID_CHARS",
    "MAX_MESSAGE_CHARS",
    "AgentModeChanged",
    "AgentModeSet",
    "AnyPageEvent",
    "AskAnswer",
    "AskClosed",
    "AskOpened",
    "ChatClosed",
    "ChatLine",
    "ChatMessage",
    "ChatSend",
    "ChatStop",
    "ErrorEvent",
    "EventError",
    "GroupDone",
    "GroupLabel",
    "KaggleDownloadAskOpened",
    "KagglePopup",
    "PageEvent",
    "PanelAction",
    "PanelPropsEvent",
    "PanelResult",
    "PickersChanged",
    "ReplyContentFields",
    "ReplyDelta",
    "ReplyEnded",
    "ReplyStarted",
    "ServerEvent",
    "SessionReady",
    "SettingsChanged",
    "SettingsUpdate",
    "StepDone",
    "ThinkingDelta",
    "Toast",
    "parse_page_event",
]
