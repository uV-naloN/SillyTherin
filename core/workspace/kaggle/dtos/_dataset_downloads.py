"""The four answers of one Kaggle dataset download request."""

from typing import Literal, TypedDict


class KaggleDownloadAccepted(TypedDict):
    state: Literal["accepted"]
    dataset_id: str  # public project dataset ID, not the pool ID


class KaggleDownloadSkipped(TypedDict):
    state: Literal["skipped"]


class KaggleDownloadError(TypedDict):
    code: str
    message: str  # sanitized one-line text for the picker


class KaggleDownloadRejected(TypedDict):
    state: Literal["error"]
    error: KaggleDownloadError
