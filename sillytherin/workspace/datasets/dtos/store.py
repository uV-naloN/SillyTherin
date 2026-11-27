"""A project's dataset row and the words describing it."""

from dataclasses import dataclass, field
from typing import Literal

Source = Literal["huggingface", "zenodo", "link", "kaggle"]
StoredStatus = Literal["queued", "downloading", "ready", "failed"]
DatasetStatus = Literal["queued", "downloading", "ready", "failed", "missing"]  # missing is computed on read


@dataclass
class ProjectDataset:
    id: str  # generated slug, folder name under files/datasets/
    pool_entry: str  # which pooled version this project uses; changes only with update: true
    selection: list[str] | None  # every `files` pattern asked for so far; None = all files
    status: StoredStatus
    files: list[str] = field(default_factory=list)  # paths only; sizes and checksums live in the pool entry
    requested_at: str = ""  # ISO 8601 UTC; queue order
    linked_at: str | None = None  # when this project got the files (may be long after the pool did)
    error: str | None = None  # one-line reason when failed
    assistant_description: str | None = None  # one line the assistant wrote for this project; cleared on update
