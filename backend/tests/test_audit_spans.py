"""The duplicate-span audit: an identical (start, end) inside one sitting is an
upstream defect (parlament.hu echoing a block's `VIDEÓ/FELSZ IDŐ` onto every
speech in it); distinct spans, and identical spans in *different* sittings, are
not."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def audit():
    path = Path(__file__).resolve().parent.parent / "audit_duplicate_spans.py"
    spec = importlib.util.spec_from_file_location("audit_duplicate_spans", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def spans_db(db_path):
    """The loaded fixture DB plus a hand-built sitting mixing clean and echoed
    spans, so the audit has both to discriminate."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT OR IGNORE INTO session(id, date, period_number) "
                 "VALUES ('99001', '2024-01-01', 42)")
    conn.execute("INSERT OR IGNORE INTO session(id, date, period_number) "
                 "VALUES ('99002', '2024-01-02', 42)")
    rows = [
        # 3 speeches echoing one 60-minute block — the defect.
        ("99001-1", "99001", 0.0, 3600.0), ("99001-2", "99001", 0.0, 3600.0),
        ("99001-3", "99001", 0.0, 3600.0),
        # 2 clean, non-overlapping speeches in the same sitting.
        ("99001-4", "99001", 3600.0, 3900.0), ("99001-5", "99001", 3900.0, 4200.0),
        # Same span as the block above, but in ANOTHER sitting — not a group.
        ("99002-1", "99002", 0.0, 3600.0),
    ]
    conn.executemany(
        "INSERT INTO speech(uid, session_id, period_number, time_start, time_end, "
        "duration, procedural) VALUES (?,?,42,?,?,?,0)",
        [(u, s, a, b, b - a) for u, s, a, b in rows])
    conn.commit()
    conn.close()
    return db_path


def _groups(audit, db, **kw):
    conn = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return audit.find_groups(conn, kw.get("min_size", 2),
                                 kw.get("min_seconds", 60), kw.get("cycles"))
    finally:
        conn.close()


def test_flags_only_the_echoed_block(audit, spans_db):
    groups = _groups(audit, spans_db)
    assert len(groups) == 1, "the clean pair and the cross-sitting match must not group"
    g = groups[0]
    assert (g["session_id"], g["n"], g["span"]) == ("99001", 3, 3600.0)


def test_phantom_time_counts_the_duplicates_only(audit, spans_db):
    """3 records × 60 min where only one block is real ⇒ 2 hours of phantom time."""
    g = _groups(audit, spans_db)[0]
    assert (g["n"] - 1) * g["span"] == 2 * 3600.0


def test_thresholds_and_cycle_filter(audit, spans_db):
    assert _groups(audit, spans_db, min_size=4) == []
    assert _groups(audit, spans_db, min_seconds=3601) == []
    assert _groups(audit, spans_db, cycles=[43]) == []
    assert len(_groups(audit, spans_db, cycles=[42])) == 1
