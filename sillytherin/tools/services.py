"""What each tool needs from the app's services, as small Protocols.

The app passes DatasetService and NotebookService; tests pass fakes that have
only the methods a tool calls.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sillytherin.workspace.datasets.service import (
    Approve,
    DatasetRecordView,
    DeleteResult,
    DownloadStarted,
)

if TYPE_CHECKING:
    from sillytherin.tools.datasets.dataset_describe import ChatLooks
    from sillytherin.workspace.service import NotebookRunOutcome


class DatasetLister(Protocol):
    def list_for_model(self, project_id: str) -> list[DatasetRecordView]: ...


class UndescribedDatasets(Protocol):
    def needs_description(self, project_id: str) -> list[DatasetRecordView]: ...


class ProjectDatasetLookup(UndescribedDatasets, Protocol):
    def project_refs(self, project_id: str) -> dict[str, DatasetRecordView]: ...


class DatasetDownloader(ProjectDatasetLookup, Protocol):
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
    ) -> DownloadStarted: ...


class DatasetDescriber(Protocol):
    def view_for_model(self, project_id: str, dataset_id: str) -> DatasetRecordView: ...

    async def save_description(self, project_id: str, dataset_id: str, text: str) -> DatasetRecordView: ...


class DatasetDeleter(Protocol):
    def describe(self, project_id: str, dataset_id: str) -> DatasetRecordView: ...

    async def delete(self, project_id: str, dataset_id: str) -> DeleteResult: ...


class NotebookReader(Protocol):
    async def read_for_model(self, project_id: str) -> str: ...


class NotebookRunner(Protocol):
    async def run_for_model(self, project_id: str, code: str) -> "str | NotebookRunOutcome": ...


class NotebookEditor(Protocol):
    async def edit_failed_cell_for_model(
        self, project_id: str, cell_id: str, source: str, version: int
    ) -> "str | NotebookRunOutcome": ...


ConfirmRetry = Callable[[str, str | None], Awaitable[bool]]  # (name, reason) -> Download again?
ApproveDelete = Callable[[str, int, int], Awaitable[bool]]  # (name, frees_bytes, shared_with) -> Delete?
ApproveRun = Callable[[str, list[str]], Awaitable[bool]]  # (code, risk reasons) -> Run?


class NotebookAccess(NotebookReader, NotebookRunner, NotebookEditor, Protocol):
    """The notebook as the tools use it."""


class DatasetAccess(DatasetLister, DatasetDownloader, DatasetDescriber, DatasetDeleter, Protocol):
    """The dataset service as the tools use it."""


@dataclass(frozen=True)
class ToolServices:
    """What a tool gets from the running message, through LangChain's runtime context; never shown to the model."""

    notebook: NotebookAccess
    datasets: DatasetAccess
    project_id: str  # the chat's project; the model can never choose it
    chat_id: str  # keys this chat's looks and downloads
    pasted_links: frozenset[str]  # links the user really pasted; only these may be downloaded
    approve_run: ApproveRun
    approve_download: Approve
    confirm_retry: ConfirmRetry
    approve_delete: ApproveDelete
    looked: "ChatLooks"  # undescribed datasets the assistant looked at in this chat
