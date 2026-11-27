"""Data that only the browser callback listener uses."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BrowserReply:
    status: int
    text: str
