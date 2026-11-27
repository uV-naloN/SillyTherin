"""The dataset limits read from config.toml."""

from dataclasses import dataclass


@dataclass
class DatasetsSettings:
    """config.toml [datasets], injected so tests can change limits."""

    min_free_disk_mb: int = 0  # extra disk always left free
    ask_above_mb: int = 200  # bigger downloads ask Download / Skip first
    max_files: int = 500  # files per request (count, not size)
    parallel_downloads: int = 2  # running at once, app-wide
    stall_timeout_seconds: float = 120  # fail when no data arrives this long
