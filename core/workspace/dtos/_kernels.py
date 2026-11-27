"""Settings that only the kernel registry uses."""

from dataclasses import dataclass
from pathlib import Path

from core.config import (
    DOCKER_CPUS,
    DOCKER_IMAGE,
    DOCKER_MEMORY,
    DOCKER_PIDS_LIMIT,
)


@dataclass
class DockerSettings:
    image: str = DOCKER_IMAGE
    memory: str = DOCKER_MEMORY
    cpus: float = DOCKER_CPUS
    pids_limit: int = DOCKER_PIDS_LIMIT


@dataclass
class ContainerMounts:
    """Host folders bound into a kernel container."""

    files_dir: Path  # → /work, read-write
    conn_dir: Path  # → /conn, read-only
    dataset_view_dir: Path  # → /.dataset-pool, read-only; only the project's own datasets
