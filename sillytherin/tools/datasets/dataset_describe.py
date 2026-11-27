"""dataset_describe tool: save one line about what a dataset holds, when it says nothing itself.

Rows marked describe "write" in dataset_list are saved at once. Every file in
the project may be read without asking the user, whoever added it.
"""

import json

from langchain.tools import ToolRuntime
from langchain_core.tools import ToolException, tool
from pydantic import Field

from sillytherin.tools.arguments import ToolArguments, TrimmedText
from sillytherin.tools.errors import error_json
from sillytherin.tools.services import ToolServices
from sillytherin.workspace.errors import ErrorCode, WorkspaceError


def step_title(args: dict) -> str:
    return "Describe dataset"


class ChatLooks:
    """Undescribed datasets the assistant ran code on, per chat (memory only, forgotten when the chat ends)."""

    def __init__(self) -> None:
        self._looked: dict[str, set[tuple[str, str]]] = {}  # session_id -> {(project_id, dataset_id)}

    def remember(self, session_id: str, project_id: str, dataset_id: str) -> None:
        self._looked.setdefault(session_id, set()).add((project_id, dataset_id))

    def has_looked(self, session_id: str, project_id: str, dataset_id: str) -> bool:
        return (project_id, dataset_id) in self._looked.get(session_id, set())

    def forget(self, session_id: str) -> None:
        """The chat ended: what the assistant looked at in it no longer counts."""
        self._looked.pop(session_id, None)


looked = ChatLooks()  # undescribed datasets the assistant looked at (notebook_run), per chat; lets downloads go


class DatasetDescribeArguments(ToolArguments):
    dataset_id: TrimmedText = Field(description="Current project's dataset ID from dataset_list.")
    description: str = Field(default=None, description="One line, at most 300 characters. Omit to ask the user.")


@tool(
    "dataset_describe",
    args_schema=DatasetDescribeArguments,
    response_format="content_and_artifact",
)
async def dataset_describe(
    dataset_id: str, runtime: ToolRuntime[ToolServices], description: str | None = None
) -> tuple[str, str]:
    """Save a one-line description of a dataset in the user's project that says nothing about itself.

    dataset_list marks the dataset with describe. Look at its first rows with notebook_run before calling
    this with its dataset_id and description.
    """
    services = runtime.context
    try:
        view = services.datasets.view_for_model(services.project_id, dataset_id)
        if view["describe"] is None:
            raise WorkspaceError(ErrorCode.NOT_NEEDED, "This dataset already says what it holds, or isn't ready.")
        if description is None:
            return json.dumps({"allowed": True, "next": NEXT_STEP}), f"Ready to note what **{view['name']}** holds."
        saved = await services.datasets.save_description(services.project_id, view["dataset_id"], description)
    except WorkspaceError as e:
        raise ToolException(error_json(e)) from e
    text = saved["assistant_description"]
    return (
        json.dumps({"saved": view["dataset_id"], "description": text}),
        f"Noted what **{view['name']}** holds: {text}",
    )


NEXT_STEP = "Look at its first rows with notebook_run, then call dataset_describe again with a one-line description."
