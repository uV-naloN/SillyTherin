"""One file a catalog plans to download."""

from dataclasses import dataclass


@dataclass
class PlannedFile:
    path: str  # safe relative path (datasets.store.safe_relpath)
    size: int  # bytes, from the catalog
    checksum: str | None  # "sha256:…" (HF LFS) / "md5:…" (Zenodo) / None
    url: str  # https download URL built from the catalog API, pinned to the revision; a link: the pasted URL
    size_is_known: bool = True  # False when a linked website sent no size; the real size is recorded at the end
