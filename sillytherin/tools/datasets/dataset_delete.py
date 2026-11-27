"""dataset_delete tool: delete one dataset, only after the user agrees.

Every model delete shows Delete / Keep with the name and the space it frees.
"""

import json

from langchain.tools import ToolRuntime
from langchain_core.tools import ToolException, tool
from pydantic import Field

from sillytherin.tools.approval import DELETE_CHOICE, DELETE_OR_KEEP, ask_choice
from sillytherin.tools.arguments import ToolArguments, TrimmedText
from sillytherin.tools.errors import error_json
from sillytherin.tools.services import ToolServices
from sillytherin.workspace.datasets.service import human_size
from sillytherin.workspace.errors import ErrorCode, WorkspaceError


def step_title(args: dict) -> str:
    return "Delete dataset"


def approval_text(name: str, frees_bytes: int, shared_with: int) -> str:
    """The Delete / Keep question; a dataset other projects use frees nothing here."""
    text = f"The assistant wants to delete the dataset **{name}** from this project, freeing **{human_size(frees_bytes)}**."
    if shared_with:
        others = "1 other project" if shared_with == 1 else f"{shared_with} other projects"
        text += f" Also used by {others}: they keep it."
    return text


async def ask_approval(name: str, frees_bytes: int, shared_with: int) -> bool:
    """Delete / Keep in the chat; False on Keep or timeout."""
    question = approval_text(name, frees_bytes, shared_with)
    return await ask_choice(question, DELETE_OR_KEEP, DELETE_CHOICE)


class DatasetDeleteArguments(ToolArguments):
    dataset_id: TrimmedText = Field(description="Current project's dataset ID from dataset_list.")


@tool(
    "dataset_delete",
    args_schema=DatasetDeleteArguments,
    response_format="content_and_artifact",
)
async def dataset_delete(dataset_id: str, runtime: ToolRuntime[ToolServices]) -> tuple[str, str]:
    """Delete a dataset from the user's project to free disk space.

    The user is always asked first. Pass the dataset_id from dataset_list.
    """
    services = runtime.context
    try:
        view = services.datasets.describe(services.project_id, dataset_id)
        frees_bytes = 0 if view["shared_with"] else view["size_bytes"]
        if not await services.approve_delete(view["name"], frees_bytes, view["shared_with"]):
            declined = WorkspaceError(ErrorCode.DECLINED, "The user chose to keep this dataset.")
            return error_json(declined), f"Kept **{view['name']}**."
        result = await services.datasets.delete(services.project_id, view["dataset_id"])
    except WorkspaceError as e:
        raise ToolException(error_json(e)) from e
    shown = f"Deleted **{view['name']}**, freed {human_size(result['freed_bytes'])}."
    if result["still_used_by"]:
        shown += " Other projects still use it, so its files stay."
    return json.dumps(result), shown
