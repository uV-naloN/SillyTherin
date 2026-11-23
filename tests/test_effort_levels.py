"""Each model offers only the Effort levels OpenRouter says it supports."""

import asyncio

from sillytherin import llm
from sillytherin.chat_start import pickers_for
from sillytherin.config import check_levels
from sillytherin.modes import (
    EFFORT_MODE_ID,
    MODEL_MODE_ID,
    PickerChoices,
    Picks,
    chosen_picks,
    effort_levels_for,
)
from sillytherin.settings import reasoning_body

# ---- Group 1 — levels per model on the server · unit
# Baseline: config levels default, off, low, medium, high; a model that thinks, lists efforts
# low, medium, high, minimal, and can turn thinking off

CONFIG_LEVELS = {"default": "Model default", "off": "Off", "low": "Low", "medium": "Medium", "high": "High"}
THINKS = frozenset({"reasoning", "tools"})


def thinking_model(**fields) -> llm.ModelInfo:
    base = {"id": "vendor/smart", "name": "Smart", "params": THINKS, "supported_efforts": ("low", "medium", "high", "minimal")}
    return llm.ModelInfo(**{**base, **fields})


def test_model_with_listed_efforts_offers_only_those_config_has():
    # Then: ["default", "off", "low", "medium", "high"]
    # Demonstrates: a model never offers a level its provider doesn't accept
    assert effort_levels_for(thinking_model(), CONFIG_LEVELS) == ("default", "off", "low", "medium", "high")
    gpt_like = thinking_model(supported_efforts=("low", "high"))
    assert effort_levels_for(gpt_like, CONFIG_LEVELS) == ("default", "off", "low", "high")


def test_mandatory_model_has_no_off():
    # Changed: thinking is mandatory · Then: no "off"
    # Demonstrates: no switch that can't work
    assert "off" not in effort_levels_for(thinking_model(thinking_mandatory=True), CONFIG_LEVELS)


def test_budget_model_offers_all_config_levels():
    # Changed: no supported_efforts · Then: every config level
    # Demonstrates: budget-based models still get every level, which OpenRouter maps to a budget
    assert effort_levels_for(thinking_model(supported_efforts=()), CONFIG_LEVELS) == tuple(CONFIG_LEVELS)


def test_server_always_sends_the_effort_labels(monkeypatch):
    # Changed: the chat's model doesn't think · Then: the Effort picker is still sent with every label
    # Demonstrates: the page can show Effort as soon as a thinking model is picked
    plain = llm.ModelInfo(id="vendor/plain", name="Plain", params=frozenset())
    monkeypatch.setattr(llm, "_catalog", {plain.id: plain})
    pickers = asyncio.run(pickers_for(Picks(model="vendor/plain", effort="default")))
    assert [picker.id for picker in pickers] == [MODEL_MODE_ID, EFFORT_MODE_ID]
    assert pickers[1].options


def test_model_options_carry_their_effort_levels(monkeypatch):
    # Then: a thinking model's option lists its levels; a plain model's option has none
    # Demonstrates: the page knows each model's levels before any message
    smart = thinking_model(supported_efforts=("low", "high"))
    plain = llm.ModelInfo(id="vendor/plain", name="Plain", params=frozenset())
    monkeypatch.setattr(llm, "_catalog", {smart.id: smart, plain.id: plain})
    model_picker = asyncio.run(pickers_for(Picks(model="vendor/smart", effort="default")))[0]
    levels = {option.id: option.effort_levels for option in model_picker.options}
    assert "low" in levels["vendor/smart"] and "medium" not in levels["vendor/smart"]
    assert levels["vendor/plain"] == ()


def test_catalog_keeps_reasoning_data():
    # Given an OpenRouter entry with reasoning {mandatory, supported_efforts, default_effort} · Then: kept on ModelInfo
    # Demonstrates: the per-model data reaches the app
    entry = {
        "id": "openai/gpt-x",
        "name": "GPT X",
        "supported_parameters": ["reasoning"],
        "architecture": {"output_modalities": ["text"]},
        "reasoning": {"mandatory": True, "supported_efforts": ["high", "low"], "default_effort": "medium"},
    }
    model = llm.parse_catalog([entry])["openai/gpt-x"]
    assert (model.thinking_mandatory, model.supported_efforts, model.default_effort) == (True, ("high", "low"), "medium")


def test_model_listing_max_offers_max():
    # Changed: supported_efforts low, high, max · Then: ["default", "off", "low", "high", "max"]; config accepts max
    # Demonstrates: the strongest level is offered where it exists
    levels = {**CONFIG_LEVELS, "max": "Max"}
    check_levels(levels)
    assert effort_levels_for(thinking_model(supported_efforts=("low", "high", "max")), levels) == ("default", "off", "low", "high", "max")


def test_max_is_sent_as_the_effort():
    # Then: reasoning_body("max", ...) gives {"reasoning": {"effort": "max"}}
    # Demonstrates: Max really reaches OpenRouter
    assert reasoning_body(effort="max", show_thinking=True, can_reason=True) == {"reasoning": {"effort": "max"}}


def test_ignores_efforts_config_does_not_know():
    # Changed: supported_efforts ["turbo"] · Then: only "default" and "off"
    # Demonstrates: odd provider data never reaches the page
    assert effort_levels_for(thinking_model(supported_efforts=("turbo",)), CONFIG_LEVELS) == ("default", "off")


def test_effort_the_model_does_not_offer_falls_back_to_default_when_sent():
    # Changed: mandatory model, message sends "off" · Then: effort "default"
    # Demonstrates: the server never trusts the page
    mandatory = thinking_model(thinking_mandatory=True)
    choices = PickerChoices(
        models={mandatory.id: "Smart"},
        levels=CONFIG_LEVELS,
        model_effort_levels={mandatory.id: effort_levels_for(mandatory, CONFIG_LEVELS)},
    )
    picks = chosen_picks({MODEL_MODE_ID: mandatory.id, EFFORT_MODE_ID: "off"}, choices, Picks(model=mandatory.id, effort="high"))
    assert picks == Picks(model=mandatory.id, effort="default")


def test_missing_reasoning_object_means_no_extra_limits():
    # Changed: no reasoning object in the entry · Then: config levels
    # Demonstrates: old or partial data still works
    entry = {"id": "vendor/old", "name": "Old", "supported_parameters": ["reasoning"], "architecture": {"output_modalities": ["text"]}}
    model = llm.parse_catalog([entry])["vendor/old"]
    assert effort_levels_for(model, CONFIG_LEVELS) == tuple(CONFIG_LEVELS)
