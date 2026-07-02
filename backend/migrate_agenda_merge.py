"""Merge agenda items that were split by per-speech type (2026-07-02).

One agenda ACT was split into several ``agenda_item`` rows whenever its speeches
had different per-speech types, because the type was (wrongly) part of the
grouping key AND classified from the speech type. So every interpelláció became a
``questioning_of_the_government`` section plus a stray ``qa`` one, and "Személyes
érintettség" split into a regular and an ügyrendi half — each a single section on
parlament.hu.

This makes the DB match the fixed loader/scraper: group by act identity
(official_title), and classify each named act's type from its own name, falling
back to its first speech's type ONLY when the name carries no type signal (so
"Az ülés napirendjének megállapítása" stays procedural rather than becoming
regular). Duplicate rows of one act are merged into the earliest, their speeches
repointed, and ``ord`` re-sequenced.

Idempotent. Run after migrate_paragraphs_and_titles.py.

Usage:  python migrate_agenda_merge.py [DB]   (default: parlamonitor.db)
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path


def _load_agenda():
    """Load the scraper's agenda module straight from its file (it imports only
    ``re``), reusing the exact classifier without importing the whole package."""
    path = (Path(__file__).resolve().parent.parent
            / "scraper" / "parlamonitor" / "agenda.py")
    spec = importlib.util.spec_from_file_location("agenda_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    db_path = sys.argv[1] if len(sys.argv) > 1 else "parlamonitor.db"
    agenda = _load_agenda()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    def act_type(agenda_item_id, official_title):
        """The fixed classification for one act: from its name, or (when the name
        gives only the generic `regular`) from its first speech's type."""
        native, core = agenda.classify(official_title)
        if core == agenda.CORE_REGULAR and native is None:
            row = conn.execute(
                "SELECT felszolalas_tipus FROM speech WHERE agenda_item_id=? "
                "ORDER BY speech_index LIMIT 1", (agenda_item_id,)).fetchone()
            native, core = agenda.classify(row["felszolalas_tipus"] if row else None)
        return core, native

    merged = repointed = retyped = 0
    sessions = [r["id"] for r in conn.execute("SELECT id FROM session ORDER BY id")]
    for sid in sessions:
        # 1. Merge duplicate named acts (same official_title) → keep earliest ord.
        rows = conn.execute(
            "SELECT id, official_title FROM agenda_item WHERE session_id=? "
            "ORDER BY ord", (sid,)).fetchall()
        groups: dict[str, list[int]] = defaultdict(list)
        for r in rows:
            oft = (r["official_title"] or "").strip()
            if oft:
                groups[oft].append(r["id"])
        for ids in groups.values():
            if len(ids) < 2:
                continue
            keep = ids[0]
            for dup in ids[1:]:
                cur = conn.execute(
                    "UPDATE speech SET agenda_item_id=? WHERE agenda_item_id=?",
                    (keep, dup))
                repointed += cur.rowcount
                conn.execute("DELETE FROM agenda_item WHERE id=?", (dup,))
                merged += 1

        # 2. Re-type every named act with the fixed rule (now that its speeches
        #    are all under one row). Structural single rows keep their type when
        #    the name is uninformative (fallback re-derives it from the speech).
        for r in conn.execute(
                "SELECT id, official_title, type, native_type FROM agenda_item "
                "WHERE session_id=? AND official_title IS NOT NULL "
                "AND official_title != ''", (sid,)).fetchall():
            core, native = act_type(r["id"], r["official_title"])
            if core != r["type"] or native != r["native_type"]:
                conn.execute("UPDATE agenda_item SET type=?, native_type=? WHERE id=?",
                             (core, native, r["id"]))
                retyped += 1

        # 3. Re-sequence ord so the remaining sections stay 0..n-1 (gap-free).
        remaining = conn.execute(
            "SELECT id FROM agenda_item WHERE session_id=? ORDER BY ord", (sid,)
        ).fetchall()
        for new_ord, r in enumerate(remaining):
            conn.execute("UPDATE agenda_item SET ord=? WHERE id=?", (new_ord, r["id"]))

    conn.commit()
    print(f"merged_items={merged} speeches_repointed={repointed} retyped={retyped}")
    conn.close()


if __name__ == "__main__":
    main()
