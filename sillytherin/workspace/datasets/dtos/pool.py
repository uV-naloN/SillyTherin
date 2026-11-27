"""The shared pool: its entries and their files."""

from dataclasses import dataclass, field

from sillytherin.contracts.datasets import Source


@dataclass
class PoolFile:
    path: str  # relative to the pool entry folder; checked by safe_relpath
    size: int  # bytes, as the catalog declared it
    checksum: str | None  # "md5:…" / "sha256:…", or None when the catalog gives none
    ready: bool = False  # downloaded, verified and moved into the entry folder
    mtime_ns: int | None = None  # lets the app notice a file changed on disk


@dataclass
class PoolEntry:
    id: str  # "<dataset-id>@<first 7 of revision>" (Zenodo: the dataset id); pool folder name
    source: Source  # which catalog
    source_id: str  # stable key in that catalog
    revision: str | None  # HF commit hash; None for Zenodo
    name: str  # title for people
    url: str | None  # landing page
    license: str | None  # shown in the details
    files: list[PoolFile] = field(default_factory=list)
    created_at: str = ""  # ISO 8601 UTC; "downloaded …" in details
    description: str | None = None  # the catalog's one-line description; the same for every project using it
