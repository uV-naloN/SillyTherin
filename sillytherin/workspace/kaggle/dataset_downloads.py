"""Server-side contract for picker-initiated Kaggle imports."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Protocol, TypedDict
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from sillytherin.workspace import ErrorCode, WorkspaceError, store
from sillytherin.workspace.datasets.sources import DatasetPlan, PlannedFile
from sillytherin.workspace.datasets.store import NAME_MAX, safe_relpath
from sillytherin.workspace.kaggle.account import HttpFactory

DOWNLOAD_ACTION = "kg_download_dataset"
DOWNLOAD_PROMPT = "kaggle_download"
MAX_PENDING_IMPORTS = 8
DatasetRpc = Literal["GetDataset", "ListDatasetFiles", "DownloadDataset"]
DATASET_RPC_URLS: dict[DatasetRpc, str] = {
    "GetDataset": "https://api.kaggle.com/v1/datasets.DatasetApiService/GetDataset",
    "ListDatasetFiles": "https://api.kaggle.com/v1/datasets.DatasetApiService/ListDatasetFiles",
    "DownloadDataset": "https://api.kaggle.com/v1/datasets.DatasetApiService/DownloadDataset",
}
STORAGE_HOST = "storage.googleapis.com"
MAX_MANIFEST_PAGES = 100
MAX_FILE_BYTES = (1 << 63) - 1  # SQLite stores sizes as signed integers
UNAVAILABLE = "Kaggle is unavailable. Try again."
NOT_AVAILABLE = "Kaggle dataset unavailable."
RECONNECT = "Connect to Kaggle again to download this dataset."
KEEP_HTTP_PRIVATE: ContextVar[bool] = ContextVar("keep_kaggle_http_private", default=False)


class KaggleImportError(StrEnum):
    STALE_PROJECT = "STALE_PROJECT"
    STALE_APPROVAL = "STALE_APPROVAL"


class KaggleDownloadPayload(BaseModel):
    """A picker selection; the socket, not this DTO, chooses the destination."""

    model_config = ConfigDict(extra="ignore", strict=True, frozen=True)

    project_id: Annotated[str, Field(min_length=1, max_length=store.PROJECT_ID_MAX, pattern=store.PROJECT_ID_RE)]
    ref: Annotated[str, Field(max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9_-]*$")]


class KaggleDownloadAccepted(TypedDict):
    state: Literal["accepted"]
    dataset_id: str  # public project dataset ID, not the pool ID


class KaggleDownloadSkipped(TypedDict):
    state: Literal["skipped"]


class KaggleDownloadError(TypedDict):
    code: str
    message: str  # sanitized one-line text for the picker


class KaggleDownloadRejected(TypedDict):
    state: Literal["error"]
    error: KaggleDownloadError


KaggleDownloadResult = KaggleDownloadAccepted | KaggleDownloadSkipped | KaggleDownloadRejected


def reject_import(code: str, message: str) -> KaggleDownloadRejected:
    return {"state": "error", "error": {"code": code, "message": message}}


@dataclass(frozen=True)
class KaggleImportSelection:
    project_id: str
    project_generation: int
    account_generation: int
    ref: str


@dataclass(frozen=True)
class KaggleApprovalOffer:
    selection: KaggleImportSelection
    dataset_name: str
    revision: str
    download_bytes: int


@dataclass(frozen=True)
class KaggleFileDownload:
    ref: str
    revision: str
    path: str
    size_bytes: int
    account_generation: int


ApproveKaggleImport = Callable[[KaggleApprovalOffer], Awaitable[bool]]


class KaggleCredentials(Protocol):
    @property
    def connection_generation(self) -> int: ...

    async def access_token_for_generation(self, generation: int) -> str | None: ...


class PrivateHttpLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not KEEP_HTTP_PRIVATE.get()


@contextmanager
def keep_http_private() -> Iterator[None]:
    # HTTP debug logs can include the redirect headers, not just its query string.
    for name in ("httpx", "httpcore.http11", "httpcore.http2"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, PrivateHttpLog) for item in logger.filters):
            logger.addFilter(PrivateHttpLog())
    token = KEEP_HTTP_PRIVATE.set(True)
    try:
        yield
    finally:
        KEEP_HTTP_PRIVATE.reset(token)


def kaggle_error(message: str) -> WorkspaceError:
    return WorkspaceError(ErrorCode.DOWNLOAD_FAILED, message)


def validate_kaggle_status(status: int) -> None:
    if status == 401:
        raise WorkspaceError(ErrorCode.KAGGLE_SIGNIN, RECONNECT)
    if status in (403, 404):
        raise kaggle_error(NOT_AVAILABLE)
    if status == 429:
        raise kaggle_error("Kaggle is busy. Try again later.")
    if status != 200:
        raise kaggle_error(UNAVAILABLE)


def text_field(value: object, maximum: int = NAME_MAX) -> str | None:
    return " ".join(value.split())[:maximum] or None if isinstance(value, str) else None


def parse_manifest_file(raw_file: object) -> PlannedFile:
    if not isinstance(raw_file, dict):
        raise kaggle_error("Kaggle returned an invalid file manifest.")
    try:
        path = safe_relpath(raw_file.get("name"))
    except WorkspaceError:
        raise kaggle_error("Kaggle returned an unsafe file name.") from None
    size = raw_file.get("totalBytes")
    if type(size) is not int or not 0 <= size <= MAX_FILE_BYTES:
        raise kaggle_error("Kaggle did not provide a valid file size.")
    return PlannedFile(path=path, size=size, checksum=None, url="")


def storage_request(location: str) -> httpx.Request:
    try:
        target = urlsplit(location)
        is_storage = target.scheme == "https" and target.hostname == STORAGE_HOST and target.port in (None, 443)
        is_clean = target.username is None and target.password is None and not target.fragment and "\\" not in location
        if is_storage and is_clean:
            return httpx.Request("GET", location, headers={"Accept-Encoding": "identity"})
    except (ValueError, httpx.InvalidURL):
        pass
    raise kaggle_error("Kaggle returned an unexpected storage redirect.")


class KaggleDatasetDownloads:
    def __init__(self, account: KaggleCredentials, http_factory: HttpFactory) -> None:
        self.account = account
        self.http_factory = http_factory

    async def resolve_current(self, selection: KaggleImportSelection, *, max_files: int) -> DatasetPlan:
        token = await self._access_token(selection.account_generation)
        owner, slug = selection.ref.split("/", 1)
        dataset = {"ownerSlug": owner, "datasetSlug": slug}
        with keep_http_private():
            async with self.http_factory() as client:
                metadata = await self._read_json(client, token, "GetDataset", dataset)
                version = metadata.get("currentVersionNumber")
                if type(version) is not int or version <= 0:
                    raise kaggle_error("Kaggle did not provide a current dataset version.")
                versioned = {**dataset, "datasetVersionNumber": version}
                files = await self._read_manifest(client, token, versioned, max_files, selection.account_generation)
        self.require_generation(selection.account_generation)
        return DatasetPlan(
            source="kaggle", source_id=selection.ref, name=text_field(metadata.get("title")) or selection.ref,
            revision=str(version), url=f"https://www.kaggle.com/datasets/{selection.ref}",
            license=text_field(metadata.get("licenseName")), files=files,
        )

    async def _access_token(self, generation: int) -> str:
        token = await self.account.access_token_for_generation(generation)
        self.require_generation(generation)
        if token is None:
            raise kaggle_error(UNAVAILABLE)
        return token

    def require_generation(self, generation: int) -> None:
        if self.account.connection_generation != generation:
            raise WorkspaceError(ErrorCode.KAGGLE_SIGNIN, RECONNECT)

    async def _read_json(self, client: httpx.AsyncClient, token: str, method: DatasetRpc, payload: dict) -> dict:
        try:
            response = await client.send(self._rpc(token, method, payload), auth=None, follow_redirects=False)
            validate_kaggle_status(response.status_code)
            parsed = response.json()
        except (httpx.HTTPError, ValueError):
            raise kaggle_error(UNAVAILABLE) from None
        if not isinstance(parsed, dict):
            raise kaggle_error("Kaggle returned an invalid response.")
        return parsed

    @staticmethod
    def _rpc(token: str, method: DatasetRpc, payload: dict) -> httpx.Request:
        return httpx.Request("POST", DATASET_RPC_URLS[method], json=payload, headers={"Authorization": f"Bearer {token}"})

    async def _read_manifest(self, client: httpx.AsyncClient, token: str, dataset: dict, max_files: int, generation: int) -> list[PlannedFile]:
        files: dict[str, PlannedFile] = {}
        cursors: set[str] = set()
        cursor = ""
        for _ in range(MAX_MANIFEST_PAGES):
            self.require_generation(generation)
            page = await self._read_json(client, token, "ListDatasetFiles", {**dataset, "pageToken": cursor, "pageSize": 100})
            self._append_files(files, page, max_files)
            cursor = page.get("nextPageToken", "")
            if not cursor:
                self._require_distinct_paths(files)
                return list(files.values())
            if not isinstance(cursor, str) or cursor in cursors:
                break
            cursors.add(cursor)
        raise kaggle_error("Kaggle returned an invalid file manifest cursor.")

    @staticmethod
    def _require_distinct_paths(files: dict[str, PlannedFile]) -> None:
        if any(str(parent) in files for path in files for parent in PurePosixPath(path).parents):
            raise kaggle_error("Kaggle returned a file path that conflicts with a folder.")

    @staticmethod
    def _append_files(files: dict[str, PlannedFile], page: dict, max_files: int) -> None:
        rows = page.get("datasetFiles", [])
        if not isinstance(rows, list):
            raise kaggle_error("Kaggle returned an invalid file manifest.")
        if len(files) + len(rows) > max_files:
            raise WorkspaceError(ErrorCode.TOO_MANY_FILES, "This Kaggle dataset has too many files.")
        for raw_file in rows:
            file = parse_manifest_file(raw_file)
            if file.path in files:
                raise kaggle_error("Kaggle returned a duplicate file name.")
            files[file.path] = file

    async def stream_file(
        self, download: KaggleFileDownload, destination: Path, *,
        on_bytes: Callable[[int], Awaitable[None]], stall_timeout_seconds: float,
    ) -> None:
        token = await self._access_token(download.account_generation)
        owner, slug = download.ref.split("/", 1)
        payload = {"ownerSlug": owner, "datasetSlug": slug, "datasetVersionNumber": int(download.revision),
                   "fileName": download.path, "raw": True}
        with keep_http_private():
            try:
                async with self.http_factory() as client:
                    request = await self._get_storage_request(client, token, payload)
                    self.require_generation(download.account_generation)
                    response = await client.send(request, stream=True, auth=None, follow_redirects=False)
                    try:
                        await self._save_file(response, destination, download.size_bytes, on_bytes, stall_timeout_seconds)
                    finally:
                        await response.aclose()
            except (httpx.HTTPError, httpx.InvalidURL):
                raise kaggle_error(UNAVAILABLE) from None
        self.require_generation(download.account_generation)

    async def _get_storage_request(self, client: httpx.AsyncClient, token: str, payload: dict) -> httpx.Request:
        rpc = self._rpc(token, "DownloadDataset", payload)
        response = await client.send(rpc, stream=True, auth=None, follow_redirects=False)
        try:
            if response.status_code != 302:
                validate_kaggle_status(response.status_code)
                raise kaggle_error("Kaggle did not return a file redirect.")
            return storage_request(response.headers.get("Location", ""))
        finally:
            await response.aclose()

    @staticmethod
    async def _save_file(
        response: httpx.Response, destination: Path, expected_bytes: int,
        on_bytes: Callable[[int], Awaitable[None]], stall_timeout_seconds: float,
    ) -> None:
        if response.status_code != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
            raise kaggle_error("Kaggle storage did not return the expected file.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        received_bytes = 0
        chunks = response.aiter_bytes()
        with destination.open("wb") as file:
            while True:
                try:
                    async with asyncio.timeout(stall_timeout_seconds):
                        chunk = await anext(chunks)
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    raise kaggle_error("Kaggle download stopped responding.") from None
                received_bytes += len(chunk)
                if received_bytes > expected_bytes:
                    raise kaggle_error("Kaggle returned a file with the wrong size.")
                file.write(chunk)
                await on_bytes(len(chunk))
        if received_bytes != expected_bytes:
            raise kaggle_error("Kaggle returned a file with the wrong size.")
