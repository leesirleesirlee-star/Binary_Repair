"""Server-side conversation history for one repair task.

The browser still keeps a localStorage cache (offline / no-server fallback), but
the console reads and writes this store so the same task conversation is
available from another browser pointed at the same service instance.

The payload is opaque client state: entries are only stored, never interpreted
or executed. Validation bounds the scope, entry count and serialized size so a
single tab cannot fill the disk.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_ENTRIES = 120
MAX_BYTES = 512 * 1024
ALLOWED_KINDS = {"message", "tool", "repair"}
DB_ENV = "BINARYREPAIR_HISTORY_DB"
DATA_DIR_ENV = "BINARYREPAIR_DATA_DIR"


class HistoryError(ValueError):
    """The scope or payload is not acceptable."""


class HistoryConflict(HistoryError):
    """The caller is writing against an older generation (stale tab)."""


def validate_scope(scope: str) -> str:
    if not isinstance(scope, str) or not SCOPE_RE.fullmatch(scope):
        raise HistoryError("invalid history scope")
    return scope


def normalize_entries(entries: Any) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        raise HistoryError("entries must be a list")
    if len(entries) > MAX_ENTRIES:
        raise HistoryError("too many entries")
    cleaned: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise HistoryError("each entry must be an object")
        kind = entry.get("kind")
        if kind not in ALLOWED_KINDS:
            raise HistoryError("unsupported entry kind")
        cleaned.append(entry)
    raw = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise HistoryError("history payload is too large")
    return cleaned


def _db_path(env: Mapping[str, str] | None = None) -> Path:
    environ = os.environ if env is None else env
    explicit = (environ.get(DB_ENV) or "").strip()
    if explicit:
        return Path(explicit)
    data_dir = Path((environ.get(DATA_DIR_ENV) or "var").strip() or "var")
    return data_dir / "gui-history.db"


class HistoryStore:
    """Tiny SQLite-backed store: one row per task scope."""

    def __init__(self, path: str | Path | None = None, *, env: Mapping[str, str] | None = None) -> None:
        self.path = Path(path) if path is not None else _db_path(env)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS conversation_history ("
            "scope TEXT PRIMARY KEY,"
            "entries TEXT NOT NULL,"
            "updated_at TEXT NOT NULL,"
            "generation INTEGER NOT NULL DEFAULT 0"
            ")"
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(conversation_history)").fetchall()
        }
        if "generation" not in columns:
            connection.execute(
                "ALTER TABLE conversation_history ADD COLUMN generation INTEGER NOT NULL DEFAULT 0"
            )
        return connection

    def load(self, scope: str) -> dict[str, Any]:
        scope = validate_scope(scope)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT entries, updated_at, generation FROM conversation_history WHERE scope = ?",
                (scope,),
            ).fetchone()
        if row is None:
            return {"scope": scope, "entries": [], "updated_at": None,
                    "storage": "server", "generation": 0}
        try:
            entries = json.loads(row[0])
        except (TypeError, ValueError):
            entries = []
        if not isinstance(entries, list):
            entries = []
        return {"scope": scope, "entries": entries, "updated_at": row[1],
                "storage": "server", "generation": int(row[2])}

    def save(self, scope: str, entries: Any, generation: int | None = None) -> dict[str, Any]:
        scope = validate_scope(scope)
        cleaned = normalize_entries(entries)
        updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            row = connection.execute(
                "SELECT generation FROM conversation_history WHERE scope = ?", (scope,)
            ).fetchone()
            current = int(row[0]) if row else 0
            if generation is not None and int(generation) != current:
                raise HistoryConflict("history generation changed")
            connection.execute(
                "INSERT INTO conversation_history (scope, entries, updated_at, generation)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(scope) DO UPDATE SET entries = excluded.entries,"
                " updated_at = excluded.updated_at",
                (scope, payload, updated_at, current),
            )
        return {"scope": scope, "entries": cleaned, "updated_at": updated_at,
                "storage": "server", "generation": current}

    def delete(self, scope: str) -> dict[str, Any]:
        """Reset the conversation and bump the generation.

        A stale tab that loaded the previous generation is rejected on its next
        save, so it cannot resurrect the cleared draft.
        """
        scope = validate_scope(scope)
        updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT generation FROM conversation_history WHERE scope = ?", (scope,)
            ).fetchone()
            existed = row is not None
            generation = (int(row[0]) + 1) if row else 1
            connection.execute(
                "INSERT INTO conversation_history (scope, entries, updated_at, generation)"
                " VALUES (?, '[]', ?, ?)"
                " ON CONFLICT(scope) DO UPDATE SET entries = '[]', updated_at = excluded.updated_at,"
                " generation = excluded.generation",
                (scope, updated_at, generation),
            )
        return {"scope": scope, "deleted": existed, "generation": generation}

    def count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM conversation_history").fetchone()
        return int(row[0]) if row else 0

    def clear_all(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM conversation_history").fetchone()
            connection.execute("DELETE FROM conversation_history")
        return int(row[0]) if row else 0


__all__ = [
    "HistoryStore",
    "HistoryError",
    "HistoryConflict",
    "MAX_ENTRIES",
    "MAX_BYTES",
    "validate_scope",
    "normalize_entries",
]
