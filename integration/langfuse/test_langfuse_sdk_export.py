"""Opt-in Langfuse SDK tests with an intercepted HTTP transport."""

import asyncio
import base64
import os
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import uuid4

import pytest
import requests
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from pydantic import SecretStr

from sillytherin.chat.ports import ChatState
from sillytherin.observability.agent_observer import NativeStageObserver
from sillytherin.observability.langfuse import (
    ExportLoss,
    LangfuseSettings,
    LossReason,
    read_settings,
    resolve_cloud_endpoint,
    worker_environment,
)
from sillytherin.observability.langfuse_worker import (
    GuardedSession,
    open_sdk_exporter,
    validate_encoded_batch,
)
from sillytherin.observability.reply_trace import (
    ReplyContent,
    ReplySpan,
    ReplyState,
    ReplyTrace,
    SpanKind,
    SpanOutcome,
    TraceClock,
)
from sillytherin.observability.turn_runner import ObservedTurnRunner
from sillytherin.turn import TurnHooks, TurnOutcome, TurnViews, UserMessage
from tests.chat.fakes import FakeAnswerView, FakeAsker, FakeChatState, FakeProgressView

MARKER = "private-sentinel"
TEST_TRACE = ReplyTrace(
    ReplySpan(SpanKind.REPLY, "reply.turn", 1000, 1080, SpanOutcome.SUCCEEDED),
    (
        ReplySpan(SpanKind.MODEL, "model.call", 1010, 1020, SpanOutcome.SUCCEEDED),
        ReplySpan(SpanKind.TOOL, "tool.web_search", 1030, 1060, SpanOutcome.SUCCEEDED),
    ),
)


@dataclass
class HttpBoundary:
    attempts: list[tuple[requests.PreparedRequest, dict[str, Any]]] = field(default_factory=list)
    status: int = 200
    response_headers: dict[str, str] = field(default_factory=dict)
    response_body: bytes = b""

    def send(self, request: requests.PreparedRequest, **options: Any) -> requests.Response:
        self.attempts.append((request, options))
        response = requests.Response()
        response.status_code = self.status
        response.headers.update(self.response_headers)
        response._content = self.response_body
        response._content_consumed = True
        return response

    def request(self) -> requests.PreparedRequest:
        [attempt] = self.attempts
        return attempt[0]

    def spans(self) -> list[Any]:
        payload = self.request().body
        assert isinstance(payload, bytes)
        batch = ExportTraceServiceRequest.FromString(payload)
        return list(batch.resource_spans[0].scope_spans[0].spans)


class RecordingSink:
    def __init__(self) -> None:
        self.traces: list[ReplyTrace] = []

    def offer(self, trace: ReplyTrace) -> None:
        self.traces.append(trace)


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> HttpBoundary:
    boundary = HttpBoundary()

    def capture(session: requests.Session, request: requests.PreparedRequest, **options: Any) -> requests.Response:
        assert not session.trust_env
        return boundary.send(request, **options)

    monkeypatch.setattr(requests.Session, "send", capture)
    return boundary


@pytest.fixture
def settings() -> LangfuseSettings:
    suffix = uuid4().hex
    return LangfuseSettings(
        resolve_cloud_endpoint("eu"), SecretStr(f"public-test-{suffix}"), SecretStr(f"secret-test-{suffix}"),
    )


def export_trace(settings: LangfuseSettings, trace: ReplyTrace = TEST_TRACE) -> ExportLoss | None:
    with open_sdk_exporter(settings) as exporter:
        return exporter.export(trace)


def test_successful_export_contains_only_allowed_wire_fields(http: HttpBoundary, settings: LangfuseSettings) -> None:
    from langchain_core.messages import HumanMessage, ToolMessage

    ticks = iter((0, 10, 20, 30, 60, 80))
    sink = RecordingSink()

    async def private_reply(chat: ChatState, message: UserMessage, views: TurnViews, *, hooks: TurnHooks) -> None:
        observer, = hooks.callbacks
        assert isinstance(observer, NativeStageObserver)
        model_id, tool_id = uuid4(), uuid4()
        await observer.on_chat_model_start({"private": MARKER}, [[HumanMessage(content=message.text)]], run_id=model_id)
        await observer.on_llm_end({"reasoning": MARKER}, run_id=model_id)
        await observer.on_tool_start({"name": "web_search", "private": MARKER}, MARKER, run_id=tool_id)
        await observer.on_tool_end(ToolMessage(content=MARKER, tool_call_id=MARKER), run_id=tool_id)
        await views.answer.finish(MARKER)
        hooks.on_completion(TurnOutcome.SUCCEEDED)

    runner = ObservedTurnRunner(private_reply, sink, TraceClock(lambda: 1000, lambda: next(ticks)))
    views = TurnViews(FakeAnswerView(), FakeProgressView(), FakeAsker(None), None)
    asyncio.run(runner(FakeChatState(chat_id=MARKER), UserMessage(MARKER, "", ""), views))
    trace, = sink.traces
    assert replace(trace, content=None) == TEST_TRACE
    assert export_trace(settings, trace) is None
    request = http.request()
    assert isinstance(request.body, bytes)
    validate_encoded_batch(request.body, trace)
    assert settings.public_key.get_secret_value().encode() not in request.body
    assert settings.secret_key.get_secret_value().encode() not in request.body
    assert MARKER.encode() in request.body
    assert not any(name.lower() == "x-langfuse-public-key" for name in request.headers)
    spans = http.spans()
    root, = (span for span in spans if not span.parent_span_id)
    assert root.start_time_unix_nano == 1000
    assert root.end_time_unix_nano == 1080
    assert all(span.trace_id == root.trace_id for span in spans)
    assert {span.parent_span_id for span in spans if span != root} == {root.span_id}


def test_failed_export_snapshot_excludes_exception_and_traceback(
    http: HttpBoundary, settings: LangfuseSettings,
) -> None:
    sink = RecordingSink()

    async def broken_reply(chat: ChatState, message: UserMessage, views: TurnViews, *, hooks: TurnHooks) -> None:
        raise RuntimeError(f"private error: {message.text}")

    runner = ObservedTurnRunner(broken_reply, sink, TraceClock(lambda: 1000, lambda: 10))
    views = TurnViews(FakeAnswerView(), FakeProgressView(), FakeAsker(None), None)
    with pytest.raises(RuntimeError, match=MARKER):
        asyncio.run(runner(FakeChatState(chat_id=MARKER), UserMessage(MARKER, "", ""), views))
    assert export_trace(settings, sink.traces[0]) is None
    assert MARKER.encode() in http.request().body
    root, = http.spans()
    assert root.status.code == 2 and not root.status.message and not root.events


def test_unlisted_tool_label_is_replaced_before_export(http: HttpBoundary, settings: LangfuseSettings) -> None:
    from sillytherin.observability.reply_trace import ReplyRecorder

    recorder = ReplyRecorder(TraceClock(lambda: 1000, lambda: 10))
    recorder.start_reply()
    observer = NativeStageObserver(recorder)

    async def invoke() -> None:
        run_id = uuid4()
        await observer.on_tool_start({"name": MARKER}, MARKER, run_id=run_id)
        await observer.on_tool_end(MARKER, run_id=run_id)

    asyncio.run(invoke())
    trace = recorder.finish_reply(SpanOutcome.SUCCEEDED)
    assert trace is not None and export_trace(settings, trace) is None
    assert MARKER.encode() not in http.request().body
    assert {span.name for span in http.spans()} == {"reply.turn", "tool.other"}


def mutate_sdk_batch(
    monkeypatch: pytest.MonkeyPatch, mutate: Callable[[ExportTraceServiceRequest], None],
) -> None:
    send = GuardedSession.send

    def tamper(session: GuardedSession, request: requests.PreparedRequest, **options: Any) -> requests.Response:
        batch = ExportTraceServiceRequest.FromString(request.body)
        mutate(batch)
        request.body = batch.SerializeToString()
        return send(session, request, **options)

    monkeypatch.setattr(GuardedSession, "send", tamper)


def test_unexpected_sdk_resource_field_blocks_entire_request(
    http: HttpBoundary, settings: LangfuseSettings, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def add_private_field(batch: ExportTraceServiceRequest) -> None:
        attr = batch.resource_spans[0].resource.attributes.add(key="host.name")
        attr.value.string_value = MARKER

    mutate_sdk_batch(monkeypatch, add_private_field)
    assert export_trace(settings) == ExportLoss(LossReason.INVALID_TRACE, 1)
    assert not http.attempts


def test_incomplete_sdk_batch_is_not_sent(
    http: HttpBoundary, settings: LangfuseSettings, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def remove_a_span(batch: ExportTraceServiceRequest) -> None:
        del batch.resource_spans[0].scope_spans[0].spans[0]

    mutate_sdk_batch(monkeypatch, remove_a_span)
    assert export_trace(settings) == ExportLoss(LossReason.INVALID_TRACE, 1)
    assert not http.attempts


def test_explicit_opt_in_uses_sdk_and_approved_tls_endpoint(http: HttpBoundary, settings: LangfuseSettings) -> None:
    enabled = read_settings({
        "SILLYTHERIN_LANGFUSE_ENABLED": "true",
        "SILLYTHERIN_LANGFUSE_REGION": "jp",
        "LANGFUSE_PUBLIC_KEY": settings.public_key.get_secret_value(),
        "LANGFUSE_SECRET_KEY": settings.secret_key.get_secret_value(),
    })
    assert enabled is not None and export_trace(enabled) is None
    credentials = f"{enabled.public_key.get_secret_value()}:{enabled.secret_key.get_secret_value()}"
    assert http.request().url == "https://jp.cloud.langfuse.com/api/public/otel/v1/traces"
    assert http.request().headers["Authorization"] == "Basic " + base64.b64encode(credentials.encode()).decode()
    assert http.request().headers["x-langfuse-ingestion-version"] == "4"
    assert http.attempts[0][1]["verify"] is True


def test_export_does_not_follow_redirect_with_credentials(http: HttpBoundary, settings: LangfuseSettings) -> None:
    http.status = 307
    http.response_headers["Location"] = "https://not-langfuse.example/path"
    assert export_trace(settings) == ExportLoss(LossReason.HTTP_REJECTED, 1, 307)
    assert http.request().url == settings.endpoint
    assert http.attempts[0][1]["allow_redirects"] is False


def test_export_ignores_environment_proxy(
    http: HttpBoundary, settings: LangfuseSettings, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://not-langfuse.example")
    assert export_trace(settings) is None
    assert http.request().url == settings.endpoint
    assert http.attempts[0][1]["proxies"] == {}


def test_sdk_endpoint_override_cannot_reroute_export(
    http: HttpBoundary, settings: LangfuseSettings, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "https://not-langfuse.example")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://not-langfuse.example")
    monkeypatch.setenv("OPENROUTER_API_KEY", MARKER)
    assert "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT" not in worker_environment(os.environ)
    assert "LANGFUSE_BASE_URL" not in worker_environment(os.environ)
    assert "OPENROUTER_API_KEY" not in worker_environment(os.environ)
    assert export_trace(settings) is None
    assert http.request().url == settings.endpoint


def test_retryable_vendor_failure_is_not_resent_by_sdk(
    http: HttpBoundary, settings: LangfuseSettings, caplog: pytest.LogCaptureFixture,
) -> None:
    http.status = 503
    http.response_body = MARKER.encode()
    assert export_trace(settings, replace(TEST_TRACE, children=())) == ExportLoss(LossReason.HTTP_REJECTED, 1, 503)
    assert http.request().url == settings.endpoint
    assert MARKER not in caplog.text


@pytest.mark.parametrize(("body", "expected"), [
    (b'{"id":"queue-job","name":"otel-ingestion","data":{}}', None),
    (b"{}", None),
    (b'{"partialSuccess":{"rejectedSpans":"0"}}', None),
    (b'{"partialSuccess":{"rejectedSpans":"1"}}', LossReason.DELIVERY_UNCONFIRMED),
    (b'{"partial_success":{"error_message":"private"}}', LossReason.DELIVERY_UNCONFIRMED),
    (b'{"success":false}', LossReason.DELIVERY_UNCONFIRMED),
    (b'{"error":"private"}', LossReason.DELIVERY_UNCONFIRMED),
    (b'{invalid', LossReason.DELIVERY_UNCONFIRMED),
])
def test_cloud_json_acknowledgement_is_distinguished_from_delivery_uncertainty(
    http: HttpBoundary, settings: LangfuseSettings, body: bytes, expected: LossReason | None,
) -> None:
    http.response_headers["Content-Type"] = "application/json; charset=utf-8"
    http.response_body = body
    loss = export_trace(settings)
    assert loss == (None if expected is None else ExportLoss(expected, 1))
    assert http.request().url == settings.endpoint


# Stopped-turn status


def test_sdk_marks_partial_cancelled_reply_as_error(http: HttpBoundary, settings: LangfuseSettings) -> None:
    trace = content_trace(SpanOutcome.CANCELLED, ReplyState.PARTIAL)
    assert export_trace(settings, trace) is None
    root = next(span for span in http.spans() if not span.parent_span_id)
    assert root.status.code == 2 and not root.status.message
    assert attributes(root)["langfuse.observation.metadata.outcome"] == "cancelled"


def test_sdk_marks_cancelled_reply_without_output_as_error(http: HttpBoundary, settings: LangfuseSettings) -> None:
    trace = content_trace(SpanOutcome.CANCELLED, ReplyState.MISSING)
    assert export_trace(settings, trace) is None
    root = next(span for span in http.spans() if not span.parent_span_id)
    assert root.status.code == 2
    assert "langfuse.observation.output" not in attributes(root)


# Root-only wire contract


def test_sdk_exports_exact_content_on_root_only(http: HttpBoundary, settings: LangfuseSettings) -> None:
    trace = content_trace()
    assert export_trace(settings, trace) is None
    root = next(span for span in http.spans() if not span.parent_span_id)
    assert attributes(root)["langfuse.observation.input"] == trace.content.message
    assert attributes(root)["langfuse.observation.output"] == trace.content.reply
    for child in http.spans():
        if child.parent_span_id:
            assert "langfuse.observation.output" not in attributes(child)


def test_child_content_blocks_wire_request(http: HttpBoundary, settings: LangfuseSettings) -> None:
    trace = content_trace()
    assert export_trace(settings, trace) is None
    batch = ExportTraceServiceRequest.FromString(http.request().body)
    child = next(span for span in batch.resource_spans[0].scope_spans[0].spans if span.parent_span_id)
    child.attributes.add(key="langfuse.observation.output").value.string_value = "private"
    with pytest.raises(ValueError):
        validate_encoded_batch(batch.SerializeToString(), trace)


def test_root_output_mismatch_blocks_wire_request(http: HttpBoundary, settings: LangfuseSettings) -> None:
    trace = content_trace()
    assert export_trace(settings, trace) is None
    batch = ExportTraceServiceRequest.FromString(http.request().body)
    root = next(span for span in batch.resource_spans[0].scope_spans[0].spans if not span.parent_span_id)
    output = next(item for item in root.attributes if item.key == "langfuse.observation.output")
    output.value.string_value = "wrong"
    with pytest.raises(ValueError):
        validate_encoded_batch(batch.SerializeToString(), trace)


def test_worker_rejects_inconsistent_reply_state(http: HttpBoundary, settings: LangfuseSettings) -> None:
    from sillytherin.observability.langfuse_worker import validate_reply_content
    with pytest.raises(ValueError):
        validate_reply_content(content_trace().content, SpanOutcome.FAILED)
    assert not http.attempts


def test_worker_rejects_non_string_content(http: HttpBoundary, settings: LangfuseSettings) -> None:
    from dataclasses import asdict

    from sillytherin.observability.langfuse_worker import trace_message
    payload = asdict(content_trace())
    payload["content"]["message"] = 42
    with pytest.raises(ValueError):
        trace_message.validate_python(payload)
    assert not http.attempts


def test_worker_rejects_unknown_content_property(http: HttpBoundary, settings: LangfuseSettings) -> None:
    from dataclasses import asdict

    from sillytherin.observability.langfuse_worker import trace_message
    payload = asdict(content_trace())
    payload["content"]["extra"] = "private"
    with pytest.raises(ValueError):
        trace_message.validate_python(payload)
    assert not http.attempts


# Destination with content


def test_content_export_uses_only_configured_tls_endpoint(http: HttpBoundary, settings: LangfuseSettings) -> None:
    assert export_trace(settings, content_trace()) is None
    request = http.request()
    assert request.url == settings.endpoint
    assert len(http.attempts) == 1
    assert http.attempts[0][1]["verify"] is True


def test_content_export_does_not_follow_vendor_redirect(http: HttpBoundary, settings: LangfuseSettings) -> None:
    http.status = 302
    http.response_headers["Location"] = "https://other.example"
    loss = export_trace(settings, content_trace())
    assert loss is not None and loss.reason is LossReason.HTTP_REJECTED
    assert len(http.attempts) == 1

def content_trace(
    outcome: SpanOutcome = SpanOutcome.SUCCEEDED, state: ReplyState = ReplyState.COMPLETE,
) -> ReplyTrace:
    return replace(TEST_TRACE, root=replace(TEST_TRACE.root, outcome=outcome),
                   content=ReplyContent('{"question":"é"}\nnext', None if state is ReplyState.MISSING else "reply\ntext", state))


def attributes(span: Any) -> dict[str, str]:
    return {item.key: item.value.string_value for item in span.attributes}
