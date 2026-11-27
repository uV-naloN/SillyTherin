"""One pool file a project may read."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ViewFile:
    """One pool file a project may read."""

    entry_id: str  # pool folder name (checked by check_entry_id); also the folder name in the view
    path: str  # file inside the entry (checked by safe_relpath); the same path the project's link uses
