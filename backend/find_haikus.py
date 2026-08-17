#!/usr/bin/env python3
"""Find accidental haikus hidden in the parliamentary proceedings.

The detection rules live in ``app/haiku.py`` — three lines of 5, 7 and 5
Hungarian syllables (= vowels), never splitting a word — because the Bluesky
announcer (§8.7 SOC-4) posts from the same finder; this is the corpus-wide
exploration front end for it.

Usage:
    python find_haikus.py                 # whole-sentence haikus, pretty output
    python find_haikus.py --partial       # also find 5-7-5 runs inside sentences
    python find_haikus.py --period 43     # only the 43rd electoral cycle
    python find_haikus.py --json out.json # machine-readable output
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from dataclasses import asdict

from app.haiku import Haiku, scan


def _print_pretty(haikus: list[Haiku]) -> None:
    for i, h in enumerate(haikus, 1):
        print(f"\n━━━━━━━━━━━━━━━ #{i} ━━━━━━━━━━━━━━━")
        for line in h.lines:
            print(f"  {line}")
        print(f"  — {h.speaker} · {h.period}. ciklus {h.sitting}. ülésnap · {h.date}")
        print(f"    {h.link}")


def main(argv: list[str] | None = None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    default_db = os.environ.get("PARLAMONITOR_DB", os.path.join(here, "parlamonitor.db"))

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=default_db, help=f"SQLite DB path (default: {default_db})")
    ap.add_argument("--period", type=int, default=None, help="restrict to an electoral cycle, e.g. 43")
    ap.add_argument("--partial", action="store_true",
                    help="also find 5-7-5 runs inside longer sentences (default: whole sentences only)")
    ap.add_argument("--limit", type=int, default=None, help="print at most N haikus")
    ap.add_argument("--base-url", default="", help="prefix for the viewer links (e.g. https://…)")
    ap.add_argument("--json", metavar="PATH", help="write results as JSON to PATH")
    args = ap.parse_args(argv)

    # The scan's "scanned N sentences, found M" progress goes to stderr, so piping
    # the pretty output stays clean.
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    if not os.path.exists(args.db):
        print(f"error: database not found: {args.db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.db)
    try:
        haikus = scan(conn, args.period, args.partial, args.base_url.rstrip("/"))
    finally:
        conn.close()

    if args.limit is not None:
        haikus = haikus[: args.limit]

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump([asdict(h) for h in haikus], fh, ensure_ascii=False, indent=2)
        print(f"wrote {len(haikus)} haiku(s) to {args.json}", file=sys.stderr)
    else:
        _print_pretty(haikus)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
