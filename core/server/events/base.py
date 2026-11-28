"""What every message shares: the size limits, the error codes and the two base classes."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

MAX_MESSAGE_CHARS = 100_000
MAX_FRAME_BYTES = 1_048_576  # one socket message; bigger closes the socket
MAX_ID_CHARS = 64


class EventError(StrEnum):
    BAD_EVENT = "BAD_EVENT"  # not JSON, unknown type, missing or wrong field
    BUSY = "BUSY"  # a message arrived while this chat's reply is still running
    UNKNOWN_ASK = "UNKNOWN_ASK"  # an answer to a prompt this chat doesn't have open
    INTERNAL = "INTERNAL"  # a handler failed; details stay in the app log


class PageEvent(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, frozen=True)


class ServerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
