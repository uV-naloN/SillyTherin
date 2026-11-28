"""What one scan of the created files found."""

from dataclasses import dataclass, field

from core.workspace.datasets.dtos.created import CreatedDataset, FileSnapshot


@dataclass
class ScanChanges:
    """What one scan found compared with the stored rows; computed without touching storage."""

    new_files: list[FileSnapshot] = field(default_factory=list)  # insert
    resaved: list[CreatedDataset] = field(default_factory=list)  # same id, new size/mtime
    renamed: list[CreatedDataset] = field(default_factory=list)  # same id, new path
