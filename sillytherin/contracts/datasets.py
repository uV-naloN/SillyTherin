"""What the dataset code shares with the tools, the chat and the panel."""

from dataclasses import dataclass
from typing import Literal, TypedDict

from sillytherin.contracts.notebook import DatasetOrigin

Source = Literal["huggingface", "zenodo", "link", "kaggle"]

DatasetStatus = Literal["queued", "downloading", "ready", "failed", "missing"]  # missing is computed on read

DescribeNeed = Literal["write"]


@dataclass
class PlannedFile:
    path: str  # safe relative path (datasets.store.safe_relpath)
    size: int  # bytes, from the catalog
    checksum: str | None  # "sha256:…" (HF LFS) / "md5:…" (Zenodo) / None
    url: str  # https download URL built from the catalog API, pinned to the revision; a link: the pasted URL
    size_is_known: bool = True  # False when a linked website sent no size; the real size is recorded at the end


@dataclass
class DatasetPlan:
    source: Source
    source_id: str  # HF repo id / Zenodo record id
    name: str  # HF card pretty_name or repo id; Zenodo title
    revision: str | None  # HF commit sha; None for Zenodo
    url: str  # landing page
    license: str | None
    files: list[PlannedFile]  # after the `files` filter
    description: str | None = None  # the catalog's one-line description (≤ DESCRIPTION_MAX); None when it gave none


class FileView(TypedDict):
    path: str  # /work/datasets/<id>/<file>
    size: int  # bytes


class DownloadStarted(TypedDict):
    dataset_id: str  # handle for dataset_list / dataset_delete / the panel
    status: DatasetStatus  # queued / downloading / ready (when everything was there)
    already_downloaded: bool  # nothing new was fetched
    position_in_queue: int  # 0 = running now; >0 = jobs ahead
    size_bytes: int  # total the project will have
    download_bytes: int  # what has to be downloaded now
    path: str  # /work/datasets/<id>
    files: list[FileView]
    license: str | None
    url: str | None
    note: str  # tells the model not to read the files yet
    from_pool: bool  # files were linked from the pool; nothing fetched for them


class DatasetRecordView(TypedDict):
    dataset_id: str
    name: str
    status: DatasetStatus
    done_bytes: int | None  # while downloading
    position_in_queue: int | None  # while queued
    size_bytes: int
    license: str | None
    url: str | None
    path: str  # /work/datasets/<id>
    files: list[str]  # first 50 /work/... paths
    file_count: int
    missing_files: list[str]
    downloaded_at: str | None
    error: str | None
    shared_with: int  # other projects using the same pool entry
    origin: DatasetOrigin  # tells the model a file was created in this project, not downloaded
    description: str | None  # the catalog's one-line description (downloads); None for created / link / older rows
    columns: list[str] | None  # created files' column names; [] = unknown; None for downloads
    assistant_description: str | None  # one line the assistant wrote after looking; shown as "described by the assistant"
    describe: DescribeNeed | None  # "write" when the row says nothing about itself (describe.py)
    load_code: str  # the same Python as the tab's "Copy load code" (load_code.py)


class DeleteResult(TypedDict):
    deleted: str  # dataset id
    freed_bytes: int  # 0 while other projects still use the pool entry
    still_used_by: int  # projects still using it after this delete
