"""What a failed tool call tells the model and the chat."""

import asyncio
import json

import pytest
from langchain.agents.middleware import ToolCallRequest
from langgraph.errors import GraphBubbleUp

from core.agent.tool_errors import (
    INVALID_TOOL_ARGUMENTS,
    MAX_TOOL_ERROR_CHARS,
    TOOL_FAILURE_PREFIX,
    ToolErrorBoundary,
)
from core.chat.chat_start import HISTORY_KEY
from core.tools import dataset_delete, dataset_download, notebook_run, web_search
from core.workspace.errors import ErrorCode, WorkspaceError
from tests.agent.conftest import history_of, run_one_turn
from tests.scripted_chat_model import ScriptedChatModel, text_chunk, tool_call_chunk
from tests.tool_calls import (
    FakeDatasets,
    always_yes,
    call_tool,
    failure_text,
    fake_services,
)

SECRET = "secret /etc/path"


class MissingDatasets(FakeDatasets):
    def describe(self, project_id, dataset_id):
        raise WorkspaceError(ErrorCode.NOT_FOUND, "Dataset not found.")


class LongFailureNotebook:
    async def run_for_model(self, project_id: str, code: str) -> str:
        raise WorkspaceError(ErrorCode.KERNEL_UNAVAILABLE, "x" * (MAX_TOOL_ERROR_CHARS + 50))


# Expected problems
# Baseline: a tool whose service raises a workspace error.


def test_expected_error_reaches_model_with_prefix() -> None:
    # Given the service raises a workspace error · When the tool is called
    # Then the model gets "Tool failed: " and the error JSON, with error status
    # Demonstrates: expected problems read the same as before
    services = fake_services(datasets=MissingDatasets())
    message = asyncio.run(call_tool(dataset_delete.dataset_delete, {"dataset_id": "hf-org--name"}, services))
    assert message.content.startswith(TOOL_FAILURE_PREFIX)
    assert json.loads(failure_text(message)) == {"error": "NOT_FOUND", "message": "Dataset not found."}


def test_long_expected_error_is_cut() -> None:
    # Given the error message is over the length limit · When the tool is called
    # Then the text after the prefix is cut to the limit
    # Demonstrates: error text stays bounded
    services = fake_services(notebook=LongFailureNotebook(), approve_run=always_yes)
    message = asyncio.run(call_tool(notebook_run.notebook_run, {"code": "2+2"}, services))
    assert failure_text(message) == "x" * MAX_TOOL_ERROR_CHARS


# Nothing internal leaks
# Baseline: a tool whose service raises RuntimeError("secret /etc/path").


def test_unexpected_error_becomes_generic_text(scripted: ScriptedChatModel, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given the service raises an unexpected error with a secret path · When the turn runs
    # Then the model and the chat step see only the generic failure, and the secret appears nowhere
    # Demonstrates: internal details never reach the chat
    def broken_search(query: str) -> list[dict]:
        raise RuntimeError(SECRET)

    monkeypatch.setattr(web_search, "search", broken_search)
    scripted.scripts += [[tool_call_chunk("web_search", {"query": "iris"})], [text_chunk("Done")]]
    result = run_one_turn()
    assert history_of(result)[-2]["content"] == "Tool failed: Could not complete the tool call."
    assert result.progress.tool_steps[0].output == "Failed: Could not complete the tool call."
    assert SECRET not in repr(scripted.requests) + repr(result.progress.tool_steps) + repr(result.chat.get(HISTORY_KEY))


def test_validation_text_never_includes_input() -> None:
    # Given a bad argument value "secret-value" · When the tool is called
    # Then the message names the field but not the value
    # Demonstrates: rejected input is never echoed back
    arguments = {"ref": "hf:org/name", "update": "secret-value"}
    message = asyncio.run(call_tool(dataset_download.dataset_download, arguments, fake_services()))
    assert failure_text(message) == "Invalid tool arguments (update: Input should be a valid boolean)."
    assert "secret-value" not in message.content


def test_broken_json_marker_is_not_named(scripted: ScriptedChatModel) -> None:
    # Given the guard marked the call's streamed arguments as broken JSON · When the turn runs
    # Then the model gets exactly "Tool failed: Invalid tool arguments."
    # Demonstrates: the internal marker field is never shown
    scripted.scripts += [[tool_call_chunk("web_search", '{"query": "iris"', call_id="bad")], [text_chunk("Done")]]
    result = run_one_turn()
    tool_results = [message for message in history_of(result) if message["role"] == "tool"]
    assert [message["content"] for message in tool_results] == [INVALID_TOOL_ARGUMENTS]


class ControlSignal(GraphBubbleUp):
    """A LangGraph control-flow signal, not a tool failure."""


@pytest.mark.parametrize("signal", [ControlSignal, asyncio.CancelledError])
def test_control_signals_pass_through(signal: type[BaseException]) -> None:
    # Given a tool raises a LangGraph control signal or is cancelled · When the boundary handles it
    # Then the signal is raised again, not turned into text
    # Demonstrates: the boundary never swallows control flow
    request = ToolCallRequest(
        tool_call={"name": "web_search", "args": {"query": "iris"}, "id": "call-1"}, tool=None, state={}, runtime=None
    )

    async def handler(_request: ToolCallRequest) -> None:
        raise signal()

    with pytest.raises(signal):
        asyncio.run(ToolErrorBoundary().awrap_tool_call(request, handler))
