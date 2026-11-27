"""Data that only the dataset service uses while it serves one request."""

from dataclasses import dataclass
from typing import TypedDict

from sillytherin.workspace.datasets.dtos.pool import PoolEntry
from sillytherin.workspace.datasets.dtos.store import Source
from sillytherin.workspace.datasets.sources import DatasetPlan
from sillytherin.workspace.kaggle.dataset_downloads import (
    KaggleDatasetDownloads,
    KaggleImportSelection,
)


class FileView(TypedDict):
    path: str  # /work/datasets/<id>/<file>
    size: int  # bytes


@dataclass
class Waiter:
    """One project waiting for a job: its own dataset id and chat."""

    dataset_id: str
    session_id: str
    paths: set[str]  # every file this project asked for in the job's entry
    switches_entry: bool = False  # update: true to a new version; linked all at once when complete


@dataclass
class KaggleTransfer:
    downloads: KaggleDatasetDownloads
    selection: KaggleImportSelection
    revision: str


@dataclass
class DownloadRequest:
    """One dataset_download call."""

    project_id: str
    session_id: str
    source: Source
    source_id: str
    files: list[str] | None  # glob patterns; None = all files
    update: bool  # the user asked for the newest version
    pasted_links: frozenset[str] = frozenset()  # links found in the user's own messages of this chat


@dataclass
class RequestTarget:
    """What a request resolved to, before anything is written."""

    entry: PoolEntry  # the pool version the project will use (may be new, not yet in the index)
    plan: DatasetPlan  # the files asked for, at that version
    is_new_entry: bool
