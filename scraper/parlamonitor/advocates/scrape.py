"""Scrape the nationality-advocate registry (*nemzetiségi szószólók*).

Hungary's thirteen recognised nationalities each elect a **szószóló** — a
nationality advocate who sits in the House, speaks in plenary and submits
irományok, but holds no representative mandate and casts no vote. parlament.hu
lists them on its own page (``/web/guest/szoszolok``, active-only view
``/web/guest/szoszolok-aktiv-``), backed by the Felicitas
``szoszolo-lista-query`` — **not** by the MP roster query, which is exactly why
advocates were until now only ever seen as bare speaker stubs created from the
transcripts (see ``parlamonitor.speaker_photos``).

An advocate lives in the **same person id space** as an MP (the roster's
``szoszoloId`` is a ``kepviseloId``), so:

* the id joins straight to the speeches the proceedings scraper already stamped
  with it — no name matching (EXT-2), and no re-scrape of any sitting; and
* the per-MP detail queries (bio, committees, education, per-cycle speech and
  motion counts) answer for them unchanged, so this stage reuses
  ``representatives.scrape``'s query list and record builders and emits the
  **same curated record shape**, plus the advocate-specific ``nationality``.

The output is a **separate per-cycle registry file**
(``processed/advocates-<cycle>.json``), deliberately not merged into
``representatives-<cycle>.json``: an existing scrape needs no re-run of the
(expensive) MP roster stage — dropping the new files in is enough for the
loader's incremental update to pick them up (SCR-2 / ING-5).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .. import wikidata
from ..config import Paths
from ..felicitas import PHOTO_RESOURCE, FelicitasClient
from ..names import split_name
from ..representatives.scrape import DETAIL_QUERIES, apply_details, save_photo

logger = logging.getLogger(__name__)

# The office of nationality advocate was created by the 2011 election act and
# first filled in the 2014 general election, i.e. electoral cycle 40. Earlier
# cycles simply have none; the probe below still asks (one cheap list query per
# cycle) so a correction upstream would be picked up rather than assumed away.
FIRST_CYCLE = 40

# The advocate's record ``mandate`` marker. Kept as a string (not a bare bool) so
# other non-MP mandates can join the same field later without a schema change.
MANDATE = "nationality-advocate"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _base_record(row: dict) -> dict:
    """Curated record from one advocate roster row (no detail queries needed)."""
    label = (row.get("nevRendezeshez") or row.get("szoszoloMegjelenoNeve") or "").strip()
    firstname, lastname = split_name(label)
    rec = {
        "personID": row.get("szoszoloId"),
        "label": label,
        "labelFull": (row.get("szoszoloMegjelenoNeve") or "").strip(),
        "firstname": firstname,
        "lastname": lastname,
        "mandate": MANDATE,
        # The nationality the advocate represents ("roma", "szerb", "örmény", …).
        # It stands in for the faction/constituency an MP has and neither of which
        # an advocate does, so it is their defining affiliation.
        "nationality": (row.get("nemzetiseg") or "").strip() or None,
    }
    if rec["personID"]:
        rec["photoURI"] = f"{PHOTO_RESOURCE}/{rec['personID']}"
    return rec


def _apply_roster_counts(rec: dict, row: dict, cycle: int) -> None:
    """Fill the per-cycle activity counts from the roster row.

    ``szoszolo-lista-query`` reports this cycle's speech and own-motion counts
    directly (``pStatisztikaiAdatok``), so a ``--no-details`` run still yields the
    numbers the profile shows. Only used where the (richer, all-cycles) per-person
    detail queries produced nothing — for an advocate with no activity at all they
    return no rows, and this is then the authoritative zero."""
    stats = rec.setdefault("statistics", {})
    if not stats.get("speeches") and row.get("felszolalasokSzama") is not None:
        stats["speeches"] = [{"cycle": cycle, "count": row["felszolalasokSzama"]}]
    if not stats.get("billsSubmitted") and row.get("onalloInditvanyokSzama") is not None:
        stats["billsSubmitted"] = [{"cycle": cycle,
                                    "ownBills": row["onalloInditvanyokSzama"]}]


def fetch_advocates(felicitas: FelicitasClient, cycle: int, *,
                    details: bool = True, limit: int | None = None,
                    photos_dir=None, link_wikidata: bool = True) -> dict:
    """Build the nationality-advocate registry for ``cycle``.

    Mirrors :func:`parlamonitor.representatives.scrape.fetch_representatives`:
    with ``details`` (default) each advocate is enriched via the per-person detail
    queries, ``limit`` caps how many are processed, ``photos_dir`` (a Path)
    downloads portraits into it, and ``link_wikidata`` joins Wikidata/Wikipedia
    via P4966. A cycle with no advocates yields an empty registry, not an error."""
    ranges = felicitas.cycle_ranges()
    rng = ranges.get(cycle) or {}

    roster = felicitas.advocate_list(cycle)
    logger.info("Cycle %s: %d nationality advocate(s)", cycle, len(roster))

    # One SPARQL query for the whole P4966 → Wikidata/Wikipedia map (the property
    # covers advocates too, since it keys on the parlament.hu person id); joined by
    # id below, never by name (EXT-2). Degrades to {} on failure. Skipped outright
    # when the cycle has no advocates, so an empty cycle costs one request total.
    wd_links = (wikidata.fetch_mp_links(felicitas.http)
                if (link_wikidata and roster) else {})

    records: list[dict] = []
    for i, row in enumerate(roster):
        if limit is not None and i >= limit:
            break
        rec = _base_record(row)
        pid = rec["personID"]
        link = wd_links.get(pid) if pid else None
        if link:
            rec["wikidataId"] = link.get("wikidataId")
            if link.get("wikipediaUrl"):
                rec["wikipediaUrl"] = link["wikipediaUrl"]
        if details and pid:
            fetched: dict[str, list[dict]] = {}
            for q in DETAIL_QUERIES:
                try:
                    fetched[q] = felicitas.representative_detail(q, pid)
                except Exception as e:
                    logger.warning("detail %s failed for %s: %s", q, pid, e)
                    fetched[q] = []
            apply_details(rec, fetched)
        _apply_roster_counts(rec, row, cycle)
        if photos_dir is not None and pid:
            save_photo(felicitas, photos_dir, pid, rec)
        records.append(rec)

    return {
        "meta": {
            "cycle": cycle,
            "cycleStart": rng.get("start"),
            "cycleEnd": rng.get("end"),
            "scrapedAt": _now_iso(),
            "source": "felicitas-szoszolo-api",
            "mandate": MANDATE,
            "withDetails": details,
            "withWikidata": link_wikidata,
            "wikidataLinked": sum(1 for r in records if r.get("wikidataId")),
            "count": len(records),
        },
        "data": records,
    }


def advocate_cycles(felicitas: FelicitasClient, *,
                    first_cycle: int = FIRST_CYCLE) -> list[int]:
    """Every electoral cycle from ``first_cycle`` on that has advocates.

    One cheap list query per cycle, so backfilling an already-scraped corpus needs
    no hard-coded cycle list (and a newly-opened cycle is picked up on its own)."""
    ranges = felicitas.cycle_ranges()
    out: list[int] = []
    for cycle in sorted(c for c in ranges if c >= first_cycle):
        if felicitas.advocate_list(cycle):
            out.append(cycle)
    logger.info("Cycles with nationality advocates: %s", out)
    return out


def save_advocates(paths: Paths, cycle: int, registry: dict) -> None:
    out = paths.advocates_file(cycle)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(registry, indent=2, ensure_ascii=False))
    tmp.replace(out)
    logger.info("Wrote %s (%d advocates)", out, registry["meta"]["count"])
