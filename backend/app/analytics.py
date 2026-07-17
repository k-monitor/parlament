"""Privacy-respecting search analytics (PRIV-1).

GDPR-friendly, aggregated logging of what people search for — the search
*keywords* and the *filters* they combine with them — WITHOUT any personal data:

  * NO IP addresses, user agents, cookies or session identifiers are read or
    stored (the endpoint never even looks at the request's network metadata).
  * NO exact timestamps — events are counted into whole-hour buckets, so the
    finest time resolution ever persisted is "this term was searched N times in
    the 14:00–15:00 (UTC) hour".
  * Only the aggregate COUNT per ``(hour, query, filters)`` tuple is written —
    never a per-request row that could be correlated back to a person.

Events are accumulated in memory and flushed once an hour to a SEPARATE SQLite
file — never the read-only content DB (db.py). That file is meant to live on a
host-mounted volume (``PARLAMONITOR_ANALYTICS_DB``) so the aggregates can be read
from OUTSIDE the container. Several uvicorn workers, and both blue/green deploy
colors during a swap, write the same file concurrently; every write is an
accumulating UPSERT (``searches = searches + excluded.searches``), so their
contributions add up rather than clobber one another.

Recording is best-effort and must never affect a search response: ``record()``
only touches an in-memory counter under a short lock and swallows its own errors,
and a misconfigured / unwritable analytics path disables the logger instead of
taking the API down.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .config import settings

logger = logging.getLogger("parlamonitor.analytics")

# Cap a stored keyword's length — a search box can receive a pasted essay; the
# analytics only needs the term, and an unbounded key would bloat the store.
_MAX_QUERY_LEN = 200
# Backstop against unbounded memory if a single hour sees a pathological number
# of DISTINCT (query, filters) tuples: past this many live keys we stop creating
# NEW ones (existing keys keep counting) until the next flush. Realistic traffic
# never approaches this.
_MAX_LIVE_KEYS = 100_000
_WS_RE = re.compile(r"\s+")

# The analytics schema deliberately holds ONLY the hour bucket, the keyword, the
# search filters, a zero-result flag and a count. There is no column for an IP,
# a user agent, a session id or a sub-hour timestamp — by construction there is
# nothing personal to store.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_query_hourly (
    -- Whole-hour bucket in UTC, "YYYY-MM-DDTHH:00". Deliberately coarse: no
    -- finer-grained time is ever stored (GDPR data-minimisation).
    hour         TEXT    NOT NULL,
    -- The search keyword(s): trimmed + whitespace-collapsed, otherwise as typed.
    query        TEXT    NOT NULL,
    -- The search FILTERS ('' = filter not used). Stored as TEXT so the UPSERT key
    -- is reliable: a UNIQUE index treats NULLs as DISTINCT in SQLite, which would
    -- defeat the aggregation for the (very common) no-filter case.
    date_from    TEXT    NOT NULL DEFAULT '',
    date_to      TEXT    NOT NULL DEFAULT '',
    period       TEXT    NOT NULL DEFAULT '',
    person_id    TEXT    NOT NULL DEFAULT '',
    faction_id   TEXT    NOT NULL DEFAULT '',
    agenda_type  TEXT    NOT NULL DEFAULT '',
    sort         TEXT    NOT NULL DEFAULT '',
    -- 1 when the search returned no hits (surfaces coverage gaps), else 0.
    zero_results INTEGER NOT NULL DEFAULT 0,
    -- Number of searches counted into this bucket.
    searches     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour, query, date_from, date_to, period, person_id,
                 faction_id, agenda_type, sort, zero_results)
) WITHOUT ROWID;
"""

_UPSERT = """
INSERT INTO search_query_hourly
    (hour, query, date_from, date_to, period, person_id, faction_id,
     agenda_type, sort, zero_results, searches)
VALUES
    (:hour, :query, :date_from, :date_to, :period, :person_id, :faction_id,
     :agenda_type, :sort, :zero_results, :searches)
ON CONFLICT (hour, query, date_from, date_to, period, person_id, faction_id,
             agenda_type, sort, zero_results)
DO UPDATE SET searches = searches + excluded.searches;
"""

# Order of the in-memory key tuple → the columns above (hour FIRST so a plain
# string compare tells whether a bucket's hour is already complete).
_KEY_FIELDS = ("hour", "query", "date_from", "date_to", "period", "person_id",
               "faction_id", "agenda_type", "sort", "zero_results")


def _utc_hour(now: Optional[dt.datetime] = None) -> str:
    """The current whole-hour bucket in UTC as ``YYYY-MM-DDTHH:00`` (fixed-width,
    so lexical ordering equals chronological ordering)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:00")


def _norm_query(q: str | None) -> str:
    """Trim, collapse internal whitespace and length-cap a raw query. Returns ''
    for a query with nothing loggable."""
    if not q:
        return ""
    return _WS_RE.sub(" ", q).strip()[:_MAX_QUERY_LEN]


class SearchAnalytics:
    """In-memory hourly aggregator that flushes to a standalone SQLite file.

    Thread-safe: many request threads call :meth:`record` while a single daemon
    thread flushes. Also process-safe across workers/colors via the accumulating
    UPSERT — see the module docstring.
    """

    def __init__(self, db_path: str, enabled: bool = True,
                 flush_interval: float = 60.0):
        self.db_path = db_path
        self.enabled = enabled
        self._flush_interval = flush_interval
        self._lock = threading.Lock()
        self._buckets: dict[tuple, int] = {}
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._warned_full = False

    @classmethod
    def from_settings(cls) -> "SearchAnalytics":
        # Resolve the default path lazily from settings.db_path so a db_path
        # overridden after construction (tests monkeypatch it) is still honoured.
        path = settings.analytics_db or str(
            Path(settings.db_path).resolve().parent / "search-analytics.db")
        return cls(db_path=path, enabled=settings.search_analytics)

    # -- recording (hot path: in-memory only, never touches disk) -------------
    def record(self, *, query, date_from=None, date_to=None, period=None,
               person_id=None, faction_id=None, agenda_type=None,
               sort=None, zero_results=False) -> None:
        """Count one search into the current hour's bucket. Cheap and
        exception-safe — it must never disturb the search response."""
        if not self.enabled:
            return
        try:
            q = _norm_query(query)
            if not q:
                return
            key = (
                _utc_hour(), q,
                date_from or "", date_to or "",
                "" if period is None else str(period),
                person_id or "",
                "" if faction_id is None else str(faction_id),
                agenda_type or "", sort or "",
                1 if zero_results else 0,
            )
            with self._lock:
                if key in self._buckets:
                    self._buckets[key] += 1
                elif len(self._buckets) < _MAX_LIVE_KEYS:
                    self._buckets[key] = 1
                elif not self._warned_full:
                    self._warned_full = True
                    logger.warning(
                        "search analytics buffer full (%d keys); dropping new "
                        "keys until the next flush", _MAX_LIVE_KEYS)
        except Exception:  # never let analytics break a search
            logger.exception("search analytics record failed")

    # -- flushing (cold path: hourly) ----------------------------------------
    def _drain(self, force: bool) -> list[tuple]:
        """Remove and return the buckets to persist: completed hours only, or
        everything when ``force`` (shutdown)."""
        cutoff = _utc_hour()
        with self._lock:
            if force:
                items = list(self._buckets.items())
                self._buckets.clear()
            else:
                items = [(k, v) for k, v in self._buckets.items() if k[0] < cutoff]
                for k, _ in items:
                    del self._buckets[k]
            self._warned_full = False
        return items

    def flush(self, force: bool = False) -> int:
        """Persist completed hour buckets (or everything, when ``force``).

        Returns the number of bucket rows written. Only *complete* hours are
        written by the periodic thread, so the store never holds a partial hour
        except transiently after a forced shutdown flush. Safe when disabled."""
        if not self.enabled:
            return 0
        items = self._drain(force)
        if not items:
            return 0
        try:
            Path(self.db_path).resolve().parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=30)
            try:
                # WAL + a generous busy timeout let concurrent workers/colors
                # write, and readers (an analyst opening the file) never block.
                conn.execute("PRAGMA busy_timeout=30000")
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(_SCHEMA)
                rows = [dict(zip(_KEY_FIELDS, k), searches=v) for k, v in items]
                with conn:
                    conn.executemany(_UPSERT, rows)
                # Fold the WAL back in so the .db file stays self-contained for a
                # reader copying it off the host.
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                conn.close()
            return len(items)
        except Exception:
            # Re-buffer the drained counts so a transient error loses nothing.
            logger.exception(
                "search analytics flush failed; re-buffering %d keys", len(items))
            with self._lock:
                for k, v in items:
                    self._buckets[k] = self._buckets.get(k, 0) + v
            return 0

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        """Verify the store is writable and launch the hourly flush thread. On any
        failure it logs and disables itself — a bad analytics path never takes the
        API down. Idempotent and a no-op when disabled."""
        if not self.enabled:
            logger.info("search analytics disabled")
            return
        if self._thread is not None:
            return
        try:
            Path(self.db_path).resolve().parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=30)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(_SCHEMA)
            finally:
                conn.close()
        except Exception:
            logger.exception(
                "search analytics: cannot open %s — disabling", self.db_path)
            self.enabled = False
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="search-analytics-flush", daemon=True)
        self._thread.start()
        logger.info(
            "search analytics enabled → %s (aggregated hourly, no IP / no exact "
            "timestamp)", self.db_path)

    def _run(self) -> None:
        # wait() returns True the moment stop() sets the event → prompt shutdown.
        while not self._stop_event.wait(self._flush_interval):
            self.flush()

    def stop(self) -> None:
        """Stop the flush thread and persist EVERYTHING, including the in-progress
        hour, so a graceful stop / deploy doesn't drop the partial bucket. The
        next process continues the same hour correctly via the accumulating
        UPSERT. Safe to call when never started / disabled."""
        self._stop_event.set()
        t = self._thread
        if t is not None:
            t.join(timeout=5)
            self._thread = None
        self.flush(force=True)


# Process-wide singleton the API records against. Constructed inert (no thread,
# no file) at import; started from the FastAPI lifespan (main.py) and thus only
# in a real server — never under the test client, which doesn't run lifespan.
search_analytics = SearchAnalytics.from_settings()
