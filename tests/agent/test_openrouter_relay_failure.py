"""Classify OpenRouter failures and check how the agent finishes a failed turn."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
import pytest
from openrouter.errors import BadRequestResponseError
from openrouter.errors.badrequestresponse_error import BadRequestResponseErrorData

from sillytherin.agent.model import build_chat_model, is_openrouter_relay_failure
from sillytherin.chat.ports import ChatState
from sillytherin.contracts.chat import ModelRequest, UserMessage
from sillytherin.turn import TurnHooks, TurnOutcome, TurnViews, run_turn
from tests.agent.conftest import THINKS_AND_TOOLS, history_of, run_one_turn
from tests.scripted_chat_model import (
    HANG,
    ScriptedChatModel,
    reasoning_chunk,
    text_chunk,
    tool_call_chunk,
)

RELAY_MESSAGE = "Request path cannot be relayed"
RELAY_NOTICE = "OpenRouter couldn’t route this request. Try sending your message again in a moment."
RELAY_WARNING = "OpenRouter chat request failed reason=relay_failed http_status=400"
INTERRUPTION_REASON = "openrouter_relay_failed"
SENSITIVE_MARKER = "<script>private-router-detail</script>"


def relay_error(
    http_status: int = 400, *, code: int = 400, message: str = RELAY_MESSAGE,
) -> BadRequestResponseError:
    payload = {"error": {"message": message, "code": code, "metadata": {"raw": SENSITIVE_MARKER}}}
    response = httpx.Response(http_status, json=payload, headers={"X-Test-Secret": SENSITIVE_MARKER})
    return BadRequestResponseError(BadRequestResponseErrorData.model_validate(payload), response)


@dataclass(frozen=True)
class StreamFailure:
    error: BaseException

    def model_copy(self) -> None:
        raise self.error


def capture_provider_error(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response,
) -> Exception:
    def send(_client: httpx.Client, request: httpx.Request, **_options: object) -> httpx.Response:
        response.request = request
        return response

    monkeypatch.setattr("sillytherin.agent.model.OPENROUTER_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(httpx.Client, "send", send)
    model = build_chat_model(ModelRequest(THINKS_AND_TOOLS, [], {}))
    with pytest.raises(Exception) as failure:
        list(model.stream("synthetic request"))
    return failure.value


def test_recognizes_openrouter_relay_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    response = httpx.Response(400, json={"error": {"code": 400, "message": RELAY_MESSAGE}})
    error = capture_provider_error(monkeypatch, response)
    assert isinstance(error, BadRequestResponseError)
    assert is_openrouter_relay_failure(error)


def test_ignores_relay_text_on_other_http_status() -> None:
    assert not is_openrouter_relay_failure(relay_error(http_status=403))


def test_ignores_other_relay_body_codes() -> None:
    assert not is_openrouter_relay_failure(relay_error(code=403))


def test_ignores_other_bad_request_messages() -> None:
    assert not is_openrouter_relay_failure(relay_error(message="Invalid request parameters"))


def test_ignores_relay_text_in_local_exception() -> None:
    assert not is_openrouter_relay_failure(RuntimeError(RELAY_MESSAGE))


@pytest.mark.parametrize("body", [b"not-json", b'{"error":{"message":"Request path cannot be relayed"}}'])
def test_ignores_unparseable_provider_error(monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
    response = httpx.Response(400, content=body, headers={"Content-Type": "application/json"})
    assert not is_openrouter_relay_failure(capture_provider_error(monkeypatch, response))


def capture_outcomes(outcomes: list[TurnOutcome]) -> Callable[[ChatState, UserMessage, TurnViews], Awaitable[None]]:
    async def run(chat: ChatState, message: UserMessage, views: TurnViews) -> None:
        await run_turn(chat, message, views, hooks=TurnHooks(on_completion=outcomes.append))

    return run


def test_relay_failure_finalizes_turn_once(scripted: ScriptedChatModel, caplog: pytest.LogCaptureFixture) -> None:
    outcomes: list[TurnOutcome] = []
    scripted.scripts.append([reasoning_chunk("thinking"), StreamFailure(relay_error())])

    result = run_one_turn(run_reply=capture_outcomes(outcomes))

    assert result.answer.final == RELAY_NOTICE
    assert result.answer.interruption_reason is None
    assert result.progress.summary is not None
    assert outcomes == [TurnOutcome.FAILED]
    assert len(scripted.requests) == 1
    assert history_of(result)[1:] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": RELAY_NOTICE},
    ]
    assert [record.message for record in caplog.records if record.message == RELAY_WARNING] == [RELAY_WARNING]


def test_relay_failure_preserves_streamed_answer(scripted: ScriptedChatModel) -> None:
    scripted.scripts.append([text_chunk("Partial response"), StreamFailure(relay_error())])

    result = run_one_turn()

    assert result.answer.streamed == ["Partial response"]
    assert result.answer.final == "Partial response"
    assert result.answer.interruption_reason == INTERRUPTION_REASON
    assert history_of(result)[-1] == {
        "role": "assistant", "content": "Partial response", "interruption_reason": INTERRUPTION_REASON,
    }


def test_relay_failure_does_not_repeat_completed_tool(
    scripted: ScriptedChatModel, searches: list[dict],
) -> None:
    scripted.scripts += [
        [tool_call_chunk("web_search", {"query": "cats"})],
        [StreamFailure(relay_error())],
    ]

    result = run_one_turn()

    assert searches == [{"query": "cats"}]
    assert len(scripted.requests) == 2
    assert history_of(result)[1:] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": RELAY_NOTICE},
    ]


def test_unrelated_provider_failure_uses_existing_reply(
    scripted: ScriptedChatModel, caplog: pytest.LogCaptureFixture,
) -> None:
    scripted.scripts.append([StreamFailure(relay_error(message="Other bad request"))])

    result = run_one_turn()

    assert result.answer.final == "Error: Other bad request"
    assert result.answer.interruption_reason is None
    assert len(scripted.requests) == 1
    assert history_of(result)[-1] == {"role": "assistant", "content": "Error: Other bad request"}
    assert RELAY_WARNING not in caplog.text


def test_stop_does_not_become_relay_failure(
    scripted: ScriptedChatModel, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes: list[TurnOutcome] = []
    scripted.scripts.append([text_chunk("Partial response"), HANG])

    async def stop_after_first_delta(chat: ChatState, message: UserMessage, views: TurnViews) -> None:
        streamed = asyncio.Event()
        send_delta = views.answer.stream

        async def signal_delta(text: str) -> None:
            await send_delta(text)
            streamed.set()

        monkeypatch.setattr(views.answer, "stream", signal_delta)
        task = asyncio.create_task(capture_outcomes(outcomes)(chat, message, views))
        try:
            await asyncio.wait_for(streamed.wait(), timeout=2)
        finally:
            task.cancel()
            await task

    result = run_one_turn(run_reply=stop_after_first_delta)

    assert result.answer.final == "Partial response"
    assert result.answer.interruption_reason is None
    assert outcomes == [TurnOutcome.CANCELLED]
    assert len(scripted.requests) == 1
    assert RELAY_WARNING not in caplog.text
