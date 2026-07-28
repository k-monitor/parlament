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
from pathlib import Path

from .. import wikidata
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


def apply_details(rec: dict, details: dict[str, list[dict]]) -> None:
    """Fold per-MP detail-query rows into the curated record (in place).

    Shared with the nationality-advocate stage (``parlamonitor.advocates``): an
    advocate lives in the same person id space, so the same ``DETAIL_QUERIES``
    answer for them and the curated record keeps one shape for both."""
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
                          photos_dir=None, link_wikidata: bool = True) -> dict:
    """Build the representative registry for ``cycle``.

    With ``details`` (default) each MP is enriched via the per-MP detail queries;
    ``limit`` caps how many MPs are processed (useful for a quick test run);
    ``photos_dir`` (a Path) downloads each MP's portrait into it when given.
    With ``link_wikidata`` (default) each MP is joined to its Wikidata item and
    Wikipedia article via property P4966 (one extra query for the whole roster)."""
    ranges = felicitas.cycle_ranges()
    rng = ranges.get(cycle)
    if not rng or not rng.get("start"):
        raise ValueError(f"No date range known for cycle {cycle}")
    start = rng["start"]
    end = rng.get("end") or datetime.now(timezone.utc).date().isoformat()

    roster = felicitas.representative_list(cycle, start, end)
    logger.info("Cycle %s roster: %d representatives", cycle, len(roster))

    # One SPARQL query for the whole P4966 -> Wikidata/Wikipedia map; joined to
    # each MP by id below (EXT-2 — never by name). Degrades to {} on failure.
    wd_links = wikidata.fetch_mp_links(felicitas.http) if link_wikidata else {}

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
        if photos_dir is not None and pid:
            save_photo(felicitas, photos_dir, pid, rec)
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
            "withWikidata": link_wikidata,
            "wikidataLinked": sum(1 for r in records if r.get("wikidataId")),
            "count": len(records),
        },
        "data": records,
    }


def save_photo(felicitas: FelicitasClient, photos_dir, pid: str, rec: dict) -> None:
    """Download one person's portrait into ``photos_dir`` and note it on ``rec``.

    Shared with the advocate stage; a missing portrait is simply skipped."""
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


# --- portraits for speakers who are NOT in the MP roster --------------------
# Ministers and nationality advocates (nemzetiségi szószólók) speak in the House
# but are not returned by the MP roster query, so they never get a portrait the
# way roster MPs do (`save_photo`). Many of them DO have a portrait in the very
# same Felicitas image resource, keyed by their kepviseloId (a nationality
# advocate like Gallai Gergely resolves; a minister with no portrait 404s). We
# fetch those here and cache the 404s so a repeat run stays polite (SCR-4).

_PHOTO_MISSING_FILE = "photos-missing.json"


def _photo_negcache_path(photos_dir: Path) -> Path:
    return photos_dir / _PHOTO_MISSING_FILE


def load_photo_negcache(photos_dir) -> set[str]:
    """Ids known to have no portrait (a definitive 404), so they aren't re-fetched."""
    try:
        return set(json.loads(_photo_negcache_path(Path(photos_dir)).read_text()))
    except (OSError, ValueError):
        return set()


def fetch_missing_photos(felicitas: FelicitasClient, photos_dir, ids) -> dict:
    """Download portraits for ``ids`` that aren't already on disk.

    Skips ids whose ``<pid>.jpg`` already exists or that are in the negative
    cache. A definitive 404 (``felicitas.photo`` returns ``None``) is recorded in
    the negative cache so a later run doesn't re-request it; a transient error
    (raises) is left uncached so it is retried next time. Returns counts.
    """
    photos_dir = Path(photos_dir)
    photos_dir.mkdir(parents=True, exist_ok=True)
    missing = load_photo_negcache(photos_dir)
    fetched = 0
    for pid in ids:
        if not pid:
            continue
        if (photos_dir / f"{pid}.jpg").exists() or pid in missing:
            continue
        try:
            data = felicitas.photo(pid)
        except Exception as e:  # transient (5xx / network) — retry next run
            logger.debug("speaker photo fetch failed for %s: %s", pid, e)
            continue
        if data:
            (photos_dir / f"{pid}.jpg").write_bytes(data)
            fetched += 1
        else:
            missing.add(pid)  # definitive 404 — no portrait upstream
    _photo_negcache_path(photos_dir).write_text(
        json.dumps(sorted(missing), ensure_ascii=False))
    return {"fetched": fetched, "cached_missing": len(missing)}


def save_representatives(paths: Paths, cycle: int, registry: dict) -> None:
    out = paths.representatives_file(cycle)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(registry, indent=2, ensure_ascii=False))
    tmp.replace(out)
    logger.info("Wrote %s (%d MPs)", out, registry["meta"]["count"])
