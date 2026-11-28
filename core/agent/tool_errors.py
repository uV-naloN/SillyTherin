"""Format tool failures that the model can act on."""

from langchain_core.tools import BaseTool, ToolException
from pydantic import ValidationError

from core.agent.middleware.model_tool_calls import INVALID_ARGUMENTS_FIELD

TOOL_FAILURE_PREFIX = "Tool failed: "
INVALID_TOOL_ARGUMENTS = f"{TOOL_FAILURE_PREFIX}Invalid tool arguments."
UNEXPECTED_TOOL_FAILURE = "Could not complete the tool call."
MAX_TOOL_ERROR_CHARS = 2000


def with_safe_error_text(native_tool: BaseTool) -> BaseTool:
    """The tool answers bad arguments and its own expected failures as error results the model can read."""
    # Set on the tool, not the boundary: LangChain formats these before the boundary sees them,
    # and its default validation text repeats the model's input.
    native_tool.handle_validation_error = explain_invalid_arguments
    native_tool.handle_tool_error = explain_expected_failure
    return native_tool


def explain_invalid_arguments(error: ValidationError) -> str:
    problems = error.errors(include_input=False, include_url=False)
    if any(INVALID_ARGUMENTS_FIELD in problem["loc"] for problem in problems):
        return INVALID_TOOL_ARGUMENTS  # the streamed arguments were not JSON; the marker field means nothing to the model
    reasons = "; ".join(f"{field_path(problem['loc'])}: {problem['msg']}" for problem in problems)
    return f"{TOOL_FAILURE_PREFIX}Invalid tool arguments ({reasons})."


def field_path(location: tuple[int | str, ...]) -> str:
    return ".".join(str(part) for part in location) or "arguments"


def explain_expected_failure(error: ToolException) -> str:
    return f"{TOOL_FAILURE_PREFIX}{str(error)[:MAX_TOOL_ERROR_CHARS]}"
