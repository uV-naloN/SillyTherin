"""Real chat socket tests using a fake owned list and Kaggle manifest."""

import json

import pytest

from sillytherin.server import web_chat
from sillytherin.workspace import actions, store
from sillytherin.workspace.datasets.sources import DatasetPlan, PlannedFile
from sillytherin.workspace.kaggle.account import KaggleAccountStatus
from sillytherin.workspace.kaggle.dtos._my_datasets import (
    MyDatasetsAnswer,
    OwnedDataset,
)
from tests.server.conftest import events_until, open_tab

REF = "ana/sample"
LARGE_FILE_BYTES = 2_000_000


class Source:
    def __init__(self) -> None:
        self.generation = 1
        self.connected = True
        self.is_list_available = True
        self.lookups = 0

    @property
    def connection_generation(self) -> int:
        return self.generation

    def status(self) -> KaggleAccountStatus:
        return KaggleAccountStatus("connected", "ana") if self.connected else KaggleAccountStatus("disconnected")

    async def list_owned(self) -> MyDatasetsAnswer:
        if not self.is_list_available:
            return MyDatasetsAnswer("unreachable")
        refs = [REF, *(f"ana/dataset-{index}" for index in range(9))]
        return MyDatasetsAnswer("listed", "ana", [OwnedDataset("Sample", ref, 1, "CC0", None) for ref in refs])

    def require_generation(self, generation: int) -> None:
        assert generation == self.generation

    async def resolve_current(self, selection, *, max_files: int) -> DatasetPlan:
        self.lookups += 1
        return DatasetPlan("kaggle", selection.ref, "Sample", "2", "", "CC0", [PlannedFile("data.csv", LARGE_FILE_BYTES, None, "")])


@pytest.fixture
def kaggle(client, monkeypatch) -> Source:
    source = Source()
    monkeypatch.setattr(actions, "kaggle_account", source)
    monkeypatch.setattr(actions, "my_kaggle_datasets", source)
    monkeypatch.setattr(actions, "kaggle_dataset_downloads", source)
    client.portal.call(actions.datasets.startup_cleanup)
    actions.datasets.settings.ask_above_mb = 1
    return source


def send_action(tab, request_id: str, action: str, payload: dict) -> None:
    tab.send_json({"type": "panel.action", "request_id": request_id, "name": action, "payload": payload})


def read_result(tab, request_id: str = "import") -> dict:
    result = events_until(tab, "panel.result")[-1]
    assert result["request_id"] == request_id
    return result["result"]


def list_owned(tab) -> dict:
    send_action(tab, "list", "kg_list_my_datasets", {})
    return read_result(tab, "list")


def start(tab, project: str, *, ref: str = REF, request_id: str = "import", extras: dict | None = None) -> None:
    send_action(tab, request_id, "kg_download_dataset", {"project_id": project, "ref": ref, **(extras or {})})


def prepare(tab) -> str:
    project = tab.receive_json()["panel"]["project"]["id"]
    assert list_owned(tab)["state"] == "listed"
    return project


def answer(tab, prompt: dict, choice: str = "download") -> None:
    tab.send_json({"type": "ask.answer", "ask_id": prompt["ask_id"], "choice": choice})


def assert_unchanged(project: str) -> None:
    assert actions.datasets.panel_state(project)[0] == []
    assert not actions.datasets.active_jobs()


@pytest.mark.parametrize("choice", ["download", "skip"], ids=[
    "test_verified_socket_selection_can_start_import", "test_skip_leaves_project_unchanged",
])
def test_chat_choice_controls_import(client, kaggle: Source, choice: str) -> None:
    with open_tab(client) as tab:
        project = prepare(tab)
        start(tab, project)
        prompt = tab.receive_json()
        assert prompt["request_id"] == "import" and prompt["purpose"] == "kaggle_download"
        assert "2.0 MB" in prompt["markdown"] and project in prompt["markdown"]
        assert_unchanged(project)
        answer(tab, prompt, choice)
        result = read_result(tab)
        if choice == "download":
            assert result["state"] == "accepted"
            row, = actions.datasets.panel_state(project)[0]
            assert row["id"] == result["dataset_id"] and row["kaggle_ref"] == REF
        else:
            assert result == {"state": "skipped"}
            assert_unchanged(project)


def test_expired_offer_cannot_start_download(client, kaggle: Source, monkeypatch) -> None:
    monkeypatch.setattr(web_chat, "APPROVAL_TIMEOUT_SECONDS", 0.02)
    with open_tab(client) as tab:
        project = prepare(tab)
        start(tab, project)
        prompt = tab.receive_json()
        assert read_result(tab) == {"state": "skipped"}
        answer(tab, prompt)
        assert tab.receive_json()["code"] == "UNKNOWN_ASK"
        assert_unchanged(project)


@pytest.mark.parametrize("failure,code", [
    ("unlisted", "NOT_FOUND"), ("wrong project", "STALE_PROJECT"),
    ("disconnected", "KAGGLE_SIGNIN"), ("relist failed", "NOT_FOUND"),
], ids=["test_foreign_owner_ref_is_not_found", "test_stale_project_request_cannot_target_other_project",
        "test_missing_signin_cannot_start_import", "test_old_listing_does_not_authorize_after_failed_relist"])
def test_invalid_selection_never_probes_kaggle(client, kaggle: Source, failure: str, code: str) -> None:
    with open_tab(client) as tab:
        project = prepare(tab)
        if failure == "disconnected":
            kaggle.connected = False
        if failure == "relist failed":
            kaggle.is_list_available = False
            assert list_owned(tab)["state"] == "unreachable"
        start(tab, "other" if failure == "wrong project" else project, ref="other/private" if failure == "unlisted" else REF)
        assert read_result(tab)["error"]["code"] == code
        assert kaggle.lookups == 0
        assert_unchanged(project)


@pytest.mark.parametrize("change", ["account", "project"], ids=[
    "test_reconnecting_same_username_invalidates_old_offer", "test_switch_away_and_back_does_not_revive_offer",
])
def test_pending_approval_cannot_cross_generation(client, services, kaggle: Source, change: str) -> None:
    with open_tab(client) as tab:
        project = prepare(tab)
        start(tab, project)
        prompt = tab.receive_json()
        if change == "account":
            kaggle.generation += 1
        else:
            store.create_project("Other", services.root, project_id="other")
            for destination in ("other", project):
                send_action(tab, "switch", "nb_switch_project", {"project_id": destination})
                assert read_result(tab, "switch")["project"]["id"] == destination
        answer(tab, prompt)
        assert read_result(tab)["error"]["code"] == "STALE_APPROVAL"
        assert_unchanged(project)


def test_foreign_socket_cannot_use_approval(client, kaggle: Source) -> None:
    with open_tab(client) as tab:
        project = prepare(tab)
        start(tab, project)
        prompt = tab.receive_json()
        with open_tab(client) as foreign:
            foreign.receive_json()
            answer(foreign, prompt)
            assert foreign.receive_json()["code"] == "UNKNOWN_ASK"
        assert_unchanged(project)
        answer(tab, prompt, "skip")
        assert read_result(tab)["state"] == "skipped"


def test_resumed_socket_cannot_answer_old_kaggle_prompt(client, kaggle: Source) -> None:
    with open_tab(client) as tab:
        ready = tab.receive_json()
        project = ready["panel"]["project"]["id"]
        list_owned(tab)
        start(tab, project)
        old_prompt = tab.receive_json()
    with open_tab(client, ready["chat_id"]) as resumed:
        assert resumed.receive_json()["type"] == "session.ready"
        answer(resumed, old_prompt)
        assert resumed.receive_json()["code"] == "UNKNOWN_ASK"
        start(resumed, project)
        assert read_result(resumed)["error"]["code"] == "NOT_FOUND"
    assert_unchanged(project)


def test_consumed_approval_cannot_be_replayed(client, kaggle: Source) -> None:
    with open_tab(client) as tab:
        project = prepare(tab)
        start(tab, project)
        prompt = tab.receive_json()
        answer(tab, prompt)
        assert read_result(tab)["state"] == "accepted"
        answer(tab, prompt)
        assert tab.receive_json()["code"] == "UNKNOWN_ASK"
        assert len(actions.datasets.active_jobs()) == 1


def test_full_preflight_budget_refuses_new_ref(client, kaggle: Source) -> None:
    with open_tab(client) as tab:
        project = prepare(tab)
        for index in range(8):
            start(tab, project, ref=f"ana/dataset-{index}", request_id=f"pending-{index}")
            assert tab.receive_json()["type"] == "ask.opened"
        start(tab, project, ref="ana/dataset-8")
        assert read_result(tab)["error"]["code"] == "BUSY"
        assert kaggle.lookups == 8
        assert_unchanged(project)


@pytest.mark.parametrize("extra_field", ["approved", "url", "headers", "version"])
def test_injected_fields_cannot_approve_or_redirect_import(client, kaggle: Source, extra_field: str) -> None:
    with open_tab(client) as tab:
        project = prepare(tab)
        start(tab, project, extras={extra_field: True})
        prompt = tab.receive_json()
        assert prompt["type"] == "ask.opened" and "2.0 MB" in prompt["markdown"]
        assert_unchanged(project)
        answer(tab, prompt, "skip")
        assert read_result(tab)["state"] == "skipped"


@pytest.mark.parametrize("malformed", [
    {"ref": REF}, {"project_id": "default"}, {"ref": None}, {"ref": 3},
    {"ref": ""}, {"ref": " "}, {"ref": "ana/" + "a" * 210}, {"ref": "https://kaggle.com/ana/sample"},
])
def test_malformed_start_is_rejected(client, kaggle: Source, malformed: dict) -> None:
    with open_tab(client) as tab:
        project = prepare(tab)
        payload = malformed if "project_id" in malformed or malformed.get("ref") == REF else {"project_id": project, **malformed}
        send_action(tab, "import", "kg_download_dataset", payload)
        assert read_result(tab)["error"]["code"] == "BAD_PAYLOAD"
        assert kaggle.lookups == 0
        assert_unchanged(project)


@pytest.mark.parametrize("missing_or_invalid", ["ask_id", "choice", "unoffered"])
def test_malformed_prompt_answer_is_rejected(client, kaggle: Source, missing_or_invalid: str) -> None:
    with open_tab(client) as tab:
        project = prepare(tab)
        start(tab, project)
        prompt = tab.receive_json()
        response = {"type": "ask.answer", "ask_id": prompt["ask_id"], "choice": "run"}
        response.pop(missing_or_invalid, None)
        tab.send_json(response)
        assert tab.receive_json()["code"] == "BAD_EVENT"
        assert_unchanged(project)
        answer(tab, prompt, "skip")
        assert read_result(tab)["state"] == "skipped"


def test_server_owns_download_target_version_and_approval(client, kaggle: Source) -> None:
    with open_tab(client) as tab:
        project = prepare(tab)
        start(tab, project, extras={"version": 99, "approved": True, "owner": "other"})
        prompt = tab.receive_json()
        assert prompt["choices"] == [{"id": "download", "label": "Download"}, {"id": "skip", "label": "Skip"}]
        assert_unchanged(project)
        answer(tab, prompt)
        assert read_result(tab)["state"] == "accepted"
        entry, = actions.datasets.pool.find_entries("kaggle", REF)
        assert entry.revision == "2"


def test_picker_import_prompt_is_not_saved_or_sent_to_model(client, kaggle: Source, model, sessions) -> None:
    with open_tab(client) as tab:
        ready = tab.receive_json()
        chat = sessions.resume(ready["chat_id"])
        previous = chat.state.get("history")
        list_owned(tab)
        start(tab, ready["panel"]["project"]["id"])
        prompt = tab.receive_json()
        answer(tab, prompt)
        assert read_result(tab)["state"] == "accepted"
        assert chat.state.get("history") == previous and REF not in json.dumps(previous)
        assert not model.requests
