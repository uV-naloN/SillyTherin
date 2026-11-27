"""What the Kaggle window lists."""

from dataclasses import asdict, dataclass, field
from typing import Literal

ListingState = Literal["listed", "not_connected", "signed_out", "unreachable"]


@dataclass(frozen=True)
class OwnedDataset:
    """One row of the window."""

    title: str  # first line; searched
    ref: str  # "owner/slug": second line; what a later download uses
    total_bytes: int | None  # Size column; None when Kaggle doesn't say
    licence: str | None  # Licence column, as Kaggle names it
    last_updated: str | None  # Updated column, ISO-8601 UTC


@dataclass(frozen=True)
class MyDatasetsAnswer:
    """What the window shows; rows only when listed, so a failure never leaves an old or half list."""

    state: ListingState
    username: str | None = None
    datasets: list[OwnedDataset] = field(default_factory=list)
    is_truncated: bool = False

    def to_page(self) -> dict:
        """The answer as the page receives it."""
        return asdict(self)
