"""Search analytics (PRIV-1) — GDPR-friendly, aggregated keyword logging.

Exercises the aggregator directly (fast, no server) and end-to-end through the
instrumented search endpoints and the click ping. The whole suite runs with
analytics OFF (conftest sets PARLAMONITOR_SEARCH_ANALYTICS=0), so these tests
opt back in explicitly with a throwaway temp DB.
"""

from __future__ import annotations

import csv
import inspect
import sqlite3

from app import analytics
from app.analytics import SOURCES, SearchAnalytics


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
    # Everything lands under the transcript search unless another box says so.
    assert orban["source"] == "proceedings" and orban["filters"] == ""


def test_every_search_box_gets_its_own_rows(tmp_path, monkeypatch):
    """A keyword typed into different search boxes is counted separately: the box
    is part of the key, and its own filters ride in the canonical `filters`
    string while the shared ones keep their columns."""
    dbp = tmp_path / "a.db"
    sa = SearchAnalytics(str(dbp), enabled=True)
    monkeypatch.setattr(analytics, "_utc_hour", lambda *a, **k: "2026-07-17T09:00")

    sa.record(source="proceedings", query="oktatás", period=[43], results=12)
    sa.record(source="bills", query="oktatás", period=[43], sort="number",
              main_type="T", status="elfogadott", results=3)
    sa.record(source="representatives", query="oktatás", role="advocate",
              sort="name", results=0)
    sa.flush(force=True)

    rows = {r["source"]: r for r in _rows(dbp)}
    assert set(rows) == {"proceedings", "bills", "representatives"}
    assert rows["proceedings"]["filters"] == "" and rows["proceedings"]["period"] == "43"
    # Module filters are sorted by name and joined with ';'.
    assert rows["bills"]["filters"] == "main_type=T;status=elfogadott"
    assert rows["bills"]["sort"] == "number"      # a shared filter keeps its column
    assert rows["bills"]["results"] == 3
    # A filter left at the endpoint's default says nothing and is left out; a
    # zero-result search still flags itself.
    assert rows["representatives"]["filters"] == "role=advocate"
    assert rows["representatives"]["zero_results"] == 1


def test_filter_values_are_canonical(tmp_path, monkeypatch):
    """The same selection always produces the same key: repeated params sort and
    de-duplicate (numerically for cycles), an omitted param and an explicitly
    passed default are the same thing, and a value can never break the
    encoding."""
    dbp = tmp_path / "a.db"
    sa = SearchAnalytics(str(dbp), enabled=True)
    monkeypatch.setattr(analytics, "_utc_hour", lambda *a, **k: "2026-07-17T09:00")

    sa.record(source="bills", query="alma", period=[44, 9, 44], type=["H", "B"])
    sa.record(source="bills", query="alma", period=[9, 44], type=["B", "H"],
              # `number` IS the endpoint's default sort → same key as omitting it
              sort="number", portfolio_role="any")
    # A value carrying the encoding's own separators must not fake a second pair.
    sa.record(source="bills", query="alma", status="a=b;c")
    sa.flush(force=True)

    rows = _rows(dbp)
    assert len(rows) == 2
    both = next(r for r in rows if r["searches"] == 2)
    assert both["period"] == "9,44" and both["filters"] == "type=B,H"
    odd = next(r for r in rows if r["searches"] == 1)
    assert odd["filters"] == "status=a%3Db%3Bc"


def test_source_defaults_match_the_endpoints():
    """The per-source defaults exist so an omitted param in a click ping resolves
    to the same key the endpoint recorded after FastAPI applied its default. If
    an endpoint's default ever changes, this catches the drift — otherwise clicks
    would quietly stop landing on their search."""
    from app.modules.bills.router import list_bills
    from app.modules.committees.router import list_committees, list_meetings
    from app.modules.portfolios.router import list_portfolios
    from app.modules.proceedings.router import search
    from app.modules.representatives.router import (list_officials,
                                                    list_representatives)
    from app.modules.votes.router import list_votes

    endpoints = {"proceedings": search, "bills": list_bills,
                 "representatives": list_representatives,
                 "officials": list_officials, "votes": list_votes,
                 "portfolios": list_portfolios, "committees": list_committees,
                 "committee-meetings": list_meetings}
    assert set(endpoints) == set(SOURCES)   # every source is a real search box
    for source, fn in endpoints.items():
        params = inspect.signature(fn).parameters
        # Every instrumented box takes a keyword, or there is nothing to record.
        assert "q" in params
        for name, default in SOURCES[source].items():
            assert name in params, f"{source}.{name} is not a {fn.__name__} param"
            got = params[name].default
            got = getattr(got, "default", got)          # unwrap Query(...)
            got = "" if got is None or got is Ellipsis else str(got)
            assert got == default, f"{source}.{name}: endpoint defaults to {got!r}"


def test_click_scores_the_search_it_belongs_to(tmp_path, monkeypatch):
    """The first result a reader opens is counted onto the very bucket its search
    was counted into, so clicks/searches is a click-through rate and
    click_rank_sum/clicks the mean first-click rank."""
    dbp = tmp_path / "a.db"
    sa = SearchAnalytics(str(dbp), enabled=True)
    monkeypatch.setattr(analytics, "_utc_hour", lambda *a, **k: "2026-07-17T09:00")

    for _ in range(4):
        sa.record(query="alma", period=[43], sort="relevance", results=50)
    sa.record_click(query="alma", period=[43], sort="relevance", rank=1)
    sa.record_click(query="alma", period=[43], sort="relevance", rank=7)
    sa.flush(force=True)

    rows = _rows(dbp)
    assert len(rows) == 1                      # one bucket, not a click row apart
    assert rows[0]["searches"] == 4 and rows[0]["results"] == 50
    assert rows[0]["clicks"] == 2 and rows[0]["click_rank_sum"] == 8
    assert rows[0]["click_rank_sum"] / rows[0]["clicks"] == 4.0   # mean rank


def test_click_ignores_what_it_cannot_attribute(tmp_path, monkeypatch):
    """A ping naming an unknown search box, carrying no keyword or reporting an
    implausible position is dropped — the mean rank must not be skewed by junk
    (the endpoint is public and unauthenticated like every other one)."""
    dbp = tmp_path / "a.db"
    sa = SearchAnalytics(str(dbp), enabled=True)
    monkeypatch.setattr(analytics, "_utc_hour", lambda *a, **k: "2026-07-17T09:00")

    sa.record_click(source="nem-létező", query="alma", rank=1)
    sa.record_click(query="  ", rank=1)
    sa.record_click(query="alma", rank=0)
    sa.record_click(query="alma", rank=-3)
    sa.record_click(query="alma", rank=10 ** 9)
    sa.record_click(query="alma", rank="nem szám")
    assert sa.flush(force=True) == 0
    # A filter the box does not have can't open a row of its own either.
    sa.record_click(query="alma", rank=2, nincs_ilyen_szuro="x")
    sa.flush(force=True)
    assert _rows(dbp)[0]["filters"] == ""


def test_page_depth_and_zero_results_are_measured(tmp_path, monkeypatch):
    dbp = tmp_path / "a.db"
    sa = SearchAnalytics(str(dbp), enabled=True)
    monkeypatch.setattr(analytics, "_utc_hour", lambda *a, **k: "2026-07-17T09:00")
    sa.record(query="alma", results=90, offset=0)
    sa.record(query="alma", results=90, offset=20)   # paged past the first page
    sa.record(query="alma", results=90, offset=40)
    sa.record(query="körte", results=0)
    sa.flush(force=True)

    rows = {r["query"]: r for r in _rows(dbp)}
    # Paging is a measure, not part of the key: one bucket per search, however
    # deep the reader went.
    assert rows["alma"]["searches"] == 3 and rows["alma"]["paged"] == 2
    assert rows["körte"]["zero_results"] == 1 and rows["körte"]["results"] == 0


def test_migrates_a_v1_file_in_place(tmp_path):
    """The store lives on a host volume and outlives any deploy: a file written
    before the source/filters/quality columns existed keeps its rows, and they
    read as what they were — transcript searches with no module filters and no
    measured hit count (NULL, not a fabricated 0)."""
    dbp = tmp_path / "v1.db"
    conn = sqlite3.connect(dbp)
    conn.executescript("""
        CREATE TABLE search_query_hourly (
            hour TEXT NOT NULL, query TEXT NOT NULL,
            date_from TEXT NOT NULL DEFAULT '', date_to TEXT NOT NULL DEFAULT '',
            period TEXT NOT NULL DEFAULT '', person_id TEXT NOT NULL DEFAULT '',
            faction_id TEXT NOT NULL DEFAULT '', agenda_type TEXT NOT NULL DEFAULT '',
            sort TEXT NOT NULL DEFAULT '', zero_results INTEGER NOT NULL DEFAULT 0,
            searches INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (hour, query, date_from, date_to, period, person_id,
                         faction_id, agenda_type, sort, zero_results)
        ) WITHOUT ROWID;
        INSERT INTO search_query_hourly (hour, query, period, sort, searches)
        VALUES ('2026-07-01T09:00', 'régi', '43', 'relevance', 5);
    """)
    conn.commit()
    conn.close()

    sa = SearchAnalytics(str(dbp), enabled=True)
    sa.record(query="régi", period=[43], sort="relevance", results=12)
    sa.flush(force=True)

    rows = _rows(dbp)
    assert len(rows) == 2                       # a new hour, beside the old row
    old = next(r for r in rows if r["hour"] == "2026-07-01T09:00")
    assert old["searches"] == 5 and old["source"] == "proceedings"
    assert old["filters"] == "" and old["results"] is None   # never measured
    new = next(r for r in rows if r["hour"] != "2026-07-01T09:00")
    assert new["results"] == 12
    # Migration is a one-off: a second open leaves the file alone.
    assert _columns(dbp) == list(analytics._CSV_COLUMNS)
    sa.record(query="újabb")
    sa.flush(force=True)
    assert _columns(dbp) == list(analytics._CSV_COLUMNS)


def test_no_personal_data_columns(tmp_path):
    """The schema stores only hour/keyword/filters/flag/counts — by construction
    there is no place to put an IP, a user agent, a session id or a sub-hour
    timestamp (GDPR data-minimisation). The quality measures added on top are
    plain counters, so they can hold nothing personal either."""
    dbp = tmp_path / "a.db"
    sa = SearchAnalytics(str(dbp), enabled=True)
    sa.record(query="teszt")
    sa.flush(force=True)
    cols = set(_columns(dbp))
    assert cols == {
        "hour", "query", "date_from", "date_to", "period", "person_id",
        "faction_id", "agenda_type", "sort", "zero_results", "searches",
        "source", "filters", "results", "paged", "clicks", "click_rank_sum",
    }
    assert cols == set(analytics._CSV_COLUMNS)   # the CSV is a faithful dump
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
    # Pretend a stale hand-edit, keeping the current header (an outdated header
    # is the one thing that DOES earn a rewrite — see the next test).
    sentinel = ",".join(analytics._CSV_COLUMNS) + "\nSENTINEL\n"
    old.write_text(sentinel, encoding="utf-8")

    # 18th is older than the 2 most-recent days AND its file exists → left alone;
    # 19th + 20th (recent) are rewritten.
    assert sa.export_csv() == 2
    assert old.read_text(encoding="utf-8") == sentinel

    # If a file goes missing it is backfilled even when it is an old day.
    old.unlink()
    assert sa.export_csv() == 3


def test_csv_export_rewrites_a_file_whose_columns_are_outdated(tmp_path, monkeypatch):
    """Every file in the directory carries the same columns: when a column is
    added, even a long-finished day's file is rewritten once so a reader can
    concatenate the whole directory."""
    dbp = tmp_path / "a.db"
    csv_dir = tmp_path / "csv"
    sa = SearchAnalytics(str(dbp), enabled=True, csv_dir=str(csv_dir))
    for day in ("2026-07-18", "2026-07-19", "2026-07-20"):
        monkeypatch.setattr(analytics, "_utc_hour", lambda *a, d=day, **k: f"{d}T09:00")
        sa.record(query="alma", results=7)
        sa.flush(force=True)
    assert sa.export_csv() == 3

    old = csv_dir / "search-analytics-2026-07-18.csv"
    old.write_text("hour,query,searches\n2026-07-18T09:00,alma,1\n", encoding="utf-8")
    assert sa.export_csv() == 3          # the outdated old day rewritten as well
    rows = _read_csv(old)
    assert rows[0] == list(analytics._CSV_COLUMNS)
    col = {c: i for i, c in enumerate(analytics._CSV_COLUMNS)}
    assert rows[1][col["source"]] == "proceedings"
    assert rows[1][col["results"]] == "7"


def test_csv_export_disabled_when_no_dir(tmp_path, monkeypatch):
    dbp = tmp_path / "a.db"
    sa = SearchAnalytics(str(dbp), enabled=True, csv_dir=None)
    monkeypatch.setattr(analytics, "_utc_hour", lambda *a, **k: "2026-07-20T09:00")
    sa.record(query="alma")
    sa.flush(force=True)
    assert sa.export_csv() == 0
    sa._maybe_export_csv()  # no-op, no crash
    assert not any(tmp_path.glob("*.csv"))


def _live_store(tmp_path, monkeypatch, name="endpoint.db"):
    """Point the process-wide aggregator at a throwaway file and switch it on
    (the suite runs with analytics disabled)."""
    from app.analytics import search_analytics as sa

    dbp = tmp_path / name
    monkeypatch.setattr(sa, "enabled", True)
    monkeypatch.setattr(sa, "db_path", str(dbp))
    sa._buckets.clear()
    return sa, dbp


def test_search_endpoint_records_when_enabled(client, tmp_path, monkeypatch):
    """End-to-end: hitting `/search` counts one event with the keyword, filters
    and hit count into the shared store; trend/breakdown do not double-count."""
    sa, dbp = _live_store(tmp_path, monkeypatch)

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
    assert rows[0]["source"] == "proceedings"
    assert rows[0]["results"] == r.json()["total"]   # what the reader was shown


def test_other_search_boxes_record_too(client, tmp_path, monkeypatch):
    """The keyword typed into a module list is counted like the transcript
    search's, under that box's own source; browsing the same list without a
    keyword records nothing."""
    sa, dbp = _live_store(tmp_path, monkeypatch, "modules.db")

    assert client.get("/api/v1/bills", params={"q": "alma", "main_type": "T"}).status_code == 200
    assert client.get("/api/v1/representatives", params={"q": "Kovács"}).status_code == 200
    assert client.get("/api/v1/votes").status_code == 200          # no keyword
    assert client.get("/api/v1/bills").status_code == 200          # no keyword

    sa.flush(force=True)
    rows = {r["source"]: r for r in _rows(dbp)}
    assert set(rows) == {"bills", "representatives"}
    assert rows["bills"]["query"] == "alma" and rows["bills"]["filters"] == "main_type=T"
    assert rows["representatives"]["query"] == "Kovács"
    # The list's own default sort is what got recorded, not an empty string.
    assert rows["representatives"]["sort"] == "name"


def test_click_endpoint_scores_the_search(client, tmp_path, monkeypatch):
    """End-to-end: the SPA's click ping lands on the same bucket the search was
    counted into, and answers 204 whatever it is given."""
    sa, dbp = _live_store(tmp_path, monkeypatch, "clicks.db")

    client.get("/api/v1/proceedings/search", params={"q": "koltsegvetes", "period": 43})
    r = client.post("/api/v1/search/click", json={
        "source": "proceedings", "rank": 3,
        "params": {"q": "koltsegvetes", "period": [43], "limit": 20, "offset": 0},
    })
    assert r.status_code == 204 and not r.content
    # Junk is accepted and dropped, never argued with.
    for bad in ({"source": "nope", "rank": 1, "params": {"q": "x"}},
                {"source": "proceedings", "rank": 0, "params": {"q": "x"}},
                {"source": "proceedings", "rank": 2, "params": {}}):
        assert client.post("/api/v1/search/click", json=bad).status_code == 204

    sa.flush(force=True)
    rows = _rows(dbp)
    assert len(rows) == 1                       # the click joined the search row
    assert rows[0]["searches"] == 1
    assert rows[0]["clicks"] == 1 and rows[0]["click_rank_sum"] == 3


def test_click_endpoint_is_a_noop_when_analytics_is_off(client):
    """Disabled analytics (OPS-4) still answers the ping — the SPA fires it
    regardless — it just counts nothing."""
    from app.analytics import search_analytics as sa

    assert not sa.enabled
    r = client.post("/api/v1/search/click", json={
        "source": "proceedings", "rank": 1, "params": {"q": "alma"}})
    assert r.status_code == 204
    assert not sa._buckets
