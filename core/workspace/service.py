"""Edit and run notebooks under a per-project lock, and build panel props.

Every edit carries the version the panel last saw; a mismatch is STALE and
the latest notebook comes back instead.

Two locks per project: `edit` (short, guards the .ipynb file) and `run`
(one run at a time, later runs wait in order). Typing still saves while a
cell runs, because a run only holds the edit lock at its start and end.
"""

import ast
import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from pathlib import Path
from typing import Literal

from nbformat import NotebookNode

from core.configuration.config import NOTEBOOK_READ_MAX_CHARS, WORKSPACES_DIR
from core.contracts.notebook import (
    CellProps,
    CellStatus,
    CellType,
    DatasetProps,
    NotebookProps,
    OutputProps,
    ProjectRef,
)
from core.workspace import store
from core.workspace.dtos.kernels import NotebookBindings, RunResult
from core.workspace.errors import ErrorCode, WorkspaceError
from core.workspace.kernels import KernelRegistry
from core.workspace.recovery import require_repeatable_correction
from core.workspace.store import Direction

MODEL_OUTPUT_MAX_CHARS = 4000
MODEL_CELL_OUTPUT_MAX_CHARS = 2000
TRUNCATION_NOTICE_MAX_CHARS = 40
PUSH_INTERVAL = 0.25  # seconds between live output pushes
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

log = logging.getLogger(__name__)


class NotebookRunOutcome(Enum):
    RESTART_REQUIRED = "restart_required"


def defines_global(source: str, name: str) -> bool:
    """Recognize direct top-level definitions without evaluating notebook source."""
    try:
        statements = ast.parse(source).body
    except (SyntaxError, RecursionError):
        return False
    return any(name in defined_names(statement) for statement in statements)


def defined_names(statement: ast.stmt) -> set[str]:
    if isinstance(statement, ast.Assign):
        return {target.id for target in statement.targets if isinstance(target, ast.Name)}
    if isinstance(statement, ast.AnnAssign) and statement.value is not None and isinstance(statement.target, ast.Name):
        return {statement.target.id}
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {statement.name}
    if isinstance(statement, ast.Import):
        return {alias.asname or alias.name.split(".", 1)[0] for alias in statement.names}
    if isinstance(statement, ast.ImportFrom):
        return {alias.asname or alias.name for alias in statement.names if alias.name != "*"}
    return set()


def has_old_definition(notebook: NotebookNode, cell_id: str, generation: str, name: str) -> bool:
    has_previous_definition = False
    for cell in notebook.cells:
        if cell.id == cell_id:
            return has_previous_definition
        if cell.cell_type != "code" or _status(cell) != "done":
            continue
        if not defines_global(_text(cell.source), name):
            continue
        previous_generation = store.cell_kernel_generation(cell)
        if previous_generation == generation:
            return False
        has_previous_definition |= bool(previous_generation)
    return False


# ---- NotebookProps (server -> panel) --------------------------------------


Notify = Callable[[str, NotebookProps], Awaitable[None]]
DatasetState = Callable[[str], tuple[list[DatasetProps], int]]  # project_id -> (datasets, datasets_rev)
RunBy = Literal["assistant", "user"]  # who started a run: notebook_run, or a cell run from the panel
AfterRun = Callable[[str, RunBy | None], None]  # (project_id, by) -> scan its files/ for created datasets; called after every cell run


# ---- output conversion ----------------------------------------------------------


def _text(value) -> str:
    return "".join(value) if isinstance(value, list) else str(value or "")


def output_props(out: NotebookNode) -> OutputProps:
    """One nbformat output -> what the panel draws. HTML is never rendered."""
    kind = out.get("output_type")
    if kind == "stream":
        # Shell commands print CRLF and ANSI colour codes; the panel shows plain text.
        text = ANSI_RE.sub("", _text(out.get("text"))).replace("\r\n", "\n")
        return {"kind": "stderr" if out.get("name") == "stderr" else "stdout", "text": text, "image": ""}
    if kind == "error":
        trace = "\n".join(out.get("traceback") or []) or f"{out.get('ename')}: {out.get('evalue')}"
        return {"kind": "error", "text": ANSI_RE.sub("", trace), "image": ""}
    data = out.get("data", {})
    if "image/png" in data:
        return {"kind": "image", "text": "", "image": _text(data["image/png"]).strip()}
    for mime in ("text/plain", "text/markdown", "text/html"):
        if mime in data:
            return {"kind": "result", "text": _text(data[mime]), "image": ""}
    return {"kind": "result", "text": "[output type not shown]", "image": ""}


def _status(cell: NotebookNode) -> CellStatus:
    return cell.metadata.get("sillytherin", {}).get("status", "idle")


def _set_status(cell: NotebookNode, status: CellStatus) -> None:
    cell.metadata.setdefault("sillytherin", {})["status"] = status


def _image_size(b64: str) -> str:
    """'WxH' of a base64 PNG, read from its header."""
    import base64

    try:
        head = base64.b64decode(b64[:64] + "=" * (-len(b64[:64]) % 4))
        return f"{int.from_bytes(head[16:20], 'big')}x{int.from_bytes(head[20:24], 'big')}"
    except ValueError:  # binascii.Error from bad base64
        return "?x?"


def outputs_as_text(outputs: list[OutputProps]) -> str:
    """Outputs as plain text for the model; images are described, not sent."""
    parts = []
    for o in outputs:
        if o["kind"] == "image":
            parts.append(f"[image {_image_size(o['image'])}]")
        else:
            parts.append(o["text"].rstrip("\n"))
    return "\n".join(p for p in parts if p)


def trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[cut off at {limit:,} characters]"


def render_notebook_bindings(bindings: NotebookBindings | None, max_chars: int) -> str:
    if bindings is None:
        return "Bindings unavailable"[:max_chars]
    modules = ", ".join(f"{alias}={module}" for alias, module in sorted(bindings.modules.items()))
    variables = ", ".join(sorted(bindings.variables))
    sections = [label for label in (f"modules {modules}" if modules else "", f"variables {variables}" if variables else "") if label]
    footer = "Live bindings: " + ("; ".join(sections) if sections else "none")
    if bindings.truncated:
        footer += " (some names omitted)"
    if len(footer) > max_chars:
        omission = "… (some names omitted)"
        if max_chars <= len(omission):
            return omission[:max_chars]
        return footer[:max_chars - len(omission)] + omission
    return footer


def dependency_error_hint(result: RunResult) -> str | None:
    error_name = result.error_name
    if error_name is None:
        error_name = next(
            (output.get("ename") for output in result.outputs if output.get("output_type") == "error"),
            None,
        )
    if result.status != "error" or error_name not in {"NameError", "ModuleNotFoundError"}:
        return None
    return f"{error_name}: check the live bindings before correcting this step."


def describe_run_provenance(cell: NotebookNode, generation: str | None) -> str:
    recorded_generation = store.cell_kernel_generation(cell)
    if recorded_generation is None:
        return "run unverified" if cell.get("execution_count") else "not run"
    return "current kernel" if recorded_generation == generation else "old kernel"


def render_model_cell(cell: NotebookNode, generation: str | None, version: int, status: CellStatus) -> str:
    if cell.cell_type == "markdown":
        return f"[note]\n{_text(cell.source)}"
    execution_count = cell.get("execution_count")
    run_label = f", run {execution_count}" if execution_count else ", not run"
    output = outputs_as_text([output_props(output) for output in cell.get("outputs", [])])
    rendered_output = f"\n--- output ---\n{trim(output, MODEL_CELL_OUTPUT_MAX_CHARS)}" if output else ""
    provenance = describe_run_provenance(cell, generation)
    metadata = f"\n[id={cell.id} version={version} {status}; {provenance}]"
    return f"[cell{run_label}]\n{_text(cell.source)}{rendered_output}{metadata}"


def fit_notebook_context(blocks: list[str], footer: str, max_chars: int) -> str:
    kept: list[str] = []
    used = len(footer) + 2
    for block in reversed(blocks):
        if used + len(block) + 2 <= max_chars:
            kept.insert(0, block)
            used += len(block) + 2
    dropped = len(blocks) - len(kept)
    if not dropped:
        return "\n\n".join(kept + [footer])
    while kept:
        heading = f"[{dropped} earlier cells not shown]"
        context = "\n\n".join([heading] + kept + [footer])
        if len(context) <= max_chars:
            return context
        kept.pop(0)
        dropped += 1
    heading = f"[{dropped} earlier cells not shown]"
    context = f"{heading}\n\n{footer}"
    return context if len(context) <= max_chars else footer


def require_correctable_cell(notebook: NotebookNode, cell_id: str, source: str) -> None:
    cell = store.find_cell(notebook, cell_id)
    is_assistant_cell = cell.metadata.get("sillytherin", {}).get("created_by") == "assistant"
    if cell.cell_type != "code" or not is_assistant_cell or _status(cell) != "error":
        raise WorkspaceError(ErrorCode.BAD_PAYLOAD, "Only a failed assistant-created code cell can be corrected.")
    if store.cell_kernel_generation(cell) is None:
        raise WorkspaceError(ErrorCode.STALE, "The failed cell changed; read the notebook again.")
    if len(source.encode("utf-8")) > store.MAX_SOURCE_BYTES:
        raise WorkspaceError(ErrorCode.TOO_LARGE, "A cell can hold at most 100 KB.")
    if not source.strip():
        raise WorkspaceError(ErrorCode.BAD_PAYLOAD, "Replacement code must not be blank.")
    require_repeatable_correction(_text(cell.source), source)


def render_run_result(result: RunResult, cell_id: str, version: int) -> str:
    text = outputs_as_text([output_props(output) for output in result.outputs])
    label = {"done": "out", "error": "error", "interrupted": "interrupted", "timeout": "timed out"}[result.status]
    hint = dependency_error_hint(result)
    prefix = f"{hint}\n" if hint else ""
    metadata = f"\nCell id: {cell_id}; notebook version: {version}"
    return prefix + trim(
        f"[cell {result.execution_count}] {label}: {text or '(no output)'}",
        MODEL_OUTPUT_MAX_CHARS - len(prefix) - len(metadata) - TRUNCATION_NOTICE_MAX_CHARS,
    ) + metadata


class NotebookService:
    """The only place that changes notebooks or runs code."""

    def __init__(
        self,
        kernels: KernelRegistry,
        root: Path = WORKSPACES_DIR,
        notify: Notify | None = None,
        datasets: DatasetState | None = None,
        after_run: AfterRun | None = None,
    ):
        self.kernels = kernels
        self.root = root
        self.notify = notify
        self.datasets = datasets  # set by actions.py after DatasetService is built
        self.after_run = after_run  # DatasetService.sync_created, set by actions.py like `datasets`
        self._edit_locks: dict[str, asyncio.Lock] = {}
        self._run_locks: dict[str, asyncio.Lock] = {}
        self._running: dict[str, str] = {}  # project_id -> running cell id
        self._queued: dict[str, list[str]] = {}  # project_id -> waiting cell ids
        self._live: dict[str, list[OutputProps]] = {}  # cell id -> outputs so far

    # ---- helpers ----

    def _edit_lock(self, pid: str) -> asyncio.Lock:
        return self._edit_locks.setdefault(pid, asyncio.Lock())

    def _run_lock(self, pid: str) -> asyncio.Lock:
        return self._run_locks.setdefault(pid, asyncio.Lock())

    def run_lock(self, project_id: str) -> asyncio.Lock:
        """The project's run lock, for CreatedDatasets.delete: no cell runs between its check and unlink."""
        return self._run_lock(project_id)

    def _props(self, pid: str, nb: NotebookNode, error: WorkspaceError | None = None) -> NotebookProps:
        project = store.get_project(pid, self.root)
        datasets, datasets_rev = self.datasets(pid) if self.datasets else ([], 0)
        running = self._running.get(pid)
        queued = self._queued.get(pid, [])
        cells: list[CellProps] = []
        for cell in nb.cells:
            is_code = cell.cell_type == "code"
            status = "running" if cell.id == running else "queued" if cell.id in queued else _status(cell)
            outputs = self._live.get(cell.id) if cell.id == running else [output_props(o) for o in cell.get("outputs", [])]
            cells.append(
                {
                    "id": cell.id,
                    "type": "code" if is_code else "markdown",
                    "source": _text(cell.source),
                    "execution_count": cell.get("execution_count") if is_code else None,
                    "status": status if is_code else "idle",
                    "outputs": (outputs or []) if is_code else [],
                }
            )
        return {
            "project": {"id": project.id, "name": project.name},
            "version": store.get_version(nb),
            "kernel": self.kernels.status(pid),
            "running_cell_id": running,
            "cells": cells,
            "error": {"code": error.code, "message": error.message} if error else None,
            "datasets": datasets,
            "datasets_rev": datasets_rev,
        }

    async def _push(self, pid: str, props: NotebookProps) -> None:
        if self.notify:
            await self.notify(pid, props)

    async def _change(self, pid: str, version: int | None, change: Callable[[NotebookNode], None]) -> NotebookProps:
        """Load, check the version, apply `change`, bump, save, push."""
        async with self._edit_lock(pid):
            nb = store.load_notebook(pid, self.root)
            if version is not None and version != store.get_version(nb):
                raise WorkspaceError(ErrorCode.STALE, "Notebook changed, reloaded.")
            change(nb)
            store.bump_version(nb)
            store.save_notebook(pid, nb, self.root)
            props = self._props(pid, nb)
        await self._push(pid, props)
        return props

    # ---- reading ----

    async def props(self, project_id: str, error: WorkspaceError | None = None) -> NotebookProps:
        """Current state of a project's notebook for the panel."""
        nb = store.load_notebook(project_id, self.root)
        return self._props(project_id, nb, error)

    # ---- editing (all check `version`) ----

    async def add_cell(self, project_id: str, version: int, after_id: str | None, cell_type: CellType) -> NotebookProps:
        return await self._change(project_id, version, lambda nb: store.add_cell(nb, cell_type, after_id))

    async def update_cell(self, project_id: str, version: int, cell_id: str, source: str) -> NotebookProps:
        return await self._change(project_id, version, lambda nb: store.update_cell(nb, cell_id, source))

    async def delete_cell(self, project_id: str, version: int, cell_id: str) -> NotebookProps:
        return await self._change(project_id, version, lambda nb: store.delete_cell(nb, cell_id))

    async def move_cell(self, project_id: str, version: int, cell_id: str, direction: Direction) -> NotebookProps:
        return await self._change(project_id, version, lambda nb: store.move_cell(nb, cell_id, direction))

    async def reorder_cell(
        self, project_id: str, version: int, cell_id: str, before_id: str | None
    ) -> NotebookProps:
        """Drag-and-drop: put `cell_id` just before `before_id` (None = at the end)."""
        return await self._change(project_id, version, lambda nb: store.reorder_cell(nb, cell_id, before_id))

    async def duplicate_cell(self, project_id: str, version: int, cell_id: str) -> NotebookProps:
        return await self._change(project_id, version, lambda nb: store.duplicate_cell(nb, cell_id))

    async def clear_cell_outputs(self, project_id: str, version: int, cell_id: str) -> NotebookProps:
        return await self._change(project_id, version, lambda nb: store.clear_cell_outputs(nb, cell_id))

    async def clear_outputs(self, project_id: str, version: int) -> NotebookProps:
        return await self._change(project_id, version, store.clear_outputs)

    # ---- running ----

    async def run_cell(self, project_id: str, version: int, cell_id: str, source: str) -> NotebookProps:
        """Save the source, run it, push outputs to every open panel of the project."""

        def save_source(nb: NotebookNode) -> None:
            cell = store.find_cell(nb, cell_id)
            if cell.cell_type != "code":
                raise WorkspaceError(ErrorCode.NOT_CODE, "Notes don't run.")
            store.update_cell(nb, cell_id, source)

        await self._change(project_id, version, save_source)
        await self._run(project_id, cell_id)
        return await self.props(project_id)

    async def _run(
        self, pid: str, cell_id: str, by: RunBy = "user", *, validated_source: str | None = None
    ) -> RunResult | None:
        """Run one saved code cell; returns None if it vanished meanwhile. `by` goes to the created-file scan."""
        self._queued.setdefault(pid, []).append(cell_id)
        await self._push(pid, await self.props(pid))
        try:
            async with self._run_lock(pid):
                self._queued[pid].remove(cell_id)
                async with self._edit_lock(pid):
                    nb = store.load_notebook(pid, self.root)
                    try:
                        code = _text(store.find_cell(nb, cell_id).source)
                    except WorkspaceError:
                        return None
                    if validated_source is not None and code != validated_source:
                        raise WorkspaceError(ErrorCode.STALE, "The cell changed after validation; it was not run.")
                self._running[pid] = cell_id
                self._live[cell_id] = []
                await self._push(pid, await self.props(pid))

                last_push = [0.0]

                async def on_output(out: NotebookNode) -> None:
                    self._live[cell_id].append(output_props(out))
                    # At most a few pushes a second; the final state is pushed on save
                    now = time.monotonic()
                    if now - last_push[0] >= PUSH_INTERVAL:
                        last_push[0] = now
                        await self._push(pid, await self.props(pid))

                try:
                    files_dir = store.project_dir(pid, self.root) / store.FILES_DIR
                    result = await self.kernels.execute(pid, code, on_output, files_dir)
                except Exception:
                    # Tell every panel the run is over (e.g. Docker not running)
                    self._running.pop(pid, None)
                    self._live.pop(cell_id, None)
                    await self._push(pid, await self.props(pid))
                    raise
                finally:
                    self._running.pop(pid, None)
                    self._live.pop(cell_id, None)

                self._scan_created_datasets(pid, by)  # before save_result's push, so that push shows the new card

                def save_result(nb: NotebookNode) -> None:
                    try:
                        cell = store.find_cell(nb, cell_id)
                    except WorkspaceError:
                        return  # deleted while running: drop the result
                    cell.outputs = result.outputs
                    cell.execution_count = result.execution_count
                    _set_status(cell, result.status)
                    if result.kernel_generation is not None and _text(cell.source) == code:
                        store.record_kernel_generation(cell, result.kernel_generation)
                    else:
                        store.clear_kernel_generation(cell)

                await self._change(pid, None, save_result)
                return result
        finally:
            if cell_id in self._queued.get(pid, []):
                self._queued[pid].remove(cell_id)

    def _scan_created_datasets(self, pid: str, by: RunBy | None = None) -> None:
        """Let the after-run hook list files the cell wrote; a scan problem never fails the run."""
        if self.after_run is None:
            return
        try:
            self.after_run(pid, by)
        except Exception:
            log.exception("Scanning %s for created datasets failed", pid)

    async def run_all(self, project_id: str, version: int) -> NotebookProps:
        """Run code cells top to bottom, skip notes, stop at the first error."""
        nb = store.load_notebook(project_id, self.root)
        if version != store.get_version(nb):
            raise WorkspaceError(ErrorCode.STALE, "Notebook changed, reloaded.")
        for cell_id in [c.id for c in nb.cells if c.cell_type == "code"]:
            result = await self._run(project_id, cell_id)
            if result is not None and result.status != "done":
                break
        return await self.props(project_id)

    async def interrupt(self, project_id: str) -> NotebookProps:
        await self.kernels.interrupt(project_id)
        return await self.props(project_id)

    async def restart(self, project_id: str) -> NotebookProps:
        """Fresh kernel: variables cleared, cells and outputs kept."""
        await self.kernels.restart(project_id, store.project_dir(project_id, self.root) / store.FILES_DIR)
        props = await self.props(project_id)
        await self._push(project_id, props)
        return props

    # ---- projects (bar under the message box) ----

    async def list_projects(self) -> list[ProjectRef]:
        return [{"id": p.id, "name": p.name} for p in store.list_projects(self.root)]

    async def create_project(self, name: str) -> NotebookProps:
        project = store.create_project(name, self.root)
        return await self.props(project.id)

    # ---- model tools ----

    async def read_for_model(self, project_id: str, max_chars: int = NOTEBOOK_READ_MAX_CHARS) -> str:
        """Cells, trimmed outputs and variable names as text (notebook_read)."""
        notebook = store.load_notebook(project_id, self.root)
        generation = self.kernels.generation(project_id)
        bindings = await self.kernels.notebook_bindings(project_id) if self.kernels.status(project_id) == "idle" else None
        if generation != self.kernels.generation(project_id):
            bindings = None
            generation = None
        statuses: dict[str, CellStatus] = dict.fromkeys(self._queued.get(project_id, []), "queued")
        if running_cell_id := self._running.get(project_id):
            statuses[running_cell_id] = "running"
        version = store.get_version(notebook)
        blocks = [render_model_cell(cell, generation, version, statuses.get(cell.id, _status(cell))) for cell in notebook.cells]
        footer = render_notebook_bindings(bindings, max_chars)
        return fit_notebook_context(blocks, footer, max_chars)

    async def diagnose_failed_cell(
        self, project_id: str, cell_id: str, result: RunResult
    ) -> NotebookRunOutcome | None:
        generation = result.kernel_generation
        name = result.missing_name
        if result.status != "error" or not generation or not name:
            return None
        if self.kernels.generation(project_id) != generation:
            return None
        bindings = await self.kernels.notebook_bindings(project_id)
        if bindings is None or bindings.truncated or name in bindings.modules or name in bindings.variables:
            return None
        if self.kernels.generation(project_id) != generation:
            return None
        notebook = store.load_notebook(project_id, self.root)
        if has_old_definition(notebook, cell_id, generation, name):
            return NotebookRunOutcome.RESTART_REQUIRED
        return None

    async def edit_failed_cell_for_model(
        self, project_id: str, cell_id: str, source: str, version: int
    ) -> str | NotebookRunOutcome:
        async with self._edit_lock(project_id):
            notebook = store.load_notebook(project_id, self.root)
            if type(version) is not int or version != store.get_version(notebook):
                raise WorkspaceError(ErrorCode.STALE, "Notebook changed, read it again before correcting.")
            require_correctable_cell(notebook, cell_id, source)
        files_dir = store.project_dir(project_id, self.root) / store.FILES_DIR
        await self.kernels.validate_cell_source(project_id, files_dir, source)

        def replace_source(notebook: NotebookNode) -> None:
            require_correctable_cell(notebook, cell_id, source)
            store.update_cell(notebook, cell_id, source)

        await self._change(project_id, version, replace_source)
        return await self.run_model_cell(project_id, cell_id, source)

    async def run_for_model(self, project_id: str, code: str) -> str | NotebookRunOutcome:
        """Add a code cell at the end, run it, return trimmed output text (notebook_run)."""
        files_dir = store.project_dir(project_id, self.root) / store.FILES_DIR
        await self.kernels.validate_cell_source(project_id, files_dir, code)
        added: dict[str, str] = {}

        def add(nb: NotebookNode) -> None:
            cell = store.add_cell(nb, "code", None)
            store.update_cell(nb, cell.id, code)
            store.mark_assistant_cell(cell)
            added["id"] = cell.id

        await self._change(project_id, None, add)
        return await self.run_model_cell(project_id, added["id"], code)

    async def run_model_cell(self, project_id: str, cell_id: str, source: str) -> str | NotebookRunOutcome:
        result = await self._run(project_id, cell_id, by="assistant", validated_source=source)
        if result is None:
            return "The cell was deleted before it ran."
        outcome = await self.diagnose_failed_cell(project_id, cell_id, result)
        if outcome is not None:
            return outcome
        notebook = store.load_notebook(project_id, self.root)
        return render_run_result(result, cell_id, store.get_version(notebook))
