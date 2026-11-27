"""What the Kaggle code shares with the chat, the socket and the dataset service."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from core.contracts.notebook import PROJECT_ID_MAX, PROJECT_ID_RE

AccountState = Literal["disconnected", "connecting", "connected"]


@dataclass(frozen=True)
class KaggleAccountStatus:
    """What Settings shows; never holds a token."""

    state: AccountState
    username: str | None = None
    notice: str | None = None


class KaggleImportError(StrEnum):
    STALE_PROJECT = "STALE_PROJECT"
    STALE_APPROVAL = "STALE_APPROVAL"


class KaggleDownloadPayload(BaseModel):
    """A picker selection; the socket, not this DTO, chooses the destination."""

    model_config = ConfigDict(extra="ignore", strict=True, frozen=True)

    project_id: Annotated[str, Field(min_length=1, max_length=PROJECT_ID_MAX, pattern=PROJECT_ID_RE)]
    ref: Annotated[str, Field(max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9_-]*$")]


@dataclass(frozen=True)
class KaggleImportSelection:
    project_id: str
    project_generation: int
    account_generation: int
    ref: str


@dataclass(frozen=True)
class KaggleApprovalOffer:
    selection: KaggleImportSelection
    dataset_name: str
    revision: str
    download_bytes: int
