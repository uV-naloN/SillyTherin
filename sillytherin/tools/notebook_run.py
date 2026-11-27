"""notebook_run tool: run Python in the project's notebook.

Safe code runs at once. Risky code is shown in the chat with the
reasons and Run / Skip buttons, and runs only on Run. Any file in the project
may be read without asking the user, whoever added it.
"""

from langchain.tools import ToolRuntime
from langchain_core.tools import ToolException, tool
from pydantic import Field

from sillytherin.config import MODEL_AUTO_RUN, RISKY_CODE_NEEDS_APPROVAL
from sillytherin.tools.approval import RUN_CHOICE, RUN_OR_SKIP, ask_choice
from sillytherin.tools.arguments import NonBlankText, ToolArguments
from sillytherin.tools.services import ToolServices
from sillytherin.workspace import risk
from sillytherin.workspace.errors import CellCompilationError, WorkspaceError
from sillytherin.workspace.service import NotebookRunOutcome

DECLINED = "User declined to run this code."


def undescribed_named_in(code: str, views: list[dict]) -> list[dict]:
    """Rows with describe "write" whose /work path or name appears in the code (any case)."""
    lowered = code.lower()
    return [
        view for view in views
        if view.get("describe") == "write" and (view["path"].lower() in lowered or view["name"].lower() in lowered)
    ]


def _remember_looks(code: str, services: ToolServices) -> None:
    """The assistant looked at these undescribed datasets in this chat: downloads may go ahead."""
    try:
        views = services.datasets.needs_description(services.project_id)
    except WorkspaceError:
        return
    for view in undescribed_named_in(code, views):
        services.looked.remember(services.chat_id, services.project_id, view["dataset_id"])


def step_title(args: dict) -> str:
    return "Notebook: run code"


async def ask_approval(code: str, reasons: list[str]) -> bool:
    """Show code + reasons with Run / Skip; False on Skip or timeout."""
    why = "\n".join(f"- {r}" for r in reasons) or "- automatic running is turned off"
    question = f"The assistant wants to run this code:\n\n```python\n{code}\n```\n\n**Asking because it:**\n{why}"
    return await ask_choice(question, RUN_OR_SKIP, RUN_CHOICE)


def compilation_failure_result(error: CellCompilationError) -> tuple[str, str]:
    diagnostic = error.message.splitlines()[0][:200]
    line = f" (line {error.line_number})" if error.line_number is not None else ""
    text = f"Code did not compile{line}: {diagnostic}"
    return text, text


class NotebookRunArguments(ToolArguments):
    code: NonBlankText = Field(description="Python code to run in the current project's notebook.")


class NotebookEditArguments(ToolArguments):
    cell_id: str = Field(description="The failed assistant-created cell to correct.")
    code: NonBlankText = Field(description="Replacement Python code for that same cell.")
    version: int = Field(description="The notebook version returned with the failed cell.")


def edit_step_title(args: dict) -> str:
    return "Notebook: correct cell"


@tool(
    "notebook_edit",
    args_schema=NotebookEditArguments,
    response_format="content_and_artifact",
)
async def notebook_edit(
    cell_id: str, code: str, version: int, runtime: ToolRuntime[ToolServices]
) -> tuple[str, str | NotebookRunOutcome]:
    """Correct one failed assistant-created cell in the active project.

    Read the notebook after a failure, then use that cell's id and current notebook version.
    Correct a repeatable calculation in place instead of adding a duplicate. Persistent or unknown effects
    require the user to review and run the cell in the notebook. Never rewrite earlier setup to restore state.
    """
    services = runtime.context
    risk_reasons = risk.check(code)
    needs_user_approval = (risk_reasons and RISKY_CODE_NEEDS_APPROVAL) or not MODEL_AUTO_RUN
    if needs_user_approval and not await services.approve_run(code, risk_reasons):
        return DECLINED, f"```python\n{code}\n```\n\nSkipped."
    try:
        result = await services.notebook.edit_failed_cell_for_model(services.project_id, cell_id, code, version)
    except CellCompilationError as error:
        return compilation_failure_result(error)
    except WorkspaceError as error:
        raise ToolException(error.message) from error
    if result is NotebookRunOutcome.RESTART_REQUIRED:
        return "Earlier notebook state needs to be restored.", result
    _remember_looks(code, services)
    return result, f"```python\n{code}\n```\n\n```\n{result}\n```"


@tool(
    "notebook_run",
    args_schema=NotebookRunArguments,
    response_format="content_and_artifact",
)
async def notebook_run(code: str, runtime: ToolRuntime[ToolServices]) -> tuple[str, str | NotebookRunOutcome]:
    """Run Python code in the user's project notebook.

    The Jupyter kernel has numpy, pandas and matplotlib; files live in the working folder. Variables persist
    between runs. Code is added as a new cell. Returns the printed output, the last value or the error.
    Before writing a data file, check dataset_list: when the project already has matching data, load it from
    its path; write a new copy only when the user asks for one.
    Add only one meaningful new step per cell and per tool round. Reuse live imports and variables from notebook_read,
    including user-run cells; avoid repeated setup and unrequested diagnostics. The first cell may set up needed data.
    After a runtime error, read current state and use notebook_edit on that failed cell instead of adding a duplicate.
    Retry only repeatable steps with no persistent or unknown effects; never replay a file write,
    database change or uncertain helper automatically.
    Failed cells remain in the notebook. If state is unavailable or a package is missing, report the blocker;
    do not install packages or replay earlier cells.
    """
    services = runtime.context
    risk_reasons = risk.check(code)
    needs_user_approval = (risk_reasons and RISKY_CODE_NEEDS_APPROVAL) or not MODEL_AUTO_RUN
    if needs_user_approval and not await services.approve_run(code, risk_reasons):
        return DECLINED, f"```python\n{code}\n```\n\nSkipped."
    try:
        result = await services.notebook.run_for_model(services.project_id, code)
    except CellCompilationError as error:
        return compilation_failure_result(error)
    except WorkspaceError as error:
        raise ToolException(error.message) from error
    if result is NotebookRunOutcome.RESTART_REQUIRED:
        return "Earlier notebook state needs to be restored.", result
    _remember_looks(code, services)
    return result, f"```python\n{code}\n```\n\n```\n{result}\n```"
