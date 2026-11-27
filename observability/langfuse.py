"""Configuration and lifecycle for optional Langfuse export."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from time import monotonic, monotonic_ns, time_ns
from typing import TYPE_CHECKING, Protocol

from pydantic import SecretStr, TypeAdapter

from core.errors import SillytherinError
from observability.dtos.langfuse import (
    ExportLoss,
    LangfuseSettings,
    LossReason,
    WorkerStartupFailure,
)
from observability.dtos.reply_trace import RecordingLoss, ReplyTrace
from observability.reply_trace import TraceClock

if TYPE_CHECKING:
    from core.server.app import ServerLifespan
    from core.server.web_chat import AnswerWaitScope
    from core.turn import TurnRunner

log = logging.getLogger("uvicorn.error").getChild("langfuse")
EXPORT_QUEUE_CAPACITY = 32
EXPORT_TIMEOUT_SECONDS = 15
WORKER_READINESS_TIMEOUT_SECONDS = 5
WORKER_START_RETRY_SECONDS = 10
LOG_SUMMARY_INTERVAL_SECONDS = 60
OTLP_TRACES_PATH = "/api/public/otel/v1/traces"
ENABLE_KEY = "SILLYTHERIN_LANGFUSE_ENABLED"
WORKER_ENVIRONMENT_KEYS = frozenset({
    "PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "LC_CTYPE",
    "PYTHONPATH", "PYTHONUTF8", "PYTHONDONTWRITEBYTECODE",
})
CLOUD_HOSTS = {
    "eu": "cloud.langfuse.com",
    "us": "us.cloud.langfuse.com",
    "jp": "jp.cloud.langfuse.com",
}


class WorkerStartupError(SillytherinError):
    def __init__(self, reason: WorkerStartupFailure) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True)
class ObservabilityLayer:
    run_reply: TurnRunner
    answer_wait: AnswerWaitScope
    lifespan: ServerLifespan


def resolve_cloud_endpoint(region: str) -> str:
    host = CLOUD_HOSTS.get(region)
    if host is None:
        raise ValueError("Unsupported Langfuse Cloud region")
    return f"https://{host}{OTLP_TRACES_PATH}"


def read_settings(environment: Mapping[str, str]) -> LangfuseSettings | None:
    if environment.get(ENABLE_KEY) != "true":
        return None
    return LangfuseSettings(
        resolve_cloud_endpoint(environment.get("SILLYTHERIN_LANGFUSE_REGION", "")),
        read_key(environment.get("LANGFUSE_PUBLIC_KEY", "")),
        read_key(environment.get("LANGFUSE_SECRET_KEY", "")),
    )


def read_key(value: str) -> SecretStr:
    if not value or not value.isascii() or not value.isprintable() or any(character.isspace() for character in value):
        raise ValueError("Invalid Langfuse project key")
    if ":" in value:
        raise ValueError("Invalid Langfuse project key")
    return SecretStr(value)


def configure_observability(environment: Mapping[str, str]) -> ObservabilityLayer | None:
    try:
        settings = read_settings(environment)
    except ValueError:
        log_loss(ExportLoss(LossReason.STARTUP_DISABLED, 0))
        return None
    if settings is None:
        return None

    from core.turn import run_turn
    from observability.agent_observer import observe_answer_wait
    from observability.turn_runner import ObservedTurnRunner

    supervisor = LangfuseSupervisor(settings)
    runner = ObservedTurnRunner(
        run_turn, supervisor, TraceClock(time_ns, monotonic_ns), on_loss=supervisor.report_recording_loss
    )
    return ObservabilityLayer(runner, observe_answer_wait, supervisor.lifespan)


class ExportWorker(Protocol):
    @property
    def has_exited(self) -> bool: ...

    async def wait_ready(self) -> None: ...

    async def send(self, trace: ReplyTrace) -> ExportLoss | None: ...

    async def stop(self) -> None: ...


def log_loss(loss: ExportLoss) -> None:
    if loss.reason is LossReason.HTTP_REJECTED and loss.http_status is not None:
        log.warning(
            "Langfuse trace export: reason=%s http_status=%d affected_traces=%d",
            loss.reason.value, loss.http_status, loss.affected_traces,
        )
    else:
        log.warning("Langfuse trace export: reason=%s affected_traces=%d", loss.reason.value, loss.affected_traces)


class LossReporter:
    def __init__(self, summary_seconds: float) -> None:
        self._summary_seconds = summary_seconds
        self._pending: dict[LossReason, ExportLoss] = {}
        self._timers: dict[LossReason, asyncio.Handle] = {}

    def offer(self, loss: ExportLoss) -> None:
        loop = asyncio.get_running_loop()
        reason = loss.reason
        if reason not in self._timers:
            self._timers[reason] = loop.call_later(self._summary_seconds, self.flush, reason)
            loop.call_soon(log_loss, loss)
            return
        previous = self._pending.get(reason)
        self._pending[reason] = loss if previous is None else merge_losses(previous, loss)

    def flush(self, reason: LossReason) -> None:
        self._timers.pop(reason).cancel()
        summary = self._pending.pop(reason, None)
        if summary is not None:
            log_loss(summary)

    def close(self) -> None:
        for reason in tuple(self._timers):
            self.flush(reason)


def merge_losses(earlier: ExportLoss, later: ExportLoss) -> ExportLoss:
    status = earlier.http_status if earlier.http_status == later.http_status else None
    return ExportLoss(earlier.reason, earlier.affected_traces + later.affected_traces, status)


class ProcessWorker:
    def __init__(self, process: asyncio.subprocess.Process, settings: LangfuseSettings) -> None:
        self._process = process
        self._settings = settings

    @property
    def has_exited(self) -> bool:
        return self._process.returncode is not None

    async def wait_ready(self) -> None:
        await self.write({
            "endpoint": self._settings.endpoint,
            "public_key": self._settings.public_key.get_secret_value(),
            "secret_key": self._settings.secret_key.get_secret_value(),
        })
        response = await self.read()
        if isinstance(response, dict) and "startup_error" in response:
            raise WorkerStartupError(WorkerStartupFailure(response["startup_error"]))
        if response != {"ready": True}:
            raise ValueError("Export worker failed to start")
        log.info("Langfuse worker ready pid=%d", self._process.pid)

    async def send(self, trace: ReplyTrace) -> ExportLoss | None:
        await self.write(trace_to_message(trace))
        response = await self.read()
        if response == {"ok": True}:
            return None
        return export_loss_message.validate_python(response)

    async def write(self, message: dict[str, object]) -> None:
        if self._process.stdin is None:
            raise BrokenPipeError
        self._process.stdin.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
        await self._process.stdin.drain()

    async def read(self) -> object:
        if self._process.stdout is None:
            raise BrokenPipeError
        line = await self._process.stdout.readline()
        if not line:
            log.warning("Langfuse worker exited pid=%d exit_code=%d", self._process.pid, await self._process.wait())
            raise BrokenPipeError
        return json.loads(line)

    async def stop(self) -> None:
        if self._process.stdin is not None:
            self._process.stdin.close()
        if self._process.returncode is None:
            try:
                self._process.kill()
            except ProcessLookupError:
                pass
        await self._process.wait()


export_loss_message = TypeAdapter(ExportLoss)


def trace_to_message(trace: ReplyTrace) -> dict[str, object]:
    return asdict(trace)


def worker_environment(environment: Mapping[str, str]) -> dict[str, str]:
    return {name: value for name, value in environment.items() if name.upper() in WORKER_ENVIRONMENT_KEYS}


async def start_process_worker(settings: LangfuseSettings) -> ExportWorker:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "observability.langfuse_worker",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, env=worker_environment(os.environ),
    )
    return ProcessWorker(process, settings)


class LangfuseSupervisor:
    def __init__(
        self, settings: LangfuseSettings,
        start_worker: Callable[[LangfuseSettings], Awaitable[ExportWorker]] = start_process_worker,
        *,
        export_timeout: float = EXPORT_TIMEOUT_SECONDS,
        readiness_timeout: float = WORKER_READINESS_TIMEOUT_SECONDS,
        summary_seconds: float = LOG_SUMMARY_INTERVAL_SECONDS,
    ) -> None:
        self._settings = settings
        self._start_worker = start_worker
        self._export_timeout = export_timeout
        self._readiness_timeout = readiness_timeout
        self._losses = LossReporter(summary_seconds)
        self._queue: asyncio.Queue[ReplyTrace] = asyncio.Queue(EXPORT_QUEUE_CAPACITY)
        self._task: asyncio.Task[None] | None = None
        self._worker: ExportWorker | None = None
        self._retry_start_at = 0.0

    def offer(self, trace: ReplyTrace) -> None:
        if monotonic() < self._retry_start_at:
            self.report_loss(ExportLoss(LossReason.STARTUP_DISABLED, 1))
            return
        try:
            self._queue.put_nowait(trace)
        except asyncio.QueueFull:
            self.report_loss(ExportLoss(LossReason.QUEUE_FULL, 1))

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        await self.start()
        try:
            yield
        finally:
            await self.stop()

    async def start(self) -> None:
        self._task = asyncio.create_task(self.run(), name="reply-trace-export")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        count = self.discard_queued()
        if count:
            self.report_loss(ExportLoss(LossReason.SHUTDOWN_DISCARDED, count))
        self._losses.close()

    def report_loss(self, loss: ExportLoss) -> None:
        self._losses.offer(loss)

    def report_recording_loss(self, loss: RecordingLoss) -> None:
        self.report_loss(ExportLoss(LossReason(loss.value), 1))

    async def run(self) -> None:
        try:
            await self.ensure_worker()
            while True:
                trace = await self._queue.get()
                try:
                    await self.export(trace)
                finally:
                    self._queue.task_done()
        finally:
            await self.stop_worker()

    async def ensure_worker(self) -> bool:
        if self._worker is not None:
            if not self._worker.has_exited:
                return True
            log.warning("Langfuse worker exited while idle; starting a replacement")
            await self.stop_worker()
        try:
            async with asyncio.timeout(self._readiness_timeout):
                self._worker = await self._start_worker(self._settings)
                await self._worker.wait_ready()
        except WorkerStartupError as error:
            failure = error.reason.value
        except TimeoutError:
            failure = "readiness_timeout"
        except Exception:  # noqa: BLE001 - subprocess failures can contain private diagnostics
            failure = "process_failed"
        else:
            return True
        self._retry_start_at = monotonic() + WORKER_START_RETRY_SECONDS
        log.warning("Langfuse worker startup failed reason=%s retry_after_seconds=%d",
                    failure, WORKER_START_RETRY_SECONDS)
        await self.stop_worker()
        self.report_loss(ExportLoss(LossReason.STARTUP_DISABLED, self.discard_queued()))
        return False

    async def export(self, trace: ReplyTrace) -> None:
        try:
            if not await self.ensure_worker() or self._worker is None:
                self.report_loss(ExportLoss(LossReason.STARTUP_DISABLED, 1))
                return
            async with asyncio.timeout(self._export_timeout):
                loss = await self._worker.send(trace)
        except asyncio.CancelledError:
            self.report_loss(ExportLoss(LossReason.SHUTDOWN_DISCARDED, 1))
            raise
        except TimeoutError:
            self.report_loss(ExportLoss(LossReason.EXPORT_TIMEOUT, 1))
            await self.stop_worker()
        except Exception:  # noqa: BLE001 - an unexpected process exit cannot affect chat
            self.report_loss(ExportLoss(LossReason.WORKER_FAILED, 1))
            await self.stop_worker()
        else:
            if loss is not None:
                self.report_loss(loss)

    async def stop_worker(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            try:
                await worker.stop()
            except Exception:  # noqa: BLE001 - do not expose diagnostics from process teardown
                self.report_loss(ExportLoss(LossReason.WORKER_FAILED, 0))

    def discard_queued(self) -> int:
        count = self._queue.qsize()
        for _ in range(count):
            self._queue.get_nowait()
            self._queue.task_done()
        return count
