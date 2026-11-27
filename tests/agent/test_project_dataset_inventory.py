"""Inventory replies follow the active project even when the model skips the tool."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from langchain_core.messages import BaseMessage
from langgraph.graph.state import CompiledStateGraph

from sillytherin import turn
from sillytherin.agent.model import ToolArgumentStream
from sillytherin.progress import Progress
from sillytherin.tools import dataset_list
from sillytherin.tools.shared.services import ToolServices
from sillytherin.workspace import actions, store
from sillytherin.workspace.datasets.created import CreatedDatasets
from sillytherin.workspace.datasets.service import DatasetService
from tests.agent.conftest import ready_chat, run_one_turn
from tests.scripted_chat_model import ScriptedChatModel
from tests.tool_calls import call_tool


@pytest.mark.usefixtures("catalog")
@pytest.mark.xfail(
    reason="Chat can publish a dataset reply without reading the active project inventory",
    raises=AssertionError,
    strict=True,
)
def test_inventory_reply_checks_empty_project_after_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    populated_project = "project-a"
    empty_project = "project-b"
    dataset_name = "project-a-only.csv"
    store.create_project("Project A", tmp_path, project_id=populated_project)
    store.create_project("Project B", tmp_path, project_id=empty_project)
    project_files = store.project_dir(populated_project, tmp_path) / store.FILES_DIR
    (project_files / dataset_name).write_text("value\n42\n", encoding="utf-8")
    datasets = DatasetService(root=tmp_path, created=CreatedDatasets(tmp_path))
    datasets.sync_created(populated_project, "user")
    inventory_reads = Mock(wraps=datasets.list_for_model)
    monkeypatch.setattr(datasets, "list_for_model", inventory_reads)
    monkeypatch.setattr(actions, "datasets", datasets)
    monkeypatch.setattr(turn, "build_chat_model", lambda _request: ScriptedChatModel())

    async def stream_inventory_reply(
        _agent: CompiledStateGraph,
        _messages: list[BaseMessage],
        answer: turn.CollectedAnswer,
        _progress: Progress,
        _tool_arguments: ToolArgumentStream,
        services: ToolServices,
        **_kwargs: object,
    ) -> list[BaseMessage]:
        if services.project_id == populated_project:
            result = await call_tool(dataset_list.dataset_list, {}, services)
            await answer.stream(result.artifact)
            return [result]
        await answer.stream(f"This project contains {dataset_name}.")
        return []

    monkeypatch.setattr(turn, "stream_agent", stream_inventory_reply)
    chat = ready_chat()
    question = "What datasets are here?"
    actions.set_active_project(chat, populated_project)
    first_reply = run_one_turn(chat, text=question).answer
    assert dataset_name in (first_reply.final or "")

    inventory_reads.reset_mock()
    actions.set_active_project(chat, empty_project)
    second_reply = run_one_turn(chat, text=question).answer

    assert second_reply.final == "No datasets in this project yet."
    assert dataset_name not in "".join(second_reply.streamed)
    inventory_reads.assert_any_call(empty_project)
