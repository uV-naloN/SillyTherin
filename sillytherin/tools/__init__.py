"""Select the native tools offered to the model and their UI step titles."""

from langchain_core.tools import BaseTool
from pydantic import BaseModel

from sillytherin.agent.tool_errors import with_safe_error_text
from sillytherin.tools import notebook_read, notebook_run, read_page, web_search
from sillytherin.tools.datasets import delete as dataset_delete
from sillytherin.tools.datasets import describe as dataset_describe
from sillytherin.tools.datasets import download as dataset_download
from sillytherin.tools.datasets import list as dataset_list
from sillytherin.tools.datasets import search as dataset_search

TOOLS: tuple[BaseTool, ...] = tuple(
    with_safe_error_text(native_tool)
    for native_tool in (web_search.web_search, read_page.read_page, dataset_search.dataset_search)
)
NOTEBOOK_TOOLS: tuple[BaseTool, ...] = tuple(
    with_safe_error_text(native_tool)
    for native_tool in (
        notebook_read.notebook_read,
        notebook_run.notebook_run,
        notebook_run.notebook_edit,
        dataset_download.dataset_download,
        dataset_list.dataset_list,
        dataset_delete.dataset_delete,
        dataset_describe.dataset_describe,
    )
)
_STEP_TITLE_FUNCTIONS = (
    web_search.step_title, read_page.step_title, dataset_search.step_title,
    notebook_read.step_title, notebook_run.step_title, notebook_run.edit_step_title,
    dataset_download.step_title, dataset_list.step_title, dataset_delete.step_title, dataset_describe.step_title,
)
_STEP_TITLES = {
    native_tool.name: (native_tool, step_title)
    for native_tool, step_title in zip(TOOLS + NOTEBOOK_TOOLS, _STEP_TITLE_FUNCTIONS, strict=True)
}


def chosen_tools(settings: dict) -> list[BaseTool]:
    web_tools = TOOLS if settings.get("web_search") else ()
    notebook_tools = NOTEBOOK_TOOLS if settings.get("notebook") else ()
    return [*web_tools, *notebook_tools]


def native_tool_step_title(tool_name: str, arguments: dict) -> str:
    entry = _STEP_TITLES.get(tool_name)
    if entry is None:
        return "Unavailable tool"
    native_tool, step_title = entry
    model_arguments = {name: value for name, value in arguments.items() if name != "runtime"}
    schema = native_tool.tool_call_schema  # what the model may send; the runtime is filled in by the agent
    try:
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            if set(model_arguments) - set(schema.model_fields):
                raise ValueError("unknown tool arguments")  # rejected by the tool, so not shown in the title
            schema.model_validate(model_arguments)
        return step_title(model_arguments)
    except ValueError:
        return step_title({})
