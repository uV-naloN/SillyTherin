"""What every tool's arguments have in common."""

from typing import Annotated, Any

from langchain.tools import ToolRuntime
from pydantic import AfterValidator, BaseModel, ConfigDict


class ToolArguments(BaseModel):
    """Strict arguments: an unknown field or a wrong type is rejected before the tool runs.

    `runtime` is filled by the agent, never by the model; LangChain leaves it out of the model's schema.
    """

    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    runtime: ToolRuntime[Any]


def reject_blank(text: str) -> str:
    if not text.strip():
        raise ValueError("must not be blank")
    return text


def reject_blank_and_trim(text: str) -> str:
    return reject_blank(text).strip()


NonBlankText = Annotated[str, AfterValidator(reject_blank)]
TrimmedText = Annotated[str, AfterValidator(reject_blank_and_trim)]
