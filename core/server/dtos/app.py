"""The settings the web server is started with."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServerSettings:
    port: int
    page_dir: Path  # the built page; empty until the page exists
    extra_origins: frozenset[str] = frozenset()  # the Vite dev page, only with --dev
