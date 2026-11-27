"""Call a tool the way the agent does, with fake services in its runtime context."""

import dataclasses

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from sillytherin.tools import (
    dataset_delete,
    dataset_describe,
    dataset_download,
    notebook_run,
)
from sillytherin.tools.shared.services import ToolServices

TEST_CALL_ID = "call-1"


async def call_tool(tool: BaseTool, arguments: dict, services: ToolServices | None) -> ToolMessage:
    """The tool's message for one call, after the same validation and error handling the agent applies."""
    runtime = ToolRuntime(
        state={}, context=services, config={}, stream_writer=ignore_stream, tool_call_id=TEST_CALL_ID, store=None
    )
    tool_call = {"type": "tool_call", "id": TEST_CALL_ID, "name": tool.name, "args": {**arguments, "runtime": runtime}}
    return await tool.ainvoke(tool_call)


def ignore_stream(*_chunks: object) -> None:
    return None


async def call_tool_output(tool: BaseTool, arguments: dict, services: ToolServices | None) -> tuple[str, str]:
    """(model text, step text) of a call that is expected to succeed."""
    message = await call_tool(tool, arguments, services)
    assert message.status == "success", message.content
    return message.content, message.artifact


def failure_text(message: ToolMessage) -> str:
    """What a failed call told the model, without the shared failure prefix."""
    from sillytherin.agent.tool_errors import TOOL_FAILURE_PREFIX

    assert message.status == "error", message.content
    return message.content.removeprefix(TOOL_FAILURE_PREFIX)


class FakeNotebook:
    async def read_for_model(self, project_id: str) -> str:
        return "cells"

    async def run_for_model(self, project_id: str, code: str) -> str:
        return "4"


class FakeDatasets:
    """Every dataset operation the tools need, returning controlled values."""

    def __init__(self) -> None:
        self.records: list[dict] = []

    async def request(self, project_id, session_id, ref, files=None, update=False, *, approve, pasted_links=frozenset()):
        raise AssertionError("no download expected")

    def list_for_model(self, project_id):
        return self.records

    def needs_description(self, project_id):
        return [record for record in self.records if record.get("describe") == "write"]

    def project_refs(self, project_id):
        return {record["ref"]: record for record in self.records if record.get("ref")}

    def describe(self, project_id, dataset_id):
        return {"dataset_id": dataset_id, "name": "Iris", "shared_with": 0, "size_bytes": 1000}

    async def delete(self, project_id, dataset_id):
        return {"deleted": dataset_id, "freed_bytes": 0, "still_used_by": 0}

    def view_for_model(self, project_id, dataset_id):
        return {"dataset_id": dataset_id, "name": "Iris", "describe": "write"}

    async def save_description(self, project_id, dataset_id, description):
        return {"assistant_description": description}


def fake_services(**overrides: object) -> ToolServices:
    """Fixed fake services for project "p1"; prompts and looks are the app's own unless a test overrides them."""
    services = ToolServices(
        notebook=FakeNotebook(),
        datasets=FakeDatasets(),
        project_id="p1",
        chat_id="chat-1",
        pasted_links=frozenset(),
        approve_run=notebook_run.ask_approval,
        approve_download=dataset_download.ask_approval,
        confirm_retry=dataset_download.ask_retry,
        approve_delete=dataset_delete.ask_approval,
        looked=dataset_describe.looked,
    )
    return dataclasses.replace(services, **overrides)


async def always_yes(*_details: object) -> bool:
    return True


async def always_no(*_details: object) -> bool:
    return False
