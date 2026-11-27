"""One file of a Kaggle dataset download."""

from dataclasses import dataclass


@dataclass(frozen=True)
class KaggleFileDownload:
    ref: str
    revision: str
    path: str
    size_bytes: int
    account_generation: int
