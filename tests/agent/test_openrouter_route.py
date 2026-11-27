"""The chat client and model picker use the configured OpenRouter address."""

import asyncio

import pytest

from sillytherin import llm
from sillytherin.agent import model as model_module
from sillytherin.agent.model import APP_TITLE, build_chat_model
from sillytherin.contracts.chat import ModelRequest
from tests.agent.conftest import THINKS_AND_TOOLS
from tests.network_fakes import fake_openrouter

SYNTHETIC_KEY = "synthetic-test-key"


def test_chat_model_calls_configured_openrouter_address(monkeypatch: pytest.MonkeyPatch) -> None:
    with fake_openrouter() as openrouter:
        monkeypatch.setattr(model_module, "OPENROUTER_URL", openrouter.base_url)
        monkeypatch.setattr(model_module, "OPENROUTER_API_KEY", SYNTHETIC_KEY)
        chat_model = build_chat_model(ModelRequest(THINKS_AND_TOOLS, [], {}))

        async def collect_reply() -> None:
            async for _chunk in chat_model.astream("hello"):
                pass

        asyncio.run(collect_reply())

    [request] = openrouter.requests
    assert (request.method, request.path) == ("POST", "/api/v1/chat/completions")
    assert request.headers["x-title"] == APP_TITLE
    assert "http-referer" not in request.headers


def test_catalog_fetches_models_from_configured_openrouter_address(monkeypatch: pytest.MonkeyPatch) -> None:
    models = [{"id": "x/y", "name": "X: Y", "architecture": {"output_modalities": ["text"]}}]
    monkeypatch.setattr(llm, "_catalog", None)
    with fake_openrouter(models=models) as openrouter:
        monkeypatch.setattr(llm, "OPENROUTER_URL", openrouter.base_url)
        catalog = asyncio.run(llm.model_catalog())

    assert list(catalog) == ["x/y"]
    assert [request.path for request in openrouter.requests] == ["/api/v1/models"]
