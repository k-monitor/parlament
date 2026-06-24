"""Read-only SQLite access for the API (ING-2 / NFR-2).

The backend never writes: connections are opened in read-only mode against the
file the loader builds and atomically swaps in (DB-4). Connections are cheap for
SQLite, so we open one per request and close it after — this also means an
atomic file replacement is picked up by the next request with no restart.
"""

from __future__ import annotations

import sqlite3
import unicodedata
from pathlib import Path

from .config import settings


def fold_text(s: str | None) -> str | None:
    """Accent- and case-fold text for diacritic-insensitive matching (FOLD-1..4).

    SQLite's built-in ``LIKE``/``NOCASE`` only case-fold ASCII and never strip
    accents, so the simple name/title/subject filters compare accent-sensitively
    (`dora` would not match *Dóra*). We normalize to NFKD, drop combining marks
    — this correctly folds the full Hungarian set including ``ő``/``ű`` (whose
    NFKD form is o/u + a combining double-acute) — then ``casefold()``. Registered
    on every connection as the SQL function ``fold(x)`` and applied symmetrically
    to both column and query (FOLD-3). Matching only — display text is untouched
    (FOLD-5).
    """
    if s is None:
        return None
    decomposed = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.casefold()


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
    conn.create_function("fold", 1, fold_text, deterministic=True)
    return conn


def get_db():
    """FastAPI dependency yielding a per-request read-only connection."""
    conn = open_connection()
    try:
        yield conn
    finally:
        conn.close()
