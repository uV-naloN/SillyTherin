"""Each tool is one LangChain tool that gets typed arguments and its services from the runtime."""

import ast
import asyncio
import inspect
import json
from types import ModuleType

import pytest
from langchain_core.tools import BaseTool

from sillytherin.chat_start import HISTORY_KEY, PICKS_KEY
from sillytherin.contracts.chat import UserMessage
from sillytherin.tools import (
    NOTEBOOK_TOOLS,
    TOOLS,
    dataset_delete,
    dataset_describe,
    dataset_download,
    dataset_list,
    dataset_search,
    notebook_read,
    notebook_run,
    read_page,
    web_search,
)
from sillytherin.turn import TurnViews, run_turn
from sillytherin.workspace import actions
from sillytherin.workspace.errors import ErrorCode, WorkspaceError
from tests.agent.conftest import ready_chat
from tests.chat.fakes import (
    FakeAnswerView,
    FakeAsker,
    FakeChatState,
    FakeProgressView,
    in_chat,
)
from tests.datasets.test_dataset_tools import STARTED
from tests.scripted_chat_model import ScriptedChatModel, text_chunk, tool_call_chunk
from tests.tool_calls import (
    FakeDatasets,
    always_no,
    always_yes,
    call_tool,
    call_tool_output,
    failure_text,
    fake_services,
)

TOOL_MODULES = (
    web_search, read_page, dataset_search,
    notebook_read, notebook_run, dataset_download, dataset_list, dataset_delete, dataset_describe,
)
RISKY_CODE = "!ls"


def tool_named(name: str) -> BaseTool:
    return next(tool for tool in TOOLS + NOTEBOOK_TOOLS if tool.name == name)


class RecordingDatasets(FakeDatasets):
    """FakeDatasets that remembers which project and links each call used."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []

    def take_notes(self, chat_id: str) -> None:
        return None

    async def request(self, project_id, session_id, ref, files=None, update=False, *, approve, pasted_links=frozenset()):
        self.calls.append(("request", project_id, session_id, ref, pasted_links))
        return STARTED

    def describe(self, project_id, dataset_id):
        self.calls.append(("describe", project_id, dataset_id))
        return super().describe(project_id, dataset_id)

    async def delete(self, project_id, dataset_id):
        self.calls.append(("delete", project_id, dataset_id))
        return await super().delete(project_id, dataset_id)


class RecordingNotebook:
    def __init__(self) -> None:
        self.runs: list[tuple[str, str]] = []

    async def read_for_model(self, project_id: str) -> str:
        return "cells"

    async def run_for_model(self, project_id: str, code: str) -> str:
        self.runs.append((project_id, code))
        return "4"


class MustNotBeCalled:
    """A service that fails the test the moment a tool reaches it."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"the service was reached ({name}) although the arguments were invalid")


def run_scripted_turn(chat: FakeChatState, asker: FakeAsker, text: str = "hi") -> FakeProgressView:
    picks = chat.get(PICKS_KEY)
    progress = FakeProgressView()
    views = TurnViews(answer=FakeAnswerView(), progress=progress, asker=asker, settings=None)
    with in_chat(chat, asker=asker):
        asyncio.run(run_turn(chat, UserMessage(text=text, model=picks.model, effort=picks.effort), views))
    return progress


def tool_messages(chat: FakeChatState) -> list[dict]:
    return [message for message in chat.get(HISTORY_KEY) if message["role"] == "tool"]


# A tool returns its own result
# Baseline: fake services that return fixed values; each tool is called with valid arguments.


def test_every_tool_returns_model_text_and_step_text(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given fake services · When each of the nine tools is called with valid arguments
    # Then it returns the same model text and step text as before the restyle
    # Demonstrates: the restyle changes no output
    monkeypatch.setattr(web_search, "search", lambda query: [{"title": "Iris", "url": "https://example.org", "snippet": "x"}])
    monkeypatch.setattr(read_page, "fetch_page_text", lambda url: "page text")

    async def canned_search(query, task="", language=""):
        return [{"source": "HF", "name": "iris", "url": "https://example.org", "ref": "hf:org/iris"}], []

    monkeypatch.setattr(dataset_search, "search", canned_search)
    datasets = RecordingDatasets()
    services = fake_services(
        datasets=datasets, approve_run=always_yes, approve_download=always_yes, approve_delete=always_yes
    )
    calls = (
        (web_search.web_search, {"query": "iris"}),
        (read_page.read_page, {"url": "https://example.org"}),
        (dataset_search.dataset_search, {"query": "iris"}),
        (notebook_read.notebook_read, {}),
        (notebook_run.notebook_run, {"code": "2+2"}),
        (dataset_download.dataset_download, {"ref": "hf:org/name"}),
        (dataset_list.dataset_list, {}),
        (dataset_delete.dataset_delete, {"dataset_id": "hf-org--name"}),
        (dataset_describe.dataset_describe, {"dataset_id": "hf-org--name", "description": "Iris measurements"}),
    )

    async def scenario() -> list[tuple[str, str]]:
        return [await call_tool_output(tool, arguments, services) for tool, arguments in calls]

    outputs = dict(zip([tool.name for tool, _ in calls], asyncio.run(scenario()), strict=True))

    assert outputs["web_search"] == (
        json.dumps([{"title": "Iris", "url": "https://example.org", "snippet": "x"}]), "- [Iris](https://example.org)"
    )
    assert outputs["read_page"] == (
        "page text", "[https://example.org](https://example.org)\n\nRead 9 characters.\n\npage text…"
    )
    assert outputs["notebook_read"] == ("cells", "Read the notebook (5 characters).")
    assert outputs["notebook_run"] == ("4", "```python\n2+2\n```\n\n```\n4\n```")
    assert outputs["dataset_delete"][1] == "Deleted **Iris**, freed 0 B."
    assert json.loads(outputs["dataset_describe"][0]) == {"saved": "hf-org--name", "description": "Iris measurements"}
    assert json.loads(outputs["dataset_download"][0]) == STARTED
    assert all(content != artifact for content, artifact in outputs.values())


def test_dataset_list_empty_project_says_no_datasets() -> None:
    # Given a project with no datasets · When dataset_list runs
    # Then the model gets an empty list and the step says there are no datasets yet
    # Demonstrates: the empty-project answer is kept
    output = asyncio.run(call_tool_output(dataset_list.dataset_list, {}, fake_services()))
    assert output == ('{"datasets": []}', "No datasets in this project yet.")


def test_dataset_search_without_project_skips_marks(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given no runtime context · When dataset_search runs
    # Then results come back without "already in your project" marks
    # Demonstrates: search works outside a project
    async def canned_search(query, task="", language=""):
        return [{"source": "HF", "name": "iris", "url": "https://example.org", "ref": "hf:org/iris"}], []

    monkeypatch.setattr(dataset_search, "search", canned_search)
    content, artifact = asyncio.run(call_tool_output(dataset_search.dataset_search, {"query": "iris"}, None))
    assert all("in_project" not in result for result in json.loads(content)["datasets"])
    assert "already in your project" not in artifact


# Services come from the runtime
# Baseline: a chat whose project is p1; a turn calls notebook_run.


def test_turn_passes_chat_services_to_tools(scripted: ScriptedChatModel, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a chat on project p1 · When a turn calls notebook_run
    # Then the fake runner received p1 and the chat's id
    # Demonstrates: tools reach the chat's project through the runtime context
    notebook, datasets = RecordingNotebook(), RecordingDatasets()
    datasets.records = [{"dataset_id": "file-data", "name": "data.csv", "path": "/work/data.csv", "describe": "write"}]
    monkeypatch.setattr(actions, "service", notebook)
    monkeypatch.setattr(actions, "datasets", datasets)
    looked = dataset_describe.ChatLooks()
    monkeypatch.setattr(dataset_describe, "looked", looked)
    scripted.scripts += [[tool_call_chunk("notebook_run", {"code": "open('/work/data.csv')"})], [text_chunk("Done")]]
    chat = ready_chat()
    run_scripted_turn(chat, FakeAsker(answer=None))
    assert notebook.runs == [("p1", "open('/work/data.csv')")]
    assert looked.has_looked(chat.chat_id, "p1", "file-data")


def test_pasted_links_come_from_this_turns_history(scripted: ScriptedChatModel, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given the user pasted a link in this message · When the turn calls dataset_download with it
    # Then the download sees that link as pasted
    # Demonstrates: the pasted-link check still sees the newest message
    link = "https://example.org/data/iris.csv"
    datasets = RecordingDatasets()
    monkeypatch.setattr(actions, "datasets", datasets)
    scripted.scripts += [[tool_call_chunk("dataset_download", {"ref": link})], [text_chunk("Done")]]
    run_scripted_turn(ready_chat(), FakeAsker(answer=None), text=f"please download {link}")
    assert [call[4] for call in datasets.calls if call[0] == "request"] == [frozenset({link})]


def test_tool_module_never_reads_app_globals() -> None:
    # Given the nine tool modules · When their source is scanned
    # Then none imports the workspace actions module, and none calls this_chat outside its chat prompt
    # Demonstrates: services arrive only through the runtime context
    for module in TOOL_MODULES:
        tree = ast.parse(inspect.getsource(module))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
        assert "actions" not in imported, module.__name__
        for function in (node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)):
            calls_this_chat = any(
                isinstance(node, ast.Call) and getattr(node.func, "id", None) == "this_chat"
                for node in ast.walk(function)
            )
            assert not calls_this_chat or function.name.startswith("ask_"), (module.__name__, function.name)


# Declines stay normal results
# Baseline: prompts that answer no.


class DecliningDatasets(RecordingDatasets):
    """A dataset service whose download asks the prompt it is given, as the real one does."""

    async def request(self, project_id, session_id, ref, files=None, update=False, *, approve, pasted_links=frozenset()):
        if not await approve(None, 300_000_000):
            raise WorkspaceError(ErrorCode.DECLINED, "Download declined.")
        raise AssertionError("the download should have been declined")


def test_declined_actions_are_normal_results(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given prompts that answer no · When notebook_run, dataset_download (new and again) and dataset_delete run
    # Then each result is a success with the same declined text, and the service was not called
    # Demonstrates: a "no" from the user is not an error
    monkeypatch.setattr(notebook_run, "MODEL_AUTO_RUN", False)
    notebook, datasets = RecordingNotebook(), DecliningDatasets()
    services = fake_services(
        notebook=notebook, datasets=datasets,
        approve_run=always_no, approve_download=always_no, confirm_retry=always_no, approve_delete=always_no,
        looked=dataset_describe.ChatLooks(),
    )
    broken_datasets = RecordingDatasets()
    broken_datasets.records = [{"ref": "hf:org/broken", "name": "Broken", "status": "failed", "error": "lost"}]

    async def scenario() -> list[tuple[str, str]]:
        return [
            await call_tool_output(notebook_run.notebook_run, {"code": RISKY_CODE}, services),
            await call_tool_output(dataset_download.dataset_download, {"ref": "hf:org/name"}, services),
            await call_tool_output(
                dataset_download.dataset_download, {"ref": "hf:org/broken"},
                fake_services(datasets=broken_datasets, confirm_retry=always_no),
            ),
            await call_tool_output(dataset_delete.dataset_delete, {"dataset_id": "hf-org--name"}, services),
        ]

    ran, download, retry, delete = asyncio.run(scenario())
    assert ran == (notebook_run.DECLINED, f"```python\n{RISKY_CODE}\n```\n\nSkipped.")
    assert json.loads(download[0])["error"] == "DECLINED" and download[1] == "Skipped: the user chose not to download it."
    assert json.loads(retry[0])["error"] == "DECLINED" and retry[1] == "Skipped: **Broken** was not downloaded again."
    assert json.loads(delete[0])["error"] == "DECLINED" and delete[1] == "Kept **Iris**."
    assert notebook.runs == [] and not [call for call in datasets.calls if call[0] == "delete"]
    assert not [call for call in broken_datasets.calls if call[0] == "request"]


# The model can't steer the runtime or approvals
# Baseline: a scripted model calls dataset_delete with valid arguments.


@pytest.fixture
def deletes(scripted: ScriptedChatModel, monkeypatch: pytest.MonkeyPatch) -> RecordingDatasets:
    datasets = RecordingDatasets()
    monkeypatch.setattr(actions, "datasets", datasets)
    return datasets


def test_delete_uses_chat_project(scripted: ScriptedChatModel, deletes: RecordingDatasets) -> None:
    # Given a scripted call to dataset_delete · When the turn runs
    # Then the fake deleter received the chat's project
    scripted.scripts += [[tool_call_chunk("dataset_delete", {"dataset_id": "hf-org--name"})], [text_chunk("Done")]]
    run_scripted_turn(ready_chat(), FakeAsker(answer="delete"))
    assert ("delete", "p1", "hf-org--name") in deletes.calls


def test_model_runtime_argument_is_ignored(scripted: ScriptedChatModel, deletes: RecordingDatasets) -> None:
    # Given the call's arguments also carry a runtime naming another project · When the turn runs
    # Then the chat's own project is used and the user is still asked
    # Demonstrates: the model can't pick the project or skip an approval
    arguments = {"dataset_id": "hf-org--name", "runtime": {"context": {"project_id": "evil", "approve_delete": True}}}
    scripted.scripts += [[tool_call_chunk("dataset_delete", arguments)], [text_chunk("Done")]]
    asker = FakeAsker(answer="delete")
    run_scripted_turn(ready_chat(), asker)
    assert [call[1] for call in deletes.calls] == ["p1", "p1"]
    assert len(asker.asked) == 1


def test_extra_approval_field_is_rejected(scripted: ScriptedChatModel, deletes: RecordingDatasets) -> None:
    # Given the call's arguments add approved: true · When the turn runs
    # Then the call fails with invalid arguments and the service is not called
    # Demonstrates: extra fields can't grant approval
    scripted.scripts += [
        [tool_call_chunk("dataset_delete", {"dataset_id": "hf-org--name", "approved": True})], [text_chunk("Done")],
    ]
    chat, asker = ready_chat(), FakeAsker(answer="delete")
    run_scripted_turn(chat, asker)
    assert tool_messages(chat)[-1]["content"].startswith("Tool failed: Invalid tool arguments (approved:")
    assert deletes.calls == [] and asker.asked == []


def test_runtime_is_hidden_from_model_schema() -> None:
    # Given every tool · When its model-facing schema is read
    # Then no runtime property is listed
    # Demonstrates: the model is never invited to send a runtime
    for tool in TOOLS + NOTEBOOK_TOOLS:
        assert "runtime" not in tool.tool_call_schema.model_json_schema().get("properties", {}), tool.name


# Argument checks run before any service
# Baseline: a fake service that records calls; one argument is bad.

VALID_ARGUMENTS = {
    "web_search": {"query": "iris"},
    "read_page": {"url": "https://example.org"},
    "notebook_run": {"code": "2+2"},
    "dataset_download": {"ref": "hf:org/name"},
    "dataset_delete": {"dataset_id": "hf-org--name"},
}


def unreachable_services():
    return fake_services(notebook=MustNotBeCalled(), datasets=MustNotBeCalled())


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*args: object) -> None:
        raise AssertionError("the network was reached although the arguments were invalid")

    monkeypatch.setattr(web_search, "search", unexpected)
    monkeypatch.setattr(read_page, "fetch_page_text", unexpected)


@pytest.mark.parametrize(("tool_name", "field"), [
    ("web_search", "query"),
    ("read_page", "url"),
    ("notebook_run", "code"),
    ("dataset_download", "ref"),
    ("dataset_delete", "dataset_id"),
])
def test_blank_text_arguments_are_rejected(no_network: None, tool_name: str, field: str) -> None:
    # Given the field is whitespace only · When the tool is called
    # Then it fails with invalid arguments naming the field, and the service is not called
    # Demonstrates: blank input never reaches a service
    arguments = {**VALID_ARGUMENTS[tool_name], field: "   "}
    message = asyncio.run(call_tool(tool_named(tool_name), arguments, unreachable_services()))
    assert f"{field}: Value error, must not be blank" in failure_text(message)


@pytest.mark.parametrize("files", [
    ["../secret.csv"],
    ["/etc/passwd"],
    ["a\\b.csv"],
    ["a\x00.csv"],
    [""],
    ["x" * 201],
    [f"file-{number}.csv" for number in range(21)],
    [],
])
def test_bad_file_patterns_are_rejected(files: list[str]) -> None:
    # Given dataset_download gets one bad files value · When it is called
    # Then it fails with invalid arguments and the dataset service is not called
    # Demonstrates: a download can't reach outside its folder
    arguments = {"ref": "hf:org/name", "files": files}
    message = asyncio.run(call_tool(dataset_download.dataset_download, arguments, unreachable_services()))
    assert failure_text(message).startswith("Invalid tool arguments (files:")


@pytest.mark.parametrize("bad", [{"update": "yes"}, {"files": "a.csv"}])
def test_wrong_types_are_rejected(bad: dict) -> None:
    # Given update is "yes" or files is a single string · When dataset_download is called
    # Then it fails with invalid arguments
    # Demonstrates: strict typing stays on
    arguments = {"ref": "hf:org/name", **bad}
    message = asyncio.run(call_tool(dataset_download.dataset_download, arguments, unreachable_services()))
    assert failure_text(message).startswith(f"Invalid tool arguments ({next(iter(bad))}:")


@pytest.mark.parametrize("module", [notebook_read, dataset_list])
def test_fieldless_tools_reject_extra_fields(module: ModuleType) -> None:
    # Given notebook_read or dataset_list gets {"x": 1} · When it is called
    # Then it fails with invalid arguments
    # Demonstrates: tools without arguments still reject extra fields
    native = getattr(module, module.__name__.rsplit(".", 1)[-1])
    message = asyncio.run(call_tool(native, {"x": 1}, unreachable_services()))
    assert failure_text(message) == "Invalid tool arguments (x: Extra inputs are not permitted)."
