"""The saved Kaggle sign-in: one owner-only JSON file outside every project folder.

Only the refresh token is saved; access tokens live in memory.
"""

import json
import os
import stat
from dataclasses import asdict
from pathlib import Path

from core.workspace.kaggle.dtos.token_file import SavedSignIn

OWNER_ONLY_MODE = 0o600
GROUP_OR_OTHER_BITS = stat.S_IRWXG | stat.S_IRWXO


class TokenFile:
    """Reads, writes and deletes the sign-in file; no network."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> SavedSignIn | None:
        if not self.path.is_file() or self._readable_by_others():
            return None
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return sign_in_from(saved)

    def save(self, sign_in: SavedSignIn) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f".{self.path.name}.tmp")
        descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, OWNER_ONLY_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
            json.dump(asdict(sign_in), temp_file)
        os.chmod(temp_path, OWNER_ONLY_MODE)  # an older temp file may have had other bits
        os.replace(temp_path, self.path)

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)

    def _readable_by_others(self) -> bool:
        return bool(self.path.stat().st_mode & GROUP_OR_OTHER_BITS)


def sign_in_from(saved: object) -> SavedSignIn | None:
    """A saved sign-in from the file's JSON, or None when any field is missing or empty."""
    if not isinstance(saved, dict):
        return None
    values = {name: saved.get(name) for name in ("client_id", "refresh_token", "username", "connected_at")}
    has_every_field = all(isinstance(value, str) and value for value in values.values())
    return SavedSignIn(**values) if has_every_field else None
