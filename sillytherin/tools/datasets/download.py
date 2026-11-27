"""dataset_download tool: start a background download into the project.

Quick checks happen here; the transfer runs in the background and the tool
returns at once. Downloads above ask_above_mb show Download / Skip, and a
dataset whose copy in the project failed or is missing shows "Download again?"
first. Unknown arguments are rejected, so {"approved": true} can't skip a prompt.
"""

import json
from typing import TYPE_CHECKING, Annotated

from langchain.tools import ToolRuntime
from langchain_core.tools import ToolException, tool
from pydantic import AfterValidator, Field

from sillytherin.contracts.datasets import DatasetPlan
from sillytherin.tools.shared.approval import (
    DOWNLOAD_AGAIN_CHOICE,
    DOWNLOAD_AGAIN_OR_SKIP,
    DOWNLOAD_CHOICE,
    DOWNLOAD_OR_SKIP,
    ask_choice,
)
from sillytherin.tools.shared.arguments import ToolArguments, TrimmedText
from sillytherin.tools.shared.errors import error_json
from sillytherin.tools.shared.services import (
    ProjectDatasetLookup,
    ToolServices,
    UndescribedDatasets,
)
from sillytherin.tools.shared.titles import shorten
from sillytherin.workspace.datasets import links
from sillytherin.workspace.datasets.describe import look_first_message
from sillytherin.workspace.datasets.service import REF_PREFIXES, human_size
from sillytherin.workspace.errors import ErrorCode, WorkspaceError

if TYPE_CHECKING:
    from sillytherin.tools.datasets.describe import ChatLooks

MAX_PATTERNS = 20
PATTERN_MAX_CHARS = 200
SOURCE_NAMES = {"huggingface": "Hugging Face", "zenodo": "Zenodo"}


def step_title(args: dict) -> str:
    ref = args.get("ref")
    return f"Download dataset: {shorten(ref)}" if isinstance(ref, str) and ref.strip() else "Download dataset"


def is_safe_file_pattern(pattern: str) -> bool:
    """A name or glob inside the dataset's own folder: no absolute path, parent step, backslash or NUL."""
    return (
        1 <= len(pattern) <= PATTERN_MAX_CHARS
        and bool(pattern.strip())
        and not pattern.startswith("/")
        and "\\" not in pattern
        and "\x00" not in pattern
        and ".." not in pattern.split("/")
    )


def reject_unsafe_file_patterns(patterns: list[str]) -> list[str]:
    if not patterns:
        raise ValueError('must list file names or patterns like "*.csv"')
    if len(patterns) > MAX_PATTERNS:
        raise ValueError(f"can have at most {MAX_PATTERNS} names or patterns")
    if not all(is_safe_file_pattern(pattern) for pattern in patterns):
        raise ValueError("has a bad file name or pattern")
    return patterns


FilePatterns = Annotated[list[str], AfterValidator(reject_unsafe_file_patterns)]


async def ask_approval(plan: DatasetPlan, download_bytes: int) -> bool:
    """Name, file count, size and licence with Download / Skip; False on Skip or timeout."""
    count = len(plan.files)
    question = (
        f"The assistant wants to download **{plan.name}** from {SOURCE_NAMES.get(plan.source, plan.source)}: "
        f"{count:,} file{'s' if count != 1 else ''}, **{human_size(download_bytes)}**.\n\n"
        f"Licence: {plan.license or 'not stated'} · {plan.url}"
    )
    return await ask_choice(question, DOWNLOAD_OR_SKIP, DOWNLOAD_CHOICE)


BROKEN_STATUSES = ("failed", "missing")  # a copy the project can't use; downloading it again needs the user's yes


async def ask_retry(name: str, reason: str | None) -> bool:
    """Download again / Skip for a copy that can't be used; False on Skip or timeout."""
    why = f" ({reason})" if reason else ""
    question = f"This project's copy of **{name}** can't be used{why}. Download it again?"
    return await ask_choice(question, DOWNLOAD_AGAIN_OR_SKIP, DOWNLOAD_AGAIN_CHOICE)


def undescribed_not_looked_at(
    datasets: UndescribedDatasets, project_id: str, chat_id: str, ref: str, looked: "ChatLooks"
) -> list[dict]:
    """Ready rows with describe "write" the assistant hasn't looked at in this chat, so no download yet; none for a pasted link."""
    try:
        if links.parse_dataset_ref(ref)[0] == links.LINK_SOURCE:
            return []  # the user pasted it: their explicit wish
        views = datasets.needs_description(project_id)
    except WorkspaceError:
        return []  # the request reports a bad ref or project
    return [view for view in views if not looked.has_looked(chat_id, project_id, view["dataset_id"])]


def broken_copy(datasets: ProjectDatasetLookup, project_id: str, ref: str) -> dict | None:
    """The project's own copy of this catalog entry when it is failed or missing; None otherwise (links too)."""
    try:
        source, source_id = links.parse_dataset_ref(ref)
    except WorkspaceError:
        return None  # the request reports the bad ref
    prefix = REF_PREFIXES.get(source)
    project_copy = datasets.project_refs(project_id).get(f"{prefix}:{source_id}") if prefix else None
    return project_copy if project_copy and project_copy["status"] in BROKEN_STATUSES else None


def download_size_text(download_bytes: int) -> str:
    """The size to download, or "size unknown" when a linked website sent none (a download is never 0 B)."""
    return human_size(download_bytes) if download_bytes else "size unknown"


def look_first_error(not_looked_at: list[dict]) -> WorkspaceError:
    """Undescribed data must be looked at before a new download."""
    return WorkspaceError(
        ErrorCode.LOOK_FIRST,
        look_first_message([view["name"] for view in not_looked_at]),
        {"datasets": [view["dataset_id"] for view in not_looked_at]},
    )


def started_line(started: dict) -> str:
    """The step text for a download that was accepted: ready, queued or downloading."""
    folder = f"`{started['path']}/`"
    if started["from_pool"] and started["already_downloaded"]:
        return f"Ready from the shared pool, nothing downloaded: {folder}"
    if started["already_downloaded"]:
        return f"Already downloaded: {folder}"
    size = download_size_text(started["download_bytes"])
    if started["status"] == "queued":
        return f"Queued ({size}), {started['position_in_queue']} ahead → {folder}"
    return f"Downloading in the background ({size}) → {folder}"


class DatasetDownloadArguments(ToolArguments):
    ref: TrimmedText = Field(
        description="A dataset_search reference, such as hf:org/name, or an HTTPS URL pasted by the user."
    )
    files: FilePatterns = Field(default=None, description="At most 20 file names or patterns. Omit to use all files.")
    update: bool = Field(default=False, description="Fetch the newest version only when the user requests it.")


@tool(
    "dataset_download",
    args_schema=DatasetDownloadArguments,
    response_format="content_and_artifact",
)
async def dataset_download(
    ref: str, runtime: ToolRuntime[ToolServices], files: list[str] | None = None, update: bool = False
) -> tuple[str, str]:
    """Download a dataset found with dataset_search into the user's project in the background.

    Pass its `ref` or an HTTPS link to a data file that the user pasted into the chat. Never pass a link from
    anywhere else: show it to the user and ask them to paste it if they want it. A link has no licence information;
    don't state one. Returns at once with the dataset ID and the /work/datasets/<id>/ paths. You'll be told when
    it's ready; don't read the files before then. Use `files` (names or globs like "*.csv") for big datasets.
    Set `update` only when the user asks for the newest version. Data other projects already have is ready at once.
    Dataset files are read-only: copy a file elsewhere in /work before changing it. Check dataset_list first:
    when the project already has matching data, use it; download a new copy only when the user asks for one
    or for a different version.
    """
    services = runtime.context
    datasets, project_id, chat_id = services.datasets, services.project_id, services.chat_id
    not_looked_at = undescribed_not_looked_at(datasets, project_id, chat_id, ref, services.looked)
    if not_looked_at:
        raise ToolException(error_json(look_first_error(not_looked_at)))
    broken = broken_copy(datasets, project_id, ref)
    if broken and not await services.confirm_retry(broken["name"], broken.get("error")):
        declined = WorkspaceError(
            ErrorCode.DECLINED,
            "The user chose not to download it again. Don't download it or any other copy now; tell the user "
            "the project's copy can't be used and stop.",
        )
        return error_json(declined), f"Skipped: **{broken['name']}** was not downloaded again."
    try:
        started = await datasets.request(
            project_id, chat_id, ref, files, update, approve=services.approve_download,
            pasted_links=services.pasted_links,
        )
    except WorkspaceError as error:
        if error.code == ErrorCode.DECLINED:
            return error_json(error), "Skipped: the user chose not to download it."
        raise ToolException(error_json(error)) from error
    return json.dumps(started), started_line(started)
