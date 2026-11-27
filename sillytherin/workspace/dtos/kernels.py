"""What a notebook kernel reports back."""

from dataclasses import dataclass, field
from typing import Literal

from nbformat import NotebookNode

RunStatus = Literal["done", "error", "interrupted", "timeout"]


@dataclass
class RunResult:
    status: RunStatus
    execution_count: int | None
    outputs: list[NotebookNode] = field(default_factory=list)  # nbformat outputs, ready to save
    error_name: str | None = None
    kernel_generation: str | None = None
    missing_name: str | None = None


@dataclass(frozen=True)
class NotebookBindings:
    """Metadata from the live notebook namespace, without values."""

    modules: dict[str, str]  # public alias to dotted module name
    variables: list[str]  # public names other than module aliases
    truncated: bool  # some names were omitted, not confirmed absent
