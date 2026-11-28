"""dataset_list tool: the project's datasets with status, progress and paths."""

import json

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from core.tools.shared.arguments import ToolArguments
from core.tools.shared.services import ToolServices
from core.workspace.datasets.describe import one_line
from core.workspace.datasets.service import human_size

REUSE_RULES = (
    "You may read any of these without asking the user. "
    "Before you search, download or write data, check whether one of these is what the user wants. "
    "Judge by what the user asks for and what each dataset is about (description, columns), not by the name alone. "
    "Follow these steps in order:\n"
    "1. A dataset marked describe \"write\" says nothing about itself, but may be what the user wants: look at its "
    "first rows with notebook_run (use its load_code) and save one line with dataset_describe before you search "
    "or download anything.\n"
    "2. If one matches and is ready, use it: give its path, and its load_code when the user wants to work with it, "
    "instead of making a copy. Don't search or download.\n"
    "3. If several match, name them and ask which one.\n"
    "4. If the match is missing or failed, say it can't be used and ask whether to download it again. Don't "
    "download it until the user says yes, and if they say no, don't fetch another copy instead.\n"
    "5. Make a new copy only when the user asks for one or for a different version."
)
NAME_MAX_CHARS = 120  # per name in a line; longer ones are cut with "…"
TEXT_MAX_CHARS = 300  # per description in a line
PATH_MAX_CHARS = 120  # per path in a chat line; the JSON keeps the exact path for code


def step_title(args: dict) -> str:
    return "List datasets"


def describe_contents(dataset_view: dict) -> list[str]:
    """What the line says the data holds: catalog text, columns, the assistant's note, or that nothing is known."""
    if dataset_view.get("description"):
        return [f'"{dataset_view["description"]}"']
    if dataset_view.get("columns"):
        return ["columns: " + ", ".join(dataset_view["columns"])]
    if dataset_view.get("assistant_description"):
        return [f'described by the assistant: "{dataset_view["assistant_description"]}"']
    if dataset_view.get("describe") == "write":
        return ["nothing known"]
    return []


def _created_line(dataset_view: dict) -> str:
    """`- Iris.csv · ready · 4.6 KB · created in this project · columns: … · `/work/Iris.csv``."""
    path = one_line(dataset_view["path"], PATH_MAX_CHARS)
    size = human_size(dataset_view["size_bytes"])
    parts = [
        f"- {dataset_view['name']}", dataset_view["status"], size, "created in this project",
        *describe_contents(dataset_view), f"`{path}`",
    ]
    return " · ".join(parts)


def _downloaded_line(dataset_view: dict) -> str:
    status = dataset_view["status"]
    size_bytes = dataset_view["size_bytes"]
    if status == "downloading" and size_bytes:
        status += f" {100 * (dataset_view['done_bytes'] or 0) // size_bytes}%"
    shared_with = dataset_view["shared_with"]
    shared = f" · also in {shared_with} other project(s)" if shared_with else ""
    folder = one_line(dataset_view["path"], PATH_MAX_CHARS)
    parts = [
        f"- {dataset_view['name']}", status, human_size(size_bytes), *describe_contents(dataset_view), f"`{folder}/`",
    ]
    return " · ".join(parts) + shared


LINE_BY_ORIGIN = {"downloaded": _downloaded_line, "created": _created_line}  # one chat line per dataset kind


def _safe_view(dataset_view: dict) -> dict:
    """The view with every free text on one capped line, for the chat line and the JSON alike."""
    safe = dict(dataset_view, name=one_line(dataset_view["name"], NAME_MAX_CHARS))
    for key in ("description", "assistant_description"):
        if safe.get(key):
            safe[key] = one_line(safe[key], TEXT_MAX_CHARS)
    if safe.get("columns"):
        safe["columns"] = [one_line(column, NAME_MAX_CHARS) for column in safe["columns"]]
    return safe


class DatasetListArguments(ToolArguments):
    pass


@tool(
    "dataset_list",
    args_schema=DatasetListArguments,
    response_format="content_and_artifact",
)
async def dataset_list(runtime: ToolRuntime[ToolServices]) -> tuple[str, str]:
    """List the datasets in the user's project.

    Includes ID, name, status (queued, downloading, ready, failed, missing), progress, size, licence,
    what each one holds (catalog description, column names or a description you saved),
    the /work/datasets/... file paths and load_code to use in notebook code.
    Data files the project's own code saved are listed too, as created in this project, with their
    /work/<file> path. Call this first whenever the user wants data, before dataset_search, dataset_download
    or writing a data file with notebook_run: the project may already have it.
    """
    services = runtime.context
    views = [_safe_view(view) for view in services.datasets.list_for_model(services.project_id)]
    if not views:
        return json.dumps({"datasets": []}), "No datasets in this project yet."
    shown = "\n".join(LINE_BY_ORIGIN[view["origin"]](view) for view in views)
    return json.dumps({"datasets": views, "note": REUSE_RULES}), shown
