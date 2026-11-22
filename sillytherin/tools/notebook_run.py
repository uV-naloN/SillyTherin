"""notebook_run tool: run Python in the project's notebook.

Safe code runs at once. Risky code is shown in the chat with the
reasons and Run / Skip buttons, and runs only on Run. Any file in the project
may be read without asking the user, whoever added it.
"""

from typing import TYPE_CHECKING

from langchain.tools import ToolRuntime
from langchain_core.tools import ToolException, tool
from pydantic import Field

from sillytherin.chat.current import this_chat
from sillytherin.chat.ports import Choice
from sillytherin.config import MODEL_AUTO_RUN, RISKY_CODE_NEEDS_APPROVAL
from sillytherin.tools.arguments import NonBlankText, ToolArguments
from sillytherin.tools.services import ToolServices
from sillytherin.workspace import WorkspaceError, risk

if TYPE_CHECKING:
    from sillytherin.workspace.kernels import CellCompilationError

APPROVAL_TIMEOUT_SECONDS = 120
DECLINED = "User declined to run this code."


def undescribed_named_in(code: str, views: list[dict]) -> list[dict]:
    """Rows with describe "write" whose /work path or name appears in the code (any case)."""
    lowered = code.lower()
    return [v for v in views if v.get("describe") == "write" and (v["path"].lower() in lowered or v["name"].lower() in lowered)]


def _remember_looks(code: str, services: ToolServices) -> None:
    """The assistant looked at these undescribed datasets in this chat: downloads may go ahead."""
    try:
        views = services.datasets.needs_description(services.project_id)
    except WorkspaceError:
        return
    for view in undescribed_named_in(code, views):
        services.looked.remember(services.chat_id, services.project_id, view["dataset_id"])


RUN_CHOICE = Choice("run", "Run")
RUN_OR_SKIP = (RUN_CHOICE, Choice("skip", "Skip"))


def step_title(args: dict) -> str:
    return "Notebook: run code"


async def ask_approval(code: str, reasons: list[str]) -> bool:
    """Show code + reasons with Run / Skip; False on Skip or timeout."""
    why = "\n".join(f"- {r}" for r in reasons) or "- automatic running is turned off"
    question = f"The assistant wants to run this code:\n\n```python\n{code}\n```\n\n**Asking because it:**\n{why}"
    answer = await this_chat().asker.ask_choice(question, RUN_OR_SKIP, APPROVAL_TIMEOUT_SECONDS)
    return answer == RUN_CHOICE.id


def compilation_failure_result(error: "CellCompilationError") -> tuple[str, str]:
    # TODO: Return a bounded compile diagnostic without a cell ID or raw traceback.
    raise NotImplementedError("Notebook compile error presentation is not wired.")


class NotebookRunArguments(ToolArguments):
    code: NonBlankText = Field(description="Python code to run in the current project's notebook.")


@tool(
    "notebook_run",
    args_schema=NotebookRunArguments,
    response_format="content_and_artifact",
)
async def notebook_run(code: str, runtime: ToolRuntime[ToolServices]) -> tuple[str, str]:
    """Run Python code in the user's project notebook.

    The Jupyter kernel has numpy, pandas and matplotlib; files live in the working folder. Variables persist
    between runs. Code is added as a new cell. Returns the printed output, the last value or the error.
    Before writing a data file, check dataset_list: when the project already has matching data, load it from
    its path; write a new copy only when the user asks for one.
    """
    # TODO: Describe one step per cell and when it is safe to retry.
    services = runtime.context
    reasons = risk.check(code)
    needs_ok = (reasons and RISKY_CODE_NEEDS_APPROVAL) or not MODEL_AUTO_RUN
    if needs_ok and not await services.approve_run(code, reasons):
        return DECLINED, f"```python\n{code}\n```\n\nSkipped."
    try:
        result = await services.notebook.run_for_model(services.project_id, code)
    except WorkspaceError as e:
        raise ToolException(e.message) from e
    _remember_looks(code, services)
    return result, f"```python\n{code}\n```\n\n```\n{result}\n```"
