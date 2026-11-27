"""The Model and Effort pickers in the chat box, and reading what a message chose."""

from typing import TYPE_CHECKING

from core.contracts.chat import Picker, PickerChoices, PickerOption, Picks

if TYPE_CHECKING:
    from core.contracts.chat import ModelInfo

MODEL_MODE_ID = "model"
MODEL_MODE_NAME = "Model"
EFFORT_MODE_ID = "effort"
EFFORT_MODE_NAME = "Effort"
DEFAULT_LEVEL = "default"  # send no effort: the model decides
OFF_LEVEL = "off"
REASONING_PARAM = "reasoning"  # OpenRouter parameter a thinking model accepts


def picker_models(names: dict[str, str], default_model: str) -> dict[str, str]:
    """Model id -> label for the picker: the default first, then A→Z by label.

    The default is offered even when OpenRouter doesn't list it (or can't be reached), so the chat
    always has the model config.toml or .env names.
    """
    others = sorted(((label, model_id) for model_id, label in names.items() if model_id != default_model))
    return {default_model: names.get(default_model, default_model), **{model_id: label for label, model_id in others}}


def build_modes(choices: PickerChoices, picks: Picks) -> list[Picker]:
    """Model and Effort, each starting at this chat's pick.

    Effort is always sent with every configured label; each model option lists the levels it offers,
    so the page can show or hide Effort as soon as a model is picked.
    """
    return [build_model_picker(choices, picks.model), build_effort_picker(choices.levels, picks.effort)]


def build_model_picker(choices: PickerChoices, picked: str) -> Picker:
    options = tuple(
        PickerOption(
            id=model_id,
            name=label,
            selected=model_id == picked,
            detail=choices.model_details.get(model_id, ""),
            effort_levels=choices.model_effort_levels.get(model_id, ()),
        )
        for model_id, label in choices.models.items()
    )
    return Picker(id=MODEL_MODE_ID, name=MODEL_MODE_NAME, options=options)


def build_effort_picker(levels: dict[str, str], picked: str) -> Picker:
    options = tuple(PickerOption(id=level, name=label, selected=level == picked) for level, label in levels.items())
    return Picker(id=EFFORT_MODE_ID, name=EFFORT_MODE_NAME, options=options)


def effort_levels_for(model: "ModelInfo | None", configured_levels: dict[str, str]) -> tuple[str, ...]:
    """The Effort level ids `model` offers, in config order; empty when it doesn't think."""
    if model is None or REASONING_PARAM not in model.params:
        return ()
    allowed = offered_level_ids(model, configured_levels)
    return tuple(level for level in configured_levels if level in allowed)


def offered_level_ids(model: "ModelInfo", configured_levels: dict[str, str]) -> set[str]:
    # OpenRouter's "none" effort is our Off; a mandatory model can't turn thinking off
    can_turn_off = not model.thinking_mandatory
    always_offered = {DEFAULT_LEVEL} | ({OFF_LEVEL} if can_turn_off else set())
    if not model.supported_efforts:
        # a budget-based model: OpenRouter turns any effort into a thinking budget
        return always_offered | (set(configured_levels) - {OFF_LEVEL})
    return always_offered | set(model.supported_efforts)


def chosen_picks(modes: dict[str, str] | None, choices: PickerChoices, fallback: Picks) -> Picks:
    """What a message picked; a value that isn't offered (or is missing) keeps the fallback,
    and an effort the picked model doesn't offer becomes the model's default."""
    sent = modes or {}
    model = offered_or(sent.get(MODEL_MODE_ID), choices.models, fallback.model)
    effort = offered_or(sent.get(EFFORT_MODE_ID), choices.levels, fallback.effort)
    model_levels = choices.model_effort_levels.get(model, ())
    is_level_unoffered = bool(model_levels) and effort not in model_levels
    return Picks(model=model, effort=DEFAULT_LEVEL if is_level_unoffered else effort)


def offered_or(value: str | None, offered: dict[str, str], fallback: str) -> str:
    """`value` when it is one of the offered keys, else `fallback`: the browser can send anything."""
    return value if value in offered else fallback
