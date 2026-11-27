"""Reply timing and outcome contracts for the optional app layer."""

import asyncio
import inspect
import json
from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk

from sillytherin import turn
from sillytherin.agent import stream
from sillytherin.chat.ports import ChatState
from sillytherin.chat_start import HISTORY_KEY, PICKS_KEY
from sillytherin.observability.agent_observer import (
    NativeStageObserver,
    observe_answer_wait,
)
from sillytherin.observability.answer_capture import AnswerCapture
from sillytherin.observability.dtos.reply_trace import (
    RecordingLoss,
    ReplyContent,
    ReplySpan,
    ReplyState,
    ReplyTrace,
    SpanKind,
    SpanOutcome,
)
from sillytherin.observability.reply_trace import (
    MAX_CHILD_SPANS,
    ApprovalWait,
    ReplyRecorder,
    TraceClock,
)
from sillytherin.observability.turn_runner import ObservedTurnRunner
from sillytherin.server.events import AskOpened, ServerEvent
from sillytherin.server.web_chat import OpenPrompts, SocketAsker
from sillytherin.tools import web_search
from sillytherin.tools.approval import RUN_OR_SKIP
from sillytherin.turn import TurnHooks, TurnViews, UserMessage, run_turn
from tests.agent.conftest import ready_chat, run_one_turn
from tests.chat.fakes import FakeAnswerView, FakeAsker, FakeProgressView, in_chat
from tests.scripted_chat_model import (
    HANG,
    ScriptedChatModel,
    text_chunk,
    tool_call_chunk,
)


@dataclass
class TimingHarness:
    traces: list[ReplyTrace] = field(default_factory=list)
    losses: list[RecordingLoss] = field(default_factory=list)
    tick: int = 0

    def monotonic_ns(self) -> int:
        self.tick += 1
        return self.tick

    def offer(self, trace: ReplyTrace) -> None:
        self.traces.append(trace)

    def runner(self, *, manual: bool = False) -> ObservedTurnRunner:
        clock = TraceClock(lambda: 1000, (lambda: self.tick) if manual else self.monotonic_ns)
        return ObservedTurnRunner(run_turn, self, clock, on_loss=self.losses.append)

    def recorder(self) -> ReplyRecorder:
        recorder = ReplyRecorder(TraceClock(lambda: 1000, lambda: self.tick))
        recorder.start_reply()
        return recorder


def duration(span: ReplySpan) -> int:
    return span.end_time_unix_nano - span.start_time_unix_nano


def test_reply_trace_groups_model_and_tool_durations(
    scripted: ScriptedChatModel, searches: list[dict], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-only")
    timing = TimingHarness()
    runner = ObservedTurnRunner(run_turn, timing, TraceClock(lambda: 1000, timing.monotonic_ns))
    scripted.scripts = [
        [
            tool_call_chunk("web_search", {"query": "one"}, call_id="first"),
            tool_call_chunk("web_search", {"query": "two"}, call_id="second", index=1),
        ],
        [text_chunk("done")],
    ]
    assert run_one_turn(run_reply=runner).answer.final == "done"
    assert len(searches) == 2
    assert scripted.tracing_seen == [False, False]
    trace, = timing.traces
    assert trace.root.start_time_unix_nano == 1000
    assert sorted(span.kind for span in trace.children) == [SpanKind.MODEL] * 2 + [SpanKind.TOOL] * 2
    assert all(
        1000 < span.start_time_unix_nano < span.end_time_unix_nano < trace.root.end_time_unix_nano
        for span in trace.children
    )


def test_reply_without_tools_keeps_its_model_span(scripted: ScriptedChatModel) -> None:
    timing = TimingHarness()
    assert run_one_turn(run_reply=timing.runner()).answer.final == "ok"
    assert [span.kind for span in timing.traces[0].children] == [SpanKind.MODEL]


def test_reply_timing_survives_wall_clock_change() -> None:
    clock = {"epoch": 1000, "tick": 10}
    recorder = ReplyRecorder(TraceClock(lambda: clock["epoch"], lambda: clock["tick"]))
    recorder.start_reply()
    clock.update(epoch=300, tick=25)
    trace = recorder.finish_reply(SpanOutcome.SUCCEEDED)
    assert trace is not None and trace.root.end_time_unix_nano == 1015


def test_concurrent_chats_keep_separate_reply_traces() -> None:
    timing = TimingHarness()

    async def exercise() -> None:
        arrivals: asyncio.Queue[None] = asyncio.Queue()
        release = asyncio.Event()

        async def model_turn(chat: ChatState, message: UserMessage, views: TurnViews, *, hooks: TurnHooks) -> None:
            callback, = hooks.callbacks
            assert isinstance(callback, NativeStageObserver)
            run_id = uuid4()
            await callback.on_chat_model_start({}, [], run_id=run_id)
            arrivals.put_nowait(None)
            await release.wait()
            await callback.on_llm_end(None, run_id=run_id)
            hooks.on_completion(turn.TurnOutcome.SUCCEEDED)

        runner = ObservedTurnRunner(model_turn, timing, TraceClock(lambda: 1000, timing.monotonic_ns))
        views = TurnViews(FakeAnswerView(), FakeProgressView(), FakeAsker(None), None)
        tasks = [asyncio.create_task(runner(ready_chat(), UserMessage(text, "", ""), views)) for text in ("private-a", "private-b")]
        for _ in tasks:
            await asyncio.wait_for(arrivals.get(), timeout=2)
        release.set()
        await asyncio.gather(*tasks)

    asyncio.run(exercise())
    assert len(timing.traces) == 2
    assert all(len(trace.children) == 1 for trace in timing.traces)
    assert "private-" not in repr(timing.traces)


class ApprovingSender:
    def __init__(self, timing: TimingHarness, prompts: OpenPrompts) -> None:
        self.timing = timing
        self.prompts = prompts

    async def send(self, event: ServerEvent) -> None:
        if isinstance(event, AskOpened):
            asyncio.get_running_loop().call_soon(self.approve, event.ask_id)

    def approve(self, ask_id: str) -> None:
        self.timing.tick += 10
        assert self.prompts.answer(ask_id, "run") == "accepted"


def test_approval_wait_counts_toward_reply_but_not_tool_duration(
    scripted: ScriptedChatModel, monkeypatch: pytest.MonkeyPatch,
) -> None:
    timing, prompts = TimingHarness(), OpenPrompts()
    asker = SocketAsker(ApprovingSender(timing, prompts), prompts, answer_wait=observe_answer_wait)

    async def tool(query: str, runtime: object) -> tuple[str, str]:
        timing.tick += 1
        assert await asker.ask_choice("private", RUN_OR_SKIP, 1) == "run"
        timing.tick += 1
        return query, "done"

    monkeypatch.setattr(web_search.web_search, "coroutine", tool)
    scripted.scripts = [[tool_call_chunk("web_search", {"query": "query"})], [text_chunk("done")]]
    assert run_one_turn(run_reply=timing.runner(manual=True)).answer.final == "done"
    trace, = timing.traces
    tool_span, = (span for span in trace.children if span.kind is SpanKind.TOOL)
    assert duration(trace.root) == 12
    assert duration(tool_span) == 2


def test_parallel_tool_approval_wait_only_affects_its_own_duration() -> None:
    timing = TimingHarness()
    recorder = timing.recorder()
    waiting, parallel = uuid4(), uuid4()
    approval = ApprovalWait(recorder)
    recorder.start_stage(waiting, SpanKind.TOOL, "tool.notebook_run", approval_wait=approval)
    with approval.observe():
        recorder.start_stage(parallel, SpanKind.TOOL, "tool.web_search")
        timing.tick = 10
        recorder.finish_stage(parallel, SpanOutcome.SUCCEEDED)
    timing.tick = 12
    recorder.finish_stage(waiting, SpanOutcome.SUCCEEDED)
    trace = recorder.finish_reply(SpanOutcome.SUCCEEDED)
    assert trace is not None
    assert {span.name: duration(span) for span in trace.children} == {"tool.web_search": 10, "tool.notebook_run": 2}


def test_multiple_approval_waits_are_excluded_from_one_tool_duration() -> None:
    timing = TimingHarness()
    recorder = timing.recorder()
    run_id, approval = uuid4(), ApprovalWait(recorder)
    recorder.start_stage(run_id, SpanKind.TOOL, "tool.dataset_download", approval_wait=approval)
    for _ in range(2):
        timing.tick += 2
        with approval.observe():
            timing.tick += 10
    recorder.finish_stage(run_id, SpanOutcome.SUCCEEDED)
    trace = recorder.finish_reply(SpanOutcome.SUCCEEDED)
    assert trace is not None and duration(trace.root) == 24
    assert [duration(span) for span in trace.children] == [4]


def test_unavailable_tool_does_not_create_execution_span(scripted: ScriptedChatModel) -> None:
    timing = TimingHarness()
    scripted.scripts = [[tool_call_chunk("unknown_tool", {})], [text_chunk("done")]]
    assert run_one_turn(run_reply=timing.runner()).answer.final == "done"
    assert len(timing.traces[0].children) == 2
    assert all(span.kind is SpanKind.MODEL for span in timing.traces[0].children)


def test_reply_over_stage_limit_drops_trace_without_changing_reply() -> None:
    timing = TimingHarness()

    async def many_stages(chat: ChatState, message: UserMessage, views: TurnViews, *, hooks: TurnHooks) -> None:
        callback, = hooks.callbacks
        assert isinstance(callback, NativeStageObserver)
        for _ in range(MAX_CHILD_SPANS + 1):
            run_id = uuid4()
            await callback.on_chat_model_start({}, [], run_id=run_id)
            await callback.on_llm_end(None, run_id=run_id)
        await views.answer.finish("still done")
        hooks.on_completion(turn.TurnOutcome.SUCCEEDED)

    runner = ObservedTurnRunner(
        many_stages, timing, TraceClock(lambda: 1000, timing.monotonic_ns), on_loss=timing.losses.append,
    )
    assert run_one_turn(run_reply=runner).answer.final == "still done"
    assert not timing.traces and timing.losses == [RecordingLoss.STAGE_LIMIT]


def test_trace_observer_failure_does_not_interrupt_the_reply(
    scripted: ScriptedChatModel, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    timing = TimingHarness()

    def fail_timing(*args: object, **kwargs: object) -> None:
        raise RuntimeError("private-timing-error")

    monkeypatch.setattr(ReplyRecorder, "start_stage", fail_timing)
    assert run_one_turn(run_reply=timing.runner()).answer.final == "ok"
    assert not timing.traces and timing.losses == [RecordingLoss.OBSERVER_FAILED]
    assert "private-timing-error" not in caplog.text


def test_completed_reply_records_success(scripted: ScriptedChatModel) -> None:
    timing = TimingHarness()
    assert run_one_turn(run_reply=timing.runner()).answer.final == "ok"
    assert timing.traces[0].root.outcome is SpanOutcome.SUCCEEDED


def test_model_failure_preserves_chat_error_and_marks_trace_failed(scripted: ScriptedChatModel) -> None:
    timing = TimingHarness()
    scripted.scripts = [[None]]  # The invalid chunk fails inside the scripted model's stream.
    result = run_one_turn(run_reply=timing.runner())
    assert result.answer.final.startswith("Error:")
    assert [message["role"] for message in result.chat.get(HISTORY_KEY)] == ["system", "user", "assistant"]
    trace, = timing.traces
    assert trace.root.outcome is trace.children[0].outcome is SpanOutcome.FAILED
    assert "NoneType" not in repr(trace)


def test_setup_failure_preserves_exception_and_marks_trace_failed(
    catalog: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    timing, chat = TimingHarness(), ready_chat()

    async def fail_setup(*args: object) -> None:
        raise RuntimeError("setup marker")

    monkeypatch.setattr(turn, "model_request", fail_setup)
    with pytest.raises(RuntimeError, match="setup marker"):
        run_one_turn(chat, run_reply=timing.runner())
    assert [message["role"] for message in chat.get(HISTORY_KEY)] == ["system", "user"]
    assert not timing.traces[0].children
    assert timing.traces[0].root.outcome is SpanOutcome.FAILED


def test_answer_publish_failure_marks_trace_failed(
    scripted: ScriptedChatModel, monkeypatch: pytest.MonkeyPatch,
) -> None:
    timing, chat = TimingHarness(), ready_chat()

    async def fail_finish(*args: object) -> None:
        raise RuntimeError("view marker")

    monkeypatch.setattr(FakeAnswerView, "finish", fail_finish)
    with pytest.raises(RuntimeError, match="view marker"):
        run_one_turn(chat, run_reply=timing.runner())
    assert chat.get(HISTORY_KEY)[-1]["role"] == "user"
    assert timing.traces[0].root.outcome is SpanOutcome.FAILED


def test_stop_preserves_partial_reply_and_closes_pending_spans(scripted: ScriptedChatModel) -> None:
    timing = TimingHarness()
    scripted.scripts = [[text_chunk("partial"), HANG]]

    async def stop() -> FakeAnswerView:
        started = asyncio.Event()

        class SignallingAnswer(FakeAnswerView):
            async def stream(self, text: str) -> None:
                await super().stream(text)
                started.set()

        chat, answer = ready_chat(), SignallingAnswer()
        picks = chat.get(PICKS_KEY)
        views = TurnViews(answer, FakeProgressView(), FakeAsker(None), None)
        with in_chat(chat):
            task = asyncio.create_task(timing.runner()(chat, UserMessage("hi", picks.model, picks.effort), views))
            await asyncio.wait_for(started.wait(), timeout=2)
            task.cancel()
            await asyncio.wait_for(task, timeout=2)
        return answer

    assert asyncio.run(stop()).final == "partial"
    trace, = timing.traces
    child, = trace.children
    assert child.outcome is trace.root.outcome is SpanOutcome.CANCELLED


def test_recovered_tool_failure_does_not_fail_reply_trace(
    scripted: ScriptedChatModel, monkeypatch: pytest.MonkeyPatch,
) -> None:
    timing = TimingHarness()

    async def fail_tool(query: str, runtime: object) -> tuple[str, str]:
        raise RuntimeError("tool secret")

    monkeypatch.setattr(web_search.web_search, "coroutine", fail_tool)
    scripted.scripts = [[tool_call_chunk("web_search", {"query": "query"})], [text_chunk("recovered")]]
    assert run_one_turn(run_reply=timing.runner()).answer.final == "recovered"
    trace, = timing.traces
    assert trace.root.outcome is SpanOutcome.SUCCEEDED
    assert [span.outcome for span in trace.children if span.kind is SpanKind.TOOL] == [SpanOutcome.FAILED]


def test_missing_terminal_callback_closes_child_as_interrupted() -> None:
    timing = TimingHarness()
    recorder = timing.recorder()
    run_id = uuid4()
    recorder.start_stage(run_id, SpanKind.MODEL, "model.call")
    timing.tick = 8
    trace = recorder.finish_reply(SpanOutcome.SUCCEEDED)
    recorder.finish_stage(run_id, SpanOutcome.SUCCEEDED)
    assert trace is not None and trace.root.outcome is SpanOutcome.SUCCEEDED
    assert trace.children[0].outcome is SpanOutcome.INTERRUPTED
    assert recorder.finish_reply(SpanOutcome.SUCCEEDED) is None


def test_stop_during_approval_excludes_partial_wait_from_tool() -> None:
    timing = TimingHarness()
    recorder = timing.recorder()
    run_id, approval = uuid4(), ApprovalWait(recorder)
    recorder.start_stage(run_id, SpanKind.TOOL, "tool.notebook_run", approval_wait=approval)
    timing.tick = 2
    with pytest.raises(asyncio.CancelledError), approval.observe():
        timing.tick = 12
        raise asyncio.CancelledError
    trace = recorder.finish_reply(SpanOutcome.CANCELLED)
    assert trace is not None and duration(trace.root) == 12
    assert duration(trace.children[0]) == 2
    assert trace.children[0].outcome is SpanOutcome.CANCELLED


def test_core_turn_and_stream_have_no_observability_dependency(scripted: ScriptedChatModel) -> None:
    for module in (turn, stream):
        assert "observability" not in inspect.getsource(module)
    assert run_one_turn().answer.final == "ok"


def test_core_turn_does_not_enable_export_from_environment(
    scripted: ScriptedChatModel, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {
        "SILLYTHERIN_LANGFUSE_ENABLED": "true", "SILLYTHERIN_LANGFUSE_REGION": "eu",
        "LANGFUSE_PUBLIC_KEY": "public-test", "LANGFUSE_SECRET_KEY": "secret-test",
    }.items():
        monkeypatch.setenv(name, value)

    def forbid_observer(*args: object, **kwargs: object) -> None:
        pytest.fail("A direct core turn must not construct an observer")

    monkeypatch.setattr(NativeStageObserver, "__init__", forbid_observer)
    assert run_one_turn().answer.final == "ok"


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


def test_restart_trace_uses_delivered_restart_reply() -> None:
    from sillytherin.turn import RESTART_REPLY
    content, losses, _ = capture_reply(final=RESTART_REPLY)
    assert content.reply == RESTART_REPLY and not losses


def test_empty_model_trace_matches_displayed_fallback() -> None:
    from sillytherin.turn import EMPTY_ANSWER
    content, _, _ = capture_reply(chunks=(), final=EMPTY_ANSWER)
    assert content.reply == EMPTY_ANSWER and content.reply_state is ReplyState.COMPLETE


def test_setup_failure_exports_input_without_output() -> None:
    content, _, _ = capture_reply(chunks=(), outcome=SpanOutcome.FAILED)
    assert content.message == "question" and content.reply is None
    assert content.reply_state is ReplyState.MISSING


def test_interrupted_delta_is_not_part_of_partial_output() -> None:
    capture = AnswerCapture(ReceiptAnswer((True, False, True)), lambda loss: pytest.fail(str(loss)))
    async def deliver() -> None:
        await capture.stream("one")
        await capture.stream("dropped")
        await capture.stream("later")
    asyncio.run(deliver())
    assert capture.seal("input", SpanOutcome.CANCELLED).reply == "one"


def test_failed_final_publication_keeps_only_delivered_prefix() -> None:
    class BrokenAnswer(ReceiptAnswer):
        async def finish(self, text: str) -> bool:
            raise RuntimeError("send failed")
    capture = AnswerCapture(BrokenAnswer(), lambda loss: pytest.fail(str(loss)))
    async def deliver() -> None:
        await capture.stream("prefix")
        with pytest.raises(RuntimeError, match="send failed"):
            await capture.finish("unsent")
    asyncio.run(deliver())
    assert capture.seal("input", SpanOutcome.FAILED).reply == "prefix"


def test_answer_capture_failure_does_not_change_delivery() -> None:
    content, losses, answer = capture_reply(chunks=("\ud800",))
    assert content is None and losses == [RecordingLoss.OBSERVER_FAILED]
    assert answer.delivered == ["\ud800", "final"]


def test_model_children_pair_round_messages_and_responses(
    scripted: ScriptedChatModel, searches: list[dict],
) -> None:
    timing = TimingHarness()
    scripted.scripts = [
        [tool_call_chunk("web_search", {"query": "Find the source"})],
        [text_chunk("The source says hello.")],
    ]

    reply = run_one_turn(text="hello", run_reply=timing.runner())

    assert reply.answer.final == "The source says hello."
    assert searches == [{"query": "Find the source"}]
    trace, = timing.traces
    model_spans = [span for span in trace.children if span.kind is SpanKind.MODEL]
    assert len(model_spans) == 2
    first_exchange, second_exchange = [span.model_exchange for span in model_spans]
    assert first_exchange is not None and second_exchange is not None
    first_input = json.loads(first_exchange.input_json)
    second_input = json.loads(second_exchange.input_json)
    assert first_input[-1] == {"role": "user", "content": "hello"}
    assert second_input[-1]["role"] == "tool"
    assert json.loads(first_exchange.output_json or "{}")["messages"][0]["tool_calls"][0]["name"] == "web_search"
    assert json.loads(second_exchange.output_json or "{}")["messages"][0]["content"] == "The source says hello."


def test_model_exchange_preserves_structured_content_and_returned_reasoning() -> None:
    # Given the shared valid setup, Use structured messages and returned reasoning.
    # When the model exchange is observed and exported.
    # Then All content blocks and supported reasoning fields remain structured and ordered.
    # Demonstrates Full callback-visible content.
    pytest.fail("TODO: implement this behavior")


def test_model_exchange_preserves_empty_completed_message() -> None:
    # Given the shared valid setup, Last model call yields a real empty result.
    # When the model exchange is observed and exported.
    # Then Completed empty assistant message. Chat may deliver its existing fallback.
    # Demonstrates Empty model output is observable.
    pytest.fail("TODO: implement this behavior")


def test_overlapping_model_runs_keep_each_runs_content() -> None:
    # Given the shared valid setup, Finish overlapping runs in reverse order across two turns, including a repeated UUID across recorders.
    # When the model exchange is observed and exported.
    # Then Each child owns its original input/output, and representations exclude sentinels.
    # Demonstrates Run and turn isolation.
    pytest.fail("TODO: implement this behavior")


def test_model_exchange_preserves_raw_tool_fragments_after_argument_recovery() -> None:
    # Given the shared valid setup, Split an invalid argument across chunks.
    # When the model exchange is observed and exported.
    # Then Preserve fragments and any terminal parsed/invalid fields. Tool guard retains its current decision.
    # Demonstrates Observable raw tool syntax.
    pytest.fail("TODO: implement this behavior")


def test_model_callbacks_for_unknown_run_are_ignored() -> None:
    # Given the shared valid setup, Inject a chunk or terminal event for an unstarted run.
    # When the model exchange is observed and exported.
    # Then No extra child and no change to known runs or chat.
    # Demonstrates Callback ownership.
    pytest.fail("TODO: implement this behavior")


def test_duplicate_and_late_model_callbacks_preserve_sealed_content() -> None:
    # Given the shared valid setup, Replay callbacks with different content.
    # When the model exchange is observed and exported.
    # Then First start and terminal values win. Post-seal events change neither snapshot nor count.
    # Demonstrates Stable lifecycle.
    pytest.fail("TODO: implement this behavior")


def test_provider_failure_exports_only_received_model_fragments() -> None:
    timing = TimingHarness()

    async def interrupted_turn(
        chat: ChatState, message: UserMessage, views: TurnViews, *, hooks: TurnHooks,
    ) -> None:
        observer, = hooks.callbacks
        run_id = uuid4()
        await observer.on_chat_model_start({}, [[HumanMessage(content="prompt")]], run_id=run_id)
        chunks = (
            AIMessageChunk(content="", additional_kwargs={"reasoning_content": "a thought"}),
            text_chunk("partial"),
            tool_call_chunk("web_search", "{"),
        )
        for chunk in chunks:
            await observer.on_llm_new_token("", chunk=ChatGenerationChunk(message=chunk), run_id=run_id)
        await observer.on_llm_error(RuntimeError("private exception"), run_id=run_id)
        await views.answer.finish("The request failed")
        hooks.on_completion(turn.TurnOutcome.FAILED)

    runner = ObservedTurnRunner(interrupted_turn, timing, TraceClock(lambda: 1000, timing.monotonic_ns))
    assert run_one_turn(run_reply=runner).answer.final == "The request failed"
    trace, = timing.traces
    child, = trace.children
    assert child.outcome is SpanOutcome.FAILED
    exchange = child.model_exchange
    assert exchange is not None
    assert json.loads(exchange.input_json) == [{"role": "user", "content": "prompt"}]
    assert exchange.output_json is not None
    reasoning, text, tool = json.loads(exchange.output_json)["chunks"]
    assert reasoning["reasoning_content"] == "a thought"
    assert text["content"] == "partial"
    assert tool["tool_call_fragments"][0]["args"] == "{"
    assert "private exception" not in exchange.output_json


def test_stopping_model_stream_keeps_cancelled_partial_exchange() -> None:
    # Given the shared valid setup, Replace provider failure with socket Stop.
    # When the model exchange is observed and exported.
    # Then Child is cancelled and partial. The socket delivers its normal stopped reply.
    # Demonstrates Real cancellation.
    pytest.fail("TODO: implement this behavior")


def test_unfinished_model_callback_is_interrupted_at_turn_seal() -> None:
    # Given the shared valid setup, Omit terminal callback.
    # When the model exchange is observed and exported.
    # Then Open child ends at turn seal with `interrupted` and exact buffered content.
    # Demonstrates Abandoned run.
    pytest.fail("TODO: implement this behavior")


def test_error_response_content_is_used_when_no_stream_chunks_arrived() -> None:
    # Given the shared valid setup, Deliver content only in the error callback result.
    # When the model exchange is observed and exported.
    # Then Partial contains the actual supplied messages and no diagnostic fields.
    # Demonstrates Terminal error payload fallback.
    pytest.fail("TODO: implement this behavior")


def test_failure_before_output_omits_model_output() -> None:
    # Given the shared valid setup, Fail before any content.
    # When the model exchange is observed and exported.
    # Then Input is present and output omitted with `missing`.
    # Demonstrates No fabricated output.
    pytest.fail("TODO: implement this behavior")


def test_error_metadata_does_not_become_model_output() -> None:
    # Given the shared valid setup, Provide only an empty HTTP error generation.
    # When the model exchange is observed and exported.
    # Then Output omitted. Markers for body, headers and exception stay out of exchange, wire and logs. Existing chat error behavior remains.
    # Demonstrates Safe missing output.
    pytest.fail("TODO: implement this behavior")


def test_model_capture_accepts_exact_utf8_budget() -> None:
    # Given the shared valid setup, —.
    # When the model exchange is observed and exported.
    # Then Exact-byte payload is retained without truncating.
    # Demonstrates Inclusive capture limit.
    pytest.fail("TODO: implement this behavior")


def test_small_model_result_replaces_oversized_pending_text() -> None:
    # Given the shared valid setup, Overflow only speculative text, then supply a legal terminal result.
    # When the model exchange is observed and exported.
    # Then Complete output uses exact terminal result and retains any required in-budget tool fragments.
    # Demonstrates Bounded scratch storage with success recovery.
    pytest.fail("TODO: implement this behavior")


def test_model_capture_drops_turn_when_rounds_exceed_total_budget() -> None:
    # Given the shared valid setup, Add one byte across otherwise valid model rounds.
    # When the model exchange is observed and exported.
    # Then No queued partial trace. Safe `invalid_trace`. Model requests retain their full context and chat and history match the same run with tracing disabled.
    # Demonstrates Combined tracing budget across children and independent model context.
    pytest.fail("TODO: implement this behavior")


def test_interrupted_model_capture_drops_overflowing_prefix() -> None:
    # Given the shared valid setup, In an interrupted variant, make the retained stream exceed its scratch allowance.
    # When the model exchange is observed and exported.
    # Then No trace or truncated prefix sent. Existing stopped reply continues.
    # Demonstrates Honest incomplete output under pressure.
    pytest.fail("TODO: implement this behavior")


def test_model_capture_drops_trace_when_required_tool_fragments_overflow() -> None:
    # Given the shared valid setup, Overflow raw tool fragments while the terminal result fits.
    # When the model exchange is observed and exported.
    # Then No trace and safe loss. Tool guard and chat retain native behavior.
    # Demonstrates Required raw evidence cannot silently disappear.
    pytest.fail("TODO: implement this behavior")
