"""Reply timing and outcome contracts for the optional app layer."""

import asyncio
import inspect
from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from sillytherin import turn
from sillytherin.agent import stream
from sillytherin.chat.ports import ChatState
from sillytherin.chat_start import HISTORY_KEY, PICKS_KEY
from sillytherin.observability.agent_observer import (
    NativeStageObserver,
    observe_answer_wait,
)
from sillytherin.observability.reply_trace import (
    MAX_CHILD_SPANS,
    ApprovalWait,
    RecordingLoss,
    ReplyRecorder,
    ReplySpan,
    ReplyTrace,
    SpanKind,
    SpanOutcome,
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
