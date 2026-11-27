"""How the browser hand-off ended."""

from dataclasses import dataclass, field
from typing import Literal

CallbackOutcome = Literal["code", "cancelled", "timed_out"]


@dataclass(frozen=True)
class CallbackResult:
    """How the browser hand-off ended; `code` is set only when the user approved."""

    outcome: CallbackOutcome
    code: str | None = field(default=None, repr=False)
