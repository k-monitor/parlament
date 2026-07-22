"""Search analytics (PRIV-1) — GDPR-friendly, aggregated keyword logging.

Exercises the aggregator directly (fast, no server) and once end-to-end through
the `/search` endpoint. The whole suite runs with analytics OFF (conftest sets
PARLAMONITOR_SEARCH_ANALYTICS=0), so these tests opt back in explicitly with a
throwaway temp DB.
"""

from __future__ import annotations

import csv
import sqlite3

from app import analytics
from app.analytics import SearchAnalytics


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.reader(fh))


def _rows(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM search_query_hourly ORDER BY query, zero_results")]
    finally:
        conn.close()


def _columns(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(search_query_hourly)")]
    finally:
        conn.close()


def test_aggregates_keywords_and_filters_hourly(tmp_path, monkeypatch):
    dbp = tmp_path / "analytics.db"
    sa = SearchAnalytics(str(dbp), enabled=True)

    # Pin an already-complete hour so the recorded events land in one bucket.
    monkeypatch.setattr(analytics, "_utc_hour", lambda *a, **k: "2026-07-17T09:00")
    # Two searches for the same keyword+filters (with sloppy whitespace on one)
    # must aggregate into a single row with searches=2.
    sa.record(query="  Orbán   Viktor ", period=43, faction_id=7)
    sa.record(query="Orbán Viktor", period=43, faction_id=7)
    # A different keyword with a filter and no results.
    sa.record(query="költségvetés", agenda_type="voting", zero_results=True)

    # Nothing is written until the hour completes (buffered in memory).
    assert not dbp.exists()

    # Advance the clock; the 09:00 bucket is now complete → flushed.
    monkeypatch.setattr(analytics, "_utc_hour", lambda *a, **k: "2026-07-17T10:00")
    assert sa.flush() == 2  # two distinct buckets

    rows = _rows(dbp)
    assert len(rows) == 2
    orban = next(r for r in rows if r["query"] == "Orbán Viktor")
    assert orban["searches"] == 2            # the two identical searches summed
    assert orban["hour"] == "2026-07-17T09:00"
    assert orban["period"] == "43" and orban["faction_id"] == "7"
    assert orban["date_from"] == "" and orban["agenda_type"] == ""
    assert orban["zero_results"] == 0

    kolts = next(r for r in rows if r["query"] == "költségvetés")
    assert kolts["searches"] == 1
    assert kolts["agenda_type"] == "voting" and kolts["zero_results"] == 1


def test_no_personal_data_columns(tmp_path):
    """The schema stores only hour/keyword/filters/flag/count — by construction
    there is no place to put an IP, a user agent, a session id or a sub-hour
    timestamp (GDPR data-minimisation)."""
    dbp = tmp_path / "a.db"
    sa = SearchAnalytics(str(dbp), enabled=True)
    sa.record(query="teszt")
    sa.flush(force=True)
    cols = set(_columns(dbp))
    assert cols == {
        "hour", "query", "date_from", "date_to", "period", "person_id",
        "faction_id", "agenda_type", "sort", "zero_results", "searches",
    }
    forbidden = ("ip", "addr", "agent", "session", "cookie", "user")
    assert not any(bad in c for c in cols for bad in forbidden)
    # `hour` is coarse — an hour bucket, never a full timestamp.
    assert _rows(dbp)[0]["hour"].endswith(":00") and len(_rows(dbp)[0]["hour"]) == 16


def test_current_hour_not_flushed_until_complete(tmp_path, monkeypatch):
    dbp = tmp_path / "a.db"
    sa = SearchAnalytics(str(dbp), enabled=True)
    monkeypatch.setattr(analytics, "_utc_hour", lambda *a, **k: "2026-07-17T09:00")
    sa.record(query="teszt")
    # Same hour still in progress → the periodic flush writes nothing.
    assert sa.flush() == 0
    # A forced (shutdown) flush persists the partial hour so nothing is lost.
    assert sa.flush(force=True) == 1
    assert _rows(dbp)[0]["searches"] == 1


def test_repeated_flush_accumulates_across_processes(tmp_path, monkeypatch):
    """Two separate aggregators writing the same file (as two workers / deploy
    colors would) must ADD their counts via the UPSERT, not clobber."""
    dbp = tmp_path / "a.db"
    monkeypatch.setattr(analytics, "_utc_hour", lambda *a, **k: "2026-07-17T09:00")

    a = SearchAnalytics(str(dbp), enabled=True)
    b = SearchAnalytics(str(dbp), enabled=True)
    a.record(query="közös")
    b.record(query="közös")
    a.flush(force=True)
    b.flush(force=True)

    rows = _rows(dbp)
    assert len(rows) == 1 and rows[0]["searches"] == 2


def test_disabled_is_a_noop(tmp_path):
    dbp = tmp_path / "a.db"
    sa = SearchAnalytics(str(dbp), enabled=False)
    sa.record(query="teszt")
    sa.start()          # no thread, no file
    assert sa.flush(force=True) == 0
    assert not dbp.exists()


def test_normalisation_and_empty_query(tmp_path, monkeypatch):
    dbp = tmp_path / "a.db"
    sa = SearchAnalytics(str(dbp), enabled=True)
    monkeypatch.setattr(analytics, "_utc_hour", lambda *a, **k: "2026-07-17T09:00")
    sa.record(query="   ")          # nothing loggable → dropped
    sa.record(query=None)           # dropped
    long = "a" * 500
    sa.record(query=long)           # length-capped
    sa.flush(force=True)
    rows = _rows(dbp)
    assert len(rows) == 1
    assert len(rows[0]["query"]) == 200


def test_csv_export_writes_one_file_per_day(tmp_path, monkeypatch):
    """The daily export dumps the aggregates to per-UTC-day CSV files in a
    host-accessible directory, faithfully mirroring the (non-personal) columns."""
    dbp = tmp_path / "a.db"
    csv_dir = tmp_path / "csv"
    sa = SearchAnalytics(str(dbp), enabled=True, csv_dir=str(csv_dir))

    # Day one: two identical searches → one bucket, searches=2.
    monkeypatch.setattr(analytics, "_utc_hour", lambda *a, **k: "2026-07-20T09:00")
    sa.record(query="alma", period=43)
    sa.record(query="alma", period=43)
    monkeypatch.setattr(analytics, "_utc_hour", lambda *a, **k: "2026-07-20T10:00")
    assert sa.flush() == 1  # 09:00 bucket complete
    # Day two: a different keyword.
    monkeypatch.setattr(analytics, "_utc_hour", lambda *a, **k: "2026-07-21T08:00")
    sa.record(query="körte", zero_results=True)
    monkeypatch.setattr(analytics, "_utc_hour", lambda *a, **k: "2026-07-21T09:00")
    assert sa.flush() == 1  # 08:00 bucket complete

    assert sa.export_csv() == 2
    d1 = csv_dir / "search-analytics-2026-07-20.csv"
    d2 = csv_dir / "search-analytics-2026-07-21.csv"
    assert d1.exists() and d2.exists()

    rows1 = _read_csv(d1)
    assert rows1[0] == list(analytics._CSV_COLUMNS)  # header mirrors the schema
    assert len(rows1) == 2  # header + one data row
    col = {c: i for i, c in enumerate(analytics._CSV_COLUMNS)}
    assert rows1[1][col["hour"]] == "2026-07-20T09:00"
    assert rows1[1][col["query"]] == "alma"
    assert rows1[1][col["period"]] == "43"
    assert rows1[1][col["searches"]] == "2"

    rows2 = _read_csv(d2)
    assert rows2[1][col["query"]] == "körte"
    assert rows2[1][col["zero_results"]] == "1"

    # No personal-data columns leak into the CSV either (same guarantee as the DB).
    forbidden = ("ip", "addr", "agent", "session", "cookie", "user")
    assert not any(bad in h for h in rows1[0] for bad in forbidden)


def test_csv_export_runs_at_most_once_per_day(tmp_path, monkeypatch):
    dbp = tmp_path / "a.db"
    sa = SearchAnalytics(str(dbp), enabled=True, csv_dir=str(tmp_path / "csv"))
    monkeypatch.setattr(analytics, "_utc_hour", lambda *a, **k: "2026-07-20T09:00")
    sa.record(query="alma")
    sa.flush(force=True)

    calls = []
    monkeypatch.setattr(sa, "export_csv", lambda: calls.append(1))
    sa._maybe_export_csv()   # first this day → export
    sa._maybe_export_csv()   # same day → no-op
    assert len(calls) == 1
    monkeypatch.setattr(analytics, "_utc_hour", lambda *a, **k: "2026-07-21T00:00")
    sa._maybe_export_csv()   # day rolled over → export again
    assert len(calls) == 2


def test_csv_export_backfills_missing_day_but_leaves_old_files(tmp_path, monkeypatch):
    """An older day is (re)written only when its file is missing; the two most
    recent days are always refreshed (so the just-ended day gets finalised)."""
    dbp = tmp_path / "a.db"
    csv_dir = tmp_path / "csv"
    sa = SearchAnalytics(str(dbp), enabled=True, csv_dir=str(csv_dir))
    for day in ("2026-07-18", "2026-07-19", "2026-07-20"):
        monkeypatch.setattr(analytics, "_utc_hour", lambda *a, d=day, **k: f"{d}T09:00")
        sa.record(query="alma")
        sa.flush(force=True)

    assert sa.export_csv() == 3  # all three written the first time
    old = csv_dir / "search-analytics-2026-07-18.csv"
    old.write_text("SENTINEL\n", encoding="utf-8")  # pretend a stale hand-edit

    # 18th is older than the 2 most-recent days AND its file exists → left alone;
    # 19th + 20th (recent) are rewritten.
    assert sa.export_csv() == 2
    assert old.read_text(encoding="utf-8") == "SENTINEL\n"

    # If a file goes missing it is backfilled even when it is an old day.
    old.unlink()
    assert sa.export_csv() == 3


def test_csv_export_disabled_when_no_dir(tmp_path, monkeypatch):
    dbp = tmp_path / "a.db"
    sa = SearchAnalytics(str(dbp), enabled=True, csv_dir=None)
    monkeypatch.setattr(analytics, "_utc_hour", lambda *a, **k: "2026-07-20T09:00")
    sa.record(query="alma")
    sa.flush(force=True)
    assert sa.export_csv() == 0
    sa._maybe_export_csv()  # no-op, no crash
    assert not any(tmp_path.glob("*.csv"))


def test_search_endpoint_records_when_enabled(client, tmp_path, monkeypatch):
    """End-to-end: hitting `/search` counts one event with the keyword + filters
    into the shared store; trend/breakdown do not double-count."""
    from app.modules.proceedings.router import search_analytics as sa

    dbp = tmp_path / "endpoint.db"
    monkeypatch.setattr(sa, "enabled", True)
    monkeypatch.setattr(sa, "db_path", str(dbp))
    sa._buckets.clear()

    r = client.get("/api/v1/proceedings/search",
                   params={"q": "koltsegvetes", "period": 43})
    assert r.status_code == 200
    # trend + breakdown fire for the same query in the SPA — they must NOT record.
    client.get("/api/v1/proceedings/search/trend", params={"q": "koltsegvetes"})
    client.get("/api/v1/proceedings/search/breakdown", params={"q": "koltsegvetes"})

    assert sa.flush(force=True) == 1
    rows = _rows(dbp)
    assert len(rows) == 1
    assert rows[0]["query"] == "koltsegvetes"
    assert rows[0]["period"] == "43"
    assert rows[0]["searches"] == 1
