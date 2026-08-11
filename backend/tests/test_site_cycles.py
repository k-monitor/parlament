"""The served cycle window (§4A CYC-7, `PARLAMONITOR_SITE_CYCLES`).

A deployment can be run as a window onto part of the corpus: the DB still holds
every cycle it was built with, but the site behaves as though only the named ones
exist. That claim is only worth anything if it holds on *every* route, so these
tests seed a second cycle into the fixture DB (as test_multi_cycle does), window
the site down to cycle 43 and then go looking for cycle 42 everywhere it could
still surface — the chooser, the homepage totals, list/search endpoints, the
by-id detail pages and the sitemaps a crawler is handed.

The load-bearing case is the *narrowing* one: a request that asks for the cycle
outside the window, or asks for "all cycles", must be answered over the window —
never over the corpus, and never with an empty result set that reads as "the
House did nothing".
"""

from __future__ import annotations

import re
import sqlite3

import pytest

from app.config import parse_site_cycles


@pytest.fixture
def two_cycles(db_path):
    """Seed a cycle-42 sitting alongside the fixture's cycle 43: one speech with
    text, one MP membership, one bill and one vote — everything a page could be
    built from, so each route below has something to leak if it doesn't scope."""
    c = sqlite3.connect(db_path)
    fidesz = c.execute("SELECT id FROM faction WHERE label='Fidesz'").fetchone()[0]
    c.execute("INSERT INTO electoral_period (number, label, date_start, date_end) "
              "VALUES (42, '42. ciklus', '2022-05-02', '2026-04-30')")
    c.execute("INSERT INTO session (id, period_number, sitting, date) "
              "VALUES ('42001', 42, 1, '2024-03-05')")
    c.execute(
        """INSERT INTO speech (uid, origin_id, session_id, period_number,
               speech_index, person_id, speaker_label, faction_id, has_text,
               duration, procedural)
           VALUES ('42001-1','42-1-1','42001',42,1,'k001','Kovács Béla',?,1,100.0,0)""",
        (fidesz,))
    c.execute("INSERT INTO sentence (speech_id, ord, text) VALUES ('42001-1',0,?)",
              ("A költségvetés régi vitája.",))
    c.execute("INSERT INTO membership (person_id, faction_id, period_number) "
              "VALUES ('k001', ?, 42)", (fidesz,))
    c.execute("INSERT INTO person_stats (person_id, period_number, speech_count, "
              "speaking_seconds, sentence_count) VALUES ('k001', 42, 1, 100.0, 1)")
    c.execute("INSERT INTO bill (id, bill_number, number_sort, period_number, title, "
              "type, main_type, status, submitted_date) "
              "VALUES ('bill-42','T/1',1,42,'Régi törvényjavaslat','törvényjavaslat',"
              "'T','kihirdetve','2024-03-01T09:00:00Z')")
    c.execute("INSERT INTO vote (id, period_number, vote_datetime, voting_mode, subject, "
              "result, yes, no, abstain, has_per_mp) "
              "VALUES ('v-42',42,'2024-03-05T10:00:00Z','Gépi szavazás','régi szavazás',"
              "'Elfogadva',1,0,0,1)")
    c.commit()
    c.close()
    return db_path


@pytest.fixture
def windowed(monkeypatch):
    """Serve cycle 43 only — what ``PARLAMONITOR_SITE_CYCLES=43`` does.

    Set on the Settings object each module actually holds, not just on
    ``app.config.settings``: every module binds ``settings`` at import, and
    another test in this suite ``importlib.reload``s ``app.config`` + ``app.main``
    (rebinding them to fresh objects), so patching one name can leave a module
    reading an unpatched copy — the same hazard test_og.py documents. Distinct
    objects only, since most of these are the same one."""
    from app import config as config_module, db as db_module
    from app import main as main_module, seo as seo_module
    from app.modules.bills import router as bills_router
    from app.modules.proceedings import router as proceedings_router
    from app.modules.representatives import router as representatives_router
    from app.modules.votes import router as votes_router

    seen: set[int] = set()
    for module in (config_module, db_module, main_module, seo_module, bills_router,
                   proceedings_router, representatives_router, votes_router):
        target = getattr(module, "settings", None)   # not every router holds one
        if target is not None and id(target) not in seen:
            seen.add(id(target))
            monkeypatch.setattr(target, "site_cycles", "43")
    return (43,)


# --- the setting itself -----------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("", ()), ("   ", ()), ("all", ()), ("*", ()),          # no window: whole corpus
    ("43", (43,)),
    ("43,42", (42, 43)),                                    # canonical: sorted
    (" 42 ; 43 ", (42, 43)),                                # tolerant of spacing/;
    ("43,43", (43,)),
    ("mind", ()),                                           # unreadable → open up
])
def test_parse_site_cycles(raw, expected):
    assert parse_site_cycles(raw) == expected


def test_unset_is_the_whole_corpus(client, two_cycles):
    """The default: nothing is windowed, both cycles are on offer, and /meta says
    so explicitly rather than by omission."""
    meta = client.get("/api/v1/meta").json()
    assert meta["site_cycles"] == []
    assert {p["number"] for p in meta["periods"]} == {42, 43}
    assert client.get("/api/v1/proceedings/sessions").json()["total"] == 2


# --- what the reader is offered ---------------------------------------------

def test_meta_offers_only_the_served_cycles(client, two_cycles, windowed):
    """The chooser is built from /meta's periods, so a cycle the site doesn't
    serve is not one the reader can select."""
    meta = client.get("/api/v1/meta").json()
    assert [p["number"] for p in meta["periods"]] == [43]
    assert meta["site_cycles"] == [43]


def test_homepage_totals_count_only_the_served_cycles(client, two_cycles, windowed):
    """The headline counts describe the site, not the file behind it: the seeded
    cycle-42 sitting, speech and sentence are outside the window and outside the
    totals."""
    counts = client.get("/api/v1/meta").json()["counts"]
    c = sqlite3.connect(two_cycles)
    served = {
        "sessions": c.execute(
            "SELECT COUNT(*) FROM session WHERE period_number=43").fetchone()[0],
        "speeches": c.execute(
            "SELECT COUNT(*) FROM speech WHERE period_number=43").fetchone()[0],
        "sentences": c.execute(
            "SELECT COUNT(*) FROM sentence s JOIN speech sp ON sp.uid=s.speech_id "
            "WHERE sp.period_number=43").fetchone()[0],
    }
    whole_corpus = c.execute("SELECT COUNT(*) FROM sentence").fetchone()[0]
    c.close()
    assert {k: counts[k] for k in served} == served
    assert counts["sentences"] < whole_corpus      # cycle 42's is genuinely out
    # MPs are counted through their seats in these cycles, not "ever an MP".
    assert counts["representatives"] >= 1


# --- list / search endpoints ------------------------------------------------

def _get(client, path, periods, **params):
    return client.get(path, params=[("period", p) for p in periods]
                      + list(params.items())).json()


def test_all_cycles_request_is_answered_over_the_window(client, two_cycles, windowed):
    """No `period` param at all means "all cycles" — which, windowed, is the
    window. It must not fall back to the corpus."""
    sessions = client.get("/api/v1/proceedings/sessions").json()
    assert sessions["total"] == 1
    assert {s["period_number"] for s in sessions["sessions"]} == {43}


def test_out_of_window_period_param_cannot_widen_the_scope(client, two_cycles, windowed):
    """A hand-written `?period=42` names a cycle the site does not serve. The
    answer is the window's own content — not cycle 42's, and not an empty list."""
    sessions = _get(client, "/api/v1/proceedings/sessions", [42])
    assert {s["period_number"] for s in sessions["sessions"]} == {43}
    assert sessions["total"] == 1
    # Mixed scopes keep only the served part.
    assert _get(client, "/api/v1/proceedings/sessions", [42, 43])["total"] == 1


def test_search_never_returns_out_of_window_hits(client, two_cycles, windowed):
    """Both cycles have a költségvetés speech; only the served one is findable,
    whichever cycle the request asks for."""
    for periods in ([], [42], [43], [42, 43]):
        hits = _get(client, "/api/v1/proceedings/search", periods, q="koltsegvetes")
        assert {r["period"] for r in hits["results"]} == {43}


def test_bills_and_votes_lists_are_windowed(client, two_cycles, windowed):
    ids = {b["id"] for b in client.get("/api/v1/bills").json()["bills"]}
    assert "bill-42" not in ids
    votes = client.get("/api/v1/votes").json()["votes"]
    assert "v-42" not in {v["id"] for v in votes}


def test_representative_statistics_cover_the_window_only(client, two_cycles, windowed):
    """The MP sat in both cycles; the profile's numbers are the served cycle's,
    and the per-cycle breakdown offers no row for the other one."""
    stats = client.get("/api/v1/representatives/k001/statistics").json()
    assert stats["scope"]["periods"] == [43]
    assert {row["period"] for row in stats["by_period"]} == {43}


# --- the by-id pages --------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/api/v1/proceedings/sessions/42001",
    "/api/v1/proceedings/sessions/42001/wordcloud",
    "/api/v1/proceedings/sessions/42001/top-speakers",
    "/api/v1/proceedings/sessions/42001/new-words",
    "/api/v1/proceedings/speeches/42001-1",
    "/api/v1/proceedings/speeches/42001-1/text",
    "/api/v1/proceedings/speeches/42001-1/clip",
    "/api/v1/bills/bill-42",
    "/api/v1/votes/v-42",
])
def test_out_of_window_pages_are_not_found(client, two_cycles, windowed, path):
    """A detail page carries no `period` to clamp, so each one checks its own
    row's cycle: outside the window it answers 404, the same as an id that was
    never loaded."""
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", [
    "/api/v1/proceedings/sessions/43001",
    "/api/v1/proceedings/sessions/43001/wordcloud",
    "/api/v1/proceedings/speeches/43001-1",
    "/api/v1/proceedings/speeches/43001-1/text",
])
def test_in_window_pages_still_load(client, two_cycles, windowed, path):
    assert client.get(path).status_code == 200


# --- what a crawler is handed -----------------------------------------------

def _sitemap(client, name):
    return client.get(f"/sitemap-{name}-1.xml").text


def test_sitemaps_stop_advertising_out_of_window_pages(client, two_cycles, windowed):
    """A sitemap listing a page the site 404s is a broken sitemap, so the window
    has to reach the crawler routes too."""
    sessions = _sitemap(client, "sessions")
    assert "/sessions/43001" in sessions
    assert "/sessions/42001" not in sessions
    assert "/votes/v-42" not in _sitemap(client, "votes")
    assert "/bills/bill-42" not in _sitemap(client, "bills")


def test_share_cards_follow_the_window(tmp_path, db_path, two_cycles, windowed,
                                       monkeypatch):
    """The other crawler-facing surface: a share card for an out-of-window page
    would be a card for a page the site 404s, so it answers 404 with the shell
    (`og._missing`) exactly as an unknown id does."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app import db as db_module, og

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<!DOCTYPE html><html><head><title>Parlamonitor</title></head>"
        "<body></body></html>", encoding="utf-8")
    monkeypatch.setattr(og.settings, "frontend_dist", str(dist))
    monkeypatch.setattr(og.settings, "site_cycles", "43")   # og binds its own settings
    monkeypatch.setattr(db_module.settings, "db_path", str(db_path))
    monkeypatch.setattr(og, "_shell_cache", None)
    app = FastAPI()
    og.register(app)
    og_client = TestClient(app)

    assert og_client.get("/sessions/43001").status_code == 200
    for path in ("/sessions/42001", "/proceedings/42001-1", "/bills/bill-42",
                 "/votes/v-42"):
        assert og_client.get(path).status_code == 404, path


def test_sitemap_index_stays_consistent_with_the_window(client, two_cycles, windowed):
    """Every child the index advertises still resolves — the count query and the
    page query must carry the same window, or pagination drifts."""
    index = client.get("/sitemap.xml").text
    for loc in re.findall(r"<loc>([^<]+)</loc>", index):
        assert client.get("/" + loc.split("/", 3)[-1]).status_code == 200
