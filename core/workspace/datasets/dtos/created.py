"""A created data file as one scan saw it and as it is stored."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FileSnapshot:
    """What one scan saw of a data file: enough to spot a re-save or a rename."""

    path: str  # POSIX, relative to files/
    size_bytes: int
    mtime_ns: int  # changes on every save; part of the rename match
    inode: int  # kept by a rename; with size and mtime it finds the card of a renamed file


@dataclass
class CreatedDataset:
    """One created card, as stored."""

    id: str  # stored slug (file-iris-csv); kept on rename
    snapshot: FileSnapshot  # path, size, mtime, inode at the last scan that changed it
    first_seen_at: str  # ISO 8601 UTC, project_datasets.requested_at; sort order
    changed_at: str  # ISO 8601 UTC, project_datasets.linked_at; "added 2 h ago"
    columns: list[str] | None = None  # column names from the file's start; None = not read yet, [] = none found
    created_by: str | None = None  # "assistant" / "user" (a cell run by either) or "upload"; None counts as an upload
    assistant_description: str | None = None  # one line the assistant wrote after looking at the file
