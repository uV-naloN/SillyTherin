"""Turn one socket frame into the page event it holds."""

from typing import Annotated

from pydantic import Field, TypeAdapter, ValidationError

from sillytherin.server.events.base import PageEvent
from sillytherin.server.events.page import (
    AgentModeSet,
    AskAnswer,
    ChatSend,
    ChatStop,
    PanelAction,
    SettingsUpdate,
)

AnyPageEvent = Annotated[ChatSend | AgentModeSet | ChatStop | SettingsUpdate | AskAnswer | PanelAction, Field(discriminator="type")]


_PAGE_EVENTS: TypeAdapter[PageEvent] = TypeAdapter(AnyPageEvent)


def parse_page_event(frame: str) -> PageEvent:
    """The event in one socket frame; raises ValueError for anything that isn't a valid page event."""
    try:
        return _PAGE_EVENTS.validate_json(frame)
    except ValidationError as e:
        raise ValueError("Not a valid event.") from e
