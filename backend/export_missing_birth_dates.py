#!/usr/bin/env python3
"""List the roster people whose birth date we don't have (read-only).

The birth date comes from Wikidata (P569) via the P4966 id join, and only at
**day precision** — a year-only statement can't fix a sun sign and is never
stored. This lists everyone in an MP or nationality-advocate roster who is left
without one, with what we do know about why, so the gap is a worklist rather
than a mystery:

    no-wikidata-item   not linked at all — nobody has put a P4966 on an item
    no-birth-date      the item exists, but carries no P569 at all
    year-only          P569 is there at year (or month) precision — the value is
                       shown, but it isn't a day, so we store nothing
    unknown            the precision probe was skipped or unreachable

The first three are the actionable ones, and each row carries the Wikidata item
and the Wikipedia article, which is where the date is usually already written in
prose. Nothing here is written back — this is a report.

    python export_missing_birth_dates.py --cycle 43
    python export_missing_birth_dates.py --out ../exports/missing-birth-dates.csv

``--no-probe`` skips the Wikidata round trip (offline; every linked person is
then reported as ``unknown``).
"""

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys

# The scraper owns the Wikidata endpoint and the polite HTTP client; reuse them
# rather than opening a second way to talk to WDQS.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scraper"))

BATCH = 100  # QIDs per probe query


def _rows(conn: sqlite3.Connection, cycle: int | None, mps_only: bool) -> list[dict]:
    """Roster people (MPs, advocates, or both) with no stored birth date."""
    where = ["p.date_of_birth IS NULL"]
    where.append("p.is_mp = 1" if mps_only else "(p.is_mp = 1 OR p.is_advocate = 1)")
    params: list = []
    if cycle is not None:
        where.append("EXISTS (SELECT 1 FROM membership m WHERE m.person_id = p.person_id "
                     "AND m.period_number = ?)")
        params.append(cycle)
    sql = f"""
        SELECT p.person_id, p.label, p.is_mp, p.is_advocate, p.active,
               p.wikidata_id, p.wikipedia_url,
               (SELECT GROUP_CONCAT(period_number)
                  FROM (SELECT DISTINCT period_number FROM membership
                         WHERE person_id = p.person_id AND period_number IS NOT NULL
                         ORDER BY period_number)) AS cycles
          FROM person p
         WHERE {' AND '.join(where)}
         ORDER BY p.label COLLATE NOCASE
    """
    return [dict(r) for r in conn.execute(sql, params)]


def _probe_precision(qids: list[str]) -> dict[str, tuple[str, str]]:
    """``{QID: (value, precision)}`` for every P569 statement, whatever its precision.

    This is the complement of the scraper's query, which keeps only day precision:
    here we *want* the coarse ones, since "Wikidata knows the year" is a different
    (and much shorter) job to finish than "Wikidata knows nothing". Unreachable →
    empty map, and every row falls back to ``unknown``."""
    from parlamonitor.config import RuntimeConfig
    from parlamonitor.http_client import HttpClient, HttpError

    out: dict[str, tuple[str, str]] = {}
    with HttpClient(RuntimeConfig.from_env()) as http:
        for i in range(0, len(qids), BATCH):
            batch = qids[i:i + BATCH]
            values = " ".join(f"wd:{q}" for q in batch)
            query = ("SELECT ?item ?dob ?prec WHERE { VALUES ?item {" + values + "} "
                     "?item p:P569/psv:P569 [ wikibase:timeValue ?dob ; "
                     "wikibase:timePrecision ?prec ] . }")
            try:
                http.polite_sleep()
                data = http.get_json(
                    "https://query.wikidata.org/sparql",
                    params={"query": query, "format": "json"},
                    headers={"Accept": "application/sparql-results+json"})
            except (HttpError, ValueError) as e:
                print(f"  probe batch {i // BATCH + 1} failed ({e}); "
                      "those stay 'unknown'", file=sys.stderr)
                continue
            for row in data.get("results", {}).get("bindings", []):
                qid = row["item"]["value"].rsplit("/", 1)[-1]
                # Keep the most precise statement a person has.
                prec = row.get("prec", {}).get("value", "0")
                value = row.get("dob", {}).get("value", "")
                if qid not in out or int(prec) > int(out[qid][1]):
                    out[qid] = (value, prec)
    return out


# Wikidata time precision: 7 century, 8 decade, 9 year, 10 month, 11 day. Only 11
# is stored, so everything coarser shows up here with the value it does have —
# "the year is on record, the day isn't" is a much shorter job to finish than
# "nobody has entered anything".
_PRECISION_STATUS = {"7": "century-only", "8": "decade-only",
                     "9": "year-only", "10": "month-only"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("PARLAMONITOR_DB", "parlamonitor.db"))
    ap.add_argument("--cycle", type=int, default=None,
                    help="only people who sat in this electoral cycle")
    ap.add_argument("--mps-only", action="store_true",
                    help="exclude nationality advocates (nemzetiségi szószólók)")
    ap.add_argument("--out", default="../exports/missing-birth-dates.csv")
    ap.add_argument("--no-probe", action="store_true",
                    help="skip the Wikidata precision probe (offline)")
    ap.add_argument("--print", dest="show", type=int, default=25,
                    help="how many rows to print (0 = none)")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    people = _rows(conn, args.cycle, args.mps_only)
    total = conn.execute(
        "SELECT COUNT(*) FROM person WHERE is_mp = 1 OR is_advocate = 1").fetchone()[0]

    linked = sorted({p["wikidata_id"] for p in people if p["wikidata_id"]})
    known: dict[str, tuple[str, str]] = {}
    if linked and not args.no_probe:
        print(f"probing {len(linked)} Wikidata items for a coarser P569 …")
        known = _probe_precision(linked)

    for p in people:
        qid = p["wikidata_id"]
        if not qid:
            p["status"], p["known_date"] = "no-wikidata-item", None
        elif args.no_probe:
            p["status"], p["known_date"] = "unknown", None
        elif qid in known:
            value, prec = known[qid]
            p["status"] = _PRECISION_STATUS.get(prec, f"precision-{prec}")
            p["known_date"] = value[:10].lstrip("+")
        else:
            p["status"], p["known_date"] = "no-birth-date", None

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["person_id", "name", "role", "cycles", "active", "status",
                    "known_date", "wikidata_id", "wikidata_url", "wikipedia_url"])
        for p in people:
            role = "+".join(r for r, on in (("MP", p["is_mp"]),
                                            ("advocate", p["is_advocate"])) if on)
            w.writerow([
                p["person_id"], p["label"], role, (p["cycles"] or "").replace(",", ";"),
                "" if p["active"] is None else int(bool(p["active"])),
                p["status"], p["known_date"] or "", p["wikidata_id"] or "",
                f"https://www.wikidata.org/wiki/{p['wikidata_id']}" if p["wikidata_id"] else "",
                p["wikipedia_url"] or "",
            ])

    scope = f"cycle {args.cycle}" if args.cycle is not None else "all cycles"
    print(f"{len(people)} roster people without a birth date ({scope}; "
          f"{total} roster people in the DB) -> {args.out}")
    by_status: dict[str, int] = {}
    for p in people:
        by_status[p["status"]] = by_status.get(p["status"], 0) + 1
    for status, n in sorted(by_status.items(), key=lambda kv: -kv[1]):
        print(f"  {status:<18} {n}")
    for p in people[:args.show]:
        extra = f"  ({p['status']}{', ' + p['known_date'] if p['known_date'] else ''})"
        print(f"    {p['person_id']:<6} {p['label']}{extra}")
    if args.show and len(people) > args.show:
        print(f"    … {len(people) - args.show} more in the CSV")
    conn.close()


if __name__ == "__main__":
    main()
