"""Scrape the representative registry from the Felicitas ``kepviselo`` API.

This is a self-contained module (requirements EXT-1) supplying everything the
Representatives & Statistics module needs (§6) directly from parlament.hu — no
Wikidata round-trip required:

* the per-cycle MP roster (``kepviselo-lista-idopontban``), and
* per-MP detail: bio + contact, faction-membership history, committee
  memberships, constituency / election history, education, offices, and the
  per-cycle **speech counts** and **bills-submitted counts** that REP-3's
  statistics are built from.

Each MP is keyed by ``personID`` (``kepviseloId``), the same id the proceedings
scraper stamps onto every speech's speaker, so a speech joins to an MP profile
with no name matching (EXT-2).

Detail fetching is one HTTP call per query per MP, so it is opt-in (``details``)
and throttled by the shared politeness delay (SCR-4); the roster alone is a
single paged query.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..config import Paths
from ..felicitas import PHOTO_RESOURCE, FelicitasClient
from ..names import split_name

logger = logging.getLogger(__name__)

# Per-MP detail queries, keyed on {"pId": kepviseloId}. Mapped into the curated
# record by the builders below; add a query here and a mapping to surface more.
DETAIL_QUERIES = (
    "kepviselo-adatok-query",
    "kepviselo-frakcioja-query",
    "kepviselo-bizottsagi-tagsagai-query",
    "kepviselo-valasztasi-adatok-query",
    "kepviselo-vegzettsege-query",
    "kepviselo-tisztseg-query",
    "kepviselo-felszolalasok-szama-query",
    "kepviselo-benyujtott-iromanyok-szama-query",
    "kepviselo-aktivitas-query",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _committees_from_list(nested) -> list[str]:
    """Flatten the roster's nested ``bizottsagiTagsag`` blob to display strings."""
    if not isinstance(nested, dict):
        return []
    return [r[0] for r in nested.get("rows", []) if r]


def _base_record(row: dict) -> dict:
    """Curated record from one roster row (no detail queries needed)."""
    label = (row.get("kepviseloNevRendezeshez") or row.get("kepviseloNev") or "").strip()
    firstname, lastname = split_name(label)
    rec = {
        "personID": row.get("kepviseloId"),
        "label": label,
        "labelFull": (row.get("kepviseloNev") or "").strip(),
        "firstname": firstname,
        "lastname": lastname,
        "faction": {
            "id": row.get("frakcioId"),
            "label": row.get("frakcioNev"),
            "position": row.get("frakcioTisztsegNeve"),
        },
        "committees": _committees_from_list(row.get("bizottsagiTagsag")),
    }
    if rec["personID"]:
        rec["photoURI"] = f"{PHOTO_RESOURCE}/{rec['personID']}"
    return rec


def _apply_details(rec: dict, details: dict[str, list[dict]]) -> None:
    """Fold per-MP detail-query rows into the curated record (in place)."""
    adatok = (details.get("kepviselo-adatok-query") or [None])[0]
    if adatok:
        rec.update({
            "seat": adatok.get("ulohely"),
            "email": adatok.get("emailCim"),
            "website": adatok.get("honlap"),
            "highestEducation": adatok.get("legmagasabbIskolaiVegzettseg"),
            "parliamentaryOffice": adatok.get("orszaggyulesiTisztseg"),
            "stateOffice": adatok.get("allamiTisztseg"),
            "active": adatok.get("kepviseloAktivE"),
        })

    rec["factionHistory"] = [{
        "cycle": r.get("ciklus"),
        "label": r.get("frakcioNev"),
        "start": r.get("frakcioTagsagKezdete"),
        "end": r.get("frakcioTagsagVege"),
    } for r in details.get("kepviselo-frakcioja-query", [])]

    rec["committeeMemberships"] = [{
        "cycle": r.get("ciklus"),
        "committee": r.get("bizottsagNeve"),
        "committeeId": r.get("bizottsagId"),
        "role": r.get("bizottsagiTisztseg"),
        "isSubcommittee": r.get("albizottsag"),
        "start": r.get("tisztsegKezdete"),
        "end": r.get("tisztsegVege"),
    } for r in details.get("kepviselo-bizottsagi-tagsagai-query", [])]

    election = [{
        "cycle": r.get("cikus") or r.get("ciklus"),
        "constituency": r.get("valasztasiKerulet"),
        "electionDate": r.get("megvalasztasNapja"),
        "mandateStart": r.get("mandatumKezdete"),
        "mandateEnd": r.get("mandatumVege"),
    } for r in details.get("kepviselo-valasztasi-adatok-query", [])]
    rec["electionHistory"] = election
    if election:
        rec["constituency"] = election[0].get("constituency")

    rec["education"] = [{
        "degree": r.get("vegzettseg"),
        "institution": r.get("intezmeny"),
        "faculty": r.get("kar"),
        "year": r.get("vegzesEve"),
    } for r in details.get("kepviselo-vegzettsege-query", [])]

    rec["offices"] = [{
        "title": r.get("megnevezes"),
        "start": r.get("kezdet"),
        "end": r.get("vege"),
    } for r in details.get("kepviselo-tisztseg-query", [])]

    rec.setdefault("statistics", {})
    rec["statistics"]["speeches"] = [{
        "cycle": r.get("ciklusId"),
        "cycleLabel": r.get("ciklusNeve"),
        "count": r.get("felszolalasokSzama"),
        "technicalCount": r.get("technikaiFelszolalasokSzama"),
    } for r in details.get("kepviselo-felszolalasok-szama-query", [])]
    rec["statistics"]["billsSubmitted"] = [{
        "cycle": r.get("ciklusId"),
        "cycleLabel": r.get("ciklusNeve"),
        "ownBills": r.get("benyujtottOnalloIromany"),
        "amendments": r.get("benyujtottModositoIromany"),
    } for r in details.get("kepviselo-benyujtott-iromanyok-szama-query", [])]
    rec["statistics"]["activity"] = [{
        "label": r.get("felirat"),
        "count": r.get("szamossag"),
        "order": r.get("sorrend"),
    } for r in details.get("kepviselo-aktivitas-query", [])]


def fetch_representatives(felicitas: FelicitasClient, cycle: int, *,
                          details: bool = True, limit: int | None = None,
                          photos_dir=None) -> dict:
    """Build the representative registry for ``cycle``.

    With ``details`` (default) each MP is enriched via the per-MP detail queries;
    ``limit`` caps how many MPs are processed (useful for a quick test run);
    ``photos_dir`` (a Path) downloads each MP's portrait into it when given."""
    ranges = felicitas.cycle_ranges()
    rng = ranges.get(cycle)
    if not rng or not rng.get("start"):
        raise ValueError(f"No date range known for cycle {cycle}")
    start = rng["start"]
    end = rng.get("end") or datetime.now(timezone.utc).date().isoformat()

    roster = felicitas.representative_list(cycle, start, end)
    logger.info("Cycle %s roster: %d representatives", cycle, len(roster))

    records: list[dict] = []
    for i, row in enumerate(roster):
        if limit is not None and i >= limit:
            break
        rec = _base_record(row)
        pid = rec["personID"]
        if details and pid:
            fetched: dict[str, list[dict]] = {}
            for q in DETAIL_QUERIES:
                try:
                    fetched[q] = felicitas.representative_detail(q, pid)
                except Exception as e:
                    logger.warning("detail %s failed for %s: %s", q, pid, e)
                    fetched[q] = []
            _apply_details(rec, fetched)
        if photos_dir is not None and pid:
            _save_photo(felicitas, photos_dir, pid, rec)
        records.append(rec)
        if (i + 1) % 25 == 0:
            logger.info("  …%d/%d MPs", i + 1, len(roster))

    return {
        "meta": {
            "cycle": cycle,
            "cycleStart": start,
            "cycleEnd": rng.get("end"),
            "scrapedAt": _now_iso(),
            "source": "felicitas-kepviselo-api",
            "withDetails": details,
            "count": len(records),
        },
        "data": records,
    }


def _save_photo(felicitas: FelicitasClient, photos_dir, pid: str, rec: dict) -> None:
    photos_dir.mkdir(parents=True, exist_ok=True)
    try:
        data = felicitas.photo(pid)
    except Exception as e:
        logger.debug("photo fetch failed for %s: %s", pid, e)
        return
    if not data:
        return
    out = photos_dir / f"{pid}.jpg"
    out.write_bytes(data)
    rec["photoFile"] = out.name


def save_representatives(paths: Paths, cycle: int, registry: dict) -> None:
    out = paths.representatives_file(cycle)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(registry, indent=2, ensure_ascii=False))
    tmp.replace(out)
    logger.info("Wrote %s (%d MPs)", out, registry["meta"]["count"])
