"""What is kept between app restarts."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SavedSignIn:
    """What is kept between app restarts."""

    client_id: str
    refresh_token: str = field(repr=False)
    username: str
    connected_at: str  # ISO 8601 UTC, from clock.utc_now
