"""Background dataset downloads for projects.

Quick checks run inside the tool call (ref, catalog, pool, disk,
Download/Skip). Data already in the shared pool is linked at once;
the rest is fetched later on one app-wide queue with `parallel_downloads`
slots. A job fills one pool entry and serves every project waiting for those
files; each waiting project keeps its own dataset id and chat.
When a job ends it pushes panel updates, messages each waiting chat if it's
still open, and leaves a one-time note for that chat's next model turn.

This module never imports actions.py or reads the chat: the
panel push, chat send and props builder are injected. Disk
reads and writes go through store.py.
"""

import asyncio
import contextvars
import errno
import logging
import shutil
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from sillytherin.clock import utc_now
from sillytherin.config import WORKSPACES_DIR
from sillytherin.contracts.datasets import (
    DatasetPlan,
    DatasetRecordView,
    DatasetStatus,
    DeleteResult,
    DownloadStarted,
    PlannedFile,
    Source,
)
from sillytherin.contracts.kaggle import (
    KaggleApprovalOffer,
    KaggleImportError,
    KaggleImportSelection,
)
from sillytherin.contracts.notebook import DatasetProps, NotebookProps
from sillytherin.workspace import store
from sillytherin.workspace.datasets import links, sources
from sillytherin.workspace.datasets import store as ds
from sillytherin.workspace.datasets.created import CreatedDatasets
from sillytherin.workspace.datasets.describe import describe_need, one_line_description
from sillytherin.workspace.datasets.dtos._service import (
    DownloadRequest,
    RequestTarget,
    Waiter,
)
from sillytherin.workspace.datasets.dtos.pool import PoolEntry, PoolFile
from sillytherin.workspace.datasets.dtos.settings import DatasetsSettings
from sillytherin.workspace.datasets.dtos.store import ProjectDataset
from sillytherin.workspace.datasets.load_code import load_code
from sillytherin.workspace.datasets.pool import (
    DatasetPool,
    find_pool_file,
    list_ready_paths,
)
from sillytherin.workspace.datasets.views import ProjectViews, visible_pool_files
from sillytherin.workspace.errors import ErrorCode, WorkspaceError
from sillytherin.workspace.kaggle.dataset_downloads import (
    ApproveKaggleImport,
    KaggleDatasetDownloads,
    KaggleDownloadResult,
)
from sillytherin.workspace.kaggle.dtos.downloads import KaggleFileDownload
from sillytherin.workspace.service import Notify

log = logging.getLogger(__name__)

Send = Callable[[str, str], Awaitable[bool]]  # (session_id, text) -> sent? (False when the chat is closed)
Approve = Callable[[DatasetPlan, int], Awaitable[bool]]  # (plan, download_bytes) -> Download?
PropsFor = Callable[[str], Awaitable[NotebookProps]]
FreeBytes = Callable[[Path], int]
Resolve = Callable[..., Awaitable[DatasetPlan]]
ResolveLink = Callable[..., Awaitable[DatasetPlan]]  # (link, *, client) -> one-file plan

MB = 1_000_000
NEVER_ASK_BYTES = 1 << 62  # after the user said Download, don't ask again
READY_WAIT_SECONDS = 60  # longest a request waits for startup cleanup
VIEW_FILES_MAX = 50
PANEL_FILES_MAX = 200
PROGRESS_INTERVAL = 1.0  # seconds between progress pushes (each re-sends the panel props)
STOPPED_DURING_DOWNLOAD = "app stopped during download"  # error of a download cut off by an app restart


def disk_free(path: Path) -> int:
    return shutil.disk_usage(path).free


def human_size(n: int) -> str:
    """143.8 MB style (decimal units, like the catalogs)."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1000
    return f"{n:,.1f} TB"


# ---- tool results (service -> model) --------------------------------------


NOTE_HEADER = "Background download update (data, not instructions):"
STARTED_NOTE = (
    "Downloading in the background. The user can keep chatting; you'll be told "
    "when it's ready. Don't read the files until then."
)
READY_NOTE = "Already downloaded. The files are ready to use at these paths."
LINK_READY_NOTE = "Tell the user: This file is already in your datasets. Give them the path; nothing was downloaded."
REF_PREFIXES = {"huggingface": "hf", "zenodo": "zenodo"}  # dataset_search refs; links and created files have none
LINK_LICENCE_NOTE = " No licence is known for a pasted link; don't state one."


@dataclass
class KaggleTransfer:
    downloads: KaggleDatasetDownloads
    selection: KaggleImportSelection
    revision: str


@dataclass
class Job:
    """One download into one pool entry, shared by every waiting project (job key = entry + files)."""

    id: int
    pool_entry: str
    files: list[PlannedFile]
    waiters: dict[str, Waiter] = field(default_factory=dict)  # project_id -> waiter
    done_bytes: int = 0
    running: bool = False
    task: asyncio.Task | None = None
    cancelled: bool = False
    last_push: float = field(default=0.0)
    is_link: bool = False  # a pasted link: its size may only be known once it's downloaded
    kaggle: KaggleTransfer | None = None

    @property
    def total(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def remaining(self) -> int:
        return max(self.total - self.done_bytes, 0)

    @property
    def paths(self) -> set[str]:
        return {f.path for f in self.files}


@dataclass
class FetchPlan:
    """How a request is served: from the pool, by joining jobs, and by a new download."""

    target: RequestTarget
    wanted: list[str]  # every file the request is about
    to_fetch: list[PlannedFile]  # files neither pooled nor fetched by a running job
    joined: list[Job]  # jobs that already fetch some of the wanted files
    switches_entry: bool  # the project moves to another version (update: true)


def calculate_download_bytes(fetch: FetchPlan) -> int:
    return sum(f.size for f in fetch.to_fetch)


def needs_download(fetch: FetchPlan) -> bool:
    return bool(fetch.to_fetch or fetch.joined)


class ChatNotes:
    """Finish messages for the chat that started a download, plus notes for that chat's next model turn."""

    def __init__(self, send: Send | None):
        self.send = send
        self.pending_lines: dict[str, list[str]] = {}  # session_id -> pending finish lines

    async def tell(self, session_id: str, message: str, note: str) -> None:
        """Message the starting chat; leave a note for its next turn only if the chat is still open."""
        was_sent = False
        if self.send:
            try:
                was_sent = await self.send(session_id, message)
            except Exception:
                log.exception("Couldn't message chat %s", session_id)
        if was_sent:
            self.pending_lines.setdefault(session_id, []).append(note)

    def take(self, session_id: str) -> str | None:
        lines = self.pending_lines.pop(session_id, None)
        if not lines:
            return None
        return "\n".join([NOTE_HEADER, *lines])

    def drop(self, session_id: str) -> None:
        self.pending_lines.pop(session_id, None)


class DatasetService:
    """The only place that downloads, lists or deletes datasets."""

    def __init__(
        self,
        *,
        settings: DatasetsSettings | None = None,
        root: Path = WORKSPACES_DIR,
        props: PropsFor | None = None,
        notify: Notify | None = None,
        send: Send | None = None,
        free_bytes: FreeBytes = disk_free,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        resolve: Resolve = sources.resolve,
        resolve_link: ResolveLink = links.resolve_link,
        progress_interval: float = PROGRESS_INTERVAL,
        pool: DatasetPool | None = None,
        created: CreatedDatasets | None = None,
        views: ProjectViews | None = None,
    ):
        # Only stores dependencies: actions.py builds this at import time.
        self.settings = settings or DatasetsSettings()
        self.root = root
        self.props = props
        self.notify = notify
        self.chat_notes = ChatNotes(send)
        self.free_bytes = free_bytes
        self.client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=sources.API_TIMEOUT))
        self.resolve = resolve
        self.resolve_link = resolve_link
        self.progress_interval = progress_interval
        self.pool = pool or DatasetPool(root)  # index, links, and the one lock for both
        self.created = created or CreatedDatasets(root)  # files the project's own code wrote
        self.views = views or ProjectViews(self.pool, root)  # what each project's kernel may read
        self.rev = 0  # datasets_rev for NotebookProps
        self._datasets: dict[str, list[ProjectDataset]] = {}  # project_id -> datasets (cache of its database rows)
        self._jobs: list[Job] = []  # app-wide, in queue order (running ones included)
        self._job_ids = 0
        self._ready = asyncio.Event()  # set by startup_cleanup

    # ---- helpers ----

    @property
    def _lock(self) -> asyncio.Lock:
        return self.pool.lock

    def _load(self, project_id: str) -> list[ProjectDataset]:
        if project_id not in self._datasets:
            self._datasets[project_id] = ds.load_project_datasets(project_id, self.root)
        return self._datasets[project_id]

    def _save(self, project_id: str) -> None:
        ds.save_project_datasets(project_id, self._load(project_id), self.root)
        self.rev += 1

    def _entry(self, dataset: ProjectDataset) -> PoolEntry:
        """The pool entry a project dataset points to; an empty stand-in if the index lost it."""
        entry = self.pool.get_entry(dataset.pool_entry)
        if entry is None:
            entry = PoolEntry(id=dataset.pool_entry, source="huggingface", source_id="", revision=None,
                              name=dataset.id, url=None, license=None)
        return entry

    def _find_dataset(self, project_id: str, source: Source, source_id: str) -> ProjectDataset | None:
        for dataset in self._load(project_id):
            entry = self.pool.get_entry(dataset.pool_entry)
            if entry is not None and entry.source == source and entry.source_id == source_id:
                return dataset
        return None

    def active_jobs(self) -> list[Job]:
        """Queued and running jobs, in queue order (a copy)."""
        return list(self._jobs)

    def _jobs_for(self, project_id: str, dataset_id: str) -> list[Job]:
        return [j for j in self._jobs if project_id in j.waiters and j.waiters[project_id].dataset_id == dataset_id]

    def _jobs_on_entry(self, entry_id: str) -> list[Job]:
        return [j for j in self._jobs if j.pool_entry == entry_id]

    def _queue_position(self, job: Job) -> int:
        """0 when running; otherwise 1-based place among waiting jobs, app-wide."""
        if job.running:
            return 0
        waiting = [j for j in self._jobs if not j.running]
        return waiting.index(job) + 1

    def _missing(self, project_id: str, dataset: ProjectDataset) -> list[str]:
        """Files the project should see but can't: link gone, or pooled bytes gone or changed."""
        entry = self._entry(dataset)
        return [p for p in dataset.files if not self.pool.is_linked(project_id, dataset.id, entry, p)]

    def _status(self, project_id: str, dataset: ProjectDataset) -> DatasetStatus:
        jobs = self._jobs_for(project_id, dataset.id)
        if jobs:
            return "downloading" if any(j.running for j in jobs) else "queued"
        if dataset.status == "ready" and self._missing(project_id, dataset):
            return "missing"
        return dataset.status

    def _size(self, dataset: ProjectDataset) -> int:
        entry = self._entry(dataset)
        return sum(f.size for f in (find_pool_file(entry, p) for p in dataset.files) if f is not None)

    def _done_bytes(self, project_id: str, dataset: ProjectDataset) -> int:
        entry = self._entry(dataset)
        jobs = self._jobs_for(project_id, dataset.id)
        if any(j.waiters[project_id].switches_entry for j in jobs):
            return sum(j.done_bytes for j in jobs)
        ready = sum(f.size for f in (find_pool_file(entry, p) for p in dataset.files) if f is not None and f.ready)
        return ready + sum(j.done_bytes for j in jobs)

    def _users_by_entry(self) -> dict[str, set[str]]:
        """entry id -> project ids using it, from every project's dataset rows (one read for many rows)."""
        users: dict[str, set[str]] = {}
        for project in store.list_projects(self.root):
            try:
                datasets = self._load(project.id)
            except WorkspaceError:
                continue  # not migrated yet: logged by _load
            for dataset in datasets:
                users.setdefault(dataset.pool_entry, set()).add(project.id)
        return users

    async def _push(self, project_id: str) -> None:
        """Send fresh props to every open panel of the project; errors only logged."""
        if not (self.props and self.notify):
            return
        try:
            await self.notify(project_id, await self.props(project_id))
        except Exception:
            log.exception("Dataset panel push failed for %s", project_id)

    async def _push_all(self, project_ids: Iterable[str]) -> None:
        for project_id in sorted(set(project_ids)):
            await self._push(project_id)

    async def _wait_ready(self) -> None:
        try:
            await asyncio.wait_for(self._ready.wait(), READY_WAIT_SECONDS)
        except TimeoutError:
            log.warning("Dataset startup cleanup didn't finish; continuing")

    @staticmethod
    def _plan_from_entry(entry: PoolEntry, paths: list[str]) -> DatasetPlan:
        files = [f for f in (find_pool_file(entry, p) for p in paths) if f is not None]
        return DatasetPlan(
            source=entry.source, source_id=entry.source_id, name=entry.name, revision=entry.revision,
            url=entry.url or "", license=entry.license,
            files=[DatasetService._planned_from_pool(entry, f) for f in files],
        )

    @staticmethod
    def _planned_from_pool(entry: PoolEntry, pool_file: PoolFile) -> PlannedFile:
        """A pooled file to fetch again. An unfinished link file's stored size isn't trusted:
        the website may have sent none, or the file may have changed since."""
        url = links.download_url(entry.source, entry.source_id, entry.revision, pool_file.path)
        is_unfinished_link = entry.source == links.LINK_SOURCE and not pool_file.ready
        return PlannedFile(pool_file.path, pool_file.size, pool_file.checksum, url, size_is_known=not is_unfinished_link)

    def _started(self, project_id: str, dataset: ProjectDataset, job: Job | None, files: list[PlannedFile], from_pool: bool) -> DownloadStarted:
        status: DatasetStatus = "ready" if job is None else ("downloading" if job.running else "queued")
        is_update = job is not None and job.waiters[project_id].switches_entry
        return {
            "dataset_id": dataset.id,
            "status": status,
            "already_downloaded": job is None,
            "position_in_queue": 0 if job is None else self._queue_position(job),
            "size_bytes": sum(f.size for f in files) if is_update else self._size(dataset),
            "download_bytes": 0 if job is None else sum(j.total for j in self._jobs_for(project_id, dataset.id)),
            "path": ds.kernel_path(dataset.id),
            "files": [{"path": ds.kernel_path(dataset.id, f.path), "size": f.size} for f in files[:VIEW_FILES_MAX]],
            "license": self._entry(dataset).license,
            "url": self._entry(dataset).url,
            "note": self._note(self._entry(dataset), job),
            "from_pool": from_pool,
        }

    @staticmethod
    def _note(entry: PoolEntry, job: Job | None) -> str:
        """What the model should do with the result; a link never has a licence to repeat."""
        if entry.source != links.LINK_SOURCE:
            return READY_NOTE if job is None else STARTED_NOTE
        return (LINK_READY_NOTE if job is None else STARTED_NOTE) + LINK_LICENCE_NOTE

    # ---- tool-call side (quick checks) ----

    async def request_kaggle_import(
        self, selection: KaggleImportSelection, downloads: KaggleDatasetDownloads, *,
        approve: ApproveKaggleImport, is_selection_current: Callable[[], bool],
    ) -> KaggleDownloadResult:
        await self._wait_ready()
        async with self._lock:
            self._require_kaggle_selection(selection, downloads, is_selection_current)
            active = self._existing_kaggle_import(selection)
            if active is not None:
                return {"state": "accepted", "dataset_id": active.id}
        plan = await self._resolve_kaggle(selection, downloads, is_selection_current)
        offered = await self._accept_or_offer_kaggle(selection, plan, downloads, is_selection_current)
        if not isinstance(offered, KaggleApprovalOffer):
            return offered
        if not await approve(offered):
            return {"state": "skipped"}
        if not is_selection_current():
            raise WorkspaceError(KaggleImportError.STALE_APPROVAL, "The project or Kaggle account changed. Try again.")
        plan = await self._resolve_kaggle(selection, downloads, is_selection_current)
        result = await self._accept_or_offer_kaggle(selection, plan, downloads, is_selection_current, approved=offered)
        assert not isinstance(result, KaggleApprovalOffer)
        return result

    def _require_kaggle_selection(
        self, selection: KaggleImportSelection, downloads: KaggleDatasetDownloads, is_current: Callable[[], bool],
    ) -> None:
        downloads.require_generation(selection.account_generation)
        if not is_current():
            raise WorkspaceError(KaggleImportError.STALE_PROJECT, "The project or picker changed. Open /kaggle and try again.")
        store.get_project(selection.project_id, self.root)

    def _existing_kaggle_import(self, selection: KaggleImportSelection) -> ProjectDataset | None:
        dataset = self._find_dataset(selection.project_id, "kaggle", selection.ref)
        if dataset is not None and self._status(selection.project_id, dataset) in ("ready", "queued", "downloading"):
            return dataset
        return None

    async def _resolve_kaggle(
        self, selection: KaggleImportSelection, downloads: KaggleDatasetDownloads, is_current: Callable[[], bool],
    ) -> DatasetPlan:
        try:
            return await downloads.resolve_current(selection, max_files=self.settings.max_files)
        except WorkspaceError as error:
            if error.code == ErrorCode.DOWNLOAD_FAILED and is_current():
                await self.record_kaggle_preflight_failure(selection, error.message, is_current)
            raise

    async def record_kaggle_preflight_failure(
        self, selection: KaggleImportSelection, reason: str, is_current: Callable[[], bool],
    ) -> None:
        project_id = selection.project_id
        async with self._lock:
            if not is_current() or self._existing_kaggle_import(selection) is not None:
                return
            dataset = self._find_dataset(project_id, "kaggle", selection.ref)
            if dataset is None:
                request = DownloadRequest(project_id, "", "kaggle", selection.ref, None, False)
                plan = DatasetPlan("kaggle", selection.ref, selection.ref, None, "", None, [])
                target = self._target_for_plan(request, plan)
                if target.is_new_entry:
                    self.pool.add_entry(target.entry)
                    self.pool.save_index()
                dataset = self._new_dataset(request, target.entry)
            dataset.status, dataset.error = "failed", reason[:200]
            self._refresh_view(project_id, dataset)
            self._save(project_id)
        await self._push(project_id)

    async def _accept_or_offer_kaggle(
        self, selection: KaggleImportSelection, plan: DatasetPlan, downloads: KaggleDatasetDownloads,
        is_current: Callable[[], bool], *, approved: KaggleApprovalOffer | None = None,
    ) -> KaggleDownloadResult | KaggleApprovalOffer:
        project_id = selection.project_id
        if not plan.files:
            raise WorkspaceError(ErrorCode.NO_FILES, "Kaggle dataset has no downloadable files.")
        async with self._lock:
            if approved and (not is_current() or approved.revision != plan.revision):
                raise WorkspaceError(KaggleImportError.STALE_APPROVAL, "This Kaggle download changed. Try again.")
            self._require_kaggle_selection(selection, downloads, is_current)
            active = self._existing_kaggle_import(selection)
            if active is not None:
                return {"state": "accepted", "dataset_id": active.id}
            request = DownloadRequest(project_id, "", "kaggle", selection.ref, None, False)
            target = self._target_for_plan(request, plan)
            pending_bytes, fetch = self._plan_kaggle_fetch(target, selection.account_generation)
            if fetch.to_fetch:
                self._check_disk(calculate_download_bytes(fetch), plan)
            if approved and pending_bytes > approved.download_bytes:
                raise WorkspaceError(KaggleImportError.STALE_APPROVAL, "This download needs more space than approved. Try again.")
            if approved is None and pending_bytes > self.settings.ask_above_mb * MB:
                return KaggleApprovalOffer(selection, plan.name, plan.revision or "", pending_bytes)
            transfer = KaggleTransfer(downloads, selection, plan.revision or "")
            dataset = self._stage_kaggle_fetch(request, fetch, transfer)
        await self._push(project_id)
        return {"state": "accepted", "dataset_id": dataset.id}

    def _plan_kaggle_fetch(self, target: RequestTarget, generation: int) -> tuple[int, FetchPlan]:
        entry = target.entry
        jobs = [] if target.is_new_entry else self._jobs_on_entry(entry.id)
        if any(job.kaggle is None or job.kaggle.selection.account_generation != generation for job in jobs):
            raise WorkspaceError(ErrorCode.BUSY, "An earlier Kaggle download is still stopping. Try again.")
        wanted = [file.path for file in target.plan.files]
        missing = [file for file in target.plan.files if not self._is_verified_kaggle_file(entry, file)]
        running_paths = set().union(*(job.paths for job in jobs))
        to_fetch = [file for file in missing if file.path not in running_paths]
        return sum(file.size for file in missing), FetchPlan(target, wanted, to_fetch, jobs, False)

    def _is_verified_kaggle_file(self, entry: PoolEntry, file: PlannedFile) -> bool:
        stored = find_pool_file(entry, file.path)
        return stored is not None and stored.ready and stored.size == file.size and self.pool.is_file_unchanged(entry, stored)

    def _stage_kaggle_fetch(self, request: DownloadRequest, fetch: FetchPlan, transfer: KaggleTransfer) -> ProjectDataset:
        project_id, entry = request.project_id, fetch.target.entry
        self._store_entry(fetch)
        dataset = self._find_dataset(project_id, "kaggle", request.source_id) or self._new_dataset(request, entry)
        previous_entry = dataset.pool_entry
        if not needs_download(fetch):
            self.pool.link_files(project_id, dataset.id, entry, fetch.wanted)
        else:
            self.pool.unlink_dataset(project_id, dataset.id)
        if previous_entry != entry.id:
            dataset.assistant_description = None
        dataset.pool_entry, dataset.files = entry.id, sorted(fetch.wanted)
        dataset.status, dataset.error = ("queued" if needs_download(fetch) else "ready"), None
        dataset.linked_at = None if needs_download(fetch) else utc_now()
        job = self._queue_downloads(request, dataset, fetch)
        if job is not None:
            job.kaggle = transfer
        self.pool.save_index()
        self._save(project_id)
        self._refresh_view(project_id, dataset)
        if previous_entry != entry.id:
            self._remove_entry_if_unused(previous_entry)
        self._schedule()
        return dataset

    async def request(
        self,
        project_id: str,
        session_id: str,
        ref: str,
        files: list[str] | None = None,
        update: bool = False,
        *,
        approve: Approve,
        pasted_links: frozenset[str] = frozenset(),
    ) -> DownloadStarted:
        """Link what the pool has, queue the rest; returns before any transfer ends.

        Request flow: a project keeps its version unless `update`; a
        project new to the dataset takes the pool's newest version; only
        files the pool lacks are fetched, by joining a job that already fetches
        them or queueing one. Big downloads ask first, with the
        lock released, and everything is re-checked after.
        """
        await self._wait_ready()
        store.get_project(project_id, self.root)  # NOT_FOUND for an unknown project
        source, source_id = links.parse_dataset_ref(ref)
        if source == links.LINK_SOURCE:
            links.require_pasted(source_id, pasted_links)
            links.refuse_file_options(files, update)
        request = DownloadRequest(project_id, session_id, source, source_id, files, update, pasted_links)
        ask_above_bytes = self.settings.ask_above_mb * MB
        while True:
            async with self._lock:
                outcome = await self._plan_request(request, ask_above_bytes)
                if not isinstance(outcome, tuple):
                    return outcome
            plan, download_bytes = outcome
            if not await approve(plan, download_bytes):
                raise WorkspaceError(ErrorCode.DECLINED, "The user chose not to download this dataset.")
            ask_above_bytes = NEVER_ASK_BYTES

    async def _plan_request(self, request: DownloadRequest, ask_above_bytes: int) -> DownloadStarted | tuple[DatasetPlan, int]:
        """Under the lock: a DownloadStarted, or (plan, bytes) when the user must be asked first."""
        dataset = self._find_dataset(request.project_id, request.source, request.source_id)
        self._refuse_update_while_downloading(request, dataset)
        target = await self._resolve_target(request, dataset)
        fetch = self._plan_fetch(dataset, target)
        if fetch.to_fetch:
            self._check_disk(calculate_download_bytes(fetch), target.plan)
            # Pasted links don't ask yet; a size prompt for them is a separate change
            asks_first = request.source != links.LINK_SOURCE and calculate_download_bytes(fetch) > ask_above_bytes
            if asks_first:
                return (target.plan, calculate_download_bytes(fetch))
        return await self._carry_out(request, dataset, fetch)

    def _refuse_update_while_downloading(self, request: DownloadRequest, dataset: ProjectDataset | None) -> None:
        """One version change at a time: an update waits for the running download."""
        if request.update and dataset is not None and self._jobs_for(request.project_id, dataset.id):
            raise WorkspaceError(ErrorCode.BUSY, "This dataset is downloading; update it when that's done.")

    def _plan_fetch(self, dataset: ProjectDataset | None, target: RequestTarget) -> FetchPlan:
        """What the pool already has, what running jobs already fetch, and what's left to download."""
        entry, wanted = target.entry, [f.path for f in target.plan.files]
        if not target.is_new_entry:
            self.pool.ensure_ready_files_intact(entry, wanted)
        ready = list_ready_paths(entry)
        jobs_here = self._jobs_on_entry(entry.id)
        in_jobs = {p for j in jobs_here for p in j.paths}
        return FetchPlan(
            target=target,
            wanted=wanted,
            to_fetch=[f for f in target.plan.files if f.path not in ready and f.path not in in_jobs],
            joined=[j for j in jobs_here if j.paths & (set(wanted) - ready)],
            switches_entry=dataset is not None and dataset.pool_entry != entry.id,
        )

    async def _carry_out(self, request: DownloadRequest, dataset: ProjectDataset | None, fetch: FetchPlan) -> DownloadStarted:
        """Write it all: pool entry, the project's dataset, links, and the jobs it waits for."""
        project_id, entry = request.project_id, fetch.target.entry
        self._store_entry(fetch)
        dataset = dataset or self._new_dataset(request, entry)
        newly_linked = self._show_ready_files(project_id, dataset, fetch, request.files)
        job = self._queue_downloads(request, dataset, fetch)
        if needs_download(fetch):
            dataset.status = dataset.status if dataset.status == "ready" else "queued"
            dataset.error = None
        self.pool.save_index()
        self._save(project_id)
        self._schedule()
        shown_job = job or (fetch.joined[-1] if fetch.joined else None)
        shown_files = list(job.files if job and not fetch.joined else fetch.target.plan.files)
        from_pool = bool(newly_linked) and shown_job is None  # linked from the pool, nothing fetched
        result = self._started(project_id, dataset, shown_job, shown_files, from_pool)
        await self._push(project_id)
        return result

    def _store_entry(self, fetch: FetchPlan) -> None:
        """A new version joins the index; files about to be fetched are listed (not ready yet)."""
        entry = fetch.target.entry
        if fetch.target.is_new_entry:
            self.pool.add_entry(entry)
        for f in fetch.to_fetch:
            if find_pool_file(entry, f.path) is None:
                entry.files.append(PoolFile(f.path, f.size, f.checksum))

    def _show_ready_files(self, project_id: str, dataset: ProjectDataset, fetch: FetchPlan, files: list[str] | None) -> list[str]:
        """Link the ready pooled files now; switch versions now when nothing is left to fetch. Newly linked paths."""
        entry = fetch.target.entry
        if fetch.switches_entry and needs_download(fetch):
            return []  # the old version stays until the new one is complete
        if fetch.switches_entry:
            self._switch_entry(project_id, dataset, entry, fetch.wanted)
        else:
            self._merge_selection(dataset, files)
            dataset.files = sorted(set(dataset.files) | set(fetch.wanted))
        visible_before = set(fetch.wanted) - set(self._missing(project_id, dataset))
        self.pool.link_files(project_id, dataset.id, entry, fetch.wanted)
        newly_linked = [p for p in fetch.wanted if p not in visible_before and p in list_ready_paths(entry)]
        if not needs_download(fetch) and not self._missing(project_id, dataset):
            dataset.status, dataset.error = "ready", None
            if newly_linked or dataset.linked_at is None:
                dataset.linked_at = utc_now()
        self._refresh_view(project_id, dataset)  # after the status, so a view failure is what the card shows
        return newly_linked

    def _queue_downloads(self, request: DownloadRequest, dataset: ProjectDataset, fetch: FetchPlan) -> Job | None:
        """Join the jobs that already fetch some of these files; queue one job for the rest."""
        project_id = request.project_id
        waiter = Waiter(dataset.id, request.session_id, set(fetch.wanted), fetch.switches_entry)
        for job in fetch.joined:
            job.waiters.setdefault(project_id, waiter)
        if not fetch.to_fetch:
            return None
        self._job_ids += 1
        is_link = fetch.target.entry.source == links.LINK_SOURCE
        job = Job(self._job_ids, fetch.target.entry.id, fetch.to_fetch, {project_id: waiter}, is_link=is_link)
        self._jobs.append(job)
        return job

    async def _resolve_target(self, request: DownloadRequest, dataset: ProjectDataset | None) -> RequestTarget:
        """Which pool version and files this request is about."""
        files = request.files
        if dataset is not None and not request.update and self._selection_covers(dataset.selection, files):
            return self._target_from_own_entry(dataset, files)
        pinned = self._pinned_entry(request, dataset)
        if pinned is not None and dataset is None and self._pool_covers(pinned, files):
            wanted = [f.path for f in pinned.files if sources.match_files([f.path], files)]
            if wanted:
                return RequestTarget(pinned, self._plan_from_entry(pinned, wanted), is_new_entry=False)
        async with self.client_factory() as client:
            plan = await self._fetch_plan(request, dataset, pinned, client)
        return self._target_for_plan(request, plan)

    async def _fetch_plan(
        self, request: DownloadRequest, dataset: ProjectDataset | None, pinned: PoolEntry | None, client: httpx.AsyncClient
    ) -> DatasetPlan:
        """Ask the source what to download: the catalog's file list, or a probe of the pasted link."""
        if request.source == links.LINK_SOURCE:
            return await self.resolve_link(request.source_id, client=client)
        return await self.resolve(
            request.source, request.source_id, self._patterns(request, dataset), max_files=self.settings.max_files,
            revision=pinned.revision if pinned is not None else None, client=client,
        )

    def _target_from_own_entry(self, dataset: ProjectDataset, files: list[str] | None) -> RequestTarget:
        """The project's own version covers the request: no catalog call."""
        entry = self._entry(dataset)
        wanted = [p for p in dataset.files if files is None or sources.match_files([p], files)]
        if not wanted:
            raise WorkspaceError(ErrorCode.NO_FILES, "No files match that selection.", {"available_files": dataset.files[:100]})
        return RequestTarget(entry, self._plan_from_entry(entry, wanted), is_new_entry=False)

    def _pinned_entry(self, request: DownloadRequest, dataset: ProjectDataset | None) -> PoolEntry | None:
        """The version to stay on: the project's own, else the pool's newest; none with update."""
        if request.update:
            return None
        if dataset is not None:
            return self._entry(dataset)
        if request.source == links.LINK_SOURCE:
            return None  # each project downloads its own copy of a link: the file may have changed since
        entries = self.pool.find_entries(request.source, request.source_id)
        return entries[0] if entries else None

    @staticmethod
    def _patterns(request: DownloadRequest, dataset: ProjectDataset | None) -> list[str] | None:
        """update: true keeps every pattern the project asked for so far, plus the new ones."""
        if dataset is None or not request.update:
            return request.files
        if dataset.selection is None:
            return None
        if request.files is None:
            return dataset.selection
        return sorted(set(dataset.selection) | set(request.files))

    def _target_for_plan(self, request: DownloadRequest, plan: DatasetPlan) -> RequestTarget:
        existing = next((e for e in self.pool.find_entries(request.source, request.source_id) if e.revision == plan.revision), None)
        if existing is not None:
            return RequestTarget(existing, plan, is_new_entry=False)
        entry_id = (self.pool.make_kaggle_entry_id(request.source_id, plan.revision) if request.source == "kaggle"
                    else self.pool.make_entry_id(ds.base_id(request.source, request.source_id), plan.revision))
        entry = PoolEntry(
            id=entry_id,
            source=request.source, source_id=request.source_id, revision=plan.revision, name=plan.name,
            url=plan.url, license=plan.license, created_at=utc_now(), description=plan.description,
        )
        return RequestTarget(entry, plan, is_new_entry=True)

    @staticmethod
    def _selection_covers(selection: list[str] | None, files: list[str] | None) -> bool:
        return selection is None or (files is not None and set(files) <= set(selection))

    def _pool_covers(self, entry: PoolEntry, files: list[str] | None) -> bool:
        """Some project asked for at least these patterns of this entry, so its file list is complete for them."""
        users = self._users_by_entry().get(entry.id, set())
        selections = [d.selection for project_id in users for d in self._load(project_id) if d.pool_entry == entry.id]
        return any(self._selection_covers(s, files) for s in selections)

    def _new_dataset(self, request: DownloadRequest, entry: PoolEntry) -> ProjectDataset:
        datasets = self._load(request.project_id)
        dataset = ProjectDataset(
            id=ds.make_id(request.source, request.source_id, {d.id for d in datasets}), pool_entry=entry.id,
            selection=sorted(set(request.files)) if request.files is not None else None,
            status="queued", requested_at=utc_now(),
        )
        datasets.append(dataset)
        return dataset

    def _switch_entry(self, project_id: str, dataset: ProjectDataset, entry: PoolEntry, wanted: list[str]) -> None:
        """update: true — move this project (only) to another pooled version, then drop the old one if unused."""
        old_entry = dataset.pool_entry
        self.pool.unlink_dataset(project_id, dataset.id)
        dataset.pool_entry, dataset.files = entry.id, sorted(wanted)
        dataset.assistant_description = None  # the note described the old version's data
        self.pool.link_files(project_id, dataset.id, entry, wanted)
        self.pool.save_index()  # the new version may be new to the index; the project row points to it (FK)
        self._save(project_id)
        self._refresh_view(project_id, dataset)
        self._remove_entry_if_unused(old_entry)

    @staticmethod
    def _merge_selection(dataset: ProjectDataset, files: list[str] | None) -> None:
        if dataset.selection is None or files is None:
            dataset.selection = None
        else:
            dataset.selection = sorted(set(dataset.selection) | set(files))

    def _check_disk(self, need: int, plan: DatasetPlan) -> None:
        """NO_SPACE when need + space reserved by queued/running jobs + margin is over the free space."""
        free = self.free_bytes(self.root)
        reserved = sum(j.remaining for j in self._jobs)
        margin = self.settings.min_free_disk_mb * MB
        if need + reserved + margin > free:
            raise WorkspaceError(
                ErrorCode.NO_SPACE,
                f"Not enough disk: this download needs {human_size(need)}, "
                f"{human_size(max(free - reserved - margin, 0))} is available.",
                {
                    "needed_bytes": need,
                    "free_bytes": free,
                    "reserved_bytes": reserved,
                    "min_free_bytes": margin,
                    "available_files": [{"path": f.path, "size": f.size} for f in plan.files[:100]],
                },
            )

    # ---- reading ----

    def _view(self, project_id: str, dataset: ProjectDataset, users: dict[str, set[str]]) -> DatasetRecordView:
        status = self._status(project_id, dataset)
        entry = self._entry(dataset)
        jobs = self._jobs_for(project_id, dataset.id)
        waiting = [self._queue_position(j) for j in jobs if not j.running]
        view: DatasetRecordView = {
            "dataset_id": dataset.id,
            "name": entry.name,
            "status": status,
            "done_bytes": self._done_bytes(project_id, dataset) if status == "downloading" else None,
            "position_in_queue": min(waiting) if status == "queued" and waiting else None,
            "size_bytes": self._size(dataset),
            "license": entry.license,
            "url": entry.url,
            "path": ds.kernel_path(dataset.id),
            "files": [ds.kernel_path(dataset.id, p) for p in dataset.files[:VIEW_FILES_MAX]],
            "file_count": len(dataset.files),
            "missing_files": self._missing(project_id, dataset) if status == "missing" else [],
            "downloaded_at": dataset.linked_at,
            "error": dataset.error,
            "shared_with": len(users.get(dataset.pool_entry, set()) - {project_id}),
            "origin": "downloaded",
            "description": entry.description,
            "columns": None,
            "assistant_description": dataset.assistant_description,
        }
        view["describe"] = describe_need(view)
        view["load_code"] = load_code(view)
        return view

    async def sync_view(self, project_id: str) -> Path:
        """Rebuild the project's kernel view from its tab under the pool lock; returns the folder to mount."""
        async with self._lock:
            return self._sync_view(project_id, self._load(project_id))

    def _sync_view(self, project_id: str, datasets: list[ProjectDataset]) -> Path:
        return self.views.sync(project_id, visible_pool_files(datasets, self.pool.entries()))

    def _refresh_view(self, project_id: str, changed: ProjectDataset | None = None) -> None:
        """Under the lock: the project's kernel view follows its tab at once, so no restart is needed.

        When the view can't take the changed dataset, that dataset is marked failed and left out of
        the view, so the project never reads half of it and the rest of the tab stays readable."""
        datasets = self._load(project_id)
        try:
            self._sync_view(project_id, datasets)
        except WorkspaceError as error:
            if changed is None:
                raise
            changed.status, changed.error = "failed", str(error)[:200]
            self._save(project_id)
            self._sync_view(project_id, [d for d in datasets if d is not changed])

    def sync_created(self, project_id: str, by: str | None = None) -> None:
        """NotebookService.after_run: scan the project's files/ and bump datasets_rev when created rows changed."""
        if self.created.sync(project_id, by):
            self.rev += 1

    def list_for_model(self, project_id: str) -> list[DatasetRecordView]:
        """dataset_list result: every dataset with status, progress and /work paths."""
        users = self._users_by_entry()
        downloaded = [self._view(project_id, d, users) for d in self._load(project_id)]
        return downloaded + self.created.model_views(project_id)

    def project_refs(self, project_id: str) -> dict[str, DatasetRecordView]:
        """"hf:<id>" / "zenodo:<id>" → this project's own row for that catalog entry; link and created rows left out."""
        try:
            datasets = self._load(project_id)
        except WorkspaceError:
            return {}  # unknown project
        users = self._users_by_entry()
        refs: dict[str, DatasetRecordView] = {}
        for dataset in datasets:
            entry = self.pool.get_entry(dataset.pool_entry)
            prefix = REF_PREFIXES.get(entry.source) if entry is not None else None
            if prefix:
                refs[f"{prefix}:{entry.source_id}"] = self._view(project_id, dataset, users)
        return refs

    def needs_description(self, project_id: str) -> list[DatasetRecordView]:
        """Ready rows that say nothing about themselves and may be looked at freely (describe "write")."""
        return [v for v in self.list_for_model(project_id) if v.get("describe") == "write"]

    def view_for_model(self, project_id: str, dataset_id: str) -> DatasetRecordView:
        """One of this project's datasets as dataset_list shows it (either kind); NOT_FOUND otherwise."""
        view = next((v for v in self.list_for_model(project_id) if v["dataset_id"] == dataset_id), None)
        if view is None:
            raise WorkspaceError(ErrorCode.NOT_FOUND, "Unknown dataset.")
        return view

    async def save_description(self, project_id: str, dataset_id: str, text: str) -> DatasetRecordView:
        """Store the assistant's one-line description on one of this project's datasets; returns the new view.

        Only a row that says nothing about itself takes one (NOT_NEEDED otherwise).
        """
        view = self.view_for_model(project_id, dataset_id)
        if view["describe"] is None:
            raise WorkspaceError(ErrorCode.NOT_NEEDED, "This dataset already says what it holds, or isn't ready.")
        line = one_line_description(text) if isinstance(text, str) else ""
        if not line:
            raise WorkspaceError(ErrorCode.BAD_DESCRIPTION, "Give a one-line description of what the data holds.")
        if view["origin"] == "created":
            self.created.save_description(project_id, dataset_id, line)
        else:
            async with self._lock:
                dataset = ds.find_project_dataset(self._load(project_id), dataset_id)
                if dataset is None:
                    raise WorkspaceError(ErrorCode.NOT_FOUND, "Unknown dataset.")
                dataset.assistant_description = line
                self._save(project_id)
        self.rev += 1
        await self._push(project_id)
        return self.view_for_model(project_id, dataset_id)

    def describe(self, project_id: str, dataset_id: str) -> DatasetRecordView:
        """One dataset, for the Delete / Keep prompt (name, size it frees, other users)."""
        dataset = ds.find_project_dataset(self._load(project_id), dataset_id) if isinstance(dataset_id, str) else None
        if dataset is None:
            raise WorkspaceError(ErrorCode.NOT_FOUND, "Unknown dataset.")
        return self._view(project_id, dataset, self._users_by_entry())

    # ---- delete / cancel ----

    async def delete(self, project_id: str, dataset_id: str) -> DeleteResult:
        """Remove the dataset from this project; its pool entry goes when no project uses it."""
        if self.created.owns(project_id, dataset_id):
            return await self._delete_created(project_id, dataset_id)
        async with self._lock:
            datasets = self._load(project_id)
            dataset = ds.find_project_dataset(datasets, dataset_id) if isinstance(dataset_id, str) else None
            if dataset is None:
                raise WorkspaceError(ErrorCode.NOT_FOUND, "Unknown dataset.")
            if self._jobs_for(project_id, dataset_id):
                raise WorkspaceError(ErrorCode.BUSY, "This dataset is downloading. Cancel it first.")
            self.pool.unlink_dataset(project_id, dataset.id)
            datasets.remove(dataset)
            self._save(project_id)
            self._refresh_view(project_id)
            freed = self._remove_entry_if_unused(dataset.pool_entry)
            still_used_by = len(self.pool.projects_using(dataset.pool_entry))
        await self._push(project_id)
        return {"deleted": dataset_id, "freed_bytes": freed, "still_used_by": still_used_by}

    async def _delete_created(self, project_id: str, dataset_id: str) -> DeleteResult:
        """A created file has no pool entry and no other user: its bytes are freed here."""
        freed = await self.created.delete(project_id, dataset_id)
        self.rev += 1
        await self._push(project_id)
        return {"deleted": dataset_id, "freed_bytes": freed, "still_used_by": 0}

    def _remove_entry_if_unused(self, entry_id: str) -> int:
        if self._jobs_on_entry(entry_id):
            return 0
        return self.pool.remove_unused_entry(entry_id)

    async def cancel(self, project_id: str, dataset_id: str) -> None:
        """Stop waiting for a download in this project only; the job stops when no project waits."""
        jobs = self._jobs_for(project_id, dataset_id) if isinstance(dataset_id, str) else []
        if not jobs:
            raise WorkspaceError(ErrorCode.NOT_FOUND, "No download to cancel.")
        entry_ids = {j.pool_entry for j in jobs}
        for job in jobs:
            del job.waiters[project_id]
            if not job.waiters:
                await self._stop_job(job)
        async with self._lock:
            self._drop_unfinished(project_id, dataset_id)
            for entry_id in entry_ids:
                self._remove_entry_if_unused(entry_id)
        self._schedule()
        await self._push(project_id)

    async def _stop_job(self, job: Job) -> None:
        job.cancelled = True
        if job.task is not None:
            job.task.cancel()
            try:
                await job.task
            except BaseException:
                pass
        # A task cancelled before it started never runs its own cleanup
        if job in self._jobs:
            self._jobs.remove(job)
        self._remove_job_folder(job)

    def _drop_unfinished(self, project_id: str, dataset_id: str) -> None:
        """After a cancel: keep what the project can already use (its current version); no files → no dataset."""
        datasets = self._load(project_id)
        dataset = ds.find_project_dataset(datasets, dataset_id)
        if dataset is None:
            return
        if self._entry(dataset).source == "kaggle":
            self.pool.unlink_dataset(project_id, dataset_id)
            datasets.remove(dataset)
            self._save(project_id)
            self._refresh_view(project_id)
            return
        ready = list_ready_paths(self._entry(dataset))
        dataset.files = [p for p in dataset.files if p in ready]
        if dataset.files and not self._jobs_for(project_id, dataset_id):
            dataset.status = "ready"
        elif not dataset.files:
            self.pool.unlink_dataset(project_id, dataset_id)
            datasets.remove(dataset)
        self._save(project_id)
        self._refresh_view(project_id)

    # ---- background side ----

    async def startup_cleanup(self) -> None:
        """Stale downloads -> failed; clear partial files."""
        try:
            self._datasets.clear()
            async with self._lock:
                ds.remove_tree(self.pool.partial_dir())
                for project in store.list_projects(self.root):
                    self._fail_stale(project.id)
                self.views.prune({project.id for project in store.list_projects(self.root)})
        finally:
            self._ready.set()

    def _fail_stale(self, project_id: str) -> None:
        try:
            datasets = self._load(project_id)
            stale = [d for d in datasets if d.status in ("queued", "downloading")]
            for d in stale:
                d.status, d.error = "failed", STOPPED_DURING_DOWNLOAD
            if stale:
                self._save(project_id)
        except Exception:
            log.exception("Dataset cleanup failed for project %s", project_id)

    def _schedule(self) -> None:
        """Start waiting jobs while slots are free; one job per pool entry at a time."""
        running = [j for j in self._jobs if j.running]
        busy = {j.pool_entry for j in running}
        for job in self._jobs:
            if len(running) >= self.settings.parallel_downloads:
                break
            if job.running or job.pool_entry in busy:
                continue
            job.running = True
            if job.kaggle is not None:
                self.rev += 1
            running.append(job)
            busy.add(job.pool_entry)
            # A fresh context: the job must not carry the starting chat's context
            job.task = asyncio.get_running_loop().create_task(self._run_job(job), context=contextvars.Context())

    async def wait_idle(self) -> None:
        """Wait until the queue is empty and no job runs (tests)."""
        while self._jobs:
            tasks = {j.task for j in self._jobs if j.task is not None and not j.task.done()}
            if not tasks:
                return  # waiting jobs with no free slot (parallel_downloads = 0)
            await asyncio.wait(tasks)

    async def close(self) -> None:
        """Cancel every job (tests and shutdown)."""
        for job in list(self._jobs):
            job.cancelled = True
            if job.task is not None:
                job.task.cancel()
        tasks = [j.task for j in self._jobs if j.task is not None]
        await asyncio.gather(*tasks, return_exceptions=True)
        self._jobs.clear()

    def _job_folder(self, job: Job) -> Path:
        return self.pool.partial_dir() / f"job-{job.id}"

    def _remove_job_folder(self, job: Job) -> None:
        """A finished, failed or cancelled job leaves no partial files."""
        ds.remove_tree(self._job_folder(job))
        try:
            self.pool.partial_dir().rmdir()  # only when empty
        except OSError:
            pass

    async def _on_bytes(self, job: Job, n: int) -> None:
        if job.kaggle is not None:
            job.kaggle.downloads.require_generation(job.kaggle.selection.account_generation)
        job.done_bytes += n
        free = self.free_bytes(self.root)
        if free <= self.settings.min_free_disk_mb * MB:
            raise WorkspaceError(ErrorCode.DOWNLOAD_FAILED, "disk full")
        now = time.monotonic()
        if now - job.last_push >= self.progress_interval:
            job.last_push = now
            if job.kaggle is not None:
                self.rev += 1
            await self._push_all(job.waiters)

    async def _run_job(self, job: Job) -> None:
        """Stream into .dataset-partial/job-<n>/, verify, move into the pool entry, link, push, message, note."""
        error: str | None = None
        try:
            await self._push_all(job.waiters)
            folder = self._job_folder(job)
            async with self.client_factory() as client:
                for f in job.files:
                    dest = ds.path_inside(folder, f.path)
                    if job.kaggle is not None:
                        await self._stream_kaggle_file(job, f, dest)
                    elif job.is_link:
                        f.size = await links.stream_link_file(
                            f, dest, client=client, on_bytes=lambda n: self._on_bytes(job, n),
                            stall_timeout=self.settings.stall_timeout_seconds,
                        )
                    else:
                        await sources.stream_file(
                            f, dest, client=client, on_bytes=lambda n: self._on_bytes(job, n),
                            stall_timeout=self.settings.stall_timeout_seconds,
                        )
                    await self._commit_file(job, f, dest)
        except asyncio.CancelledError:
            if job.cancelled:
                return  # cancel() cleans up
            raise
        except WorkspaceError as e:
            error = e.message
        except OSError as e:
            if e.errno == errno.ENOSPC:
                error = "disk full"
            else:
                self._log_download_failure(job, e)
                error = "couldn't write the files (see the app log)"
        except Exception as failure:  # noqa: BLE001 - Unexpected job failures must still release the queue slot.
            self._log_download_failure(job, failure)
            error = "unexpected error (see the app log)"
        finally:
            if job in self._jobs:
                self._jobs.remove(job)
            self._remove_job_folder(job)
        if error is not None:
            await self._finish_failed(job, error)
        else:
            try:
                await self._finish_ready(job)
            except WorkspaceError as failure:
                await self._finish_failed(job, failure.message)
            except OSError:
                await self._finish_failed(job, "Couldn't link the downloaded files.")
        self._schedule()

    @staticmethod
    def _log_download_failure(job: Job, failure: Exception) -> None:
        if job.kaggle is not None:
            log.warning("Kaggle download into %s failed (%s)", job.pool_entry, type(failure).__name__)
        else:
            log.exception("Dataset download into %s failed", job.pool_entry)

    async def _stream_kaggle_file(self, job: Job, file: PlannedFile, destination: Path) -> None:
        transfer = job.kaggle
        assert transfer is not None
        download = KaggleFileDownload(transfer.selection.ref, transfer.revision, file.path, file.size, transfer.selection.account_generation)
        await transfer.downloads.stream_file(
            download, destination, on_bytes=lambda count: self._on_bytes(job, count),
            stall_timeout_seconds=self.settings.stall_timeout_seconds,
        )

    async def _commit_file(self, job: Job, f: PlannedFile, downloaded: Path) -> None:
        """Move one verified file into the pool and link it into every waiting project that sees it now."""
        async with self._lock:
            if job.kaggle is not None:
                job.kaggle.downloads.require_generation(job.kaggle.selection.account_generation)
            entry = self.pool.get_entry(job.pool_entry)
            if entry is None:
                raise WorkspaceError(ErrorCode.DOWNLOAD_FAILED, "the dataset was removed during the download")
            pool_file = find_pool_file(entry, f.path) or PoolFile(f.path, f.size, f.checksum)
            pool_file.size, pool_file.checksum = f.size, f.checksum
            self.pool.store_file(entry, pool_file, downloaded)
            self.pool.save_index()
            if job.kaggle is not None:
                return
            for project_id, waiter in job.waiters.items():
                dataset = ds.find_project_dataset(self._load(project_id), waiter.dataset_id)
                if dataset is not None and not waiter.switches_entry and f.path in waiter.paths:
                    self.pool.link_files(project_id, dataset.id, entry, [f.path])
                    self._refresh_view(project_id, dataset)

    async def _finish_ready(self, job: Job) -> None:
        async with self._lock:
            if job.kaggle is not None:
                job.kaggle.downloads.require_generation(job.kaggle.selection.account_generation)
            entry = self.pool.get_entry(job.pool_entry)
            finished = [(project_id, w) for project_id, w in job.waiters.items() if self._finish_waiter(project_id, w, entry)]
        await self._push_all(job.waiters)
        if job.kaggle is not None:
            return
        for project_id, waiter in finished:
            dataset = ds.find_project_dataset(self._load(project_id), waiter.dataset_id)
            if dataset is None:
                continue
            size, did = human_size(self._size(dataset)), dataset.id
            await self.chat_notes.tell(
                waiter.session_id,
                f"Dataset **{self._entry(dataset).name}** is ready ({size}) at `{ds.kernel_path(did)}/`.",
                f"- {did}: ready, {size}, files at {ds.kernel_path(did)}/",
            )

    def _finish_waiter(self, project_id: str, waiter: Waiter, entry: PoolEntry | None) -> bool:
        """Under the lock: one project's part of a finished job. True when its dataset is now ready."""
        dataset = ds.find_project_dataset(self._load(project_id), waiter.dataset_id)
        if dataset is None or entry is None or self._jobs_for(project_id, waiter.dataset_id):
            return False
        if entry.source == "kaggle":
            if not waiter.paths <= list_ready_paths(entry):
                raise WorkspaceError(ErrorCode.DOWNLOAD_FAILED, "Not all Kaggle files could be verified.")
            self.pool.link_files(project_id, dataset.id, entry, sorted(waiter.paths))
        if waiter.switches_entry:
            if not waiter.paths <= list_ready_paths(entry):
                return False
            self._switch_entry(project_id, dataset, entry, sorted(waiter.paths))
        dataset.status, dataset.error, dataset.linked_at = "ready", None, utc_now()
        self._save(project_id)
        self._refresh_view(project_id, dataset)  # a view failure turns the card failed, not ready
        return dataset.status == "ready"

    async def _finish_failed(self, job: Job, reason: str) -> None:
        async with self._lock:
            failed = []
            for project_id, waiter in job.waiters.items():
                dataset = ds.find_project_dataset(self._load(project_id), waiter.dataset_id)
                if dataset is None:
                    continue
                # A failed update keeps the version the project already has; asking again shows it ready
                dataset.status = "failed"
                dataset.error = reason[:200]
                self._save(project_id)
                if job.kaggle is not None:
                    self._refresh_view(project_id)
                failed.append((waiter, self._entry(dataset).name))
            if job.waiters and all(w.switches_entry for w in job.waiters.values()):
                self._remove_entry_if_unused(job.pool_entry)
        await self._push_all(job.waiters)
        if job.kaggle is not None:
            return
        for waiter, name in failed:
            await self.chat_notes.tell(waiter.session_id, f"Download of **{name}** failed: {reason}", f"- {waiter.dataset_id}: failed ({reason[:200]})")

    # ---- panel and chat ----

    def panel_state(self, project_id: str) -> tuple[list[DatasetProps], int]:
        """(datasets, datasets_rev) for NotebookProps; NotebookService calls this in _props."""
        users = self._users_by_entry()
        rows: list[DatasetProps] = []
        try:
            datasets = self._load(project_id)
        except WorkspaceError:
            return rows, self.rev  # not migrated yet; logged by _load
        for dataset in datasets:
            view = self._view(project_id, dataset, users)
            entry = self._entry(dataset)
            status = view["status"]
            rows.append(
                {
                    "id": dataset.id,
                    "name": entry.name,
                    "source": entry.source,
                    **({"kaggle_ref": entry.source_id} if entry.source == "kaggle" else {}),
                    "status": status,
                    "done_bytes": view["done_bytes"],
                    "position_in_queue": view["position_in_queue"],
                    "license": entry.license,
                    "size_bytes": view["size_bytes"],
                    "file_count": len(dataset.files),
                    "url": entry.url if (entry.url or "").startswith("https://") else None,
                    "path": ds.kernel_path(dataset.id),
                    "downloaded_at": dataset.linked_at,
                    "requested_at": dataset.requested_at,
                    "files": [{"path": p, "size": f.size if f else 0} for p, f in ((p, find_pool_file(entry, p)) for p in dataset.files[:PANEL_FILES_MAX])],
                    "error": dataset.error if status == "failed" else None,
                    "shared_with": view["shared_with"],
                    "origin": "downloaded",
                    "assistant_description": dataset.assistant_description,
                }
            )
        return rows + self.created.panel_rows(project_id), self.rev

    def take_notes(self, session_id: str) -> str | None:
        """The pending finish note for this chat's next turn, once; None if nothing finished."""
        return self.chat_notes.take(session_id)

    def drop_notes(self, session_id: str) -> None:
        """Forget a closed chat's notes."""
        self.chat_notes.drop(session_id)
