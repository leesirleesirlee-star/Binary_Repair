"""SQLite connection helper for the minimal job store."""

from __future__ import annotations

import sqlite3
from pathlib import Path

def connect(data_dir: str | Path) -> sqlite3.Connection:
    """Open ``<data_dir>/app.db`` and bring it to the latest schema."""

    from app.db.migrate import init_db

    return init_db(data_dir)
