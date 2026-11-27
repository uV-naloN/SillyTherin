"""Process boundary for the optional Langfuse SDK."""

from __future__ import annotations

import json
import logging
import os
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Protocol, TextIO

import requests
from pydantic import TypeAdapter, ValidationError
from requests.auth import HTTPBasicAuth

from sillytherin.observability.dtos.langfuse import (
    ExportLoss,
    LangfuseSettings,
    LossReason,
    WorkerStartupFailure,
)
from sillytherin.observability.dtos.reply_trace import (
    MAX_REPLY_CONTENT_BYTES,
    ModelExchange,
    ModelOutputState,
    ReplyContent,
    ReplySpan,
    ReplyState,
    ReplyTrace,
    SpanKind,
    SpanOutcome,
)
from sillytherin.observability.langfuse import (
    CLOUD_HOSTS,
    OTLP_TRACES_PATH,
    resolve_cloud_endpoint,
)
from sillytherin.observability.reply_trace import (
    MAX_CHILD_SPANS,
    MODEL_NAME,
    REPLY_NAME,
    SAFE_TOOL_LABELS,
)

if TYPE_CHECKING:
    from langfuse import Langfuse
    from opentelemetry.context import Context
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )
    from opentelemetry.proto.trace.v1.trace_pb2 import Span as WireSpan
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.trace import Span, Tracer

OTLP_CONTENT_TYPE = "application/x-protobuf"
LANGFUSE_INGESTION_VERSION = "4"
INGESTION_HEADER = "x-langfuse-ingestion-version"
TRACE_SCOPE = "sillytherin.reply_trace"
SERVICE_NAME = "SillyTherin"
ROOT_ATTRIBUTE = "langfuse.internal.is_app_root"
MAX_ACK_BYTES = 16_384
MAX_REQUEST_BYTES = 262_144
HTTP_OK_MIN = 200
HTTP_OK_LIMIT = 300
INVALID_REQUEST_STATUS = 400
FLUSH_INTERVAL_SECONDS = 60
HTTP_EXPORT_TIMEOUT_SECONDS = 10
INTERNAL_SPAN_KIND = 1
LOCAL_SPAN_FLAGS = frozenset({0, 1, 256, 257})
SpanSignature = tuple[str, int, int, int, tuple[tuple[str, str, bytes], ...]]

settings_message = TypeAdapter(LangfuseSettings)
trace_message = TypeAdapter(ReplyTrace)


class ExportChannel(Protocol):
    def signal_ready(self) -> None: ...

    def signal_startup_failure(self, reason: WorkerStartupFailure) -> None: ...

    def receive_trace(self) -> ReplyTrace | None: ...

    def send_result(self, loss: ExportLoss | None) -> None: ...


class SdkExporter(Protocol):
    def export(self, trace: ReplyTrace) -> ExportLoss | None: ...


def run_export_worker(settings: LangfuseSettings, channel: ExportChannel) -> None:
    with ExitStack() as resources:
        try:
            exporter = resources.enter_context(open_sdk_exporter(settings))
        except ImportError:
            channel.signal_startup_failure(WorkerStartupFailure.DEPENDENCY_MISSING)
            raise
        except Exception:
            channel.signal_startup_failure(WorkerStartupFailure.INITIALIZATION_FAILED)
            raise
        channel.signal_ready()
        while (trace := channel.receive_trace()) is not None:
            channel.send_result(exporter.export(trace))


@contextmanager
def open_sdk_exporter(settings: LangfuseSettings) -> Iterator[SdkExporter]:
    from langfuse import Langfuse
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        Compression,
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    if settings.endpoint not in {resolve_cloud_endpoint(region) for region in CLOUD_HOSTS}:
        raise ValueError("Unapproved Langfuse endpoint")
    provider = TracerProvider(resource=Resource({"service.name": SERVICE_NAME}), shutdown_on_exit=False)
    session = GuardedSession(settings)
    exporter = OTLPSpanExporter(
        endpoint=settings.endpoint, session=session, compression=Compression.NoCompression,
        headers={INGESTION_HEADER: LANGFUSE_INGESTION_VERSION}, timeout=HTTP_EXPORT_TIMEOUT_SECONDS,
        max_request_size=MAX_REQUEST_BYTES,
    )
    client = Langfuse(
        public_key=settings.public_key.get_secret_value(), secret_key=settings.secret_key.get_secret_value(),
        base_url=settings.endpoint.removesuffix(OTLP_TRACES_PATH), tracer_provider=provider,
        span_exporter=exporter, should_export_span=belongs_to_reply_scope,
        flush_at=MAX_CHILD_SPANS + 2, flush_interval=FLUSH_INTERVAL_SECONDS,
    )
    try:
        yield LangfuseBatchExporter(provider.get_tracer(TRACE_SCOPE), client, session)
    finally:
        session.trace = None
        client.shutdown()
        provider.shutdown()


def belongs_to_reply_scope(span: ReadableSpan) -> bool:
    return span.instrumentation_scope is not None and span.instrumentation_scope.name == TRACE_SCOPE


class LangfuseBatchExporter:
    def __init__(self, tracer: Tracer, client: Langfuse, session: GuardedSession) -> None:
        self._tracer = tracer
        self._client = client
        self._session = session

    def export(self, trace: ReplyTrace) -> ExportLoss | None:
        from opentelemetry.context import Context
        from opentelemetry.trace import set_span_in_context

        try:
            validate_trace_snapshot(trace)
        except (TypeError, ValueError, RecursionError):
            return ExportLoss(LossReason.INVALID_TRACE, 1)

        self._session.arm(trace)
        try:
            root = start_reply_span(self._tracer, trace, Context())
            parent = set_span_in_context(root, Context())
            for child in trace.children:
                start_span(self._tracer, child, parent).end(end_time=child.end_time_unix_nano)
            root.end(end_time=trace.root.end_time_unix_nano)
            self._client.flush()
            return self._session.result
        finally:
            self._session.trace = None


def start_reply_span(tracer: Tracer, trace: ReplyTrace, context: Context) -> Span:
    from opentelemetry.trace import Status, StatusCode

    attributes = reply_attributes(trace)
    span = tracer.start_span(
        trace.root.name, context=context, start_time=trace.root.start_time_unix_nano,
        attributes=attributes, record_exception=False, set_status_on_exception=False,
    )
    span.set_status(Status(StatusCode(reply_status(trace))))
    return span


def reply_status(trace: ReplyTrace) -> int:
    if trace.content is not None and trace.root.outcome is SpanOutcome.CANCELLED:
        return 2
    return status_code(trace.root.outcome)


def reply_attributes(trace: ReplyTrace) -> dict[str, str]:
    attributes = span_attributes(trace.root)
    if trace.content is None:
        return attributes
    attributes["langfuse.observation.input"] = trace.content.message
    attributes["langfuse.observation.metadata.reply_state"] = trace.content.reply_state.value
    if trace.content.reply is not None:
        attributes["langfuse.observation.output"] = trace.content.reply
    return attributes


def start_span(tracer: Tracer, snapshot: ReplySpan, context: Context) -> Span:
    from opentelemetry.trace import Status, StatusCode

    span = tracer.start_span(
        snapshot.name, context=context, start_time=snapshot.start_time_unix_nano,
        attributes=span_attributes(snapshot), record_exception=False, set_status_on_exception=False,
    )
    span.set_status(Status(StatusCode(child_status(snapshot))))
    return span


def span_attributes(snapshot: ReplySpan) -> dict[str, str]:
    attributes = {
        "langfuse.trace.name": REPLY_NAME,
        "langfuse.observation.type": snapshot.kind.value,
        "langfuse.observation.metadata.outcome": snapshot.outcome.value,
    }
    if snapshot.model_exchange is not None:
        attributes.update(model_exchange_attributes(snapshot.model_exchange))
    return attributes


def model_exchange_attributes(exchange: ModelExchange) -> dict[str, str]:
    """Project an approved model exchange onto Langfuse generation fields."""
    attributes = {
        "langfuse.observation.input": exchange.input_json,
        "langfuse.observation.metadata.output_state": exchange.output_state.value,
    }
    if exchange.output_json is not None:
        attributes["langfuse.observation.output"] = exchange.output_json
    return attributes


def validate_model_exchange(exchange: ModelExchange, outcome: SpanOutcome) -> None:
    content_size = len(exchange.input_json.encode("utf-8")) + len((exchange.output_json or "").encode("utf-8"))
    if content_size > MAX_REQUEST_BYTES:
        raise ValueError("Oversized model exchange")

    if not isinstance(json.loads(exchange.input_json), list):
        raise TypeError("Invalid model input")
    if exchange.output_state is ModelOutputState.COMPLETE and outcome is not SpanOutcome.SUCCEEDED:
        raise ValueError("Inconsistent model outcome")
    if (exchange.output_json is None) != (exchange.output_state is ModelOutputState.MISSING):
        raise ValueError("Inconsistent model output state")
    if exchange.output_json is not None:
        validate_model_output(exchange.output_json, exchange.output_state)


def validate_model_output(output_json: str, state: ModelOutputState) -> None:
    output = json.loads(output_json)
    required_field = "messages" if state is ModelOutputState.COMPLETE else "chunks"
    allowed_fields = {"messages", "tool_call_fragments"} if state is ModelOutputState.COMPLETE else {"chunks"}
    if not isinstance(output, dict) or required_field not in output or output.keys() - allowed_fields:
        raise ValueError("Invalid model output")
    if any(not isinstance(items, list) or not items for items in output.values()):
        raise ValueError("Invalid model output items")


STATUS_CODES = {SpanOutcome.SUCCEEDED: 1, SpanOutcome.FAILED: 2}


def status_code(outcome: SpanOutcome) -> int:
    return STATUS_CODES.get(outcome, 0)


def child_status(snapshot: ReplySpan) -> int:
    if snapshot.model_exchange is not None and snapshot.outcome is not SpanOutcome.SUCCEEDED:
        return status_code(SpanOutcome.FAILED)
    return status_code(snapshot.outcome)


class GuardedSession(requests.Session):
    def __init__(self, settings: LangfuseSettings) -> None:
        super().__init__()
        self.trust_env = False
        self._endpoint = settings.endpoint
        self._auth = HTTPBasicAuth(settings.public_key.get_secret_value(), settings.secret_key.get_secret_value())
        self.trace: ReplyTrace | None = None
        self.result: ExportLoss | None = None
        self._attempted = False

    def arm(self, trace: ReplyTrace) -> None:
        self.trace = trace
        self.result = ExportLoss(LossReason.DELIVERY_UNCONFIRMED, 1)
        self._attempted = False

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        if self._attempted or self.trace is None:
            return safe_response(INVALID_REQUEST_STATUS)
        self._attempted = True
        try:
            self.validate_request(request, self.trace)
        except (TypeError, ValueError, RecursionError):
            self.result = ExportLoss(LossReason.INVALID_TRACE, 1)
            return safe_response(INVALID_REQUEST_STATUS)
        try:
            with super().send(
                request, allow_redirects=False, verify=True, proxies={}, stream=True, timeout=kwargs.get("timeout"),
            ) as response:
                self.result = response_loss(response)
        except requests.RequestException:
            self.result = ExportLoss(LossReason.CONNECTION_FAILED, 1)
        return safe_response(HTTP_OK_MIN if self.result is None else INVALID_REQUEST_STATUS)

    def validate_request(self, request: requests.PreparedRequest, trace: ReplyTrace) -> None:
        if request.method != "POST" or request.url != self._endpoint or not isinstance(request.body, bytes):
            raise ValueError("Unexpected SDK request destination or body")
        validate_encoded_batch(request.body, trace)
        request.headers.clear()
        request.prepare_content_length(request.body)
        request.headers.update({
            "Content-Type": OTLP_CONTENT_TYPE, INGESTION_HEADER: LANGFUSE_INGESTION_VERSION,
            "Accept-Encoding": "identity",
        })
        self._auth(request)


def safe_response(status: int) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = b""
    response._content_consumed = True
    return response


def response_loss(response: requests.Response) -> ExportLoss | None:
    from google.protobuf.message import DecodeError
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceResponse,
    )

    status = response.status_code
    if not HTTP_OK_MIN <= status < HTTP_OK_LIMIT:
        return ExportLoss(LossReason.HTTP_REJECTED, 1, status if 100 <= status <= 599 else None)
    acknowledgement = bytearray()
    for chunk in response.iter_content(chunk_size=MAX_ACK_BYTES):
        acknowledgement.extend(chunk)
        if len(acknowledgement) > MAX_ACK_BYTES:
            return ExportLoss(LossReason.DELIVERY_UNCONFIRMED, 1)
    media_type = response.headers.get("Content-Type", "").partition(";")[0].strip().lower()
    if acknowledgement and media_type == "application/json":
        # Langfuse Cloud may acknowledge the queued upload in JSON even when sent protobuf.
        if accepts_json_acknowledgement(bytes(acknowledgement)):
            return None
        return ExportLoss(LossReason.DELIVERY_UNCONFIRMED, 1)
    try:
        result = ExportTraceServiceResponse.FromString(bytes(acknowledgement))
    except DecodeError:
        return ExportLoss(LossReason.DELIVERY_UNCONFIRMED, 1)
    if result.partial_success.rejected_spans or result.partial_success.error_message:
        return ExportLoss(LossReason.DELIVERY_UNCONFIRMED, 1)
    return None


def accepts_json_acknowledgement(payload: bytes) -> bool:
    try:
        acknowledgement = json.loads(payload)
    except (ValueError, UnicodeError):
        return False
    if not isinstance(acknowledgement, dict):
        return False
    if acknowledgement.get("error") or acknowledgement.get("errors") or acknowledgement.get("success") is False:
        return False
    for key in ("partialSuccess", "partial_success"):
        if key in acknowledgement and not accepts_partial_success(acknowledgement[key]):
            return False
    return True


def accepts_partial_success(partial: object) -> bool:
    if not isinstance(partial, dict):
        return False
    rejected_counts = ("rejectedSpans", "rejected_spans")
    error_fields = ("errorMessage", "error_message")
    return all(partial.get(key, 0) in (0, "0") for key in rejected_counts) and not any(
        partial.get(key) for key in error_fields
    )


def validate_encoded_batch(payload: bytes, trace: ReplyTrace) -> None:
    from google.protobuf.message import DecodeError
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    if len(payload) > MAX_REQUEST_BYTES or len(trace.children) > MAX_CHILD_SPANS:
        raise ValueError("Oversized SDK batch")
    try:
        batch = ExportTraceServiceRequest.FromString(payload)
    except DecodeError as error:
        raise ValueError("Invalid SDK batch encoding") from error
    batch.DiscardUnknownFields()
    if payload != batch.SerializeToString():
        raise ValueError("Unexpected protobuf representation")
    spans = validated_envelope(batch)
    validate_trace_snapshot(trace)
    roots = [span for span in spans if not span.parent_span_id]
    if len(roots) != 1 or len(spans) != len(trace.children) + 1:
        raise ValueError("Incomplete SDK batch")
    root = roots[0]
    validate_span_ids(spans, root)
    if wire_span_signature(root) != reply_snapshot_signature(trace):
        raise ValueError("Unexpected root span")
    children = Counter(wire_span_signature(span) for span in spans if span.parent_span_id)
    if children != Counter(child_snapshot_signature(child) for child in trace.children):
        raise ValueError("Unexpected child spans")


def validate_snapshot_bounds(trace: ReplyTrace) -> None:
    start, end = trace.root.start_time_unix_nano, trace.root.end_time_unix_nano
    if not 0 <= start <= end:
        raise ValueError("Invalid reply timestamps")
    if any(not start <= span.start_time_unix_nano <= span.end_time_unix_nano <= end for span in trace.children):
        raise ValueError("Invalid stage timestamps")


def validate_trace_snapshot(trace: ReplyTrace) -> None:
    validate_snapshot_bounds(trace)
    root = trace.root
    if root.kind is not SpanKind.REPLY or root.name != REPLY_NAME or root.outcome is SpanOutcome.INTERRUPTED:
        raise ValueError("Invalid reply span")
    if root.model_exchange is not None or len(trace.children) > MAX_CHILD_SPANS:
        raise ValueError("Invalid reply stages")
    if trace.content is not None:
        validate_reply_content(trace.content, root.outcome)
    for child in trace.children:
        validate_child_label(child)
        if child.model_exchange is not None:
            if child.kind is not SpanKind.MODEL:
                raise ValueError("Unexpected model exchange")
            validate_model_exchange(child.model_exchange, child.outcome)


def validate_reply_content(content: ReplyContent, outcome: SpanOutcome) -> None:
    for text in (content.message, content.reply):
        if text is None:
            continue
        if not isinstance(text, str) or len(text) > MAX_REPLY_CONTENT_BYTES:
            raise ValueError("Invalid content size")
        if len(text.encode("utf-8")) > MAX_REPLY_CONTENT_BYTES:
            raise ValueError("Invalid content size")
    if (content.reply is None) != (content.reply_state is ReplyState.MISSING):
        raise ValueError("Inconsistent missing reply")
    if content.reply_state is ReplyState.PARTIAL and not content.reply:
        raise ValueError("Empty partial reply")
    if content.reply_state is ReplyState.COMPLETE and outcome is not SpanOutcome.SUCCEEDED:
        raise ValueError("Inconsistent completed reply")


def validated_envelope(batch: ExportTraceServiceRequest) -> Sequence[WireSpan]:
    from opentelemetry.proto.common.v1.common_pb2 import (
        AnyValue,
        InstrumentationScope,
        KeyValue,
    )
    from opentelemetry.proto.resource.v1.resource_pb2 import Resource

    if len(batch.resource_spans) != 1 or len(batch.resource_spans[0].scope_spans) != 1:
        raise ValueError("Unexpected SDK export envelope")
    resource = batch.resource_spans[0]
    scope = resource.scope_spans[0]
    expected_resource = Resource(attributes=[KeyValue(key="service.name", value=AnyValue(string_value=SERVICE_NAME))])
    if resource.schema_url or scope.schema_url or resource.resource != expected_resource:
        raise ValueError("Unexpected SDK resource")
    if scope.scope != InstrumentationScope(name=TRACE_SCOPE):
        raise ValueError("Unexpected SDK instrumentation scope")
    return scope.spans


def validate_span_ids(spans: Sequence[WireSpan], root: WireSpan) -> None:
    span_ids: set[bytes] = set()
    for span in spans:
        if (
            span.trace_id != root.trace_id or len(span.trace_id) != 16 or not any(span.trace_id)
            or len(span.span_id) != 8 or not any(span.span_id) or span.span_id in span_ids
            or (span.parent_span_id and span.parent_span_id != root.span_id)
        ):
            raise ValueError("Unexpected SDK trace hierarchy")
        span_ids.add(span.span_id)


def wire_span_signature(span: WireSpan) -> SpanSignature:
    if (
        span.trace_state or span.kind != INTERNAL_SPAN_KIND or span.events or span.links or span.status.message
        or span.dropped_attributes_count or span.dropped_events_count or span.dropped_links_count
        or span.flags not in LOCAL_SPAN_FLAGS
    ):
        raise ValueError("Unexpected SDK span fields")
    attributes = []
    for item in span.attributes:
        value_kind = item.value.WhichOneof("value")
        if value_kind not in ("string_value", "bool_value"):
            raise ValueError("Unexpected SDK attribute type")
        attributes.append((item.key, value_kind, item.value.SerializeToString()))
    return (
        span.name, span.start_time_unix_nano, span.end_time_unix_nano, span.status.code, tuple(sorted(attributes)),
    )


def child_snapshot_signature(snapshot: ReplySpan) -> SpanSignature:
    from opentelemetry.proto.common.v1.common_pb2 import AnyValue

    attributes = [
        (key, "string_value", AnyValue(string_value=value).SerializeToString())
        for key, value in span_attributes(snapshot).items()
    ]
    return (
        snapshot.name, snapshot.start_time_unix_nano, snapshot.end_time_unix_nano,
        child_status(snapshot), tuple(sorted(attributes)),
    )


def reply_snapshot_signature(trace: ReplyTrace) -> SpanSignature:
    from opentelemetry.proto.common.v1.common_pb2 import AnyValue

    attributes = [
        (key, "string_value", AnyValue(string_value=value).SerializeToString())
        for key, value in reply_attributes(trace).items()
    ]
    attributes.append((ROOT_ATTRIBUTE, "bool_value", AnyValue(bool_value=True).SerializeToString()))
    root = trace.root
    return (root.name, root.start_time_unix_nano, root.end_time_unix_nano,
            reply_status(trace), tuple(sorted(attributes)))


def validate_child_label(snapshot: ReplySpan) -> None:
    if snapshot.kind is SpanKind.MODEL and snapshot.name == MODEL_NAME:
        return
    if snapshot.kind is SpanKind.TOOL and snapshot.name in SAFE_TOOL_LABELS:
        return
    raise ValueError("Unexpected stage label")


class StdioChannel:
    def __init__(self, reader: TextIO, writer: TextIO) -> None:
        self._reader = reader
        self._writer = writer

    def signal_ready(self) -> None:
        self.write({"ready": True})

    def signal_startup_failure(self, reason: WorkerStartupFailure) -> None:
        self.write({"startup_error": reason.value})

    def receive_trace(self) -> ReplyTrace | None:
        while line := self._reader.readline():
            try:
                return trace_message.validate_json(line)
            except ValidationError:
                self.send_result(ExportLoss(LossReason.INVALID_TRACE, 1))
        return None

    def send_result(self, loss: ExportLoss | None) -> None:
        self.write({"ok": True} if loss is None else asdict(loss))

    def write(self, message: dict[str, object]) -> None:
        self._writer.write(json.dumps(message, separators=(",", ":")) + "\n")
        self._writer.flush()


def main() -> int:
    logging.disable(logging.CRITICAL)
    os.environ["LANGFUSE_MEDIA_UPLOAD_ENABLED"] = "false"
    try:
        settings = settings_message.validate_json(sys.stdin.readline())
        run_export_worker(settings, StdioChannel(sys.stdin, sys.stdout))
    except Exception:  # noqa: BLE001 - the supervisor records a fixed startup or worker failure reason
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
