"""Incremental loader update (`update_database`) — the continuous-sync DB path.

Exercises the real snapshot → selective reload → atomic-swap machinery against a
freshly built small DB, so it covers the production code path (OPS-3): only
changed processed files are reloaded, an idle update is a cheap no-op, and a DB
predating the load_state bookkeeping degrades to a full rebuild.
"""

from __future__ import annotations

import json
import sqlite3

from app import loader

try:
    from tests.conftest import (_committee_minutes_registry, _committees_registry,
                                _session_record)
except ImportError:  # when pytest imports conftest as a top-level module
    from conftest import (_committee_minutes_registry, _committees_registry,
                          _session_record)


def _count(db, sql, *args):
    c = sqlite3.connect(db)
    try:
        return c.execute(sql, args).fetchone()[0]
    finally:
        c.close()


def _load_state_names(db):
    c = sqlite3.connect(db)
    try:
        return {r[0] for r in c.execute("SELECT name FROM load_state")}
    finally:
        c.close()


def test_full_build_seeds_load_state(db_path):
    """A full build records every processed file so a later --update has a baseline."""
    assert _load_state_names(db_path) == {
        "representatives-43.json", "bills-43.json", "votes-43.json",
        "committees-43.json", "committee-minutes-43.json",
        "committee-videos.json", "43001-session.json", "officeholders.json",
        "aktualis.json"}


def test_update_is_noop_when_nothing_changed(data_dir, db_path):
    before = db_path.stat().st_mtime_ns
    changed = loader.update_database(data_dir, db_path)
    assert changed is False
    # No swap happened, so the file was left untouched.
    assert db_path.stat().st_mtime_ns == before


def test_update_adds_new_sitting_without_touching_existing(data_dir, db_path):
    assert _count(db_path, "SELECT COUNT(*) FROM session") == 1
    new = _session_record(session="43002", sitting=2, date="2026-05-16")
    (data_dir / "processed" / "43002-session.json").write_text(
        json.dumps(new, ensure_ascii=False))

    changed = loader.update_database(data_dir, db_path)

    assert changed is True
    assert _count(db_path, "SELECT COUNT(*) FROM session") == 2
    assert _count(db_path, "SELECT COUNT(*) FROM session WHERE id='43001'") == 1
    assert _count(db_path, "SELECT COUNT(*) FROM session WHERE id='43002'") == 1
    assert "43002-session.json" in _load_state_names(db_path)
    # Aggregates were rebuilt over the new sitting too (both speakers counted).
    assert _count(db_path, "SELECT COUNT(*) FROM person_session_stats "
                           "WHERE session_id='43002'") >= 1


def test_update_reloads_a_changed_sitting(data_dir, db_path):
    rec = _session_record()
    # Change a sentence (and its length, so the size-based signature always
    # differs regardless of filesystem mtime granularity).
    rec["data"][0]["textContents"][0]["textBody"][0]["sentences"][0]["text"] = \
        "Ez egy teljesen új, sokkal hosszabb mondat a frissítés tesztjéhez."
    (data_dir / "processed" / "43001-session.json").write_text(
        json.dumps(rec, ensure_ascii=False))

    assert loader.update_database(data_dir, db_path) is True

    c = sqlite3.connect(db_path)
    try:
        texts = [r[0] for r in c.execute("SELECT text FROM sentence")]
    finally:
        c.close()
    assert any("teljesen új" in t for t in texts)
    # Still exactly one sitting — a reload replaces, never duplicates (ING-4).
    assert _count(db_path, "SELECT COUNT(*) FROM session") == 1


def test_update_removes_a_sitting_whose_file_is_gone(data_dir, db_path):
    """The only way a sitting leaves: parlament.hu cancels an announced day and the
    scraper deletes its processed file. Loading is otherwise purely additive, so
    without this the cancelled day is served as an upcoming sitting for ever."""
    gone = data_dir / "processed" / "43002-session.json"
    gone.write_text(json.dumps(_session_record(session="43002", sitting=2,
                                               date="2026-05-16"),
                               ensure_ascii=False))
    assert loader.update_database(data_dir, db_path) is True
    assert _count(db_path, "SELECT COUNT(*) FROM session") == 2

    gone.unlink()
    # A removal is a change in itself: nothing else is stale, and the update must
    # still run (and swap) rather than report the DB up to date.
    assert loader.update_database(data_dir, db_path) is True

    assert _count(db_path, "SELECT COUNT(*) FROM session WHERE id='43002'") == 0
    assert _count(db_path, "SELECT COUNT(*) FROM speech WHERE session_id='43002'") == 0
    assert _count(db_path, "SELECT COUNT(*) FROM agenda_item "
                           "WHERE session_id='43002'") == 0
    assert _count(db_path, "SELECT COUNT(*) FROM person_session_stats "
                           "WHERE session_id='43002'") == 0
    assert "43002-session.json" not in _load_state_names(db_path)
    # The sitting that is still on record is untouched.
    assert _count(db_path, "SELECT COUNT(*) FROM session WHERE id='43001'") == 1
    assert _count(db_path, "SELECT COUNT(*) FROM speech WHERE session_id='43001'") >= 1
    # …and the next update has nothing left to do.
    assert loader.update_database(data_dir, db_path) is False


def test_update_reloads_changed_bills_only(data_dir, db_path):
    bills = json.loads((data_dir / "processed" / "bills-43.json").read_text())
    bills["data"][0]["title"] = "Módosított cím a frissítés tesztjéhez"
    (data_dir / "processed" / "bills-43.json").write_text(
        json.dumps(bills, ensure_ascii=False))

    assert loader.update_database(data_dir, db_path) is True
    assert _count(db_path, "SELECT COUNT(*) FROM bill "
                           "WHERE title='Módosított cím a frissítés tesztjéhez'") == 1


def test_update_adds_the_declaration_columns_to_a_pre_feature_db(data_dir, db_path):
    """REP-13: a DB built before the asset-declaration/CV columns existed gains them
    in place on the next --update, so the feature lands on a live deployment by
    re-loading the registry alone — no (multi-minute) full rebuild."""
    c = sqlite3.connect(db_path)
    try:
        c.execute("ALTER TABLE person DROP COLUMN cv_url")
        c.execute("ALTER TABLE person DROP COLUMN asset_declarations_json")
        c.commit()
        assert "cv_url" not in {r[1] for r in c.execute("PRAGMA table_info(person)")}
    finally:
        c.close()

    # Touch the registry so the incremental update reloads that one file.
    reg_file = data_dir / "processed" / "representatives-43.json"
    reg = json.loads(reg_file.read_text())
    reg["meta"]["scrapedAt"] = "2026-08-07T00:00:00+00:00"
    reg_file.write_text(json.dumps(reg, ensure_ascii=False))
    assert loader.update_database(data_dir, db_path) is True

    c = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(person)")}
        assert {"cv_url", "asset_declarations_json"} <= cols
        row = c.execute("SELECT cv_url, asset_declarations_json FROM person "
                        "WHERE person_id='k001'").fetchone()
        assert row[0].endswith("/kepv/eletrajz/hu/k001.pdf")
        assert len(json.loads(row[1])) == 2
    finally:
        c.close()


def test_update_builds_when_db_missing(data_dir, tmp_path):
    target = tmp_path / "fresh.db"
    assert not target.exists()
    assert loader.update_database(data_dir, target) is True
    assert target.exists()
    assert _count(target, "SELECT COUNT(*) FROM session") == 1


def test_update_falls_back_to_full_build_without_load_state(data_dir, db_path):
    # Simulate a DB built before the load_state table existed.
    c = sqlite3.connect(db_path)
    try:
        c.execute("DROP TABLE load_state")
        c.commit()
    finally:
        c.close()

    assert loader.update_database(data_dir, db_path) is True
    # The fallback full rebuild reseeds the baseline.
    assert "43001-session.json" in _load_state_names(db_path)


def test_api_picks_up_swapped_db_without_restart(client, data_dir, db_path):
    """DB-4 zero-downtime: the API's thread-cached read-only connections
    (app/db.py) must notice the loader's atomic swap (new inode) and serve the
    new data on the very next request — no restart."""
    assert client.get("/api/v1/meta").json()["counts"]["sessions"] == 1

    (data_dir / "processed" / "43002-session.json").write_text(
        json.dumps(_session_record(session="43002", sitting=2, date="2026-05-16"),
                   ensure_ascii=False))
    assert loader.update_database(data_dir, db_path) is True

    assert client.get("/api/v1/meta").json()["counts"]["sessions"] == 2


def test_update_refreshes_portfolios_when_only_bills_changed(data_dir, db_path):
    """§6C: three of the four sources the tárca tables derive from are not sittings.

    Between sitting weeks the sync keeps bringing in newly submitted/answered
    irományok and nothing else, so gating the rebuild on a changed sitting left the
    Tárcák section lagging the data it is derived from on every deployment.
    """
    assert _count(db_path, "SELECT COUNT(*) FROM portfolio_bill "
                           "WHERE portfolio_slug='belugy'") == 0

    bills_file = data_dir / "processed" / "bills-43.json"
    bills = json.loads(bills_file.read_text())
    bills["data"].append(
        {"billId": "bill-uuid-9", "billNumber": "T/109", "billNumberSort": 109,
         "title": "A belügyi tárca javaslata", "type": "törvényjavaslat",
         "mainType": "T", "status": "benyújtva",
         "submittedDate": "2026-06-02T09:00:00Z",
         "textUrl": None, "textCaption": None, "noText": True,
         "sponsors": [{"personID": None, "factionId": None, "committeeId": None,
                       "label": "kormány (belügyminiszter)"}]})
    bills_file.write_text(json.dumps(bills, ensure_ascii=False))

    assert loader.update_database(data_dir, db_path) is True

    assert _count(db_path, "SELECT COUNT(*) FROM portfolio_bill "
                           "WHERE portfolio_slug='belugy' AND bill_id='bill-uuid-9' "
                           "AND role='submitted'") == 1
    # The tárca itself is on the listing, not just the link.
    assert _count(db_path, "SELECT COUNT(*) FROM portfolio WHERE slug='belugy'") == 1


def test_update_rebuilds_portfolios_when_the_mapping_changed(data_dir, db_path,
                                                             tmp_path, monkeypatch):
    """§6C: the mapping is CODE, so editing it changes no processed file.

    An incremental update compares (mtime, size) and would see nothing to do, which
    is why a corrected label used to reach a deployment only when some unrelated
    sitting next happened to land. The fingerprint stamp makes the rebuild follow
    the deploy instead — here through the PARLAMONITOR_PORTFOLIO_MAP knob (OPS-4),
    which changes the effective mapping exactly as an edit to the table does.
    """
    assert _count(db_path, "SELECT COUNT(*) FROM portfolio "
                           "WHERE slug='penzugy' AND name='Pénzügyminisztérium'") == 1

    override = tmp_path / "portfolio-map.json"
    override.write_text(json.dumps(
        [{"slug": "penzugy", "name": "Nemzetgazdasági és Pénzügyminisztérium",
          "kind": "ministry", "aliases": ["pénzügyminiszter"]}], ensure_ascii=False))
    monkeypatch.setenv("PARLAMONITOR_PORTFOLIO_MAP", str(override))
    loader.portfolios._index.cache_clear()
    loader.portfolios.table_fingerprint.cache_clear()
    try:
        # Nothing on disk changed — only the mapping.
        assert loader.update_database(data_dir, db_path) is True
        assert _count(db_path, "SELECT COUNT(*) FROM portfolio WHERE slug='penzugy' "
                               "AND name='Nemzetgazdasági és Pénzügyminisztérium'") == 1
        # …and the stamp now matches, so the next idle pass is a no-op again.
        assert loader.update_database(data_dir, db_path) is False
    finally:
        monkeypatch.delenv("PARLAMONITOR_PORTFOLIO_MAP", raising=False)
        loader.portfolios._index.cache_clear()
        loader.portfolios.table_fingerprint.cache_clear()


def test_update_reloading_committees_also_reloads_their_minutes(data_dir, db_path):
    """Reloading a cycle's committees deletes its meetings and everything keyed
    by them — so the jegyzőkönyvek have to come back with them, even though the
    minutes file itself has not changed.

    Without this, a routine committee refresh (which a sync pass does whenever a
    seat changes hands) silently empties every transcript on the site until the
    next minutes scrape happens to rewrite its file."""
    registry = _committees_registry()
    # A seat changes hands: the smallest thing that rewrites the registry.
    registry["members"][1]["name"] = "Külső Elekné"
    registry["meta"]["counts"]["members"] = 3
    (data_dir / "processed" / "committees-43.json").write_text(
        json.dumps(registry, ensure_ascii=False))

    assert loader.update_database(data_dir, db_path) is True

    assert _count(db_path, "SELECT COUNT(*) FROM committee_minutes") == 1
    assert _count(db_path, "SELECT COUNT(*) FROM committee_speech") == 3
    # And the recordings, which point at both a body and a meeting.
    assert _count(db_path,
                  "SELECT COUNT(*) FROM committee_video "
                  "WHERE meeting_id IS NOT NULL") == 1


def test_update_reloads_changed_minutes_only(data_dir, db_path):
    registry = _committee_minutes_registry()
    registry["data"][0]["speeches"][2]["text"] = \
        "Köszönöm a szót, elnök úr. Egy teljesen új mondattal folytatom."
    (data_dir / "processed" / "committee-minutes-43.json").write_text(
        json.dumps(registry, ensure_ascii=False))

    assert loader.update_database(data_dir, db_path) is True
    c = sqlite3.connect(db_path)
    try:
        texts = [r[0] for r in c.execute(
            "SELECT text FROM committee_speech ORDER BY ord")]
    finally:
        c.close()
    assert "teljesen új mondattal" in texts[2]
    # A reload replaces, never duplicates (ING-4).
    assert _count(db_path, "SELECT COUNT(*) FROM committee_speech") == 3
