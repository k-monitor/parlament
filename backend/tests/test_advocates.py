"""Nationality advocates (nemzetiségi szószólók) — loader + API (REP-9).

Covers the two things that make the feature safe to add to a **running,
already-scraped** deployment:

* dropping an ``advocates-<cycle>.json`` next to the existing processed files and
  running the incremental ``--update`` is enough — including on a DB built before
  the advocate columns existed (the in-place ALTER path); and
* an advocate enriches the *existing* speaker stub the transcripts created (same
  person id), never a parallel person row, and never overwrites what the MP roster
  supplied for someone who has been both.
"""

from __future__ import annotations

import json
import sqlite3

from app import loader


def _advocates_registry(cycle=43, records=None):
    """An advocate registry as ``parlamonitor advocates`` writes it."""
    if records is None:
        records = [
            # n002 also appears in the MP registry fixture: the dual-mandate case
            # (advocate in one cycle, MP in another).
            {"personID": "sz01", "label": "Szerb Szószóló", "labelFull": "Szerb Szószóló",
             "firstname": "Szószóló", "lastname": "Szerb",
             "mandate": "nationality-advocate", "nationality": "szerb",
             "seat": "3/6/13", "website": "http://example.hu",
             "highestEducation": "egyetem", "active": True,
             "wikidataId": "Q999",
             "committeeMemberships": [
                 {"cycle": "2026-", "committee": "Magyarországi Nemzetiségek Bizottsága",
                  "role": "tag"}],
             "factionHistory": [], "electionHistory": [],
             "statistics": {"speeches": [{"cycle": 43, "count": 4}],
                            "billsSubmitted": [{"cycle": 43, "ownBills": 1}]}},
        ]
    return {
        "meta": {"cycle": cycle, "cycleStart": "2026-05-09", "cycleEnd": None,
                 "source": "felicitas-szoszolo-api", "mandate": "nationality-advocate",
                 "withDetails": True, "count": len(records)},
        "data": records,
    }


def _write_advocates(data_dir, registry=None, cycle=43):
    (data_dir / "processed" / f"advocates-{cycle}.json").write_text(
        json.dumps(registry or _advocates_registry(cycle), ensure_ascii=False))


def _open(db_path):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    return c


# --- full build -------------------------------------------------------------

def test_full_build_loads_advocates(tmp_path, data_dir):
    _write_advocates(data_dir)
    db = tmp_path / "adv.db"
    loader.build_database(data_dir, db)
    c = _open(db)
    try:
        row = c.execute("SELECT * FROM person WHERE person_id='sz01'").fetchone()
        assert row["is_advocate"] == 1
        assert row["nationality"] == "szerb"
        assert row["is_mp"] == 0          # an advocate holds no mandate
        assert row["seat"] == "3/6/13"
        assert row["wikidata_id"] == "Q999"
        # Inside the cycle scope every period-aware view filters on (§4A), with no
        # faction (they belong to none).
        mem = c.execute("SELECT * FROM membership WHERE person_id='sz01'").fetchall()
        assert len(mem) == 1
        assert mem[0]["period_number"] == 43 and mem[0]["faction_id"] is None
        # The advocates file is tracked, so a later update reloads it only if it changed.
        assert c.execute("SELECT 1 FROM load_state WHERE name='advocates-43.json'"
                         ).fetchone() is not None
    finally:
        c.close()


def test_advocate_enriches_the_existing_speaker_stub(tmp_path, data_dir):
    """The advocate id is the id the transcripts already carry, so the load must
    upgrade that stub rather than create a second person row."""
    # The session fixture's speaker n002 is also in the MP registry; use a speaker
    # id from the transcript that is NOT in the MP roster by re-pointing one record.
    reg = _advocates_registry(records=[
        {"personID": "n002", "label": "Nagy Anna", "mandate": "nationality-advocate",
         "nationality": "német", "firstname": "Anna", "lastname": "Nagy"}])
    _write_advocates(data_dir, reg)
    db = tmp_path / "adv.db"
    loader.build_database(data_dir, db)
    c = _open(db)
    try:
        assert c.execute("SELECT COUNT(*) FROM person WHERE person_id='n002'"
                         ).fetchone()[0] == 1
        row = c.execute("SELECT * FROM person WHERE person_id='n002'").fetchone()
        # Both marks survive: the MP roster's and the advocate registry's.
        assert row["is_mp"] == 1 and row["is_advocate"] == 1
        # …and the MP roster's richer fields are not nulled out by the advocate load.
        assert row["constituency"] == "Pest 4. OEVK"
        # The MP's faction membership for the cycle is intact alongside the
        # advocate's factionless row.
        mem = c.execute("SELECT faction_id FROM membership WHERE person_id='n002' "
                        "AND period_number=43").fetchall()
        assert sorted((m["faction_id"] is None) for m in mem) == [False, True]
    finally:
        c.close()


def test_reload_is_idempotent(tmp_path, data_dir):
    _write_advocates(data_dir)
    db = tmp_path / "adv.db"
    loader.build_database(data_dir, db)
    c = _open(db)
    try:
        loader.load_advocates(c, _advocates_registry())
        assert c.execute("SELECT COUNT(*) FROM membership WHERE person_id='sz01'"
                         ).fetchone()[0] == 1
    finally:
        c.close()


def test_per_cycle_stats_merge_across_cycles(tmp_path, data_dir):
    """A per-cycle registry knows only its own cycle's counts, so loading cycle 42
    after 43 must add to — not replace — what is on the person row."""
    _write_advocates(data_dir, _advocates_registry(43), cycle=43)
    db = tmp_path / "adv.db"
    loader.build_database(data_dir, db)
    older = _advocates_registry(42, records=[
        {"personID": "sz01", "label": "Szerb Szószóló",
         "mandate": "nationality-advocate", "nationality": "szerb",
         "statistics": {"speeches": [{"cycle": 42, "count": 9}]}}])
    c = _open(db)
    try:
        loader.load_advocates(c, older)
        stats = json.loads(c.execute(
            "SELECT external_stats_json FROM person WHERE person_id='sz01'"
        ).fetchone()[0])
        assert {s["cycle"]: s["count"] for s in stats["speeches"]} == {43: 4, 42: 9}
        # The untouched key survives the merge.
        assert stats["billsSubmitted"] == [{"cycle": 43, "ownBills": 1}]
    finally:
        c.close()


# --- migration from an already-built DB (the point of the design) ------------

def test_lands_via_incremental_update(data_dir, db_path):
    """The already-scraped case: the DB exists, no sitting changed, only the new
    advocates file appeared. One --update picks it up (no rebuild)."""
    _write_advocates(data_dir)
    assert loader.update_database(data_dir, db_path) is True
    c = _open(db_path)
    try:
        assert c.execute("SELECT is_advocate, nationality FROM person "
                         "WHERE person_id='sz01'").fetchone()["nationality"] == "szerb"
        # The pre-existing sittings/bills/votes were not reloaded, only the
        # registry — the whole point of the per-file load_state.
        assert c.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 1
    finally:
        c.close()


def test_update_adds_the_columns_to_a_pre_feature_db(data_dir, db_path):
    """A DB built before the advocate columns existed gains them in place, so the
    feature does not force a (multi-minute) full rebuild on a live deployment."""
    c = _open(db_path)
    try:
        c.execute("ALTER TABLE person DROP COLUMN is_advocate")
        c.execute("ALTER TABLE person DROP COLUMN nationality")
        c.commit()
        cols = {r[1] for r in c.execute("PRAGMA table_info(person)")}
        assert "is_advocate" not in cols
    finally:
        c.close()

    _write_advocates(data_dir)
    assert loader.update_database(data_dir, db_path) is True

    c = _open(db_path)
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(person)")}
        assert {"is_advocate", "nationality"} <= cols
        assert c.execute("SELECT is_advocate FROM person WHERE person_id='sz01'"
                         ).fetchone()[0] == 1
    finally:
        c.close()


# --- API --------------------------------------------------------------------

def test_api_list_and_profile(tmp_path, data_dir, monkeypatch):
    _write_advocates(data_dir)
    db = tmp_path / "adv.db"
    loader.build_database(data_dir, db)

    from app.config import settings
    from app import db as db_module
    monkeypatch.setattr(settings, "db_path", str(db))
    monkeypatch.setattr(db_module.settings, "db_path", str(db))
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)

    # Default (role=mp) is unchanged: advocates are not in the MP list.
    mps = client.get("/api/v1/representatives").json()
    assert "sz01" not in {r["person_id"] for r in mps["representatives"]}

    adv = client.get("/api/v1/representatives", params={"role": "advocate"}).json()
    assert [r["person_id"] for r in adv["representatives"]] == ["sz01"]
    assert adv["representatives"][0]["nationality"] == "szerb"
    assert adv["representatives"][0]["is_advocate"] is True

    both = client.get("/api/v1/representatives", params={"role": "all"}).json()
    assert both["total"] == mps["total"] + 1

    # Accent-insensitive nationality filter (FOLD-1) and cycle scope (§4A).
    assert client.get("/api/v1/representatives",
                      params={"role": "advocate", "nationality": "SZERB",
                              "period": 43}).json()["total"] == 1
    assert client.get("/api/v1/representatives",
                      params={"role": "advocate", "period": 42}).json()["total"] == 0

    prof = client.get("/api/v1/representatives/sz01").json()
    assert prof["is_advocate"] is True and prof["nationality"] == "szerb"
    assert prof["is_mp"] is False
    assert prof["current_faction"] is None

    # An advocate casts no vote, so the participation section stays suppressed
    # (as for any non-MP speaker) rather than reading "absent from everything".
    stats = client.get("/api/v1/representatives/sz01/statistics").json()
    assert stats["totals"]["vote_breakdown"] is None
    # The upstream per-cycle own-motion count still shows (they do submit irományok).
    assert stats["totals"]["bills_submitted"] == 1

    # Advocates speak in plenary, so the transcript's speaker autocomplete offers
    # them too (SEA-7) — otherwise filtering search by one would find nothing.
    sug = client.get("/api/v1/proceedings/suggest", params={"q": "szerb"}).json()
    assert "sz01" in {s["person_id"] for s in sug["speakers"]}


def test_api_degrades_on_a_pre_feature_db(tmp_path, data_dir, monkeypatch):
    """Old DB, new code: the MP list is unaffected and an advocate-scoped request
    is empty rather than silently answering with MPs."""
    db = tmp_path / "old.db"
    loader.build_database(data_dir, db)
    c = _open(db)
    try:
        c.execute("ALTER TABLE person DROP COLUMN is_advocate")
        c.execute("ALTER TABLE person DROP COLUMN nationality")
        c.commit()
    finally:
        c.close()

    from app.config import settings
    from app import db as db_module
    monkeypatch.setattr(settings, "db_path", str(db))
    monkeypatch.setattr(db_module.settings, "db_path", str(db))
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)

    mps = client.get("/api/v1/representatives").json()
    assert mps["total"] == 2
    assert mps["representatives"][0]["is_advocate"] is False
    assert client.get("/api/v1/representatives",
                      params={"role": "advocate"}).json()["total"] == 0
    assert client.get("/api/v1/representatives",
                      params={"role": "all"}).json()["total"] == 2
    prof = client.get("/api/v1/representatives/k001").json()
    assert prof["is_advocate"] is False and prof["nationality"] is None
    assert client.get("/api/v1/proceedings/suggest",
                      params={"q": "kov"}).json()["speakers"]
