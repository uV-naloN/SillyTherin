"""Exercise relay failures through the chat socket and the optional trace exporter."""

import pytest
from starlette.testclient import TestClient

from sillytherin.observability.dtos.langfuse import ExportLoss, LossReason
from sillytherin.observability.dtos.reply_trace import ReplyState, SpanOutcome
from sillytherin.server.socket import ChatSessions
from tests.agent.test_openrouter_relay_failure import (
    INTERRUPTION_REASON,
    RELAY_NOTICE,
    RELAY_WARNING,
    SENSITIVE_MARKER,
    StreamFailure,
    relay_error,
)
from tests.scripted_chat_model import HANG, ScriptedChatModel, text_chunk
from tests.server import test_reply_trace_export
from tests.server.conftest import (
    OWN_HOST,
    OWN_PAGE,
    events_until,
    open_tab,
    send_message,
)
from tests.server.test_origin_and_host import refused_code
from tests.server.test_reply_trace_export import FakeWorker

traced_client = test_reply_trace_export.traced_client
worker = test_reply_trace_export.worker


def test_chat_can_send_again_after_relay_failure(client: TestClient, model: ScriptedChatModel) -> None:
    model.scripts += [[text_chunk("Partial"), StreamFailure(relay_error())], [text_chunk("Recovered")]]
    with open_tab(client) as tab:
        tab.receive_json()
        send_message(tab)
        events = events_until(tab, "reply.ended")
        started = next(event for event in events if event["type"] == "reply.started")
        send_message(tab)
        recovered = events_until(tab, "reply.ended")[-1]

    assert events[-1] == {
        "type": "reply.ended", "message_id": started["message_id"], "text": "Partial",
        "interruption_reason": INTERRUPTION_REASON,
    }
    assert recovered["text"] == "Recovered"
    assert "interruption_reason" not in recovered
    assert len(model.requests) == 2
    assert model.requests[1]["messages"][-2] == {"role": "assistant", "content": "Partial"}


def test_relay_failure_is_captured_as_failed_trace(
    traced_client: TestClient, model: ScriptedChatModel, worker: FakeWorker,
) -> None:
    model.scripts.append([text_chunk("Partial"), StreamFailure(relay_error())])
    with open_tab(traced_client) as tab:
        tab.receive_json()
        send_message(tab)
        assert events_until(tab, "reply.ended")[-1]["interruption_reason"] == INTERRUPTION_REASON
        assert traced_client.portal is not None
        trace = traced_client.portal.call(worker.next_trace)

    assert trace.root.outcome is SpanOutcome.FAILED
    assert trace.content is not None
    assert trace.content.reply == "Partial"
    assert trace.content.reply_state is ReplyState.PARTIAL


def test_relay_failure_reply_survives_exporter_failure(
    traced_client: TestClient, model: ScriptedChatModel, worker: FakeWorker,
) -> None:
    worker.loss = ExportLoss(LossReason.CONNECTION_FAILED, 1)
    model.scripts += [[text_chunk("Partial"), StreamFailure(relay_error())], [text_chunk("Recovered")]]
    with open_tab(traced_client) as tab:
        tab.receive_json()
        send_message(tab)
        assert events_until(tab, "reply.ended")[-1]["interruption_reason"] == INTERRUPTION_REASON
        assert traced_client.portal is not None
        traced_client.portal.call(worker.next_trace)
        send_message(tab)
        assert events_until(tab, "reply.ended")[-1]["text"] == "Recovered"

    assert len(model.requests) == 2


def test_resumed_chat_restores_partial_reply_interruption(client: TestClient, model: ScriptedChatModel) -> None:
    model.scripts.append([text_chunk("Partial"), StreamFailure(relay_error())])
    with open_tab(client) as tab:
        chat_id = tab.receive_json()["chat_id"]
        send_message(tab)
        events_until(tab, "reply.ended")
    with open_tab(client, chat_id) as tab:
        resumed = tab.receive_json()

    assert resumed["chat_id"] == chat_id
    assert resumed["messages"] == [
        {"role": "user", "text": "hi"},
        {"role": "assistant", "text": "Partial", "interruption_reason": INTERRUPTION_REASON},
    ]
    assert len(model.requests) == 1


def test_relay_failure_before_first_delta_records_missing_answer(
    traced_client: TestClient, model: ScriptedChatModel, worker: FakeWorker,
) -> None:
    model.scripts.append([StreamFailure(relay_error())])
    with open_tab(traced_client) as tab:
        tab.receive_json()
        send_message(tab)
        assert events_until(tab, "reply.ended")[-1]["text"] == RELAY_NOTICE
        assert traced_client.portal is not None
        trace = traced_client.portal.call(worker.next_trace)

    assert trace.root.outcome is SpanOutcome.FAILED
    assert trace.content is not None
    assert trace.content.reply is None
    assert trace.content.reply_state is ReplyState.MISSING


def test_chat_rejects_second_send_while_first_turn_is_active(
    client: TestClient, model: ScriptedChatModel, sessions: ChatSessions,
) -> None:
    model.scripts.append([text_chunk("Partial"), HANG])
    with open_tab(client) as tab:
        chat_id = tab.receive_json()["chat_id"]
        send_message(tab, "first")
        events_until(tab, "reply.delta")
        send_message(tab, "second")
        assert events_until(tab, "error")[-1]["code"] == "BUSY"
        chat = sessions.resume(chat_id)
        assert chat is not None
        assert [entry["content"] for entry in chat.state.get("history") if entry["role"] == "user"] == ["first"]
        tab.send_json({"type": "chat.stop"})
        assert events_until(tab, "reply.ended")[-1]["text"] == "Partial"

    assert len(model.requests) == 1


def test_relay_failure_reports_only_safe_details(
    client: TestClient, model: ScriptedChatModel, sessions: ChatSessions, caplog: pytest.LogCaptureFixture,
) -> None:
    model.scripts.append([StreamFailure(relay_error())])
    with open_tab(client) as tab:
        chat_id = tab.receive_json()["chat_id"]
        send_message(tab)
        reply = events_until(tab, "reply.ended")[-1]

    chat = sessions.resume(chat_id)
    assert chat is not None
    assert reply["text"] == RELAY_NOTICE
    assert SENSITIVE_MARKER not in repr(chat.state.get("history"))
    warnings = [record for record in caplog.records if record.message == RELAY_WARNING]
    assert len(warnings) == 1
    assert warnings[0].exc_info is None
    assert SENSITIVE_MARKER not in caplog.text


def test_foreign_origin_cannot_start_relay_failure_turn(
    client: TestClient, model: ScriptedChatModel, sessions: ChatSessions, caplog: pytest.LogCaptureFixture,
) -> None:
    model.scripts.append([StreamFailure(relay_error())])
    assert refused_code(client, {"origin": "https://foreign.example", "host": OWN_HOST}) == 1008
    assert not model.requests
    assert not sessions._chats
    assert RELAY_WARNING not in caplog.text


def test_foreign_host_cannot_start_relay_failure_turn(
    client: TestClient, model: ScriptedChatModel, sessions: ChatSessions, caplog: pytest.LogCaptureFixture,
) -> None:
    model.scripts.append([StreamFailure(relay_error())])
    assert refused_code(client, {"origin": OWN_PAGE, "host": "foreign.example:8000"}) == 1008
    assert not model.requests
    assert not sessions._chats
    assert RELAY_WARNING not in caplog.text
