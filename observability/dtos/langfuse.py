"""Settings and loss reports for the Langfuse exporter."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from pydantic import Field, SecretStr

HttpStatus = Annotated[int, Field(ge=100, le=599)]


@dataclass(frozen=True)
class LangfuseSettings:
    endpoint: str
    public_key: SecretStr
    secret_key: SecretStr


class LossReason(StrEnum):
    EXPORT_TIMEOUT = "export_timeout"
    CONNECTION_FAILED = "connection_failed"
    HTTP_REJECTED = "http_rejected"
    DELIVERY_UNCONFIRMED = "delivery_unconfirmed"
    WORKER_FAILED = "worker_failed"
    INVALID_TRACE = "invalid_trace"
    QUEUE_FULL = "queue_full"
    STAGE_LIMIT = "stage_limit"
    OBSERVER_FAILED = "observer_failed"
    STARTUP_DISABLED = "startup_disabled"
    SHUTDOWN_DISCARDED = "shutdown_discarded"


class WorkerStartupFailure(StrEnum):
    DEPENDENCY_MISSING = "dependency_missing"
    INITIALIZATION_FAILED = "initialization_failed"


@dataclass(frozen=True)
class ExportLoss:
    reason: LossReason
    affected_traces: Annotated[int, Field(ge=0)]
    http_status: HttpStatus | None = None
