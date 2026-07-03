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
    from tests.conftest import _session_record
except ImportError:  # when pytest imports conftest as a top-level module
    from conftest import _session_record


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
        "43001-session.json"}


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


def test_update_reloads_changed_bills_only(data_dir, db_path):
    bills = json.loads((data_dir / "processed" / "bills-43.json").read_text())
    bills["data"][0]["title"] = "Módosított cím a frissítés tesztjéhez"
    (data_dir / "processed" / "bills-43.json").write_text(
        json.dumps(bills, ensure_ascii=False))

    assert loader.update_database(data_dir, db_path) is True
    assert _count(db_path, "SELECT COUNT(*) FROM bill "
                           "WHERE title='Módosított cím a frissítés tesztjéhez'") == 1


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
