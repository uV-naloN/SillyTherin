"""An optional app-layer decorator for the normal turn runner."""

from asyncio import CancelledError
from collections.abc import Callable
from typing import Protocol

from sillytherin.chat.ports import ChatState
from sillytherin.observability.agent_observer import (
    NativeStageObserver,
    ToolWaitMiddleware,
)
from sillytherin.observability.reply_trace import (
    RecordingLoss,
    ReplyRecorder,
    ReplyTraceSink,
    SpanOutcome,
    TraceClock,
)
from sillytherin.turn import TurnHooks, TurnOutcome, TurnViews, UserMessage


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

        def completed(turn_outcome: TurnOutcome) -> None:
            nonlocal outcome
            outcome = SpanOutcome(turn_outcome.value)

        try:
            recorder.start_reply()
            hooks = build_hooks(recorder, completed)
        except Exception:  # noqa: BLE001 - chat still runs without observation
            recorder.invalidate()
            hooks = TurnHooks()
        try:
            await self._run_turn(chat, message, views, hooks=hooks)
        except CancelledError:
            outcome = SpanOutcome.CANCELLED
            raise
        finally:
            self.offer_recording(recorder, outcome)

    def offer_recording(self, recorder: ReplyRecorder, outcome: SpanOutcome) -> None:
        try:
            trace = recorder.finish_reply(outcome)
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
            try:
                self._on_loss(loss)
            except Exception:  # noqa: BLE001 - local diagnostics must not affect the reply
                return


def build_hooks(recorder: ReplyRecorder, on_completion: Callable[[TurnOutcome], None]) -> TurnHooks:
    return TurnHooks(
        callbacks=(NativeStageObserver(recorder),),
        middleware=(ToolWaitMiddleware(recorder),),
        on_completion=on_completion,
    )
