"""Exercise native tool dispatch, lifecycle events and the existing business boundaries."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, ToolException
from langchain_openrouter.chat_models import _convert_message_to_dict

from sillytherin import turn
from sillytherin.agent.tool_errors import INVALID_TOOL_ARGUMENTS
from sillytherin.agent.tool_progress import NativeToolProgress
from sillytherin.chat_start import HISTORY_KEY, PICKS_KEY
from sillytherin.progress import Progress
from sillytherin.tools import (
    chosen_tools,
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
from sillytherin.turn import TurnViews, UserMessage
from sillytherin.workspace import ErrorCode, WorkspaceError, actions, store
from sillytherin.workspace.datasets.service import DatasetService
from sillytherin.workspace.datasets.sources import DatasetPlan
from sillytherin.workspace.service import NotebookService
from tests.agent.conftest import (
    EVERY_SWITCH_ON,
    PLAIN,
    history_of,
    ready_chat,
    run_one_turn,
)
from tests.chat.fakes import (
    FakeAnswerView,
    FakeAsker,
    FakeChatState,
    FakeProgressView,
    FakeToolStep,
    in_chat,
)
from tests.conftest import FakeDatasetService, FakeKernels
from tests.datasets.test_dataset_tools import STARTED, view
from tests.scripted_chat_model import ScriptedChatModel, text_chunk, tool_call_chunk
from tests.tool_calls import TEST_CALL_ID, call_tool, fake_services

TOOL_CASES = (
    (web_search, {"query": "iris"}),
    (read_page, {"url": "https://example.org/iris"}),
    (dataset_search, {"query": "iris", "task": "classification", "language": "en"}),
    (notebook_read, {}),
    (notebook_run, {"code": "df.head()"}),
    (dataset_download, {"ref": "hf:org/name", "files": ["*.csv"], "update": True}),
    (dataset_list, {}),
    (dataset_delete, {"dataset_id": "hf-org--name"}),
    (dataset_describe, {"dataset_id": "hf-org--name", "description": "Iris measurements"}),
)
PRIVATE_MARKER = "private-native-value"
PROJECT = "project-a"
DATASET = "hf-org--name"
REF = "hf:org/name"
RISKY_CODE = "!ls"
INVALID_ARGUMENTS_START = INVALID_TOOL_ARGUMENTS.removesuffix(".")


def tool_of(module: ModuleType) -> BaseTool:
    """The tool a module defines, which carries the module's own name."""
    return getattr(module, module.__name__.rsplit(".", 1)[-1])


def monitor_tools(monkeypatch: pytest.MonkeyPatch, modules: list[ModuleType]) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []

    def recorder(name: str):
        async def record(runtime: object, **arguments: object) -> tuple[str, str]:
            calls.append((name, arguments))
            return f"Model result for {name}", f"UI for {name}"

        return record

    for module in modules:
        native = tool_of(module)
        monkeypatch.setattr(native, "coroutine", recorder(native.name))
    return calls


def invoke_native(tool: BaseTool, arguments: dict) -> ToolMessage:
    return asyncio.run(run_native_call(tool, arguments))


async def run_native_call(tool: BaseTool, arguments: dict) -> ToolMessage:
    """One call with the chat's real services when inside a chat, or fake ones otherwise."""
    try:
        services = turn.build_tool_services(in_chat_state())
    except LookupError:
        services = fake_services()
    result = await call_tool(tool, arguments, services)
    assert isinstance(result, ToolMessage)
    return result


def in_chat_state() -> FakeChatState:
    from sillytherin.chat.current import this_chat

    return this_chat().state


def test_native_tools_dispatch_through_langchain_once(scripted: ScriptedChatModel, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = monitor_tools(monkeypatch, [module for module, _ in TOOL_CASES])
    scripted.scripts += [[
        tool_call_chunk(tool_of(module).name, arguments, call_id=f"call-{index}", index=index)
        for index, (module, arguments) in enumerate(TOOL_CASES)
    ], [text_chunk("Done")]]
    result = run_one_turn()
    assert dict(calls) == {tool_of(module).name: arguments for module, arguments in TOOL_CASES}
    assert len(calls) == len(TOOL_CASES) == len(result.progress.tool_steps)
    assert scripted.tool_names(0) == [tool_of(module).name for module, _ in TOOL_CASES]
    for native in chosen_tools(EVERY_SWITCH_ON):
        assert native.response_format == "content_and_artifact"
        assert {step.output for step in result.progress.tool_steps} >= {f"UI for {native.name}"}
    assert result.answer.final == "Done"


def test_native_tool_optional_args_keep_existing_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = monitor_tools(monkeypatch, [dataset_search, dataset_download, dataset_describe])
    invoke_native(dataset_search.dataset_search, {"query": "iris"})
    invoke_native(dataset_download.dataset_download, {"ref": REF})
    invoke_native(dataset_describe.dataset_describe, {"dataset_id": DATASET})
    assert calls == [
        ("dataset_search", {"query": "iris", "task": "", "language": ""}),
        ("dataset_download", {"ref": REF, "files": None, "update": False}),
        ("dataset_describe", {"dataset_id": DATASET, "description": None}),
    ]


@pytest.mark.parametrize("model,settings", [
    ("a/b", {**EVERY_SWITCH_ON, "notebook": False}),
    (PLAIN, EVERY_SWITCH_ON),
])
def test_unoffered_native_tool_cannot_execute(
    scripted: ScriptedChatModel, monkeypatch: pytest.MonkeyPatch, model: str, settings: dict
) -> None:
    calls = monitor_tools(monkeypatch, [notebook_run])
    scripted.scripts += [[tool_call_chunk("notebook_run", {"code": "1+1"})], [text_chunk("Done")]]
    run_one_turn(ready_chat(settings=settings, model=model))
    assert "notebook_run" not in scripted.tool_names(0)
    assert calls == []


def test_unknown_native_tool_fails_without_dispatch(scripted: ScriptedChatModel, searches: list[dict]) -> None:
    scripted.scripts += [[tool_call_chunk("missing_tool", {}, call_id="unregistered")], [text_chunk("Recovered")]]
    result = run_one_turn()
    assert searches == []
    assert history_of(result)[-2]["tool_call_id"] == "unregistered"
    assert result.answer.final == "Recovered"


@pytest.mark.parametrize("module,arguments", [
    (web_search, {}), (web_search, {"query": 1}),
    (dataset_download, {"ref": REF, "files": [23]}),
    (dataset_download, {"ref": REF, "files": None}),
    (dataset_download, {"ref": REF, "update": "false"}),
    (notebook_run, {"code": "1+1", "extra": True}),
    (dataset_list, {"unknown": None}), (notebook_read, {"project_id": "project-b"}),
])
def test_native_tool_rejects_missing_fields_and_wrong_types(
    monkeypatch: pytest.MonkeyPatch, module: ModuleType, arguments: dict
) -> None:
    calls = monitor_tools(monkeypatch, [module])
    result = invoke_native(tool_of(module), arguments)
    assert (result.status, result.tool_call_id) == ("error", TEST_CALL_ID)
    assert result.text.startswith(INVALID_ARGUMENTS_START)
    assert calls == []


@pytest.mark.parametrize("name,malformed", [
    ("notebook_read", "{bad"), ("notebook_read", ""), ("notebook_run", '{"code":"1+1"'),
])
def test_malformed_tool_stream_never_invokes_with_empty_arguments(
    scripted: ScriptedChatModel, monkeypatch: pytest.MonkeyPatch, name: str, malformed: str
) -> None:
    calls = monitor_tools(monkeypatch, [notebook_read, notebook_run, web_search])
    scripted.scripts += [[
        tool_call_chunk(name, malformed, call_id="bad", index=0),
        tool_call_chunk("web_search", '{"query":"valid ', call_id="good", index=1),
        tool_call_chunk("", 'sibling"}', call_id="", index=1),
    ], [text_chunk("Done")]]
    result = run_one_turn()
    assert calls == [("web_search", {"query": "valid sibling"})]
    tool_results = {message["tool_call_id"]: message["content"] for message in history_of(result) if message["role"] == "tool"}
    assert tool_results["bad"] == INVALID_TOOL_ARGUMENTS
    assert result.answer.final == "Done"


def test_tool_step_artifact_stays_out_of_model_input_and_history(
    scripted: ScriptedChatModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monitor_tools(monkeypatch, [web_search])
    scripted.scripts += [[tool_call_chunk("web_search", {"query": "iris"})], [text_chunk("Done")]]
    result = run_one_turn()
    expected = {"role": "tool", "content": "Model result for web_search", "tool_call_id": "call-1"}
    assert [step.output for step in result.progress.tool_steps] == ["UI for web_search"]
    assert scripted.requests[-1]["messages"][-1] == expected
    assert history_of(result)[-2] == expected
    native_result = invoke_native(web_search.web_search, {"query": "iris"})
    assert native_result.artifact == "UI for web_search"
    assert _convert_message_to_dict(native_result) == {**expected, "tool_call_id": TEST_CALL_ID}


def test_parallel_native_callbacks_finish_the_correct_steps() -> None:
    view = FakeProgressView()
    observer = NativeToolProgress(Progress(view))
    first, second = uuid4(), uuid4()

    async def observe_parallel_calls() -> None:
        for run_id in (first, second):
            await observer.on_tool_start({"name": "web_search"}, "", run_id=run_id, inputs={"query": "same"})
        assert [step.output for step in view.tool_steps] == [None, None]
        for run_id, label in ((second, "second"), (first, "first")):
            message = ToolMessage(content="for model", artifact=label, tool_call_id=label)
            await observer.on_tool_end(message, run_id=run_id)
            await observer.on_tool_end(message, run_id=run_id)

    asyncio.run(observe_parallel_calls())
    assert [step.title for step in view.tool_steps] == ["Web search: same"] * 2
    assert [step.output for step in view.tool_steps] == ["first", "second"]


def test_expected_tool_failure_keeps_step_and_round_usable(
    scripted: ScriptedChatModel, searches: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    def timed_out(url: str) -> str:
        raise ToolException("The site took too long to respond.")

    monkeypatch.setattr(read_page, "fetch_page_text", timed_out)
    scripted.scripts += [[
        tool_call_chunk("read_page", {"url": "https://example.org"}, call_id="failure", index=0),
        tool_call_chunk("web_search", {"query": "iris"}, call_id="success", index=1),
    ], [text_chunk("Done")]]
    result = run_one_turn()
    assert searches == [{"query": "iris"}]
    assert "Failed: The site took too long to respond." in [step.output for step in result.progress.tool_steps]
    assert result.answer.final == "Done"


def test_unexpected_native_tool_error_does_not_expose_details(
    scripted: ScriptedChatModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []

    def fail(query: str) -> list[dict]:
        calls.append({"query": query})
        raise ValueError(PRIVATE_MARKER)

    monkeypatch.setattr(web_search, "search", fail)
    scripted.scripts += [[tool_call_chunk("web_search", {"query": "iris"})], [text_chunk("Done")]]
    result = run_one_turn()
    assert calls == [{"query": "iris"}]
    assert result.progress.tool_steps[0].output == "Failed: Could not complete the tool call."
    assert PRIVATE_MARKER not in repr(scripted.requests)


def test_stop_cancels_native_tool_without_retrying(scripted: ScriptedChatModel, monkeypatch: pytest.MonkeyPatch) -> None:
    chat, answer, progress = ready_chat(), FakeAnswerView(), FakeProgressView()
    picks = chat.get(PICKS_KEY)
    views = TurnViews(answer=answer, progress=progress, asker=FakeAsker(None), settings=None)
    scripted.scripts.append([tool_call_chunk("web_search", {"query": "iris"})])
    calls: list[dict] = []

    async def stop_running_turn() -> None:
        started, stopped = asyncio.Event(), asyncio.Event()

        async def wait(query: str, runtime: object) -> tuple[str, str]:
            calls.append({"query": query})
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
            return "", ""

        monkeypatch.setattr(web_search.web_search, "coroutine", wait)
        with in_chat(chat):
            task = asyncio.create_task(turn.run_turn(chat, UserMessage("hello", picks.model, picks.effort), views))
            await asyncio.wait_for(started.wait(), timeout=2)
            task.cancel()
            await asyncio.wait_for(task, timeout=2)
        assert stopped.is_set()

    asyncio.run(stop_running_turn())
    assert len(calls) == 1
    assert progress.tool_steps[0].output is None
    assert all(message["role"] != "tool" for message in chat.get(HISTORY_KEY))


def test_round_limit_still_blocks_native_tools(
    scripted: ScriptedChatModel, searches: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(turn, "TOOL_MAX_ROUNDS", 1)
    scripted.scripts += [
        [tool_call_chunk("web_search", {"query": "allowed"})],
        [text_chunk("Final"), tool_call_chunk("web_search", {"query": "not allowed"}, call_id="extra")],
    ]
    result = run_one_turn()
    assert searches == [{"query": "allowed"}]
    assert result.answer.final == "Final"
    assert scripted.requests[-1]["tool_choice"] == "none"


def test_native_tool_validation_does_not_echo_invalid_values(
    scripted: ScriptedChatModel, searches: list[dict], caplog: pytest.LogCaptureFixture
) -> None:
    scripted.scripts += [
        [tool_call_chunk("web_search", {"query": PRIVATE_MARKER, "extra": PRIVATE_MARKER})], [text_chunk("Done")],
    ]
    result = run_one_turn()
    assert searches == []
    assert PRIVATE_MARKER not in repr(result.progress.tool_steps) + caplog.text
    assert scripted.requests[-1]["messages"][-1]["content"].startswith(INVALID_ARGUMENTS_START)


@pytest.mark.parametrize("broken_method", ["open", "finish"])
def test_tool_progress_failure_does_not_change_native_execution(
    scripted: ScriptedChatModel, searches: list[dict], monkeypatch: pytest.MonkeyPatch, broken_method: str
) -> None:
    async def broken_view(view: FakeProgressView, title: str) -> FakeToolStep:
        raise RuntimeError("The UI is unavailable.")

    async def broken_step(step: FakeToolStep, output: str) -> None:
        raise RuntimeError("The UI is unavailable.")

    if broken_method == "open":
        monkeypatch.setattr(FakeProgressView, "start_tool_step", broken_view)
    else:
        monkeypatch.setattr(FakeToolStep, "finish", broken_step)
    scripted.scripts += [[tool_call_chunk("web_search", {"query": "iris"})], [text_chunk("Done")]]
    result = run_one_turn()
    assert searches == [{"query": "iris"}]
    assert all(step.output is None for step in result.progress.tool_steps)
    assert result.answer.final == "Done"
    assert scripted.requests[-1]["messages"][-1]["content"].startswith("Result:")


def test_native_graph_error_creates_one_failed_step_without_callback() -> None:
    view = FakeProgressView()
    observer = NativeToolProgress(Progress(view))
    message = ToolMessage(content=PRIVATE_MARKER, status="error", tool_call_id="unregistered")

    async def observe_same_result_twice() -> None:
        await observer.observe_tool_result(message)
        await observer.observe_tool_result(message)

    asyncio.run(observe_same_result_twice())
    assert [step.title for step in view.tool_steps] == ["Unavailable tool"]
    assert PRIVATE_MARKER not in view.tool_steps[0].output


class ApprovalDatasets(FakeDatasetService):
    """Record only approved mutations while using the tools' normal chat prompts."""

    def __init__(self) -> None:
        super().__init__()
        self.mutations: list[tuple[str, str, str]] = []
        self.plan = DatasetPlan("huggingface", "org/name", "Iris", "rev", "https://example.org/iris", None, [])

    async def request(self, project_id: str, session_id: str, ref: str, files: list[str] | None = None,
                      update: bool = False, *, approve, pasted_links: frozenset[str] = frozenset()) -> dict:
        if not await approve(self.plan, 300_000_000):
            raise WorkspaceError(ErrorCode.DECLINED, "Download declined.")
        self.mutations.append(("download", project_id, ref))
        return STARTED

    def describe(self, project_id: str, dataset_id: str) -> dict:
        if (project_id, dataset_id) != (PROJECT, DATASET):
            raise WorkspaceError(ErrorCode.NOT_FOUND, "Dataset not found.")
        return view(dataset_id, "ready")

    async def delete(self, project_id: str, dataset_id: str) -> dict:
        self.mutations.append(("delete", project_id, dataset_id))
        return {"deleted": dataset_id, "freed_bytes": 0, "still_used_by": 0}


@dataclass
class ScopedTools:
    chat: FakeChatState
    datasets: ApprovalDatasets
    kernels: FakeKernels
    root: Path


@pytest.fixture
def scoped_tools(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_kernels: FakeKernels) -> ScopedTools:
    store.create_project("First project", tmp_path, project_id=PROJECT)
    store.create_project("Second project", tmp_path, project_id="project-b")
    chat = ready_chat()
    chat.set(actions.SESSION_KEY, PROJECT)
    datasets = ApprovalDatasets()
    monkeypatch.setattr(actions, "service", NotebookService(fake_kernels, root=tmp_path))
    monkeypatch.setattr(actions, "datasets", datasets)
    monkeypatch.setattr(notebook_run, "MODEL_AUTO_RUN", False)
    return ScopedTools(chat, datasets, fake_kernels, tmp_path)


@pytest.mark.parametrize("module,args,choice", [
    (notebook_run, {"code": RISKY_CODE}, "run"),
    (dataset_download, {"ref": REF}, "download"),
    (dataset_delete, {"dataset_id": DATASET}, "delete"),
])
def test_native_tools_preserve_project_and_approval_boundaries(
    scoped_tools: ScopedTools, module: ModuleType, args: dict, choice: str
) -> None:
    asker = FakeAsker(choice)
    with in_chat(scoped_tools.chat, asker=asker):
        result = invoke_native(tool_of(module), args)
    assert result.status == "success" and len(asker.asked) == 1
    assert choice in {option.id for option in asker.asked[0][1]}
    if module is notebook_run:
        assert scoped_tools.kernels.executed == [RISKY_CODE]
        assert [cell.source for cell in store.load_notebook(PROJECT, scoped_tools.root).cells] == [RISKY_CODE]
        assert store.load_notebook("project-b", scoped_tools.root).cells == []
    else:
        assert scoped_tools.datasets.mutations == [(choice, PROJECT, args.get("ref") or args["dataset_id"])]


@pytest.mark.parametrize("args", [{"dataset_id": "foreign"}, {"dataset_id": DATASET, "project_id": "project-b"}])
def test_native_tool_cannot_override_chat_project(scoped_tools: ScopedTools, args: dict) -> None:
    asker = FakeAsker("delete")
    with in_chat(scoped_tools.chat, asker=asker):
        result = invoke_native(dataset_delete.dataset_delete, args)
    assert result.status == "error"
    assert not asker.asked and not scoped_tools.datasets.mutations


def test_native_call_cannot_approve_itself(scoped_tools: ScopedTools) -> None:
    asker = FakeAsker(None)
    with in_chat(scoped_tools.chat, asker=asker):
        result = invoke_native(notebook_run.notebook_run, {"code": RISKY_CODE, "approved": True})
    assert result.text.startswith(INVALID_ARGUMENTS_START)
    assert not asker.asked and not scoped_tools.kernels.executed
    assert store.load_notebook(PROJECT, scoped_tools.root).cells == []


@pytest.mark.parametrize("module,args,choice", [
    (notebook_run, {"code": RISKY_CODE}, "skip"), (notebook_run, {"code": RISKY_CODE}, None),
    (dataset_download, {"ref": REF}, "skip"), (dataset_download, {"ref": REF}, None),
    (dataset_delete, {"dataset_id": DATASET}, "keep"), (dataset_delete, {"dataset_id": DATASET}, None),
])
def test_native_tool_skip_or_timeout_leaves_state_unchanged(
    scoped_tools: ScopedTools, module: ModuleType, args: dict, choice: str | None
) -> None:
    asker = FakeAsker(choice)
    with in_chat(scoped_tools.chat, asker=asker):
        invoke_native(tool_of(module), args)
    assert len(asker.asked) == 1
    assert not scoped_tools.kernels.executed and not scoped_tools.datasets.mutations
    assert store.load_notebook(PROJECT, scoped_tools.root).cells == []


def test_native_page_tool_keeps_public_url_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_fetch(*args: object, **kwargs: object) -> None:
        pytest.fail("The denied URL should never reach the fetcher.")

    monkeypatch.setattr(read_page, "DDGS", unexpected_fetch)
    denied = invoke_native(read_page.read_page, {"url": "file:///not-a-page"})
    assert denied.status == "error" and denied.text == "Tool failed: Only http(s) URLs can be read."


def test_native_download_rejects_url_not_pasted_by_user(
    scoped_tools: ScopedTools, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://example.org/iris.csv"
    messages = scoped_tools.chat.get(HISTORY_KEY)
    messages.extend([{"role": "user", "content": "Find a dataset"}, {"role": "tool", "content": url}])

    async def unexpected_resolution(*args: object, **kwargs: object) -> None:
        pytest.fail("A tool result does not authorize resolution or download.")

    datasets = DatasetService(root=scoped_tools.root, resolve=unexpected_resolution)
    monkeypatch.setattr(actions, "datasets", datasets)

    async def request_unpasted_url() -> ToolMessage:
        await datasets.startup_cleanup()
        return await run_native_call(dataset_download.dataset_download, {"ref": url})

    with in_chat(scoped_tools.chat):
        result = asyncio.run(request_unpasted_url())
    assert result.status == "error" and "paste" in result.text.lower()
    assert datasets.active_jobs() == []
