"""A workspace error as the JSON a tool gives the model."""

import json

from core.workspace.errors import WorkspaceError


def error_json(error: WorkspaceError) -> str:
    """The error with its structured details, for the model to act on."""
    return json.dumps({"error": error.code, "message": error.message, **(error.details or {})})
