"""Search analytics (PRIV-1) — GDPR-friendly, aggregated keyword logging.

Exercises the aggregator directly (fast, no server) and once end-to-end through
the `/search` endpoint. The whole suite runs with analytics OFF (conftest sets
PARLAMONITOR_SEARCH_ANALYTICS=0), so these tests opt back in explicitly with a
throwaway temp DB.
"""

from __future__ import annotations

import sqlite3

from app import analytics
from app.analytics import SearchAnalytics


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
