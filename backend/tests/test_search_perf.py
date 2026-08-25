"""The search fast paths, and the guarantees they must not break.

Three optimizations share this file because they share one risk: each makes the
search do *less* work, and the only thing worth testing about them is that the
answer is unchanged.

* the cycle → sentence-id range (`period_sentence_range`), a bound ANDed onto the
  cycle filter so FTS5 can skip doclist entries instead of fetching a row per
  match to reject it;
* memoization of `/search` itself, which must not also memoize the *count* of
  searches people ran;
* the wall-clock budget that stops a query no scope can make cheap.
"""

from __future__ import annotations

import sqlite3

import pytest

import app.query_cache as qc
from app.db import sentence_id_range
from app.loader import rebuild_period_sentence_ranges


# ---------------------------------------------------------------------------
# The range table itself
# ---------------------------------------------------------------------------

def test_loader_records_a_range_per_cycle(conn):
    """Every cycle with sentences gets a row, and it is the true span."""
    rows = conn.execute(
        "SELECT period_number, first_id, last_id FROM period_sentence_range").fetchall()
    assert rows, "the loader should have recorded at least the fixture's cycle"
    for r in rows:
        real = conn.execute(
            """SELECT MIN(se.id), MAX(se.id) FROM sentence se
               JOIN speech sp ON sp.uid = se.speech_id
               WHERE sp.period_number = ?""", (r["period_number"],)).fetchone()
        assert (r["first_id"], r["last_id"]) == tuple(real)


def test_range_bounds_the_scope(conn):
    """The bound must contain every sentence in scope — that is the whole
    correctness requirement, and it is what a query is entitled to assume."""
    lo, hi = sentence_id_range(conn, [43])
    outside = conn.execute(
        """SELECT COUNT(*) FROM sentence se JOIN speech sp ON sp.uid = se.speech_id
           WHERE sp.period_number = 43 AND (se.id < ? OR se.id > ?)""",
        (lo, hi)).fetchone()[0]
    assert outside == 0


def test_no_range_for_all_cycles(conn):
    """An unscoped search has nothing to narrow to."""
    assert sentence_id_range(conn, []) is None
    assert sentence_id_range(conn, None) is None


def test_no_range_when_a_cycle_is_unplaceable(conn):
    """A cycle with no row has an unknown span, so nothing can be bounded — the
    query must fall back to the exact predicate rather than invent a bound."""
    assert sentence_id_range(conn, [43]) is not None
    assert sentence_id_range(conn, [43, 99]) is None   # 99 was never loaded
    assert sentence_id_range(conn, [99]) is None


def test_no_range_when_table_is_absent(conn):
    """A DB built before the table existed searches exactly as it always did."""
    conn.execute("DROP TABLE period_sentence_range")
    assert sentence_id_range(conn, [43]) is None


# ---------------------------------------------------------------------------
# The range under adversarial data
# ---------------------------------------------------------------------------

@pytest.fixture
def interleaved(db_path):
    """A cycle whose sentences were appended *after* a later cycle's — which is
    what an `--update` backfilling an old sitting would produce.

    This is the case the design has to survive: cycle 42's sentence gets a higher
    id than every cycle-43 sentence, so the two cycles' ranges overlap and 42's
    bound also spans all of 43. The bound is then useless, but it must still be
    correct, because the exact `period_number` predicate is what actually filters."""
    c = sqlite3.connect(db_path)
    c.execute("INSERT INTO electoral_period (number, label, date_start, date_end) "
              "VALUES (42, '42. ciklus', '2022-05-02', '2026-04-30')")
    c.execute("INSERT INTO session (id, period_number, sitting, date) "
              "VALUES ('42001', 42, 1, '2024-03-05')")
    c.execute(
        """INSERT INTO speech (uid, origin_id, session_id, period_number,
               speech_index, person_id, speaker_label, has_text, duration, procedural)
           VALUES ('42001-1','42-1-1','42001',42,1,'k001','Kovács Béla',1,100.0,0)""")
    c.execute("INSERT INTO sentence (speech_id, ord, text) VALUES ('42001-1',0,?)",
              ("A költségvetés régi vitája.",))
    c.commit()
    rebuild_period_sentence_ranges(c)
    c.close()
    return db_path


def test_interleaved_cycles_still_answer_correctly(client, interleaved):
    """Overlapping ranges cost speed, never correctness."""
    c = sqlite3.connect(interleaved)
    lo42, hi42 = c.execute("SELECT first_id, last_id FROM period_sentence_range "
                           "WHERE period_number = 42").fetchone()
    lo43, hi43 = c.execute("SELECT first_id, last_id FROM period_sentence_range "
                           "WHERE period_number = 43").fetchone()
    c.close()
    assert lo42 > hi43, "fixture should have appended cycle 42 after cycle 43"

    # Each cycle returns only its own hits, despite 42's bound spanning 43 too.
    for period, expect in ((42, "régi"), (43, "fontos")):
        r = client.get("/api/v1/proceedings/search",
                       params={"q": "koltsegvetes", "period": period}).json()
        assert r["total"] >= 1
        for hit in r["results"]:
            assert hit["period"] == period
        assert any(expect in h["highlighted"] for h in r["results"])


def test_scoped_search_matches_unscoped_subset(client, interleaved):
    """The union of the per-cycle searches is the unscoped search — the property a
    bound could silently break by clipping one cycle's hits."""
    everything = client.get("/api/v1/proceedings/search",
                            params={"q": "koltsegvetes", "limit": 100}).json()
    seen = set()
    for period in (42, 43):
        r = client.get("/api/v1/proceedings/search",
                       params={"q": "koltsegvetes", "period": period,
                               "limit": 100}).json()
        seen |= {h["sentence_id"] for h in r["results"]}
    assert seen == {h["sentence_id"] for h in everything["results"]}


def test_trend_and_breakdown_agree_with_search(client, interleaved):
    """`/search`, `/search/trend` and `/search/breakdown` describe one result set,
    so the bound must be applied identically to all three or the charts stop
    matching the list they annotate."""
    args = {"q": "koltsegvetes", "period": 43}
    total = client.get("/api/v1/proceedings/search", params=args).json()["total"]
    trend = client.get("/api/v1/proceedings/search/trend", params=args).json()
    assert sum(b["hits"] for b in trend["buckets"]) == total
    breakdown = client.get("/api/v1/proceedings/search/breakdown",
                           params=args).json()
    assert sum(row["hits"] for row in breakdown["speakers"]) <= total


# ---------------------------------------------------------------------------
# Memoizing /search
# ---------------------------------------------------------------------------

@pytest.fixture
def fresh_cache(monkeypatch):
    monkeypatch.setattr(qc.settings, "query_cache_ttl", 300)
    monkeypatch.setattr(qc.settings, "query_cache_size", 64)
    qc._caches.clear()
    yield
    qc._caches.clear()


def test_search_page_is_memoized(client, fresh_cache, monkeypatch):
    from app.modules.proceedings import router as proc

    calls = {"n": 0}
    real = proc._search_compute
    monkeypatch.setattr(proc, "_search_compute",
                        lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1),
                                         real(*a, **k))[1])
    args = {"q": "koltsegvetes", "period": 43}
    first = client.get("/api/v1/proceedings/search", params=args).json()
    second = client.get("/api/v1/proceedings/search", params=args).json()
    assert first == second
    assert calls["n"] == 1, "the second identical search should hit the cache"

    # A different page of the same search is a different result, so a different key.
    client.get("/api/v1/proceedings/search", params={**args, "offset": 20})
    assert calls["n"] == 2


def test_every_search_is_counted_even_when_served_from_cache(client, fresh_cache,
                                                            monkeypatch):
    """Analytics counts searches people RAN (PRIV-1), not cache misses — so the
    counter must sit outside the memoization."""
    from app.modules.proceedings import router as proc

    recorded = []
    monkeypatch.setattr(proc.search_analytics, "record",
                        lambda **kw: recorded.append(kw))
    args = {"q": "koltsegvetes", "period": 43}
    for _ in range(3):
        client.get("/api/v1/proceedings/search", params=args)
    assert len(recorded) == 3
    # And it still reports the figure the reader is shown, not a placeholder.
    payload = client.get("/api/v1/proceedings/search", params=args).json()
    assert recorded[0]["results"] == payload["total"]


def test_cache_separates_sorts_and_scopes(client, fresh_cache):
    """Two searches that differ only in an argument the payload depends on must
    not share an entry."""
    base = {"q": "koltsegvetes", "period": 43}
    by_rank = client.get("/api/v1/proceedings/search", params=base).json()
    by_date = client.get("/api/v1/proceedings/search",
                         params={**base, "sort": "date_asc"}).json()
    assert by_rank["sort"] == "relevance" and by_date["sort"] == "date_asc"
    unscoped = client.get("/api/v1/proceedings/search",
                          params={"q": "koltsegvetes"}).json()
    assert unscoped["total"] >= by_rank["total"]


# ---------------------------------------------------------------------------
# The query budget
# ---------------------------------------------------------------------------

def test_budget_answers_503_not_a_hung_worker(client, monkeypatch):
    """Over budget, the origin declines the work — it does not hold the thread."""
    from app.modules.proceedings import router as proc
    from app import db as db_module

    # Check the deadline every VM step and give it none: the fixture DB is far too
    # small to outrun a real budget, and what is under test is the *response*, not
    # SQLite's timing.
    monkeypatch.setattr(db_module, "_BUDGET_STEP", 1)
    monkeypatch.setattr(proc.settings, "search_timeout", 1e-9)
    r = client.get("/api/v1/proceedings/search", params={"q": "koltsegvetes"})
    assert r.status_code == 503
    assert r.headers.get("Retry-After")


def test_budget_off_by_default_config_value(client, monkeypatch):
    """Disabled (0) means no handler and no ceiling — the escape hatch works."""
    from app.modules.proceedings import router as proc
    from app import db as db_module

    monkeypatch.setattr(db_module, "_BUDGET_STEP", 1)
    monkeypatch.setattr(proc.settings, "search_timeout", 0)
    r = client.get("/api/v1/proceedings/search", params={"q": "koltsegvetes"})
    assert r.status_code == 200


def test_budget_leaves_no_deadline_on_the_pooled_connection(conn):
    """Connections are reused across requests, so a budget must never outlive its
    own block — otherwise one slow search poisons the next reader's query."""
    from app.db import QueryBudgetExceeded, query_budget
    import app.db as db_module

    db_module._BUDGET_STEP = 1
    try:
        with pytest.raises(QueryBudgetExceeded):
            with query_budget(conn, 1e-9):
                conn.execute("SELECT COUNT(*) FROM sentence").fetchone()
        # Same connection, no budget in force: must simply work.
        assert conn.execute("SELECT COUNT(*) FROM sentence").fetchone()[0] >= 0
    finally:
        db_module._BUDGET_STEP = 20_000


def test_budget_does_not_swallow_real_errors(conn):
    """Only an interrupt becomes QueryBudgetExceeded; a genuine SQL error stays
    the error it was."""
    from app.db import query_budget

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        with query_budget(conn, 30):
            conn.execute("SELECT 1 FROM table_that_does_not_exist")
