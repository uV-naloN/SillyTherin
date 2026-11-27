"""Agent mode values shared by chat sessions and turns."""

from typing import Literal

AgentMode = Literal["manual", "auto", "free"]
DEFAULT_AGENT_MODE: AgentMode = "auto"
