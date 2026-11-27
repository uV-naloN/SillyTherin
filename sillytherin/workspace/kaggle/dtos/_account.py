"""Data that only the Kaggle account service uses."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CachedAccessToken:
    token: str = field(repr=False)
    refresh_at: float  # monotonic seconds
