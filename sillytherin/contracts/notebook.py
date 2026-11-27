"""What the notebook panel shows, shared with the chat and the tools."""

import re
from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict

PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

PROJECT_ID_MAX = 40

CellType = Literal["code", "markdown"]

KernelStatus = Literal["off", "starting", "idle", "busy", "dead", "unavailable"]


@dataclass
class CellCompilationError(Exception):
    """A compile rejection reported without a traceback."""

    message: str
    line_number: int | None = None


CellStatus = Literal["idle", "queued", "running", "done", "error", "interrupted", "timeout"]

OutputKind = Literal["stdout", "stderr", "result", "image", "error"]


class ProjectRef(TypedDict):
    id: str  # which workspace the panel shows (folder name)
    name: str  # friendly name for the header and project bar


class OutputProps(TypedDict):
    kind: OutputKind  # how to render it
    text: str  # stdout/stderr/result text, or a traceback
    image: str  # base64 PNG for kind == "image"


class CellProps(TypedDict):
    id: str  # stable nbformat id, survives reordering
    type: CellType  # code runs in the kernel; markdown is a note, never run
    source: str  # the code, or the note's Markdown (<= 100 KB)
    execution_count: int | None  # the [n] label; always None for notes
    status: CellStatus  # the cell's badge
    outputs: list[OutputProps]  # what to show under the cell; empty for notes


class ErrorProps(TypedDict):
    code: str  # an ErrorCode value
    message: str  # one-line banner text


DatasetPanelStatus = Literal["queued", "downloading", "ready", "failed", "missing"]

DatasetOrigin = Literal["downloaded", "created"]  # catalog download, or a file the project's code wrote


class DatasetFileProps(TypedDict):
    path: str  # relative to the dataset folder (the panel adds /work/datasets/<id>/)
    size: int  # bytes


class DatasetProps(TypedDict):
    """One card in the panel's Datasets tab."""

    id: str  # sent back in ds_delete / ds_cancel
    name: str  # row title
    source: str  # source badge (huggingface / zenodo)
    kaggle_ref: NotRequired[str]  # canonical match key, only for a Kaggle card in this project
    status: DatasetPanelStatus  # badge while not ready; a ready row has no badge
    done_bytes: int | None  # progress bar while downloading
    position_in_queue: int | None  # "2nd in queue" while queued
    license: str | None  # chip while downloading, and in the details
    size_bytes: int  # "143.8 MB"
    file_count: int  # "· 1 file" when ready
    url: str | None  # source link in the details (only if https:)
    path: str  # copy-able /work/datasets/<id> path
    downloaded_at: str | None  # "added 2 h ago"
    requested_at: str  # sort by newest
    files: list[DatasetFileProps]  # file list and "Copy load code" (first 200)
    error: str | None  # red line when failed
    shared_with: int  # other projects using the same pool entry; "Also used by N other projects"
    origin: DatasetOrigin  # which card layout to use, catalog details or "Created by myself"
    assistant_description: str | None  # "Described by the assistant: …" line; None when the assistant wrote none


class NotebookProps(TypedDict):
    project: ProjectRef
    version: int  # sent back with edits so stale ones are rejected
    kernel: KernelStatus  # status dot; enables Stop / Restart
    running_cell_id: str | None  # which cell shows the spinner
    cells: list[CellProps]
    error: ErrorProps | None  # set when the action was rejected
    datasets: list[DatasetProps]  # the project's datasets
    datasets_rev: int  # bumps on every dataset change, so the panel keeps the newest list
