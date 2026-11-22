"""The notebook_run and notebook_read model tools.
"""

import asyncio
import base64
import struct

import nbformat
import pytest

from sillytherin.tools import notebook_read, notebook_run
from sillytherin.workspace import ErrorCode, WorkspaceError, store
from sillytherin.workspace.service import NotebookService
from tests.chat.fakes import FakeAsker, in_chat
from tests.tool_calls import call_tool, failure_text, fake_services


def run(coro):
    return asyncio.run(coro)


def png(width: int, height: int) -> str:
    """A base64 PNG header with the given size (enough for the size reader)."""
    raw = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
    return base64.b64encode(raw + b"\x00" * 32).decode()


def stream(text):
    return nbformat.v4.new_output("stream", name="stdout", text=text)


def image(w, h):
    return nbformat.v4.new_output("display_data", {"image/png": png(w, h), "text/plain": "<Figure>"})


@pytest.fixture
def project(tmp_path, fake_kernels):
    root = tmp_path / "workspaces"
    pid = store.create_project("P", root).id
    pushes = []

    async def notify(project_id, props):
        pushes.append(props)

    svc = NotebookService(fake_kernels, root=root, notify=notify)
    return svc, pid, root, pushes


class Approver:
    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    async def __call__(self, code, reasons):
        self.asked.append(reasons)
        return self.answer


# ---- Model tool `notebook_run` · unit (risk check real, approval and kernel faked)
# Baseline: Given the Notebook switch is on · When the model calls `notebook_run({"code": "2+2"})`


def test_safe_code_runs_without_asking(project, fake_kernels):
    # Demonstrates: safe code is automatic
    svc, pid, root, pushes = project
    fake_kernels.results["2+2"] = ("done", [nbformat.v4.new_output("execute_result", {"text/plain": "4"}, execution_count=1)])
    approve = Approver(True)
    result = run(call_tool(notebook_run.notebook_run, {"code": "2+2"}, fake_services(notebook=svc, project_id=pid, approve_run=approve)))
    assert approve.asked == []
    assert result.content == "[cell 1] out: 4"
    cells = store.load_notebook(pid, root).cells
    assert [c.source for c in cells] == ["2+2"] and cells[0].execution_count == 1
    assert pushes and pushes[-1]["cells"][0]["outputs"][0]["text"] == "4"


def test_long_output_is_trimmed_for_model(project, fake_kernels):
    # Demonstrates: model context stays small
    svc, pid, _, _ = project
    fake_kernels.results["big()"] = ("done", [stream("x" * 10_000)])
    result = run(call_tool(notebook_run.notebook_run, {"code": "big()"}, fake_services(notebook=svc, project_id=pid, approve_run=Approver(True))))
    assert len(result.content) <= 4100 and "cut off" in result.content


def test_image_output_is_described_not_sent(project, fake_kernels):
    svc, pid, _, _ = project
    fake_kernels.results["plot()"] = ("done", [image(640, 480)])
    result = run(call_tool(notebook_run.notebook_run, {"code": "plot()"}, fake_services(notebook=svc, project_id=pid, approve_run=Approver(True))))
    assert "[image 640x480]" in result.content and "iVBOR" not in result.content


def test_risky_code_runs_after_click(project, fake_kernels):
    # Demonstrates: you can allow it
    svc, pid, _, _ = project
    approve = Approver(True)
    run(call_tool(notebook_run.notebook_run, {"code": "!ls"}, fake_services(notebook=svc, project_id=pid, approve_run=approve)))
    assert approve.asked == [["runs a shell command"]]
    assert fake_kernels.executed == ["!ls"]


def test_risky_code_skip_runs_nothing(project, fake_kernels):
    # Demonstrates: you stay in control
    svc, pid, root, _ = project
    result = run(call_tool(notebook_run.notebook_run, {"code": "!ls"}, fake_services(notebook=svc, project_id=pid, approve_run=Approver(False))))
    assert result.content == notebook_run.DECLINED
    assert fake_kernels.executed == [] and store.load_notebook(pid, root).cells == []


def test_risky_code_timeout_runs_nothing(project, fake_kernels):
    # no answer = the prompt times out
    svc, pid, root, _ = project
    with in_chat(asker=FakeAsker(answer=None)):
        result = run(call_tool(notebook_run.notebook_run, {"code": "!ls"}, fake_services(notebook=svc, project_id=pid)))
    assert result.content == notebook_run.DECLINED
    assert fake_kernels.executed == [] and store.load_notebook(pid, root).cells == []


def test_empty_code_is_rejected_without_asking(project, fake_kernels):
    svc, pid, _, _ = project
    approve = Approver(True)
    result = run(call_tool(notebook_run.notebook_run, {"code": "  "}, fake_services(notebook=svc, project_id=pid, approve_run=approve)))
    assert "code: Value error, must not be blank" in failure_text(result)
    assert approve.asked == [] and fake_kernels.executed == []


def test_docker_down_runs_nothing(project, fake_kernels):
    # Demonstrates: never runs on the host
    svc, pid, _, _ = project

    async def unavailable(*args, **kwargs):
        raise WorkspaceError(ErrorCode.KERNEL_UNAVAILABLE, "Docker isn't running: start Docker Desktop, then press Restart.")

    fake_kernels.execute = unavailable
    result = run(call_tool(notebook_run.notebook_run, {"code": "2+2"}, fake_services(notebook=svc, project_id=pid, approve_run=Approver(True))))
    assert "Docker isn't running" in failure_text(result)


# ---- Model tool `notebook_read` · unit (kernel faked)
# Baseline: Given a notebook with a note, a code cell with output and a code cell never run · When the model calls `notebook_read`


@pytest.fixture
def read_project(project):
    svc, pid, root, _ = project
    nb = store.load_notebook(pid, root)
    note = store.add_cell(nb, "markdown", None)
    note.source = "## Prices\nUse the 2025 data."
    ran = store.add_cell(nb, "code", note.id)
    ran.source = "x = 5\nx * 2"
    ran.execution_count = 1
    ran.outputs = [nbformat.v4.new_output("execute_result", {"text/plain": "10"}, execution_count=1)]
    never = store.add_cell(nb, "code", ran.id)
    never.source = "y = x + 1"
    store.save_notebook(pid, nb, root)
    return svc, pid, root


def read(svc, pid):
    return run(call_tool(notebook_read.notebook_read, {}, fake_services(notebook=svc, project_id=pid))).content


def test_read_returns_cells_in_order_with_outputs(read_project):
    # Demonstrates: the model sees your work
    svc, pid, _ = read_project
    text = read(svc, pid)
    assert text.index("[note]") < text.index("[cell, run 1]") < text.index("[cell, not run]")
    assert "Use the 2025 data." in text and "x * 2\n--- output ---\n10" in text and "y = x + 1" in text


def test_read_includes_variable_names(read_project, fake_kernels):
    svc, pid, _ = read_project
    fake_kernels.vars = ["df", "x"]
    assert read(svc, pid).endswith("variables: df, x")


def test_read_does_not_run_cells_or_change_counts(read_project, fake_kernels):
    # Demonstrates: reading is side-effect free
    svc, pid, root = read_project
    before = store.load_notebook(pid, root)
    read(svc, pid)
    assert fake_kernels.executed == []
    assert store.load_notebook(pid, root) == before


def test_read_with_kernel_off_still_returns_cells(read_project, fake_kernels):
    svc, pid, _ = read_project
    fake_kernels.state = "off"
    text = read(svc, pid)
    assert "x * 2" in text and text.endswith("variables: kernel not running")


def test_read_trims_big_notebook(read_project):
    # Demonstrates: model context stays small
    svc, pid, root = read_project
    nb = store.load_notebook(pid, root)
    for i in range(50):
        cell = store.add_cell(nb, "code", None)
        cell.source = f"print({i})"
        cell.outputs = [stream("y" * 2000)]
    store.save_notebook(pid, nb, root)
    text = run(svc.read_for_model(pid, max_chars=12_000))
    assert len(text) <= 12_000
    assert "earlier cells not shown" in text and "print(49)" in text


def test_read_describes_images_not_sends(read_project):
    svc, pid, root = read_project
    nb = store.load_notebook(pid, root)
    cell = store.add_cell(nb, "code", None)
    cell.source = "plot()"
    cell.outputs = [image(800, 600)]
    store.save_notebook(pid, nb, root)
    text = read(svc, pid)
    assert "[image 800x600]" in text and "iVBOR" not in text


# TODO: Share a temporary notebook and a controlled kernel snapshot or preflight outcome.

