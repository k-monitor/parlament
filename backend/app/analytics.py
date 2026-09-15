"""Privacy-respecting search analytics (PRIV-1).

GDPR-friendly, aggregated logging of what people search for — the search
*keywords*, the *filters* they combine with them, and how *well* the search
answered them — WITHOUT any personal data:

  * NO IP addresses, user agents, cookies or session identifiers are read or
    stored (the endpoints never even look at the request's network metadata).
  * NO exact timestamps — events are counted into whole-hour buckets, so the
    finest time resolution ever persisted is "this term was searched N times in
    the 14:00–15:00 (UTC) hour".
  * Only the aggregate COUNTS per ``(hour, source, query, filters)`` tuple are
    written — never a per-request row that could be correlated back to a person.

Every search box on the site is counted, not just the transcript search: the
``source`` column names which one (see :data:`SOURCES`). The filters several
boxes share (date range, cycle, speaker, faction, agenda type, sort) each keep
their own column — those are also the original v1 columns, so an existing row
keeps its exact meaning — and every module-specific filter is folded into one
canonical ``filters`` string (``k=v;k=v``, sorted, non-default values only).

Beside the plain search count each bucket carries a small set of QUALITY
measures, so the aggregates say not only what was searched but whether it
worked:

  * ``results``        — how many hits the query matched (NULL = not measured;
                         rows written before this column existed, and click-only
                         rows, have no count).
  * ``paged``          — how many of those searches asked for a page past the
                         first (the reader had to dig).
  * ``clicks``         — how many of them ended with the reader opening a result.
  * ``click_rank_sum`` — the sum of the 1-based positions of those first-clicked
                         results, so ``click_rank_sum / clicks`` is the MEAN
                         FIRST-CLICK RANK: 1.0 means readers take the top hit,
                         higher means they scroll past it. With ``clicks /
                         searches`` (the click-through rate) that is the
                         search-quality signal — no per-user data needed, since
                         both sides are plain counters.

The click half arrives from a separate ``POST /api/v1/search/click`` ping fired
by the SPA when a result is opened (main.py). It carries the same query+filters
the search did, so it lands on the same bucket; it holds no identifier of any
kind, and one executed search can contribute at most one click (the SPA arms the
ping per search and disarms it on the first result opened).

Events are accumulated in memory and flushed once an hour to a SEPARATE SQLite
file — never the read-only content DB (db.py). That file is meant to live on a
host-mounted volume (``PARLAMONITOR_ANALYTICS_DB``) so the aggregates can be read
from OUTSIDE the container. Several uvicorn workers, and both blue/green deploy
colors during a swap, write the same file concurrently; every write is an
accumulating UPSERT (``searches = searches + excluded.searches``), so their
contributions add up rather than clobber one another.

On top of the SQLite store the aggregates are also exported to **plain CSV, once
per UTC day** (``PARLAMONITOR_ANALYTICS_CSV_DIR``, defaulting to a ``csv/``
sub-directory next to the DB — i.e. the same host-mounted location), one file per
day (``search-analytics-YYYY-MM-DD.csv``), so the stats can be read straight off
the host without opening SQLite. Each file is written atomically (temp + rename),
so a reader — and the several worker/color processes, which all read the same
shared DB — always see a complete file.

Recording is best-effort and must never affect a search response: ``record()``
only touches an in-memory counter under a short lock and swallows its own errors,
and a misconfigured / unwritable analytics path disables the logger instead of
taking the API down.
"""

from __future__ import annotations

import csv
import datetime as dt
import logging
import os
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
# Same for a single filter value and for the whole canonical filters string.
_MAX_VALUE_LEN = 80
_MAX_FILTERS_LEN = 400
# Backstop against unbounded memory if a single hour sees a pathological number
# of DISTINCT (query, filters) tuples: past this many live keys we stop creating
# NEW ones (existing keys keep counting) until the next flush. Realistic traffic
# never approaches this.
_MAX_LIVE_KEYS = 100_000
# Sanity bound on a reported click position (the ping is unauthenticated, like
# every other public endpoint — a nonsense rank must not skew the mean).
_MAX_CLICK_RANK = 100_000
_WS_RE = re.compile(r"\s+")

# The filters that several search boxes have in common keep a column of their
# own — they are the columns v1 already had, so an existing row keeps its exact
# meaning. A search box's remaining filters go into the `filters` string.
_SHARED_FIELDS = ("date_from", "date_to", "period", "person_id", "faction_id",
                  "agenda_type", "sort")

# Every instrumented search box: the `source` value it is recorded under, mapped
# to the filter parameters it accepts BEYOND the shared ones above, each with the
# endpoint's own default value.
#
# The defaults matter for more than documentation: a click ping echoes the params
# the SPA searched with, and an omitted param there has to resolve to the same
# key the endpoint recorded after FastAPI applied its default — otherwise the
# click would land on a row of its own instead of on the search it belongs to.
# `test_analytics.py` asserts these against the endpoint signatures so the two
# cannot drift apart.
SOURCES: dict[str, dict[str, str]] = {
    # The transcript full-text search (§5.1) — all its filters are shared ones.
    "proceedings": {"sort": "relevance"},
    # The iromány list (§6): the bills page (main_type=T), the kérdések page
    # (main_type_in=A,I,K) and the all-irományok page (no fotipus scope at all)
    # are one endpoint under three filter sets — the `filters` string tells them
    # apart.
    "bills": {"sort": "number", "main_type": "", "main_type_not": "",
              "main_type_in": "", "main_type_not_in": "", "type": "",
              "status": "", "sponsor": "", "portfolio": "",
              "portfolio_role": "any", "portfolio_period": "",
              "answer_verdict": "", "answer_state": ""},
    # The representative list (REP-1) — `role` picks the MP / advocate / other
    # sub-tab, so it is a filter like any other here.
    "representatives": {"sort": "name", "role": "mp", "constituency": "",
                        "nationality": ""},
    # The office-holder registry (REP-11).
    "officials": {"sort": "start", "category": "", "status": "all",
                  "started": "all"},
    # The roll-call vote list.
    "votes": {"sort": "date_desc", "result": "", "voting_mode": "", "bill": "",
              "person": "", "value": ""},
    # The tárca list (§6C) — not paginated, so it has no sort.
    "portfolios": {"kind": ""},
}

# Parameter names a caller may pass that are NOT filters — they are either the
# event's own payload or paging noise. Kept out of the key, and stripped from a
# client-supplied params dict before it can collide with a keyword argument.
RESERVED_PARAMS = frozenset({"q", "query", "source", "rank", "results",
                             "zero_results", "limit", "offset"})

# The analytics schema deliberately holds ONLY the hour bucket, the search box,
# the keyword, the search filters, a zero-result flag and counts. There is no
# column for an IP, a user agent, a session id or a sub-hour timestamp — by
# construction there is nothing personal to store.
#
# Column order mirrors _CSV_COLUMNS (v1's columns first, the ones added since
# appended), so `SELECT *` and the exported CSV read the same way and an existing
# CSV consumer keeps finding its columns where they were.
_TABLE = "search_query_hourly"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_query_hourly (
    -- Whole-hour bucket in UTC, "YYYY-MM-DDTHH:00". Deliberately coarse: no
    -- finer-grained time is ever stored (GDPR data-minimisation).
    hour         TEXT    NOT NULL,
    -- The search keyword(s): trimmed + whitespace-collapsed, otherwise as typed.
    query        TEXT    NOT NULL,
    -- The SHARED search FILTERS ('' = filter not used). Stored as TEXT so the
    -- UPSERT key is reliable: a UNIQUE index treats NULLs as DISTINCT in SQLite,
    -- which would defeat the aggregation for the (very common) no-filter case.
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
    -- Which search box the keyword was typed into (see SOURCES). Defaulted to
    -- the transcript search: that was the only instrumented one in v1, so a
    -- migrated row says exactly where it came from.
    source       TEXT    NOT NULL DEFAULT 'proceedings',
    -- The search box's OWN filters, canonically encoded as "k=v;k=v" sorted by
    -- name, only the ones set to something other than their default. '=' and ';'
    -- inside a value are percent-escaped, so the string always splits back.
    filters      TEXT    NOT NULL DEFAULT '',
    -- How many hits the query matched. NULL = not measured (a row written before
    -- this column existed, or one that only ever received a click).
    results      INTEGER,
    -- How many of `searches` asked for a page past the first.
    paged        INTEGER NOT NULL DEFAULT 0,
    -- How many of `searches` ended with the reader opening a result, and the sum
    -- of those results' 1-based positions in the list. clicks/searches is the
    -- click-through rate; click_rank_sum/clicks the mean first-click rank.
    clicks       INTEGER NOT NULL DEFAULT 0,
    click_rank_sum INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour, query, date_from, date_to, period, person_id,
                 faction_id, agenda_type, sort, zero_results, source, filters)
) WITHOUT ROWID;
"""

# Order of the in-memory key tuple → the key columns above (hour FIRST so a plain
# string compare tells whether a bucket's hour is already complete).
_KEY_FIELDS = ("hour", "query", "date_from", "date_to", "period", "person_id",
               "faction_id", "agenda_type", "sort", "zero_results", "source",
               "filters")
# The accumulating measures, in the order they are held in a bucket's value.
_MEASURES = ("searches", "results", "paged", "clicks", "click_rank_sum")

# CSV export column order: every stored column, hour first, v1's columns in their
# original positions so an existing reader is unaffected. Kept in sync with the
# SQLite schema — the export is a faithful dump, so a CSV reader sees exactly the
# same (non-personal) fields as the DB.
_CSV_COLUMNS = ("hour", "query", "date_from", "date_to", "period", "person_id",
                "faction_id", "agenda_type", "sort", "zero_results", "searches",
                "source", "filters", "results", "paged", "clicks",
                "click_rank_sum")

_UPSERT = f"""
INSERT INTO {_TABLE} ({", ".join(_CSV_COLUMNS)})
VALUES ({", ".join(":" + c for c in _CSV_COLUMNS)})
ON CONFLICT (hour, query, date_from, date_to, period, person_id, faction_id,
             agenda_type, sort, zero_results, source, filters)
DO UPDATE SET
    searches       = searches + excluded.searches,
    -- The hit count is a property of the query, not a total to sum. max() keeps
    -- the merge order-independent (workers/colors flush in any order); COALESCE
    -- carries the known side over, since max() with a NULL argument is NULL.
    results        = COALESCE(max(results, excluded.results),
                              excluded.results, results),
    paged          = paged + excluded.paged,
    clicks         = clicks + excluded.clicks,
    click_rank_sum = click_rank_sum + excluded.click_rank_sum;
"""

# On each daily export, always (re)write this many of the most recent days present
# in the data: the day that just ended still receives its final hour(s) shortly
# after midnight, so it must be rewritten one run after it "became yesterday".
# Older days are immutable and written once (then only backfilled if a file went
# missing, or if its header predates a column being added). Two gives a one-run
# safety margin if a rollover export was ever missed.
_CSV_REWRITE_RECENT_DAYS = 2


def _utc_hour(now: Optional[dt.datetime] = None) -> str:
    """The current whole-hour bucket in UTC as ``YYYY-MM-DDTHH:00`` (fixed-width,
    so lexical ordering equals chronological ordering)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:00")


def _utc_day(now: Optional[dt.datetime] = None) -> str:
    """The current UTC day as ``YYYY-MM-DD``. Derived from :func:`_utc_hour` so a
    test that pins the hour pins the day too."""
    return _utc_hour(now)[:10]


def _norm_query(q: str | None) -> str:
    """Trim, collapse internal whitespace and length-cap a raw query. Returns ''
    for a query with nothing loggable."""
    if not q:
        return ""
    return _WS_RE.sub(" ", str(q)).strip()[:_MAX_QUERY_LEN]


def _norm_value(v) -> str:
    """One filter value as its canonical key string.

    A repeatable param (cycle scope, multi-choice type/status) collapses to a
    de-duplicated, sorted comma list — numerically when the values are numbers —
    so the same selection always produces the same key whatever order it arrived
    in. Everything else is the trimmed, length-capped string form."""
    if v is None or v is False:
        return ""
    if v is True:
        return "1"
    if isinstance(v, (list, tuple, set, frozenset)):
        items = [s for s in (str(x).strip() for x in v) if s]
        items = list(dict.fromkeys(items))
        if all(s.lstrip("-").isdigit() for s in items):
            items.sort(key=int)
        else:
            items.sort()
        return ",".join(items)[:_MAX_VALUE_LEN]
    return _WS_RE.sub(" ", str(v)).strip()[:_MAX_VALUE_LEN]


def _esc(v: str) -> str:
    """Escape the two separators of the `filters` encoding (and the escape
    character itself), so the string always splits back into its pairs."""
    return v.replace("%", "%25").replace(";", "%3B").replace("=", "%3D")


def _split_fields(source: str, params: dict) -> tuple[dict, str]:
    """Split a search box's parameters into ``(shared columns, filters string)``.

    Only parameters the box actually has are kept (:data:`SOURCES` + the shared
    ones) — anything else, including whatever a hand-crafted click ping might
    carry, is dropped rather than given a row of its own. A missing or empty
    value resolves to the endpoint's default, so an omitted param and an
    explicitly-passed default produce the SAME key."""
    spec = SOURCES[source]
    shared = {}
    for name in _SHARED_FIELDS:
        shared[name] = _norm_value(params.get(name)) or spec.get(name, "")
    extra = []
    for name, default in spec.items():
        if name in _SHARED_FIELDS:
            continue
        val = _norm_value(params.get(name)) or default
        # Only what the reader actually changed: a filter left at its default
        # says nothing, and keeping the string short keeps it readable.
        if val and val != default:
            extra.append(f"{name}={_esc(val)}")
    return shared, ";".join(sorted(extra))[:_MAX_FILTERS_LEN]


class SearchAnalytics:
    """In-memory hourly aggregator that flushes to a standalone SQLite file.

    Thread-safe: many request threads call :meth:`record` while a single daemon
    thread flushes. Also process-safe across workers/colors via the accumulating
    UPSERT — see the module docstring.
    """

    def __init__(self, db_path: str, enabled: bool = True,
                 flush_interval: float = 60.0, csv_dir: str | None = None):
        self.db_path = db_path
        self.enabled = enabled
        # Directory the daily CSV export is written to (None disables the export
        # while keeping the SQLite store). See export_csv().
        self.csv_dir = csv_dir
        self._flush_interval = flush_interval
        self._lock = threading.Lock()
        self._buckets: dict[tuple, list] = {}
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._warned_full = False
        # The last UTC day the CSV export ran for — so it fires at most once a day.
        self._last_csv_day: str | None = None

    @classmethod
    def from_settings(cls) -> "SearchAnalytics":
        # Resolve the default path lazily from settings.db_path so a db_path
        # overridden after construction (tests monkeypatch it) is still honoured.
        path = settings.analytics_db or str(
            Path(settings.db_path).resolve().parent / "search-analytics.db")
        # Default the CSV export next to the DB (→ the host-mounted analytics dir);
        # only when both analytics and the CSV export are enabled.
        csv_dir: str | None = None
        if settings.search_analytics and settings.analytics_csv_export:
            csv_dir = settings.analytics_csv_dir or str(
                Path(path).resolve().parent / "csv")
        return cls(db_path=path, enabled=settings.search_analytics, csv_dir=csv_dir)

    # -- recording (hot path: in-memory only, never touches disk) -------------
    def record(self, *, query, source: str = "proceedings", results=None,
               offset=0, zero_results=None, **filters) -> None:
        """Count one executed search into the current hour's bucket.

        ``source`` names the search box (:data:`SOURCES`); ``**filters`` takes
        that box's filter params as the endpoint received them, ``results`` the
        number of hits it matched and ``offset`` the page it asked for. Cheap and
        exception-safe — it must never disturb the search response."""
        self._add(source, query, filters, zero_results=zero_results,
                  searches=1, results=results,
                  paged=1 if _as_int(offset) else 0)

    def record_click(self, *, query, source: str = "proceedings", rank,
                     **filters) -> None:
        """Count the FIRST result a reader opened from a search, as its 1-based
        position in the result list (SEA-12 search quality).

        Lands on the same bucket the search itself was counted into — the caller
        passes back the same query + filters — unless the hour rolled over in
        between, in which case it opens a click-only bucket (``searches`` 0) that
        still aggregates correctly over any longer window. A search that returned
        nothing can have no click, so the bucket is always the ``zero_results=0``
        one."""
        rank = _as_int(rank)
        if not 1 <= rank <= _MAX_CLICK_RANK:
            return
        self._add(source, query, filters, zero_results=False,
                  clicks=1, click_rank_sum=rank)

    def _add(self, source, query, filters: dict, *, zero_results=None,
             searches: int = 0, results=None, paged: int = 0, clicks: int = 0,
             click_rank_sum: int = 0) -> None:
        """Merge one event's measures into its hour bucket. Swallows its own
        errors: analytics must never break a request."""
        if not self.enabled:
            return
        try:
            if source not in SOURCES:      # unknown search box → not recorded
                return
            q = _norm_query(query)
            if not q:
                return
            if zero_results is None:       # derived unless the caller stated it
                zero_results = results is not None and _as_int(results) == 0
            shared, extra = _split_fields(source, filters)
            key = (
                _utc_hour(), q,
                shared["date_from"], shared["date_to"], shared["period"],
                shared["person_id"], shared["faction_id"], shared["agenda_type"],
                shared["sort"],
                1 if zero_results else 0,
                source, extra,
            )
            add = [searches, None if results is None else _as_int(results),
                   paged, clicks, click_rank_sum]
            with self._lock:
                cur = self._buckets.get(key)
                if cur is not None:
                    _merge(cur, add)
                elif len(self._buckets) < _MAX_LIVE_KEYS:
                    self._buckets[key] = add
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
                _ensure_schema(conn)
                rows = [dict(zip(_KEY_FIELDS, k), **dict(zip(_MEASURES, v)))
                        for k, v in items]
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
                    cur = self._buckets.get(k)
                    if cur is None:
                        self._buckets[k] = v
                    else:
                        _merge(cur, v)
            return 0

    # -- CSV export (cold path: once a day) ----------------------------------
    def export_csv(self) -> int:
        """Dump the aggregated analytics to per-UTC-day CSV files in
        ``self.csv_dir`` — a host-accessible location — so the stats can be read
        without opening SQLite. One file per day,
        ``search-analytics-YYYY-MM-DD.csv``, holding every ``(hour, keyword,
        filters, …)`` bucket whose hour falls on that day.

        A completed day is immutable, so its file is written once (the run after
        the day ends) and then left untouched; only the most recent
        ``_CSV_REWRITE_RECENT_DAYS`` days are rewritten each run, plus any day
        whose file is missing or whose header predates a column being added
        (self-healing backfill — every file in the directory always carries the
        same columns). Each file is written atomically (temp + rename), so a
        reader — and the concurrent worker / color processes, which all read the
        same shared DB — always see a complete file. Returns the number of files
        written. Best-effort: never raises."""
        if not self.enabled or not self.csv_dir:
            return 0
        # Reading a not-yet-created DB would leave an empty file behind — skip.
        if not Path(self.db_path).exists():
            return 0
        try:
            conn = sqlite3.connect(self.db_path, timeout=30)
            try:
                conn.execute("PRAGMA busy_timeout=30000")
                rows = conn.execute(
                    "SELECT " + ", ".join(_CSV_COLUMNS)
                    + f" FROM {_TABLE} ORDER BY hour, query").fetchall()
            finally:
                conn.close()
        except sqlite3.OperationalError:
            return 0  # table absent (nothing recorded yet) — nothing to export
        except Exception:
            logger.exception("search analytics CSV export: read failed")
            return 0

        by_day: dict[str, list] = {}
        for row in rows:
            by_day.setdefault(row[0][:10], []).append(row)  # row[0] == hour
        if not by_day:
            return 0

        out_dir = Path(self.csv_dir)
        recent = set(sorted(by_day)[-_CSV_REWRITE_RECENT_DAYS:])
        written = 0
        for day, day_rows in by_day.items():
            path = out_dir / f"search-analytics-{day}.csv"
            # Rewrite the recent days always; write an older day only if its file
            # is missing or carries an outdated column set (it never changes once
            # the day is over and the schema has settled).
            if (day in recent or not path.exists()
                    or _csv_header(path) != list(_CSV_COLUMNS)):
                if self._write_day_csv(path, day_rows):
                    written += 1
        return written

    def _write_day_csv(self, path: Path, rows: list) -> bool:
        """Atomically write one day's rows to ``path`` (temp file in the same dir
        + rename). A per-PID temp name keeps concurrent workers from clobbering
        each other's in-progress file. Best-effort — returns False on any error."""
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(_CSV_COLUMNS)
                writer.writerows(rows)
            os.replace(tmp, path)
            return True
        except Exception:
            logger.exception("search analytics CSV export: write failed for %s", path)
            try:
                tmp.unlink()
            except OSError:
                pass
            return False

    def _maybe_export_csv(self) -> None:
        """Run :meth:`export_csv` at most once per UTC day (the first call after
        start, then whenever the day rolls over). A cheap no-op the rest of the
        day, and when the export is disabled."""
        if not self.csv_dir:
            return
        today = _utc_day()
        if today == self._last_csv_day:
            return
        self._last_csv_day = today
        self.export_csv()

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
                conn.execute("PRAGMA busy_timeout=30000")
                conn.execute("PRAGMA journal_mode=WAL")
                _ensure_schema(conn)
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
        if self.csv_dir:
            logger.info("search analytics daily CSV export → %s", self.csv_dir)
        # Export immediately so a freshly (re)started server publishes an
        # up-to-date CSV without waiting for the next midnight rollover.
        self._maybe_export_csv()

    def _run(self) -> None:
        # wait() returns True the moment stop() sets the event → prompt shutdown.
        while not self._stop_event.wait(self._flush_interval):
            self.flush()
            # Once a day, publish the completed days as host-readable CSV files
            # (flush first so the just-ended day's final hour is on disk).
            self._maybe_export_csv()

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


def _as_int(v) -> int:
    """Best-effort int for a value off the wire (a click ping is client-supplied).
    Anything unusable counts as 0 rather than raising into a request."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _merge(dst: list, src: list) -> None:
    """Accumulate one bucket's measures into another, in place — the in-memory
    twin of the UPSERT: counters add up, the hit count takes the larger known
    value (it is a property of the query, not a total)."""
    dst[0] += src[0]
    if src[1] is not None:
        dst[1] = src[1] if dst[1] is None else max(dst[1], src[1])
    dst[2] += src[2]
    dst[3] += src[3]
    dst[4] += src[4]


def _csv_header(path: Path) -> list[str] | None:
    """The column names of an already-exported day file, or None if unreadable."""
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return next(csv.reader(fh), [])
    except OSError:
        return None


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the analytics table, or migrate a pre-`source` file in place.

    The store lives on a host volume and outlives any single deploy, so a file
    written by an older build has to keep working: its rows are carried over as
    what they were (transcript searches with no module filters), the measures
    added since start empty, and `results` stays NULL — "never measured" rather
    than a fabricated zero."""
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({_TABLE})")]
    if not cols:
        conn.execute(_SCHEMA)            # one statement — never executescript()
        return
    if "source" in cols:
        return
    # The new columns join the PRIMARY KEY, which ALTER TABLE cannot do — so the
    # table is rebuilt and the old rows copied across. Both are transactional in
    # SQLite, and the whole rebuild runs inside one write transaction, so a
    # concurrent worker/color either sees the old table or the new one.
    carried = [c for c in cols if c in _CSV_COLUMNS]
    prev_isolation = conn.isolation_level
    conn.isolation_level = None          # explicit control: DDL + DML in one txn
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Re-check under the write lock: another process may have migrated the
        # shared file between our probe above and acquiring the lock.
        if any(r[1] == "source" for r in
               conn.execute(f"PRAGMA table_info({_TABLE})")):
            conn.execute("ROLLBACK")
            return
        try:
            # execute(), not executescript(): the latter COMMITs any pending
            # transaction first, which would take the rebuild apart.
            conn.execute(_SCHEMA.replace(_TABLE, f"{_TABLE}_new"))
            cl = ", ".join(carried)
            conn.execute(f"INSERT INTO {_TABLE}_new ({cl}) "
                         f"SELECT {cl} FROM {_TABLE}")
            conn.execute(f"DROP TABLE {_TABLE}")
            conn.execute(f"ALTER TABLE {_TABLE}_new RENAME TO {_TABLE}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        logger.info("search analytics: migrated %s to the v2 schema "
                    "(source/filters/results/paged/clicks)", _TABLE)
    finally:
        conn.isolation_level = prev_isolation


# Process-wide singleton the API records against. Constructed inert (no thread,
# no file) at import; started from the FastAPI lifespan (main.py) and thus only
# in a real server — never under the test client, which doesn't run lifespan.
search_analytics = SearchAnalytics.from_settings()
