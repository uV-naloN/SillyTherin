"""Data that only the Kaggle account service uses."""

import asyncio
from dataclasses import dataclass, field

from sillytherin.workspace.kaggle.callback import CallbackListener


@dataclass
class PendingSignIn:
    listener: CallbackListener
    finished: asyncio.Task | None = None


@dataclass(frozen=True)
class CachedAccessToken:
    token: str = field(repr=False)
    refresh_at: float  # monotonic seconds
