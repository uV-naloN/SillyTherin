"""One chat turn: the user's message in, the model's streamed answer and tool steps out."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelResponse
from langchain.agents.middleware import ModelRequest as AgentCall
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph

from sillytherin.agent.history import from_langchain, to_langchain
from sillytherin.agent.model import (
    GuardModelToolCalls,
    ToolArgumentStream,
    build_chat_model,
    is_openrouter_relay_failure,
)
from sillytherin.agent.round_limit import RoundLimit
from sillytherin.agent.stream import stream_agent
from sillytherin.agent.tool_errors import NotebookBatchGuard, ToolErrorBoundary
from sillytherin.chat.ports import (
    AnswerView,
    Asker,
    ChatState,
    ProgressView,
    SettingsView,
)
from sillytherin.chat_start import (
    HISTORY_KEY,
    PICKS_KEY,
    SETTINGS_KEY,
    current_picks,
    follow_model_change,
    picker_choices,
)
from sillytherin.config import TOOL_MAX_ROUNDS
from sillytherin.contracts.chat import (
    ModelRequest,
    Picks,
    ReplyInterruptionReason,
    UserMessage,
)
from sillytherin.llm import model_params
from sillytherin.modes import EFFORT_MODE_ID, MODEL_MODE_ID, chosen_picks
from sillytherin.progress import Progress
from sillytherin.settings import reasoning_body, usable_settings
from sillytherin.tools import (
    chosen_tools,
    dataset_delete,
    dataset_describe,
    dataset_download,
    notebook_run,
)
from sillytherin.tools.shared.services import ToolServices
from sillytherin.workspace import actions
from sillytherin.workspace.datasets.links import find_pasted_links
from sillytherin.workspace.service import NotebookRunOutcome

EMPTY_ANSWER = "(The model returned an empty response.)"
OPENROUTER_RELAY_NOTICE = "OpenRouter couldn’t route this request. Try sending your message again in a moment."
log = logging.getLogger("uvicorn.error").getChild("turn")


RESTART_REPLY = "The notebook was restarted, so earlier results are gone. Press Run all in the notebook, then ask me again."


def notebook_restart_required(messages: Sequence[BaseMessage]) -> bool:
    return any(
        isinstance(message, ToolMessage) and message.artifact is NotebookRunOutcome.RESTART_REQUIRED
        for message in messages
    )


class StopOnNotebookRestart(AgentMiddleware):
    async def awrap_model_call(
        self, request: AgentCall, handler: Callable[[AgentCall], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        if notebook_restart_required(request.messages):
            return ModelResponse(result=[AIMessage(content=RESTART_REPLY)])
        return await handler(request)


@dataclass(frozen=True)
class TurnViews:
    """Where one turn shows its work."""

    answer: AnswerView
    progress: ProgressView
    asker: Asker  # Run / Skip style prompts from the tools
    settings: SettingsView  # re-sent when the message switches to another model


TurnRunner = Callable[[ChatState, UserMessage, TurnViews], Awaitable[None]]


class TurnOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


def ignore_turn_outcome(_outcome: TurnOutcome) -> None:
    """Default completion listener for callers that only need the reply."""


@dataclass(frozen=True)
class TurnHooks:
    callbacks: tuple[BaseCallbackHandler, ...] = ()
    middleware: tuple[AgentMiddleware, ...] = ()
    on_completion: Callable[[TurnOutcome], None] = ignore_turn_outcome


class CollectedAnswer:
    """Streams to the chat and keeps the text, so a stopped turn still knows what it wrote."""

    def __init__(self, view: AnswerView) -> None:
        self.view = view
        self.text = ""

    async def stream(self, text: str) -> None:
        self.text += text
        await self.view.stream(text)

    async def finish(self, text: str) -> None:
        await self.view.finish(text)


def describe_openrouter_relay_failure(streamed_text: str) -> tuple[str, ReplyInterruptionReason | None]:
    if streamed_text:
        return streamed_text, "openrouter_relay_failed"
    return OPENROUTER_RELAY_NOTICE, None


async def run_turn(
    chat: ChatState,
    message: UserMessage,
    views: TurnViews,
    *,
    hooks: TurnHooks = TurnHooks(),
) -> None:
    """Answer one message with the agent, which calls tools round by round up to the round limit.

    The chat's history gains the message and the answer; a turn that fails or is
    stopped keeps none of its half-finished tool messages.
    """
    history: list = chat.get(HISTORY_KEY)
    history.append({"role": "user", "content": message.text})
    turn_start = len(history)
    request = await model_request(chat, await apply_picks(chat, message, views.settings))
    # Background downloads that finished since the last turn: told to the model once, never kept in history
    note = actions.datasets.take_notes(chat.chat_id)
    turn_notes = [{"role": "system", "content": note}] if note else []
    answer = CollectedAnswer(views.answer)
    progress = Progress(views.progress)
    tool_arguments = ToolArgumentStream()
    outcome = TurnOutcome.SUCCEEDED
    interruption_reason: ReplyInterruptionReason | None = None
    try:
        agent = build_agent(request, tool_arguments, extra_middleware=hooks.middleware)
        services = build_tool_services(chat)
        added = await stream_agent(
            agent, to_langchain(history + turn_notes), answer, progress, tool_arguments, services,
            extra_callbacks=hooks.callbacks,
        )
        history.extend(from_langchain(tool_round_messages(added)))
        final_text = RESTART_REPLY if notebook_restart_required(added) else answer.text
    except asyncio.CancelledError:
        # Stop: keep what was written, drop the half-finished tool turns
        outcome = TurnOutcome.CANCELLED
        del history[turn_start:]
        final_text = answer.text
    except Exception as error:  # noqa: BLE001 - any model or tool failure ends the turn with its reason
        outcome = TurnOutcome.FAILED
        del history[turn_start:]
        if is_openrouter_relay_failure(error):
            log.warning("OpenRouter chat request failed reason=relay_failed http_status=400")
            final_text, interruption_reason = describe_openrouter_relay_failure(answer.text)
        else:
            final_text = f"Error: {error}"
    finally:
        await progress.finish()
    final_text = final_text or EMPTY_ANSWER
    assistant_message = {"role": "assistant", "content": final_text}
    if interruption_reason is None:
        await views.answer.finish(final_text)
    else:
        await views.answer.finish(final_text, interruption_reason=interruption_reason)
        assistant_message["interruption_reason"] = interruption_reason
    history.append(assistant_message)
    hooks.on_completion(outcome)


async def apply_picks(chat: ChatState, message: UserMessage, settings_view: SettingsView) -> Picks:
    """The message's picks become the chat's; a new model re-sends the pickers and Settings."""
    previous = current_picks(chat)
    sent = {MODEL_MODE_ID: message.model, EFFORT_MODE_ID: message.effort}
    picks = chosen_picks(sent, await picker_choices(), previous)
    chat.set(PICKS_KEY, picks)
    if picks.model != previous.model:
        await follow_model_change(chat, settings_view, actions.kaggle_account.status())
    return picks


async def model_request(chat: ChatState, picks: Picks) -> ModelRequest:
    params = await model_params(picks.model)
    settings = usable_settings(chat.get(SETTINGS_KEY) or {}, params)
    thinking = reasoning_body(
        effort=picks.effort,
        show_thinking=settings.get("show_thinking", True),
        can_reason="reasoning" in params,
    )
    return ModelRequest(model=picks.model, tools=chosen_tools(settings), thinking=thinking)


def build_tool_services(chat: ChatState) -> ToolServices:
    """The app's services, this chat's project and its prompts, handed to every tool of this message."""
    return ToolServices(
        notebook=actions.service,
        datasets=actions.datasets,
        project_id=actions.session_project(chat),
        chat_id=chat.chat_id,
        pasted_links=find_pasted_links(chat.get(HISTORY_KEY) or []),
        approve_run=notebook_run.ask_approval,
        approve_download=dataset_download.ask_approval,
        confirm_retry=dataset_download.ask_retry,
        approve_delete=dataset_delete.ask_approval,
        looked=dataset_describe.looked,
    )


def build_agent(
    request: ModelRequest,
    tool_arguments: ToolArgumentStream,
    *,
    extra_middleware: tuple[AgentMiddleware, ...] = (),
) -> CompiledStateGraph:
    middleware = [
        RoundLimit(TOOL_MAX_ROUNDS),
        GuardModelToolCalls(tool_arguments, TOOL_MAX_ROUNDS),
        ToolErrorBoundary(),
        NotebookBatchGuard(),
        StopOnNotebookRestart(),
    ]
    return create_agent(
        build_chat_model(request), tools=request.tools,
        middleware=[*extra_middleware, *middleware], context_schema=ToolServices,
    )


def tool_round_messages(added: list[BaseMessage]) -> list[BaseMessage]:
    """The tool calls and their results; the answer itself is stored as the full streamed text."""
    return [m for m in added if isinstance(m, ToolMessage) or (isinstance(m, AIMessage) and m.tool_calls)]
