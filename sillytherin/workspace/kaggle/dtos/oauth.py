"""What the Kaggle sign-in exchanges."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OAuthClient:
    """The app's registered Kaggle client and where Kaggle sends the browser back to."""

    client_id: str
    redirect_uri: str


@dataclass(frozen=True)
class PkcePair:
    """A random verifier the app keeps, and its SHA-256 challenge that goes in the browser link."""

    verifier: str = field(repr=False)
    challenge: str


@dataclass(frozen=True)
class TokenGrant:
    """What Kaggle's token endpoint answered. Lives in memory only."""

    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_in_seconds: int
    username: str | None
