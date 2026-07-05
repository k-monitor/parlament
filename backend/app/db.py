"""Read-only SQLite access for the API (ING-2 / NFR-2).

The backend never writes: connections are opened in read-only mode against the
file the loader builds and atomically swaps in (DB-4). Each worker thread keeps
its connection open across requests (a connection's page cache is only useful
warm), but re-stats the DB file per request: the loader's atomic ``os.replace``
gives the new file a new inode, so a swap is still picked up by the very next
request with no restart — same zero-downtime behaviour as the old
open-per-request scheme, minus the per-request setup cost.
"""

from __future__ import annotations

import os
import sqlite3
import threading
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
    # Read-path tuning: mmap the whole DB so page reads come straight from the
    # OS page cache — one shared copy across every thread and worker process —
    # and keep a modest per-connection cache on top. Both are harmless no-ops
    # on SQLite builds without mmap support.
    try:
        conn.execute("PRAGMA mmap_size=1073741824")  # 1 GiB ceiling, not a reservation
        conn.execute("PRAGMA cache_size=-8192")      # ~8 MiB per connection
    except sqlite3.Error:  # pragma: no cover
        pass
    return conn


_local = threading.local()


def _thread_connection() -> sqlite3.Connection:
    """The calling thread's cached read-only connection, reopened only when the
    DB file was atomically swapped (new inode/mtime) or the configured path
    changed. The stat() is a few microseconds — negligible next to reopening
    and re-registering ``fold`` on every request."""
    path = settings.db_path
    try:
        st = os.stat(path)
    except OSError:
        raise FileNotFoundError(
            f"Database not found at {path}. Build it with "
            f"`python -m app.loader <data_dir> {path}`.")
    ident = (path, st.st_dev, st.st_ino, st.st_mtime_ns)
    if getattr(_local, "ident", None) == ident:
        return _local.conn
    old = getattr(_local, "conn", None)
    if old is not None:
        try:
            old.close()
        except sqlite3.Error:  # pragma: no cover
            pass
    _local.conn = open_connection(path)
    _local.ident = ident
    return _local.conn


def get_db():
    """FastAPI dependency yielding the thread's cached read-only connection."""
    yield _thread_connection()
