"""What the chat, the pickers and the model request share across packages."""

from dataclasses import dataclass, field
from typing import Literal

from langchain_core.tools import BaseTool

AgentMode = Literal["manual", "auto", "free"]

DEFAULT_AGENT_MODE: AgentMode = "auto"


@dataclass(frozen=True)
class Choice:
    """One button under a prompt: `id` comes back when it is clicked, `label` is what it says."""

    id: str
    label: str


@dataclass(frozen=True)
class SettingSwitch:
    """One on/off row in Settings."""

    id: str  # the key the chosen value comes back under
    label: str
    initial: bool  # where the switch starts when Settings opens
    disabled: bool  # greyed out because the picked model lacks the feature
    description: str  # what the switch does, or why it is greyed out


@dataclass(frozen=True)
class PickerOption:
    """One choice in a chat box picker."""

    id: str  # the value sent back with the message
    name: str  # the label shown to the user
    selected: bool
    detail: str = ""  # what a model can do, e.g. "thinks · tools"; empty for effort levels
    effort_levels: tuple[str, ...] = ()  # the Effort level ids this model offers; empty = it does not think


@dataclass(frozen=True)
class Picker:
    """A chat box picker, such as Model or Effort."""

    id: str  # the key the picked value comes back under
    name: str
    options: tuple[PickerOption, ...]


@dataclass(frozen=True)
class UserMessage:
    """What the user sent, with the picker choices that came with it."""

    text: str
    model: str  # model id from the Model picker; kept only if it is offered
    effort: str  # level from the Effort picker; kept only if it is offered
    agent_mode: AgentMode = DEFAULT_AGENT_MODE


@dataclass(frozen=True)
class ModelRequest:
    """How every round of one turn calls the model."""

    model: str  # OpenRouter model id
    tools: list[BaseTool]
    thinking: dict  # reasoning_body(): {"reasoning": {...}} or {} for a model that doesn't think


@dataclass(frozen=True)
class ModelInfo:
    """One OpenRouter model as the app needs it."""

    id: str
    name: str
    params: frozenset[str]
    thinking_mandatory: bool = False  # OpenRouter reasoning.mandatory: thinking cannot be turned off
    supported_efforts: tuple[str, ...] = ()  # OpenRouter reasoning.supported_efforts; empty = budget-based
    default_effort: str | None = None  # OpenRouter reasoning.default_effort


@dataclass(frozen=True)
class Picks:
    """What one chat has picked in the chat box pickers."""

    model: str
    effort: str


@dataclass(frozen=True)
class PickerChoices:
    """Everything the pickers may offer: model id -> label and effort level -> label."""

    models: dict[str, str]
    levels: dict[str, str]
    model_details: dict[str, str] = field(default_factory=dict)  # model id -> "thinks · tools" etc.
    model_effort_levels: dict[str, tuple[str, ...]] = field(default_factory=dict)  # model id -> its Effort levels


ToastKind = Literal["info", "success", "warning", "error"]

ReplyInterruptionReason = Literal["openrouter_relay_failed"]
