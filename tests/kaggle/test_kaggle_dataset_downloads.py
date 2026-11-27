"""Kaggle RPC and storage contracts against a fake provider, with no live credentials."""

import asyncio
import json
import logging
from pathlib import Path

import httpx
import pytest

from sillytherin.workspace import WorkspaceError
from sillytherin.workspace.kaggle.dataset_downloads import (
    KaggleDatasetDownloads,
    KaggleImportSelection,
)
from sillytherin.workspace.kaggle.dtos.downloads import KaggleFileDownload

TOKEN = "test-token-only-for-kaggle"
SIGNATURE = "test-private-signature"
STORAGE = f"https://storage.googleapis.com/file?signature={SIGNATURE}"
SELECTION = KaggleImportSelection("project", 0, 1, "ana/sample")
FILE = KaggleFileDownload("ana/sample", "7", "sample.csv", 3, 1)


class Credentials:
    connection_generation = 1

    async def access_token_for_generation(self, generation: int) -> str | None:
        return TOKEN if generation == self.connection_generation else None


class Provider:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.rpc_status = 302
        self.storage = STORAGE
        self.storage_response = httpx.Response(200, content=b"a,b")
        self.manifest = {"datasetFiles": [{"name": "sample.csv", "totalBytes": 3}]}
        self.downloads = KaggleDatasetDownloads(Credentials(), self.client)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self.handle), cookies={"session": "must-not-transfer"},
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET":
            return self.storage_response
        if request.url.path.endswith("/DownloadDataset"):
            logging.getLogger("httpcore.http11").debug("private headers: %s %s", self.storage, TOKEN)
            return httpx.Response(self.rpc_status, headers={"Location": self.storage})
        if request.url.path.endswith("/GetDataset"):
            return httpx.Response(200, json={"currentVersionNumber": 7, "licenseName": "CC0"})
        return httpx.Response(200, json=self.manifest)

    def stream(self, destination: Path) -> None:
        async def on_bytes(_n: int) -> None:
            return None

        asyncio.run(self.downloads.stream_file(FILE, destination, on_bytes=on_bytes, stall_timeout_seconds=0.01))

    def resolve(self) -> None:
        asyncio.run(self.downloads.resolve_current(SELECTION, max_files=10))


def test_storage_request_has_no_token_and_signature_stays_private(tmp_path, caplog) -> None:
    provider = Provider()
    with caplog.at_level(logging.DEBUG):
        provider.stream(tmp_path / "sample.csv")
    api, storage = provider.requests
    assert api.url.host == "api.kaggle.com"
    assert api.headers["authorization"] == f"Bearer {TOKEN}"
    assert storage.url.host == "storage.googleapis.com"
    assert "authorization" not in storage.headers and "cookie" not in storage.headers
    assert (tmp_path / "sample.csv").read_bytes() == b"a,b"
    assert TOKEN not in caplog.text and SIGNATURE not in caplog.text


@pytest.mark.parametrize(
    ("status", "message"),
    [(403, "dataset unavailable"), (404, "dataset unavailable"), (429, "Try again later")],
    ids=["test_permission_denied_shows_safe_failed_reason", "test_file_not_found_uses_pinned_version_and_fails",
         "test_rate_limit_fails_without_retry_loop"],
)
def test_refused_file_is_not_retried(status: int, message: str, tmp_path) -> None:
    provider = Provider()
    provider.rpc_status = status
    with pytest.raises(WorkspaceError, match=message):
        provider.stream(tmp_path / "sample.csv")
    assert len(provider.requests) == 1
    assert json.loads(provider.requests[0].content)["datasetVersionNumber"] == 7
    assert not (tmp_path / "sample.csv").exists()


@pytest.mark.parametrize("destination", [
    "https://private.test/file", "http://storage.googleapis.com/file",
    "https://storage.googleapis.com:444/file", "https://user@storage.googleapis.com/file",
])
def test_unapproved_redirect_target_is_not_contacted(destination: str, tmp_path) -> None:
    provider = Provider()
    provider.storage = destination
    with pytest.raises(WorkspaceError, match="unexpected storage redirect"):
        provider.stream(tmp_path / "sample.csv")
    assert len(provider.requests) == 1 and not (tmp_path / "sample.csv").exists()


def test_storage_redirect_cannot_chain(tmp_path) -> None:
    provider = Provider()
    provider.storage_response = httpx.Response(302, headers={"Location": "https://private.test/file"})
    with pytest.raises(WorkspaceError, match="did not return"):
        provider.stream(tmp_path / "sample.csv")
    assert len(provider.requests) == 2 and not (tmp_path / "sample.csv").exists()


class StalledStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"a"
        await asyncio.Event().wait()


def test_stalled_storage_transfer_leaves_no_partial_mount(tmp_path) -> None:
    provider = Provider()
    provider.storage_response = httpx.Response(200, stream=StalledStream())
    with pytest.raises(WorkspaceError, match="stopped responding"):
        provider.stream(tmp_path / "staging" / "sample.csv")
    assert not (tmp_path / "sample.csv").exists()


def test_short_file_is_never_published(tmp_path) -> None:
    provider = Provider()
    provider.storage_response = httpx.Response(200, content=b"x")
    with pytest.raises(WorkspaceError, match="wrong size"):
        provider.stream(tmp_path / "staging" / "sample.csv")
    assert not (tmp_path / "sample.csv").exists()


@pytest.mark.parametrize(
    ("manifest", "message"),
    [({"datasetFiles": [{"name": "../escape", "totalBytes": 3}]}, "unsafe file"),
     ({"datasetFiles": [{"name": "sample.csv", "totalBytes": 3}] * 2}, "duplicate file"),
     ({"nextPageToken": "same"}, "invalid file manifest cursor")],
    ids=["test_unsafe_manifest_path_is_rejected_before_transfer", "test_duplicate_manifest_path_is_rejected",
         "test_repeating_page_token_is_rejected"],
)
def test_invalid_manifest_does_not_stream(manifest: dict, message: str) -> None:
    provider = Provider()
    provider.manifest = manifest
    with pytest.raises(WorkspaceError, match=message):
        provider.resolve()
    assert len(provider.requests) <= 3
    assert all(not request.url.path.endswith("/DownloadDataset") for request in provider.requests)
