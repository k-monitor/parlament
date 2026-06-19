"""Loader correctness: JSON -> normalized DB, idempotency, aggregates (OPS-3)."""

from __future__ import annotations

import json

from app import loader


def test_core_rows_loaded(conn):
    assert conn.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM speech").fetchone()[0] == 2
    # Only the speech with a transcript contributes sentences.
    assert conn.execute("SELECT COUNT(*) FROM sentence").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM person WHERE is_mp=1").fetchone()[0] == 2


def test_speech_uid_is_session_plus_index(conn):
    uids = {r[0] for r in conn.execute("SELECT uid FROM speech")}
    assert uids == {"43001-1", "43001-2"}


def test_degraded_speech_ingested_not_dropped(conn):
    """SCR-5: a no-transcript speech is kept, flagged with low confidence."""
    row = conn.execute(
        "SELECT has_text, confidence, align_method FROM speech WHERE uid='43001-2'"
    ).fetchone()
    assert row["has_text"] == 0
    assert row["confidence"] == 0.5
    # Falls back to per-speech video offsets for its span.
    span = conn.execute("SELECT time_start, time_end FROM speech WHERE uid='43001-2'").fetchone()
    assert span["time_start"] == 50.0 and span["time_end"] == 60.0


def test_timing_provenance_marked_estimated(conn):
    """TIM-3: every text speech is stamped estimated-day-offset."""
    row = conn.execute("SELECT align_method, confidence FROM speech WHERE uid='43001-1'").fetchone()
    assert row["align_method"] == "estimated-day-offset"
    assert row["confidence"] == 0.7


def test_speech_span_derived_from_sentences(conn):
    row = conn.execute("SELECT time_start, time_end, duration FROM speech WHERE uid='43001-1'").fetchone()
    assert row["time_start"] == 10.0
    assert row["time_end"] == 40.0
    assert row["duration"] == 30.0


def test_aggregates_speaking_time(conn):
    # k001 spoke 30s in one speech.
    row = conn.execute(
        "SELECT speech_count, speaking_seconds FROM person_stats "
        "WHERE person_id='k001' AND period_number IS NULL").fetchone()
    assert row["speech_count"] == 1
    assert row["speaking_seconds"] == 30.0
    # Faction all-periods aggregate row exists (factions endpoint depends on it).
    fid = conn.execute("SELECT id FROM faction WHERE label='Fidesz'").fetchone()[0]
    frow = conn.execute(
        "SELECT mp_count, speaking_seconds FROM faction_stats "
        "WHERE faction_id=? AND period_number IS NULL", (fid,)).fetchone()
    assert frow["mp_count"] == 1 and frow["speaking_seconds"] == 30.0


def test_reingest_is_idempotent(conn, db_path, tmp_path):
    """ING-4: re-loading a session replaces, never duplicates."""
    from tests.conftest import _session_record
    c = loader.connect(db_path)
    loader.load_session(c, _session_record())
    loader.load_session(c, _session_record())   # twice
    loader.rebuild_aggregates(c)
    assert c.execute("SELECT COUNT(*) FROM speech WHERE session_id='43001'").fetchone()[0] == 2
    assert c.execute("SELECT COUNT(*) FROM sentence").fetchone()[0] == 2
    c.close()


def test_faction_colors_assigned(conn):
    rows = dict(conn.execute("SELECT label, color FROM faction"))
    assert rows["Fidesz"] == "#FF6A13"
    assert rows["TISZA"] == "#00A6A6"
