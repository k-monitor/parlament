"""Terminated mandates (*megszűnt mandátumok*) — loader + API (REP-14).

A cycle's roster is a history, not a snapshot: a dozen or two mandates end before
the term does, and upstream's roster query — being point-in-time — knows nothing of
the people who left. These cover the three things that follow from that:

* the cycle's list contains them, marked, with a filter for either side;
* the profile says which seat it is about, when it ran and who stood on either side
  of the handover; and
* a faction history is dated by its **real** dates, with the cycle-boundary splits
  upstream introduces merged back into one run — which is what makes a mid-cycle
  switch legible instead of rendering as the same year span repeated.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app import loader
from app.modules.representatives import router as reps_router


def _mandate(start="2026-05-08T22:00:00Z", end=None, *, terminated=False,
             reason=None, constituency="Budapest 1. OEVK",
             predecessor=None, successor=None):
    return {"start": start, "end": end, "terminated": terminated,
            "endReason": reason, "constituency": constituency,
            "electionDate": "2026-04-12",
            "predecessor": predecessor, "successor": successor}


def _registry_with_mandates():
    """The MP registry as the scraper now writes it: the two sitting MPs of the
    shared fixture, plus one the roster no longer lists because their mandate ended
    mid-cycle — and the successor who took that seat."""
    return {
        "meta": {"cycle": 43, "cycleStart": "2026-05-09", "cycleEnd": None,
                 "source": "felicitas-kepviselo-api", "withDetails": True,
                 "withChanges": True, "departed": 1, "detailsReused": 0, "count": 4},
        "data": [
            {"personID": "k001", "label": "Kovács Béla", "firstname": "Béla",
             "lastname": "Kovács", "faction": {"label": "Fidesz", "id": 7},
             "constituency": "Budapest 1. OEVK",
             "mandate": _mandate()},
            {"personID": "n002", "label": "Nagy Anna", "firstname": "Anna",
             "lastname": "Nagy", "faction": {"label": "TISZA"},
             "constituency": "Pest 4. OEVK",
             "mandate": _mandate(constituency="Pest 4. OEVK")},
            # Resigned mid-cycle: absent from the roster, present here (REP-14).
            {"personID": "d035", "label": "Dömötör Csaba", "firstname": "Csaba",
             "lastname": "Dömötör", "faction": {"label": "Fidesz", "id": 7},
             "mandate": _mandate(
                 end="2026-07-15", terminated=True,
                 reason="képviselői megbízatásról lemondott",
                 constituency="Országos lista",
                 successor={"personID": "p006", "label": "Palóc André",
                            "mandateStart": "2026-08-10"})},
            {"personID": "p006", "label": "Palóc André", "firstname": "André",
             "lastname": "Palóc", "faction": {"label": "Fidesz", "id": 7},
             "mandate": _mandate(
                 start="2026-08-10", constituency="Országos lista",
                 predecessor={"personID": "d035", "label": "Dömötör Csaba",
                              "mandateEnd": "2026-07-15",
                              "endReason": "képviselői megbízatásról lemondott"})},
        ],
    }


@pytest.fixture
def mandate_db(tmp_path, data_dir):
    """The shared fixture's data directory with a mandate-carrying MP registry."""
    (data_dir / "processed" / "representatives-43.json").write_text(
        json.dumps(_registry_with_mandates(), ensure_ascii=False))
    out = tmp_path / "mandates.db"
    loader.build_database(data_dir, out)
    return out


def _client_for(path, monkeypatch):
    """A TestClient bound to ``path`` — used where a test mutates the DB after the
    build, which the shared `client` fixture (read-only, built once) cannot do."""
    from app.config import settings
    monkeypatch.setattr(settings, "db_path", str(path))
    monkeypatch.setattr(db_module.settings, "db_path", str(path))
    from app.main import app
    return TestClient(app)


@pytest.fixture
def mandate_client(mandate_db, monkeypatch):
    return _client_for(mandate_db, monkeypatch)


def _open(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


# --- loader -----------------------------------------------------------------

def test_mandate_rows_are_loaded_per_cycle(mandate_db):
    c = _open(mandate_db)
    try:
        row = c.execute("SELECT * FROM person_mandate WHERE person_id='d035'").fetchone()
        assert row["period_number"] == 43
        assert row["terminated"] == 1
        assert row["end_reason"] == "képviselői megbízatásról lemondott"
        assert row["date_end"] == "2026-07-15"
        assert (row["successor_id"], row["successor_label"]) == ("p006", "Palóc André")
        # The other side of the same handover, from the successor's own record.
        succ = c.execute("SELECT * FROM person_mandate WHERE person_id='p006'").fetchone()
        assert (succ["predecessor_id"], succ["terminated"]) == ("d035", 0)
    finally:
        c.close()


def test_departed_mp_is_in_the_cycle_scope(mandate_db):
    """A mandate that ended is still a mandate held in that cycle, so the membership
    row every period-aware view filters on (§4A) must exist for them too."""
    c = _open(mandate_db)
    try:
        mem = c.execute("SELECT * FROM membership WHERE person_id='d035'").fetchall()
        assert [m["period_number"] for m in mem] == [43]
        assert c.execute("SELECT is_mp FROM person WHERE person_id='d035'"
                         ).fetchone()["is_mp"] == 1
    finally:
        c.close()


def test_reload_replaces_the_mandate_rather_than_duplicating_it(mandate_db):
    """Re-ingesting a cycle replaces its derived rows (ING-4); the mandate is one."""
    c = _open(mandate_db)
    try:
        loader.load_representatives(c, _registry_with_mandates())
        assert c.execute("SELECT COUNT(*) FROM person_mandate WHERE person_id='d035'"
                         ).fetchone()[0] == 1
    finally:
        c.close()


def test_mandates_of_other_cycles_survive_a_reload(mandate_db):
    """An MP serving in several cycles holds one mandate per cycle, each loaded from
    its own registry — so reloading one must not wipe the others."""
    c = _open(mandate_db)
    try:
        older = _registry_with_mandates()
        older["meta"]["cycle"] = 42
        loader.load_representatives(c, older)
        loader.load_representatives(c, _registry_with_mandates())
        periods = [r["period_number"] for r in c.execute(
            "SELECT period_number FROM person_mandate WHERE person_id='k001' "
            "ORDER BY period_number").fetchall()]
        assert periods == [42, 43]
    finally:
        c.close()


def test_a_db_without_the_table_still_serves_the_list(mandate_db, monkeypatch):
    """A deployment loaded before the composition-changes registry existed has no
    `person_mandate`. The list must degrade to "no mandate on record" — which is
    honest, unlike claiming everyone served their full term — and asking for the
    filter must not silently empty it either."""
    c = _open(mandate_db)
    try:
        c.execute("DROP TABLE person_mandate")
        c.commit()
    finally:
        c.close()
    client = _client_for(mandate_db, monkeypatch)

    listed = client.get("/api/v1/representatives", params={"period": 43})
    assert listed.status_code == 200
    assert all(x["mandate"] is None for x in listed.json()["representatives"])
    filtered = client.get("/api/v1/representatives",
                          params={"period": 43, "mandate": "active"})
    assert filtered.json()["total"] == listed.json()["total"]

    profile = client.get("/api/v1/representatives/k001", params={"period": 43})
    assert profile.status_code == 200 and profile.json()["mandate"] is None


# --- list (REP-1 / REP-14) --------------------------------------------------

def test_cycle_lists_everyone_who_held_a_mandate(mandate_client):
    r = mandate_client.get("/api/v1/representatives", params={"period": 43}).json()
    ids = {x["person_id"] for x in r["representatives"]}
    assert {"k001", "n002", "d035", "p006"} <= ids
    gone = next(x for x in r["representatives"] if x["person_id"] == "d035")
    assert gone["mandate"]["terminated"] is True
    assert gone["mandate"]["end"] == "2026-07-15"
    assert gone["mandate"]["end_reason"] == "képviselői megbízatásról lemondott"


def test_active_filter_hides_the_terminated_mandates(mandate_client):
    r = mandate_client.get("/api/v1/representatives",
                           params={"period": 43, "mandate": "active"}).json()
    ids = {x["person_id"] for x in r["representatives"]}
    assert "d035" not in ids
    assert {"k001", "n002", "p006"} <= ids
    assert all(not x["mandate"]["terminated"] for x in r["representatives"]
               if x["mandate"])


def test_terminated_filter_shows_only_them(mandate_client):
    r = mandate_client.get("/api/v1/representatives",
                           params={"period": 43, "mandate": "terminated"}).json()
    assert [x["person_id"] for x in r["representatives"]] == ["d035"]
    assert r["total"] == 1


def test_mandate_filter_is_scoped_to_the_cycle(mandate_db, monkeypatch):
    """A mandate that ended early in ANOTHER cycle says nothing about this one: an
    MP who resigned in cycle 42 and was re-elected in 43 is active in 43."""
    c = _open(mandate_db)
    try:
        older = _registry_with_mandates()
        older["meta"]["cycle"] = 42
        # In cycle 42 it was k001 who left early; in 43 they serve.
        older["data"][0]["mandate"] = _mandate(
            start="2022-05-01T22:00:00Z", end="2024-01-31", terminated=True,
            reason="összeférhetetlenség")
        loader.load_representatives(c, older)
    finally:
        c.close()
    client = _client_for(mandate_db, monkeypatch)

    active43 = client.get("/api/v1/representatives",
                          params={"period": 43, "mandate": "active"}).json()
    assert "k001" in {x["person_id"] for x in active43["representatives"]}
    ended42 = client.get("/api/v1/representatives",
                         params={"period": 42, "mandate": "terminated"}).json()
    assert "k001" in {x["person_id"] for x in ended42["representatives"]}


# --- profile (REP-2 / REP-14) -----------------------------------------------

def test_profile_carries_the_mandate_and_both_handovers(mandate_client):
    p = mandate_client.get("/api/v1/representatives/d035",
                           params={"period": 43}).json()
    assert p["mandate"]["terminated"] is True
    assert p["mandate"]["end"] == "2026-07-15"
    assert p["mandate"]["constituency"] == "Országos lista"
    assert p["mandate"]["successor"] == {"person_id": "p006", "label": "Palóc André",
                                         "has_profile": True}
    assert p["mandate"]["predecessor"] is None

    seated = mandate_client.get("/api/v1/representatives/p006",
                                params={"period": 43}).json()
    assert seated["mandate"]["terminated"] is False
    assert seated["mandate"]["predecessor"]["person_id"] == "d035"
    assert seated["mandate"]["start"] == "2026-08-10"


def test_a_handover_partner_this_db_does_not_have_is_named_not_linked(mandate_db,
                                                                     monkeypatch):
    """A successor seated after the last roster scrape is in no registry yet: naming
    them beats both hiding the handover and offering a link that 404s."""
    c = _open(mandate_db)
    try:
        c.execute("UPDATE person_mandate SET successor_id='zzz0', "
                  "successor_label='Nem Ismert' WHERE person_id='d035'")
        c.commit()
    finally:
        c.close()
    p = _client_for(mandate_db, monkeypatch).get(
        "/api/v1/representatives/d035", params={"period": 43}).json()
    assert p["mandate"]["successor"] == {"person_id": "zzz0", "label": "Nem Ismert",
                                         "has_profile": False}


# --- faction history as runs (REP-14) ---------------------------------------

def _spell(label, cycle, start, end):
    return {"label": label, "cycle": cycle, "start": start, "end": end}


def test_a_mid_cycle_switch_keeps_its_own_dates():
    """The screenshot case: LMP → független → LMP inside one cycle. Labelled by the
    cycle each spell falls in, all three read "2010-2014"; the dates are the point."""
    runs = reps_router._faction_runs([
        _spell("LMP", "2010-2014", "2013-02-20T00:00:00Z", "2014-05-05T21:59:59Z"),
        _spell("független", "2010-2014", "2013-02-15T00:00:00Z", "2013-02-19T23:59:59Z"),
        _spell("LMP", "2010-2014", "2010-05-13T22:00:00Z", "2013-02-14T23:59:59Z"),
    ])
    assert [(r["label"], r["start"][:10], r["end"][:10]) for r in runs] == [
        ("LMP", "2013-02-20", "2014-05-05"),
        ("független", "2013-02-15", "2013-02-19"),
        ("LMP", "2010-05-13", "2013-02-14"),
    ]


def test_consecutive_cycles_in_one_faction_read_as_a_single_run():
    """Upstream splits an unbroken membership at every cycle boundary — the rows meet
    within a second, so eight identical lines are really one run (REP-14)."""
    runs = reps_router._faction_runs([
        _spell("Fidesz", "2018-2022", "2018-05-07T22:00:00Z", "2022-05-01T21:59:59Z"),
        _spell("Fidesz", "2014-2018", "2014-05-05T22:00:00Z", "2018-05-07T21:59:59Z"),
        _spell("Fidesz", "2010-2014", "2010-05-13T22:00:00Z", "2014-05-05T21:59:59Z"),
    ])
    assert len(runs) == 1
    assert runs[0]["start"][:10] == "2010-05-13" and runs[0]["end"][:10] == "2022-05-01"
    assert runs[0]["cycles"] == ["2010-2014", "2014-2018", "2018-2022"]


def test_a_cycle_not_served_is_never_bridged():
    """Out of the House for a term and back is two runs, not one — merging them
    would invent a membership that never existed."""
    runs = reps_router._faction_runs([
        _spell("Fidesz", "2010-2014", "2010-05-13T22:00:00Z", "2014-05-05T21:59:59Z"),
        _spell("Fidesz", "1998-2002", "1998-06-17T22:00:00Z", "2002-05-14T21:59:59Z"),
    ])
    assert len(runs) == 2
    assert [r["cycles"] for r in runs] == [["2010-2014"], ["1998-2002"]]


def test_a_spell_still_running_keeps_an_open_end():
    runs = reps_router._faction_runs([
        _spell("Fidesz", "2026-", "2026-05-08T22:00:00Z", None),
        _spell("Fidesz", "2022-2026", "2022-05-01T22:00:00Z", "2026-05-08T21:59:59Z"),
    ])
    assert len(runs) == 1 and runs[0]["end"] is None


def test_an_undated_history_is_left_alone():
    """A registry scraped before the dates were captured must not have spells merged
    on a guess; the profile then falls back to the cycle label."""
    runs = reps_router._faction_runs([
        _spell("Fidesz", "2026-", None, None),
        _spell("Fidesz", "2022-2026", None, None),
    ])
    assert len(runs) == 2


def test_profile_serves_the_faction_history_as_runs(client):
    """End to end: the shared fixture's single-spell history still comes back with
    its dates and the cycles it covers."""
    p = client.get("/api/v1/representatives/k001").json()
    [run] = p["faction_history"]
    assert run["faction"]["label"] == "Fidesz"
    assert run["start"] == "2026" and run["end"] is None
    assert run["cycles"] == ["2026-"] and run["cycle"] == "2026-"
