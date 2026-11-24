"""Server integration for optional tracing, using a fake worker."""

import asyncio
import sys
from collections.abc import Iterator
from pathlib import Path
from time import monotonic_ns, time_ns
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from sillytherin.observability.agent_observer import observe_answer_wait
from sillytherin.observability.answer_capture import AnswerCapture
from sillytherin.observability.langfuse import (
    EXPORT_QUEUE_CAPACITY,
    ExportLoss,
    LangfuseSettings,
    LangfuseSupervisor,
    LossReason,
    configure_observability,
    read_settings,
)
from sillytherin.observability.reply_trace import (
    MAX_REPLY_CONTENT_BYTES,
    RecordingLoss,
    ReplyContent,
    ReplySpan,
    ReplyState,
    ReplyTrace,
    SpanKind,
    SpanOutcome,
    TraceClock,
)
from sillytherin.observability.turn_runner import ObservedTurnRunner
from sillytherin.server.app import ServerSettings, create_app
from sillytherin.server.events import ServerEvent
from sillytherin.server.socket import ChatSessions
from sillytherin.server.web_chat import SocketAnswerView
from sillytherin.turn import run_turn
from tests.conftest import FakeKernels
from tests.scripted_chat_model import ScriptedChatModel, text_chunk
from tests.server.conftest import OWN_PAGE, PORT, events_until, open_tab, send_message
from tests.server.test_prompt_ownership import answer, ask_to_run

TRACE_ENVIRONMENT = {
    "SILLYTHERIN_LANGFUSE_ENABLED": "true",
    "SILLYTHERIN_LANGFUSE_REGION": "eu",
    "LANGFUSE_PUBLIC_KEY": "public-test",
    "LANGFUSE_SECRET_KEY": "secret-test",
}
TEST_TRACE = ReplyTrace(ReplySpan(SpanKind.REPLY, "reply.turn", 1000, 1010, SpanOutcome.SUCCEEDED), ())


class FakeWorker:
    def __init__(self) -> None:
        self.attempts: asyncio.Queue[ReplyTrace] = asyncio.Queue()
        self.release = asyncio.Event()
        self.release.set()
        self.ready = asyncio.Event()
        self.ready.set()
        self.stopped = asyncio.Event()
        self.ready_error: Exception | None = None
        self.loss: ExportLoss | None = None
        self.starts = 0

    async def create(self, settings: LangfuseSettings) -> "FakeWorker":
        self.starts += 1
        return self

    async def wait_ready(self) -> None:
        await self.ready.wait()
        if self.ready_error is not None:
            raise self.ready_error

    async def send(self, trace: ReplyTrace) -> ExportLoss | None:
        self.attempts.put_nowait(trace)
        await self.release.wait()
        return self.loss

    async def stop(self) -> None:
        self.stopped.set()

    async def next_trace(self) -> ReplyTrace:
        return await asyncio.wait_for(self.attempts.get(), 2)

    async def wait_stopped(self) -> None:
        await asyncio.wait_for(self.stopped.wait(), 2)


def make_supervisor(
    worker: FakeWorker, *, export_timeout: float = 2, readiness_timeout: float = 2,
    summary_seconds: float = 60,
) -> LangfuseSupervisor:
    settings = read_settings(TRACE_ENVIRONMENT)
    assert settings is not None
    return LangfuseSupervisor(
        settings, worker.create, export_timeout=export_timeout, readiness_timeout=readiness_timeout,
        summary_seconds=summary_seconds,
    )


@pytest.fixture
def worker(request: pytest.FixtureRequest) -> FakeWorker:
    child = FakeWorker()
    child.ready_error = getattr(request, "param", None)
    return child


@pytest.fixture
def traced_client(
    worker: FakeWorker, services: SimpleNamespace, model: ScriptedChatModel, tmp_path: Path,
) -> Iterator[TestClient]:
    supervisor = make_supervisor(worker)
    runner = ObservedTurnRunner(run_turn, supervisor, TraceClock(time_ns, monotonic_ns))
    sessions = ChatSessions(run_reply=runner, answer_wait=observe_answer_wait)
    app = create_app(ServerSettings(PORT, tmp_path), sessions, additional_lifespan=supervisor.lifespan)
    with TestClient(app, base_url=OWN_PAGE) as client:
        yield client


def traced_reply(client: TestClient, model: ScriptedChatModel) -> None:
    model.scripts.append([text_chunk("Done")])
    with open_tab(client) as tab:
        tab.receive_json()
        send_message(tab)
        events_until(tab, "reply.ended")


def test_reply_completes_and_worker_exports_after_approval(
    traced_client: TestClient, model: ScriptedChatModel, worker: FakeWorker, fake_kernels: FakeKernels,
) -> None:
    with open_tab(traced_client) as tab:
        tab.receive_json()
        ask_id = ask_to_run(model, tab)
        assert not fake_kernels.executed
        answer(tab, ask_id)
        events_until(tab, "reply.ended")
    assert fake_kernels.executed == ["!ls"]
    assert traced_client.portal is not None
    exported = traced_client.portal.call(worker.next_trace)
    assert any(span.name == "tool.notebook_run" for span in exported.children)
    assert exported.content is not None
    assert exported.content.reply_state is ReplyState.COMPLETE


def test_stalled_export_does_not_hold_next_reply_or_approval(
    traced_client: TestClient, model: ScriptedChatModel, worker: FakeWorker, fake_kernels: FakeKernels,
) -> None:
    assert traced_client.portal is not None
    traced_client.portal.call(worker.release.clear)
    traced_reply(traced_client, model)
    traced_client.portal.call(worker.next_trace)
    with open_tab(traced_client) as tab:
        tab.receive_json()
        answer(tab, ask_to_run(model, tab))
        events = events_until(tab, "reply.ended")
    assert any(event["type"] == "reply.delta" for event in events)
    assert fake_kernels.executed == ["!ls"]
    assert not worker.release.is_set()


def test_rejected_export_does_not_change_reply_or_retry(
    traced_client: TestClient, model: ScriptedChatModel, worker: FakeWorker,
) -> None:
    worker.loss = ExportLoss(LossReason.HTTP_REJECTED, 1, 503)
    traced_reply(traced_client, model)
    assert traced_client.portal is not None
    assert traced_client.portal.call(worker.next_trace).root.outcome is SpanOutcome.SUCCEEDED
    assert worker.attempts.empty()


def test_full_export_queue_drops_newest_trace_without_waiting(caplog: pytest.LogCaptureFixture) -> None:
    async def exercise_overflow() -> None:
        worker = FakeWorker()
        worker.release.clear()
        supervisor = make_supervisor(worker)
        async with supervisor.lifespan():
            supervisor.offer(TEST_TRACE)
            await worker.next_trace()
            for _ in range(EXPORT_QUEUE_CAPACITY):
                supervisor.offer(TEST_TRACE)
            newest = ReplyTrace(ReplySpan(SpanKind.REPLY, "reply.turn", 2000, 2010, SpanOutcome.SUCCEEDED), ())
            supervisor.offer(newest)
            worker.release.set()
            for _ in range(EXPORT_QUEUE_CAPACITY):
                assert await worker.next_trace() == TEST_TRACE
            assert worker.attempts.empty()

    asyncio.run(exercise_overflow())
    assert "reason=queue_full affected_traces=1" in caplog.text


def test_skipped_approval_never_executes_tool_side_effect(
    traced_client: TestClient, model: ScriptedChatModel, fake_kernels: FakeKernels,
) -> None:
    with open_tab(traced_client) as tab:
        tab.receive_json()
        answer(tab, ask_to_run(model, tab), "skip")
        events_until(tab, "reply.ended")
    assert not fake_kernels.executed


def test_shutdown_does_not_flush_pending_traces(caplog: pytest.LogCaptureFixture) -> None:
    async def exercise() -> None:
        worker = FakeWorker()
        worker.release.clear()
        supervisor = make_supervisor(worker)
        async with supervisor.lifespan():
            supervisor.offer(TEST_TRACE)
            await worker.next_trace()
            supervisor.offer(TEST_TRACE)
        assert worker.stopped.is_set() and worker.attempts.empty()

    asyncio.run(exercise())
    losses = [record.message for record in caplog.records if "shutdown_discarded" in record.message]
    assert len(losses) == 2 and all("affected_traces=1" in loss for loss in losses)


@pytest.mark.parametrize("worker", [RuntimeError("private startup exception")], indirect=True)
def test_exporter_start_failure_leaves_chat_available(
    traced_client: TestClient, model: ScriptedChatModel, worker: FakeWorker, caplog: pytest.LogCaptureFixture,
) -> None:
    traced_reply(traced_client, model)
    assert worker.attempts.empty()
    assert "private startup exception" not in caplog.text


def test_worker_readiness_timeout_does_not_delay_chat(caplog: pytest.LogCaptureFixture) -> None:
    async def exercise() -> None:
        worker = FakeWorker()
        worker.ready.clear()
        supervisor = make_supervisor(worker, readiness_timeout=0.01)
        async with supervisor.lifespan():
            assert not worker.stopped.is_set()
            supervisor.offer(TEST_TRACE)
            await worker.wait_stopped()

    asyncio.run(exercise())
    assert "reason=startup_disabled affected_traces=1" in caplog.text


def test_selecting_another_cloud_region_only_changes_the_resolved_endpoint() -> None:
    eu = read_settings(TRACE_ENVIRONMENT)
    us = read_settings({**TRACE_ENVIRONMENT, "SILLYTHERIN_LANGFUSE_REGION": "us"})
    assert eu is not None and us is not None
    assert us.endpoint == eu.endpoint.replace("cloud.langfuse.com", "us.cloud.langfuse.com")
    assert (us.public_key, us.secret_key) == (eu.public_key, eu.secret_key)


def test_no_opt_in_sends_no_telemetry_even_with_keys() -> None:
    environment = {key: value for key, value in TRACE_ENVIRONMENT.items() if not key.endswith("_ENABLED")}
    assert configure_observability(environment) is None


def test_default_install_runs_without_sdk_or_trace_configuration(
    client: TestClient, model: ScriptedChatModel, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "langfuse", None)
    assert configure_observability({}) is None
    traced_reply(client, model)


def test_nonliteral_opt_in_does_not_export() -> None:
    assert configure_observability({**TRACE_ENVIRONMENT, "SILLYTHERIN_LANGFUSE_ENABLED": "yes"}) is None


def test_blank_trace_credential_disables_export(caplog: pytest.LogCaptureFixture) -> None:
    assert configure_observability({**TRACE_ENVIRONMENT, "LANGFUSE_SECRET_KEY": " "}) is None
    assert "reason=startup_disabled" in caplog.text
    assert "public-test" not in caplog.text


def test_unknown_region_cannot_route_trace_credentials(caplog: pytest.LogCaptureFixture) -> None:
    assert configure_observability({**TRACE_ENVIRONMENT, "SILLYTHERIN_LANGFUSE_REGION": "https://example.test"}) is None
    assert "example.test" not in caplog.text and "secret-test" not in caplog.text


def test_opt_in_without_sdk_leaves_chat_available(caplog: pytest.LogCaptureFixture) -> None:
    async def exercise() -> None:
        worker = FakeWorker()
        worker.ready_error = ModuleNotFoundError("langfuse")
        supervisor = make_supervisor(worker)
        async with supervisor.lifespan():
            await worker.wait_stopped()
            supervisor.offer(TEST_TRACE)
        assert worker.attempts.empty()

    asyncio.run(exercise())
    assert "reason=startup_disabled" in caplog.text


def test_successful_export_does_not_emit_loss_warning(
    traced_client: TestClient, model: ScriptedChatModel, worker: FakeWorker, caplog: pytest.LogCaptureFixture,
) -> None:
    traced_reply(traced_client, model)
    assert traced_client.portal is not None
    traced_client.portal.call(worker.next_trace)
    assert "Langfuse trace export:" not in caplog.text


def test_export_timeout_logs_safe_reason_and_trace_count(caplog: pytest.LogCaptureFixture) -> None:
    async def exercise() -> None:
        worker = FakeWorker()
        worker.release.clear()
        supervisor = make_supervisor(worker, export_timeout=0.01)
        async with supervisor.lifespan():
            supervisor.offer(TEST_TRACE)
            await worker.next_trace()
            await worker.wait_stopped()
            worker.release.set()
            supervisor.offer(TEST_TRACE)
            await worker.next_trace()
            assert worker.starts == 2

    asyncio.run(exercise())
    assert "reason=export_timeout affected_traces=1" in caplog.text


def test_http_rejection_logs_status_without_vendor_content(caplog: pytest.LogCaptureFixture) -> None:
    async def exercise() -> None:
        worker = FakeWorker()
        worker.loss = ExportLoss(LossReason.HTTP_REJECTED, 1, 503)
        supervisor = make_supervisor(worker)
        async with supervisor.lifespan():
            supervisor.offer(TEST_TRACE)
            await worker.next_trace()
            await asyncio.sleep(0)

    asyncio.run(exercise())
    assert [record.message for record in caplog.records] == [
        "Langfuse trace export: reason=http_rejected http_status=503 affected_traces=1",
    ]


def test_repeated_export_losses_are_counted_without_log_flooding(caplog: pytest.LogCaptureFixture) -> None:
    async def exercise() -> None:
        supervisor = make_supervisor(FakeWorker(), summary_seconds=0)
        async with supervisor.lifespan():
            for _ in range(4):
                supervisor.report_loss(ExportLoss(LossReason.CONNECTION_FAILED, 1))
            await asyncio.sleep(0)
            await asyncio.sleep(0)

    asyncio.run(exercise())
    assert [record.message for record in caplog.records] == [
        "Langfuse trace export: reason=connection_failed affected_traces=1",
        "Langfuse trace export: reason=connection_failed affected_traces=3",
    ]


# Exact delivery and isolation


def test_successive_turns_export_their_own_delivered_replies(
    traced_client: TestClient, model: ScriptedChatModel, worker: FakeWorker,
) -> None:
    with open_tab(traced_client) as tab:
        tab.receive_json()
        for message, reply in (("one", "first"), ("two", "second")):
            model.scripts.append([text_chunk(reply)])
            send_message(tab, message)
            events = events_until(tab, "reply.ended")
            trace = traced_client.portal.call(worker.next_trace)
            assert trace.content.message == message
            assert trace.content.reply == events[-1]["text"] == reply
            assert trace.content.reply_state is ReplyState.COMPLETE


def test_parallel_chats_keep_content_separate() -> None:
    async def deliver() -> None:
        first = AnswerCapture(ReceiptAnswer(), lambda loss: pytest.fail(str(loss)))
        second = AnswerCapture(ReceiptAnswer(), lambda loss: pytest.fail(str(loss)))
        await asyncio.gather(first.stream("one"), second.stream("two"))
        assert first.seal("a", SpanOutcome.CANCELLED).reply == "one"
        assert second.seal("b", SpanOutcome.CANCELLED).reply == "two"
    asyncio.run(deliver())


def test_terminal_handoff_completes_reply_after_dropped_start() -> None:
    content = capture_socket(SpanOutcome.SUCCEEDED)
    assert content.reply == "terminal" and content.reply_state is ReplyState.COMPLETE


def test_dropped_final_event_cannot_create_complete_output() -> None:
    content, _, _ = capture_reply(receipts=(True, False))
    assert content.reply == "prefix" and content.reply_state is ReplyState.PARTIAL


# Incomplete delivery


def test_failed_turn_exports_only_pre_failure_answer_text() -> None:
    content, _, _ = capture_reply(outcome=SpanOutcome.FAILED, final="Error: private")
    assert content.reply == "prefix" and content.reply_state is ReplyState.PARTIAL


def test_stopped_turn_exports_only_pre_stop_answer_text() -> None:
    content, _, _ = capture_reply(outcome=SpanOutcome.CANCELLED)
    assert content.reply == "prefix" and content.reply_state is ReplyState.PARTIAL


def test_stop_before_any_output_omits_reply() -> None:
    content, _, _ = capture_reply(chunks=(), outcome=SpanOutcome.CANCELLED)
    assert content.reply is None and content.reply_state is ReplyState.MISSING


def test_failed_turn_omits_deltas_after_dropped_start() -> None:
    content = capture_socket(SpanOutcome.FAILED)
    assert content.reply is None and content.reply_state is ReplyState.MISSING


# Activation and limits


def test_existing_switch_exports_content_at_utf8_limit() -> None:
    text = "é" * (MAX_REPLY_CONTENT_BYTES // 2)
    content, losses, _ = capture_reply(message=text, final=text)
    assert content.message == content.reply == text and not losses


def test_small_final_replacement_can_follow_oversized_stream() -> None:
    content, losses, _ = capture_reply(chunks=("x" * (MAX_REPLY_CONTENT_BYTES + 1),))
    assert content.reply == "final" and not losses


def test_disabled_tracing_sends_no_real_turn_data() -> None:
    assert configure_observability({}) is None


def test_oversized_message_drops_trace_without_changing_chat() -> None:
    content, losses, answer = capture_reply(message="x" * (MAX_REPLY_CONTENT_BYTES + 1))
    assert content is None and losses == [RecordingLoss.INVALID_TRACE]
    assert answer.delivered == ["prefix", "final"]


def test_oversized_selected_reply_drops_trace_without_changing_chat() -> None:
    text = "é" * (MAX_REPLY_CONTENT_BYTES // 2) + "x"
    content, losses, answer = capture_reply(final=text)
    assert content is None and losses == [RecordingLoss.INVALID_TRACE]
    assert answer.delivered[-1] == text


# Fail-open export and capture


def test_content_snapshot_exports_without_altering_approval(
    traced_client: TestClient, model: ScriptedChatModel, worker: FakeWorker, fake_kernels: FakeKernels,
) -> None:
    with open_tab(traced_client) as tab:
        tab.receive_json()
        ask_id = ask_to_run(model, tab)
        assert not fake_kernels.executed
        answer(tab, ask_id)
        events_until(tab, "reply.ended")
    assert fake_kernels.executed == ["!ls"]
    assert traced_client.portal is not None
    exported = traced_client.portal.call(worker.next_trace)
    assert any(span.name == "tool.notebook_run" for span in exported.children)
    assert exported.content is not None
    assert exported.content.reply_state is ReplyState.COMPLETE


def test_rejected_content_export_preserves_approval_and_reply(
    traced_client: TestClient, model: ScriptedChatModel, worker: FakeWorker,
) -> None:
    worker.loss = ExportLoss(LossReason.HTTP_REJECTED, 1, 503)
    traced_reply(traced_client, model)
    assert traced_client.portal is not None
    assert traced_client.portal.call(worker.next_trace).root.outcome is SpanOutcome.SUCCEEDED
    assert worker.attempts.empty()


def test_stalled_content_export_preserves_next_approval_and_reply(
    traced_client: TestClient, model: ScriptedChatModel, worker: FakeWorker, fake_kernels: FakeKernels,
) -> None:
    assert traced_client.portal is not None
    traced_client.portal.call(worker.release.clear)
    traced_reply(traced_client, model)
    traced_client.portal.call(worker.next_trace)
    with open_tab(traced_client) as tab:
        tab.receive_json()
        answer(tab, ask_to_run(model, tab))
        events = events_until(tab, "reply.ended")
    assert any(event["type"] == "reply.delta" for event in events)
    assert fake_kernels.executed == ["!ls"]
    assert not worker.release.is_set()


def capture_socket(outcome: SpanOutcome) -> ReplyContent:
    sender = ReceiptSender()
    capture = AnswerCapture(SocketAnswerView(sender, "message"), lambda loss: pytest.fail(str(loss)))
    async def deliver() -> None:
        await capture.stream("ignored")
        await capture.finish("terminal")
    asyncio.run(deliver())
    return capture.seal("input", outcome)


class ReceiptAnswer:
    def __init__(self, receipts: tuple[bool, ...] = ()) -> None:
        self.receipts = iter(receipts)
        self.delivered: list[str] = []

    async def stream(self, text: str) -> bool:
        accepted = next(self.receipts, True)
        if accepted:
            self.delivered.append(text)
        return accepted

    async def finish(self, text: str) -> bool:
        return await self.stream(text)


def capture_reply(
    chunks: tuple[str, ...] = ("prefix",), final: str = "final",
    outcome: SpanOutcome = SpanOutcome.SUCCEEDED, message: str = "question",
    receipts: tuple[bool, ...] = (),
) -> tuple[ReplyContent | None, list[RecordingLoss], ReceiptAnswer]:
    answer = ReceiptAnswer(receipts)
    losses: list[RecordingLoss] = []
    capture = AnswerCapture(answer, losses.append)

    async def deliver() -> None:
        for chunk in chunks:
            await capture.stream(chunk)
        await capture.finish(final)

    asyncio.run(deliver())
    return capture.seal(message, outcome), losses, answer


class ReceiptSender:
    def __init__(self) -> None:
        self.events: list[ServerEvent] = []
        self.receipts = iter((False, True, True))

    async def send(self, event: ServerEvent) -> None:
        await self.try_send(event)

    async def try_send(self, event: ServerEvent) -> bool:
        self.events.append(event)
        return next(self.receipts)
