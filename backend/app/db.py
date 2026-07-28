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
from collections.abc import Iterable
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


def like_contains(term: str) -> str:
    """A contains-match LIKE pattern that treats user input literally: ``%``,
    ``_`` and ``\\`` in the term are escaped, so e.g. ``q=100%`` matches the
    text "100%" rather than "100" followed by anything. Use together with
    ``ESCAPE '\\'`` on the LIKE."""
    escaped = (term.replace("\\", "\\\\")
                   .replace("%", "\\%")
                   .replace("_", "\\_"))
    return f"%{escaped}%"


def period_list(period: Iterable[int] | None) -> list[int]:
    """Canonical form of the ``period`` query param: sorted, de-duplicated
    electoral-period numbers. An empty list means "all cycles" (no filter).

    Every period-aware endpoint takes ``period`` as a *repeatable* param
    (``?period=42&period=43``), because the site's cycle chooser lets the reader
    scope to several cycles at once; a single ``?period=43`` is the one-element
    case and behaves exactly as it did when the param was scalar.

    Bounded at ``_MAX_PERIODS`` distinct values — far beyond any real scope (the
    House has had a handful of cycles), but enough of a cap that a hand-crafted
    request can't turn the param into a thousand-term ``IN`` list, or a thousand
    distinct aggregate-cache keys."""
    return sorted({int(p) for p in (period or ())})[:_MAX_PERIODS]


_MAX_PERIODS = 64


def period_sql(period: Iterable[int] | None, column: str) -> str:
    """SQL predicate restricting ``column`` to the selected periods, or ``""``
    when nothing is selected (all cycles).

    The numbers are inlined rather than bound: they are ints (validated by
    FastAPI, re-coerced above), so this is injection-safe, and it lets the
    fragment be spliced into any query regardless of its paramstyle — the
    period-aware queries here use named, qmark and positional binding alike."""
    nums = period_list(period)
    if not nums:
        return ""
    if len(nums) == 1:
        return f"{column} = {nums[0]}"
    return f"{column} IN ({', '.join(str(n) for n in nums)})"


def period_and(period: Iterable[int] | None, column: str) -> str:
    """``period_sql`` prefixed with ``" AND "``, for appending to a WHERE that
    already has at least one condition (``""`` when scope is all cycles)."""
    sql = period_sql(period, column)
    return f" AND {sql}" if sql else ""


def period_key(period: Iterable[int] | None) -> tuple[int, ...]:
    """Hashable canonical scope, for aggregate cache keys (``()`` = all cycles)."""
    return tuple(period_list(period))


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
        # mmap ceiling is env-configurable (PARLAMONITOR_SQLITE_MMAP_SIZE); the
        # default 1 GiB is below the current DB size, so raising it above the
        # file size maps the whole DB instead of read()ing its tail.
        conn.execute(f"PRAGMA mmap_size={int(settings.sqlite_mmap_size)}")
        conn.execute("PRAGMA cache_size=-8192")      # ~8 MiB per connection
    except sqlite3.Error:  # pragma: no cover
        pass
    return conn


# Warm connections are kept in a checkout pool rather than thread-locals:
# FastAPI runs a sync dependency and the endpoint body as separate threadpool
# jobs, so they routinely land on *different* threads. With thread-local
# caching, two in-flight requests could share one connection concurrently, and
# a DB swap would let one thread close() a connection another request was
# still querying — a use-after-free that can segfault the process. Checking a
# connection out per request makes each one single-user for the request's
# lifetime; superseded connections are closed only by their sole holder.
_pool_lock = threading.Lock()
_pool_ident: tuple | None = None
_pool: list[sqlite3.Connection] = []


def _db_ident() -> tuple:
    """Identity of the current DB file: the loader's atomic ``os.replace``
    gives a new inode, and an in-place ``loader --update`` a new mtime, so
    either kind of swap changes the ident and retires pooled connections. The
    stat() is a few microseconds — negligible next to reopening per request."""
    path = settings.db_path
    try:
        st = os.stat(path)
    except OSError:
        raise FileNotFoundError(
            f"Database not found at {path}. Build it with "
            f"`python -m app.loader <data_dir> {path}`.")
    return (path, st.st_dev, st.st_ino, st.st_mtime_ns)


def current_ident() -> tuple:
    """Public view of the current DB file's identity (see ``_db_ident``). Folded
    into the query cache's keys so the loader's atomic swap invalidates it
    automatically — a superseded DB's cached entries are never looked up again."""
    return _db_ident()


def get_db():
    """FastAPI dependency yielding a pooled read-only connection, checked out
    for the duration of the request (DB-4 zero-downtime swap preserved)."""
    global _pool_ident
    ident = _db_ident()
    conn = None
    with _pool_lock:
        if _pool_ident != ident:
            # DB file swapped: retire idle connections now; checked-out ones
            # are closed by their own request on return (see finally below).
            for stale in _pool:
                try:
                    stale.close()
                except sqlite3.Error:  # pragma: no cover
                    pass
            _pool.clear()
            _pool_ident = ident
        elif _pool:
            conn = _pool.pop()
    if conn is None:
        conn = open_connection(ident[0])
    try:
        yield conn
    finally:
        with _pool_lock:
            if _pool_ident == ident:
                _pool.append(conn)
                conn = None
        if conn is not None:  # superseded while we held it — safe: sole user
            try:
                conn.close()
            except sqlite3.Error:  # pragma: no cover
                pass
