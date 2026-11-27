"""An optional app-layer decorator for the normal turn runner."""

from asyncio import CancelledError
from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

from sillytherin.best_effort import best_effort
from sillytherin.chat.ports import ChatState
from sillytherin.contracts.chat import UserMessage
from sillytherin.observability.agent_observer import (
    NativeStageObserver,
    ToolWaitMiddleware,
)
from sillytherin.observability.answer_capture import AnswerCapture
from sillytherin.observability.dtos.reply_trace import (
    RecordingLoss,
    ReplyContent,
    SpanOutcome,
)
from sillytherin.observability.reply_trace import (
    ReplyRecorder,
    ReplyTraceSink,
    TraceClock,
)
from sillytherin.turn import TurnHooks, TurnOutcome, TurnViews


class HookedTurnRunner(Protocol):
    async def __call__(
        self, chat: ChatState, message: UserMessage, views: TurnViews, /, *, hooks: TurnHooks
    ) -> None: ...


class ObservedTurnRunner:
    def __init__(
        self, run_turn: HookedTurnRunner, sink: ReplyTraceSink, clock: TraceClock,
        *, on_loss: Callable[[RecordingLoss], None] | None = None,
    ) -> None:
        self._run_turn = run_turn
        self._sink = sink
        self._clock = clock
        self._on_loss = on_loss

    async def __call__(self, chat: ChatState, message: UserMessage, views: TurnViews) -> None:
        recorder = ReplyRecorder(self._clock)
        outcome = SpanOutcome.FAILED
        capture = None

        def completed(turn_outcome: TurnOutcome) -> None:
            nonlocal outcome
            outcome = SpanOutcome(turn_outcome.value)

        try:
            recorder.start_reply()
            hooks = build_hooks(recorder, completed)
            views, capture = self.capture_answer(views, recorder)
        except Exception:  # noqa: BLE001 - chat still runs without observation
            recorder.invalidate()
            hooks = TurnHooks()
        try:
            await self._run_turn(chat, message, views, hooks=hooks)
        except CancelledError:
            outcome = SpanOutcome.CANCELLED
            raise
        finally:
            try:
                content = capture.seal(message.text, outcome) if capture is not None else None
            except Exception:  # noqa: BLE001 - observer failure cannot replace the turn result
                recorder.invalidate()
                content = None
            self.offer_recording(recorder, outcome, content=content)

    def capture_answer(self, views: TurnViews, recorder: ReplyRecorder) -> tuple[TurnViews, AnswerCapture]:
        capture = AnswerCapture(views.answer, recorder.invalidate)
        return replace(views, answer=capture), capture

    def offer_recording(
        self, recorder: ReplyRecorder, outcome: SpanOutcome, *, content: ReplyContent | None = None,
    ) -> None:
        try:
            trace = recorder.finish_reply(outcome, content=content)
            if trace is not None:
                self._sink.offer(trace)
        except Exception:  # noqa: BLE001 - failure must not replace the chat result
            recorder.invalidate()
            self.report_loss(RecordingLoss.OBSERVER_FAILED)
            return
        if recorder.loss is not None:
            self.report_loss(recorder.loss)

    def report_loss(self, loss: RecordingLoss) -> None:
        if self._on_loss is not None:
            with best_effort():
                self._on_loss(loss)


def build_hooks(recorder: ReplyRecorder, on_completion: Callable[[TurnOutcome], None]) -> TurnHooks:
    return TurnHooks(
        callbacks=(NativeStageObserver(recorder),),
        middleware=(ToolWaitMiddleware(recorder),),
        on_completion=on_completion,
    )
