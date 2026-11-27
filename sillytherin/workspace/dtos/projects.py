"""A workspace as the app lists it."""

from dataclasses import dataclass


@dataclass
class Project:
    id: str  # safe slug, also the folder name
    name: str  # display name, any text (1-80 chars)
    created_at: str  # ISO 8601, UTC
