"""Shared pieces for the engine tests: a model catalog and a chat that is ready for a message."""

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from core import llm, turn
from core.chat_start import HISTORY_KEY, PICKS_KEY, SETTINGS_KEY
from core.contracts.chat import ModelInfo, Picks, UserMessage
from core.tools import web_search
from core.turn import TurnRunner, TurnViews, run_turn
from core.workspace import actions
from tests.chat.fakes import (
    FakeAnswerView,
    FakeAsker,
    FakeChatState,
    FakeProgressView,
    in_chat,
)
from tests.scripted_chat_model import ScriptedChatModel

THINKS_AND_TOOLS = "a/b"
TOOLS_ONLY = "c/tools"
PLAIN = "d/plain"
EVERY_SWITCH_ON = {"show_thinking": True, "web_search": True, "notebook": True}


@pytest.fixture
def catalog(monkeypatch) -> None:
    models = {
        THINKS_AND_TOOLS: ModelInfo(id=THINKS_AND_TOOLS, name="A B", params=frozenset({"reasoning", "tools"})),
        TOOLS_ONLY: ModelInfo(id=TOOLS_ONLY, name="Tools", params=frozenset({"tools"})),
        PLAIN: ModelInfo(id=PLAIN, name="Plain", params=frozenset()),
    }
    monkeypatch.setattr(llm, "_catalog", models)


def ready_chat(settings: dict | None = None, model: str = THINKS_AND_TOOLS, effort: str = "high") -> FakeChatState:
    """A chat whose history holds the system prompt, with these picks and Settings."""
    chat = FakeChatState()
    chat.set(HISTORY_KEY, [{"role": "system", "content": "You are a test assistant."}])
    chat.set(PICKS_KEY, Picks(model=model, effort=effort))
    chat.set(SETTINGS_KEY, dict(settings if settings is not None else EVERY_SWITCH_ON))
    return chat


@dataclass
class TurnRun:
    """One turn's views, as the test reads them afterwards."""

    chat: FakeChatState
    answer: FakeAnswerView
    progress: FakeProgressView


@pytest.fixture
def scripted(monkeypatch, catalog) -> ScriptedChatModel:
    """The model every turn gets; no background download notes unless a test adds one."""
    model = ScriptedChatModel()
    monkeypatch.setattr(turn, "build_chat_model", lambda _request: model)
    monkeypatch.setattr(actions, "datasets", SimpleNamespace(take_notes=lambda _chat_id: None), raising=False)
    monkeypatch.setattr(actions, "session_project", lambda _chat: "p1")
    return model


@pytest.fixture
def searches(monkeypatch) -> list[dict]:
    """web_search answers from here instead of the web, and remembers its arguments."""
    seen: list[dict] = []

    async def fake_search(query: str, runtime: object) -> tuple[str, str]:
        seen.append({"query": query})
        return "Result: https://found.example/page", "2 results"

    monkeypatch.setattr(web_search.web_search, "coroutine", fake_search)
    return seen


def run_one_turn(
    chat: FakeChatState | None = None, text: str = "hi", *, run_reply: TurnRunner = run_turn
) -> TurnRun:
    chat = chat or ready_chat()
    picks = chat.get(PICKS_KEY)
    answer, progress = FakeAnswerView(), FakeProgressView()
    views = TurnViews(answer=answer, progress=progress, asker=FakeAsker(answer=None), settings=None)
    with in_chat(chat):
        asyncio.run(run_reply(chat, UserMessage(text=text, model=picks.model, effort=picks.effort), views))
    return TurnRun(chat, answer, progress)


def history_of(run: TurnRun) -> list[dict]:
    return run.chat.get(HISTORY_KEY)
