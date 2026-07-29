"""Audit: find speeches that parlament.hu stamped with a shared block duration.

Upstream (Országgyűlési napló → "Ülésnapok, ülésidők") sometimes reports the same
`VIDEÓ/FELSZ IDŐ` for every speech in a stretch of a sitting — one block's length
echoed onto each record in it, with `FELSZÓLALÁS KEZDETE` left empty. The loader
derives `speech.duration` from those offsets (ING-1), so each affected record
claims the whole block: in sitting 41050, 176 records each claim the same 146
minutes — 428 hours of "speaking time" out of a single day.

Detection is purely structural, no heuristics about *which* record is right: two
speeches in one sitting can never legitimately share an identical
(time_start, time_end), so any such group is an upstream defect. Severity is the
phantom time it injects — (group size - 1) × the span, i.e. what the aggregates
would over-count if the group were treated as real speaking time.

Read-only; it never writes to the DB.

Run from backend/:
    .venv/bin/python audit_duplicate_spans.py [DB] [options]

    --cycle N          restrict to one electoral period (repeatable)
    --min-minutes M    only groups whose shared span is ≥ M minutes (default 1)
    --min-size N       only groups of ≥ N speeches (default 2)
    --limit N          worst-N groups to list in the detail table (default 15)
    --csv PATH         write every affected speech to PATH for triage
    --fail-on-findings exit 1 when anything is found (for a monitoring cron)
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

# One row per (sitting, shared span): the group's size, how much of the sitting
# it covers, and how many separate agenda sections it bleeds across — the last is
# the giveaway, since a single block's offsets landing on speeches in a dozen
# different sections is exactly what the screenshot of the official listing shows.
GROUPS_SQL = """
SELECT s.session_id,
       s.period_number                                   AS cycle,
       ss.date                                           AS date,
       s.time_start, s.time_end,
       (s.time_end - s.time_start)                       AS span,
       COUNT(*)                                          AS n,
       COUNT(DISTINCT s.agenda_item_id)                  AS sections,
       COUNT(DISTINCT s.person_id)                       AS speakers,
       SUM(s.procedural)                                 AS n_procedural,
       MIN(s.speaker_label)                              AS a_speaker
  FROM speech s
  JOIN session ss ON ss.id = s.session_id
 WHERE s.time_start IS NOT NULL AND s.time_end IS NOT NULL
       AND s.time_end > s.time_start
 GROUP BY s.session_id, s.time_start, s.time_end
HAVING COUNT(*) >= ? AND (s.time_end - s.time_start) >= ?
"""


def _fmt_h(seconds: float) -> str:
    return f"{seconds / 3600:,.0f}h"


def find_groups(conn, min_size: int, min_seconds: float,
                cycles: list[int] | None) -> list[sqlite3.Row]:
    rows = conn.execute(GROUPS_SQL, (min_size, min_seconds)).fetchall()
    if cycles:
        rows = [r for r in rows if r["cycle"] in cycles]
    # Worst first by the phantom time each group injects.
    return sorted(rows, key=lambda r: -(r["n"] - 1) * r["span"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db", nargs="?", default="parlamonitor.db")
    ap.add_argument("--cycle", type=int, action="append", dest="cycles")
    ap.add_argument("--min-minutes", type=float, default=1.0)
    ap.add_argument("--min-size", type=int, default=2)
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--csv", dest="csv_path")
    ap.add_argument("--fail-on-findings", action="store_true")
    args = ap.parse_args(argv)

    db = Path(args.db)
    if not db.exists():
        print(f"error: no such DB: {db}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    groups = find_groups(conn, args.min_size, args.min_minutes * 60, args.cycles)
    if not groups:
        print("No duplicated speech spans found.")
        return 0

    # --- per-cycle summary, against that cycle's own totals -----------------
    print(f"Duplicated spans (≥{args.min_size} speeches sharing one "
          f"start/end, span ≥{args.min_minutes:g} min)\n")
    by_cycle: dict[int, list[sqlite3.Row]] = {}
    for g in groups:
        by_cycle.setdefault(g["cycle"], []).append(g)

    print(f"  {'cycle':>5} {'groups':>7} {'speeches':>9} {'phantom':>9} "
          f"{'of cycle Σ':>11} {'sittings':>9}")
    total_affected = 0
    for cycle in sorted(by_cycle):
        gs = by_cycle[cycle]
        affected = sum(g["n"] for g in gs)
        phantom = sum((g["n"] - 1) * g["span"] for g in gs)
        total_affected += affected
        cycle_total = conn.execute(
            "SELECT COALESCE(SUM(duration), 0) FROM speech WHERE period_number = ?",
            (cycle,)).fetchone()[0]
        share = f"{phantom / cycle_total * 100:.0f}%" if cycle_total else "—"
        print(f"  {cycle:>5} {len(gs):>7} {affected:>9} {_fmt_h(phantom):>9} "
              f"{share:>11} {len({g['session_id'] for g in gs}):>9}")

    # Cycles with no findings are worth naming — they are the control group that
    # shows this is an upstream defect in specific periods, not a loader bug. Only
    # cycles actually examined count: one excluded by --cycle is not "clean", and
    # under a non-default threshold "clean" means "nothing above it", not "nothing".
    examined = [r[0] for r in conn.execute(
        "SELECT DISTINCT period_number FROM speech WHERE period_number IS NOT NULL "
        "ORDER BY 1") if not args.cycles or r[0] in args.cycles]
    clean = [p for p in examined if p not in by_cycle]
    filtered = args.min_size > 2 or args.min_minutes > 1.0
    if clean:
        print(f"\n  clean cycles ({'nothing above the threshold' if filtered else
                                  'no duplicated spans at all'}): "
              f"{', '.join(map(str, clean))}")

    # --- worst groups -------------------------------------------------------
    print(f"\nWorst {min(args.limit, len(groups))} groups:\n")
    print(f"  {'sitting':>8} {'date':>11} {'n':>4} {'span':>7} {'phantom':>8} "
          f"{'sections':>8} {'proc':>5}  speaker")
    for g in groups[:args.limit]:
        print(f"  {g['session_id']:>8} {g['date'] or '':>11} {g['n']:>4} "
              f"{g['span'] / 60:>6.0f}m {_fmt_h((g['n'] - 1) * g['span']):>8} "
              f"{g['sections']:>8} {g['n_procedural']:>4}/{g['n']}  "
              f"{(g['a_speaker'] or '')[:28]}"
              + ("" if g["speakers"] <= 1 else f" (+{g['speakers'] - 1} others)"))

    # --- how much of this already escapes the statistics --------------------
    # Everything flagged here is a data defect, but only the non-procedural part
    # actually reaches representative/faction aggregates (STAT-1).
    leaking = [g for g in groups if g["n"] - g["n_procedural"] > 0]
    leak_speeches = sum(g["n"] - g["n_procedural"] for g in leaking)
    print(f"\n{total_affected} affected speeches; {leak_speeches} of them are NOT "
          f"marked procedural,\nso they still reach the statistics (STAT-1).")
    if leaking:
        print("  leaking groups (worst first):")
        for g in sorted(leaking, key=lambda g: -(g["n"] - g["n_procedural"]))[:10]:
            print(f"    sitting {g['session_id']}  {g['n'] - g['n_procedural']} "
                  f"of {g['n']} counted, {g['span'] / 60:.0f}m each")

    # --- optional per-speech export ----------------------------------------
    if args.csv_path:
        spans = {(g["session_id"], g["time_start"], g["time_end"]) for g in groups}
        rows = conn.execute("""
            SELECT s.uid, s.session_id, s.period_number, ss.date, s.agenda_item_id,
                   a.title AS section, s.speaker_label, s.felszolalas_tipus,
                   s.procedural, s.time_start, s.time_end, s.duration, s.source_page
              FROM speech s
              JOIN session ss ON ss.id = s.session_id
         LEFT JOIN agenda_item a ON a.id = s.agenda_item_id
             WHERE s.time_start IS NOT NULL AND s.time_end IS NOT NULL""").fetchall()
        hits = [r for r in rows
                if (r["session_id"], r["time_start"], r["time_end"]) in spans]
        with open(args.csv_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(hits[0].keys())
            w.writerows([tuple(r) for r in hits])
        print(f"\nWrote {len(hits)} affected speeches to {args.csv_path}")

    conn.close()
    return 1 if args.fail_on_findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
