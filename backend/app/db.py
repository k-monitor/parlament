"""Read-only SQLite access for the API (ING-2 / NFR-2).

The backend never writes: connections are opened in read-only mode against the
file the loader builds and atomically swaps in (DB-4). Connections are cheap for
SQLite, so we open one per request and close it after — this also means an
atomic file replacement is picked up by the next request with no restart.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import settings


def open_connection(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or settings.db_path
    if not Path(path).exists():
        raise FileNotFoundError(
            f"Database not found at {path}. Build it with "
            f"`python -m app.loader <data_dir> {path}`.")
    # mode=ro: open existing file read-only (fails rather than creating).
    uri = f"file:{Path(path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def get_db():
    """FastAPI dependency yielding a per-request read-only connection."""
    conn = open_connection()
    try:
        yield conn
    finally:
        conn.close()
