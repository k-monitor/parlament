"""Scrape the representative registry from the Felicitas ``kepviselo`` API.

This is a self-contained module (requirements EXT-1) supplying everything the
Representatives & Statistics module needs (§6) directly from parlament.hu — no
Wikidata round-trip required:

* the per-cycle MP roster (``kepviselo-lista-idopontban``),
* the cycle's **composition changes** (REP-14) — the mandates that ended mid-cycle
  and the mid-cycle faction switches — which is also what supplies the MPs the
  roster cannot: it answers "who sat on this date", so anyone who left before it
  was asked is missing from it entirely, and
* per-MP detail: bio + contact, faction-membership history, committee
  memberships, constituency / election history, education, offices, the
  **asset declarations** (*vagyonnyilatkozatok*) and CV of REP-13, and the
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

# The three per-MP asset-declaration (vagyonnyilatkozat) queries — see the note
# in DETAIL_QUERIES for why upstream has three.
ASSET_DECLARATION_QUERIES = (
    "kepviselo-vagyon-nyilatkozata-query",
    "kepviselo-vagyon-nyilatkozata2022query",
    "kepviselo-vagyon-nyilatkozata2023query",
)

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
    # Asset declarations (REP-13). Upstream splits them across three queries by
    # the disclosure regime in force, NOT by date range: the legacy one, the
    # one-off "eredeti" declaration filed on taking the seat under the rules from
    # 2022-08-01, and the yearly ones under the rules from 2023-01-01. They are
    # disjoint in practice; `_asset_declarations` still merges + dedupes them.
    *ASSET_DECLARATION_QUERIES,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _committees_from_list(nested) -> list[str]:
    """Flatten the roster's nested ``bizottsagiTagsag`` blob to display strings."""
    if not isinstance(nested, dict):
        return []
    return [r[0] for r in nested.get("rows", []) if r]


def _declaration_url(row: dict) -> str | None:
    """The declaration PDF's absolute URL, as parlament.hu's own adatlap builds it:
    the row's public-server field + ``/vagynyil`` + the row's file path. Upstream
    still names the server over plain ``http``; we link the ``https`` original
    rather than send a reader to an insecure URL.

    ``None`` when the row carries no file — a declaration that was **due but
    never published** is still a row (and still shown, see REP-13), just not a
    link."""
    server = (row.get("vagyonnyilatkozatNyilvanosSzerver") or "").strip()
    path = (row.get("vagyonnyilatkozatFileNev") or "").strip()
    if not server or not path:
        return None
    if server.startswith("http://"):
        server = "https://" + server[len("http://"):]
    return f"{server.rstrip('/')}/vagynyil{path}"


def _asset_declarations(details: dict[str, list[dict]]) -> list[dict]:
    """The person's asset declarations (REP-13), merged from the three upstream
    queries into one list, newest first.

    Keyed on the declaration's own PDF path so a row reported by two of the
    queries is kept once; a row with no file (nothing published) can't be keyed
    that way and is kept as its own entry. The date the list is sorted on is
    ``vagyoniAllapot`` — *when the declared assets were held*, which is what the
    declaration is about — not the filing timestamp."""
    out: dict[object, dict] = {}
    for q in ASSET_DECLARATION_QUERIES:
        for i, row in enumerate(details.get(q) or []):
            url = _declaration_url(row)
            rec = {
                "title": row.get("vagyonnyilatkozatFileNevSzoveg"),
                "url": url,
                # "A vagyoni állapot időpontja" — the reference date of the
                # declared assets (two declarations can share a year: one on
                # taking the seat, one for the year's end).
                "assetDate": row.get("vagyoniAllapot"),
                "deadline": row.get("beadasiHatarido"),
                "submitted": row.get("beadva"),
                "submittedAt": row.get("benyujtasDatuma"),
                "note": row.get("vagyonmegjegyzes"),
            }
            out.setdefault(url or (q, i), rec)
    return sorted(out.values(),
                  key=lambda d: (d.get("assetDate") or "", d.get("submittedAt") or ""),
                  reverse=True)


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

    rec["assetDeclarations"] = _asset_declarations(details)

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


# --- mandates & the composition changes (REP-14) ----------------------------
# `kepviselo-lista-idopontban` is a POINT-IN-TIME roster: it answers "who sat on
# this date". A cycle scraped once therefore holds only whoever held a seat the day
# it was asked, and the dozen or two mandates that ended before that — a death, a
# resignation, an incompatibility — are absent from it altogether, along with the
# fact that a seat ever changed hands. `FelicitasClient.composition_changes` is what
# names them; they live in the same person-id space, so from there on they are
# fetched, keyed and loaded exactly like a sitting MP.


def _mandate_in_cycle(rec: dict, start: str, end: str | None) -> dict | None:
    """The MP's ``electionHistory`` entry for the cycle spanning ``[start, end]``.

    A mandate belongs to this cycle when it **ended on or after the cycle's first
    day and began before its last one**. Both halves are needed and neither may be a
    plain date-prefix comparison of the start alone: upstream dates a mandate by the
    UTC instant of a *local* midnight (9 May 2026 arrives as ``2026-05-08T22:00:00Z``)
    and a cycle's whole opening cohort begins at the first local midnight of the
    term — so every one of them carries the calendar date of the day *before* the
    cycle starts, which is the last day of the previous one (the same trap REP-11
    documents for office terms). Ending-side dates carry no such offset."""
    for row in rec.get("electionHistory") or []:
        began, ended = (row.get("mandateStart") or "")[:10], (row.get("mandateEnd") or "")[:10]
        if ended and ended < start:
            continue                      # ended before this cycle opened
        if end and began and began >= end:
            continue                      # began on/after its last day — the next cycle's
        return row
    return None


def _person_ref(pid, label, **extra) -> dict | None:
    """The other side of a handover: who they are, plus when it happened."""
    if not pid:
        return None
    return {"personID": pid, "label": (label or "").strip(), **extra}


def apply_mandates(records: list[dict], mandate_changes: list[dict],
                   cycle_start: str, cycle_end: str | None) -> None:
    """Stamp each record's ``mandate`` — the seat it held in *this* cycle (REP-14).

    ``terminated`` is upstream's own verdict rather than a date comparison of ours:
    being named by the changes registry **is** the fact that the mandate ended early.
    Someone who served to the end of the term simply is not in it, and their ``end``
    is the term's own end (or, in the running cycle, nothing at all)."""
    ended: dict[str, dict] = {}     # person id -> the change row that ended their term
    succeeded: dict[str, dict] = {}  # person id -> the change row whose seat they took
    for row in mandate_changes or []:
        if row.get("kepviseloId"):
            ended.setdefault(row["kepviseloId"], row)
        if row.get("kovetkezoKepviseloId"):
            succeeded.setdefault(row["kovetkezoKepviseloId"], row)
    for rec in records:
        pid = rec.get("personID")
        term = _mandate_in_cycle(rec, cycle_start, cycle_end) or {}
        left = ended.get(pid) or {}
        took_over_from = succeeded.get(pid) or {}
        rec["mandate"] = {
            # The per-MP election history dates the term to the minute; the change
            # row only to the day, so it is the fallback (and the whole answer for a
            # `--no-details` run, which fetches no election history at all).
            "start": term.get("mandateStart") or left.get("mandatumKezdete")
                     or took_over_from.get("kovetkezoMandatumKezdete"),
            "end": term.get("mandateEnd") or left.get("mandatumVege"),
            "electionDate": term.get("electionDate"),
            "constituency": term.get("constituency") or left.get("valasztoKeruletNeve"),
            "terminated": bool(left),
            "endReason": left.get("mandatumAllapotNeve"),
            # Named in the plain (sort) form the rest of the site labels people by,
            # not the titled one — "Budai Gyula", not "Dr. Budai Gyula".
            "predecessor": _person_ref(
                took_over_from.get("kepviseloId"),
                took_over_from.get("kepviseloNeveSorrendezeshez")
                or took_over_from.get("kepviseloNeve"),
                mandateEnd=took_over_from.get("mandatumVege"),
                endReason=took_over_from.get("mandatumAllapotNeve")),
            "successor": _person_ref(
                left.get("kovetkezoKepviseloId"),
                left.get("kovetkezoKepviseloNeveSorrendezeshez")
                or left.get("kovetkezoKepviseloNeve"),
                mandateStart=left.get("kovetkezoMandatumKezdete")),
        }


def _base_record_from_change(row: dict) -> dict:
    """Curated record for an MP the roster no longer lists, from their change row.

    The same shape ``_base_record`` builds from a roster row, so the detail pass, the
    portrait fetch and the loader treat the two alike. The faction is the one that
    lost the seat, which is what the roster would have reported while they held it."""
    label = (row.get("kepviseloNeveSorrendezeshez") or row.get("kepviseloNeve") or "").strip()
    firstname, lastname = split_name(label)
    rec = {
        "personID": row.get("kepviseloId"),
        "label": label,
        "labelFull": (row.get("kepviseloNeve") or "").strip(),
        "firstname": firstname,
        "lastname": lastname,
        "faction": {"id": row.get("frakcioId"), "label": row.get("frakcioNeve"),
                    "position": None},
    }
    if rec["personID"]:
        rec["photoURI"] = f"{PHOTO_RESOURCE}/{rec['personID']}"
    return rec


def apply_cv(felicitas: FelicitasClient, rec: dict) -> None:
    """Note the person's published CV PDF on ``rec``, when they have one (REP-13).

    Costs one HEAD, and only for someone upstream still marks active: publishing
    the CV is the MP's own choice and the file is taken down when they leave, so
    for everyone else there is nothing to find and we spend no request looking.
    Shared with the advocate stage, whose people live in the same id space. A
    failure leaves the record without a CV — a link we can't verify isn't shown."""
    pid = rec.get("personID")
    if not pid or not rec.get("active"):
        return
    try:
        url = felicitas.cv_url(pid)
    except Exception as e:
        logger.debug("CV probe failed for %s: %s", pid, e)
        return
    if url:
        rec["cvUrl"] = url


# What the per-MP detail pass (and the portrait/CV probes) put on a record — the
# expensive half. A re-run given a `previous` registry reuses these for people it
# already holds instead of re-fetching them (REP-14: backfilling the departed must
# not cost a fresh scrape of all 199 sitting MPs).
_DETAIL_KEYS = (
    "seat", "email", "website", "highestEducation", "parliamentaryOffice",
    "stateOffice", "active", "factionHistory", "committeeMemberships",
    "electionHistory", "constituency", "education", "offices", "assetDeclarations",
    "statistics", "cvUrl", "photoFile",
    # The Wikidata join is one query for the whole roster, so it is always re-run —
    # but a failed/skipped run must not blank what is already on file either.
    "wikidataId", "wikipediaUrl", "dateOfBirth", "zodiacSign", "chineseZodiacSign",
)


def fetch_representatives(felicitas: FelicitasClient, cycle: int, *,
                          details: bool = True, limit: int | None = None,
                          photos_dir=None, link_wikidata: bool = True,
                          changes: bool = True, previous: dict | None = None) -> dict:
    """Build the representative registry for ``cycle``.

    Covers **everyone who held a mandate in the cycle** (REP-14): the roster is a
    point-in-time listing, so the MPs whose mandate ended mid-cycle are added from
    the composition-changes registry and enriched exactly like the sitting ones.
    Every record carries a ``mandate`` block for this cycle, and the changes
    themselves are kept on the registry so the loader can date the handovers.

    With ``details`` (default) each MP is enriched via the per-MP detail queries;
    ``limit`` caps how many MPs are processed from each of the two groups (useful
    for a quick test run); ``photos_dir`` (a Path) downloads each MP's portrait into
    it when given. With ``link_wikidata`` (default) each MP is joined to its Wikidata
    item and Wikipedia article via property P4966 (one extra query for the whole
    roster). ``changes=False`` skips the composition-changes queries, leaving the
    old point-in-time roster. Passing ``previous`` (an already-scraped registry for
    this cycle) **reuses its per-MP details** and spends requests only on people it
    does not already hold — how the departed are backfilled into a scraped corpus."""
    ranges = felicitas.cycle_ranges()
    rng = ranges.get(cycle)
    if not rng or not rng.get("start"):
        raise ValueError(f"No date range known for cycle {cycle}")
    start = rng["start"]
    end = rng.get("end") or datetime.now(timezone.utc).date().isoformat()

    roster = felicitas.representative_list(cycle, start, end)
    logger.info("Cycle %s roster: %d representatives", cycle, len(roster))

    changed: dict[str, list[dict]] = {"mandate": [], "faction": []}
    if changes:
        try:
            changed = felicitas.composition_changes(cycle, start, end)
        except Exception as e:  # an old cycle upstream has no changes page for
            logger.warning("composition changes failed for cycle %s: %s", cycle, e)
    seated = {r.get("kepviseloId") for r in roster}
    departed = [r for r in changed.get("mandate") or []
                if r.get("kepviseloId") and r["kepviseloId"] not in seated]
    if changed.get("mandate"):
        logger.info("Cycle %s composition changes: %d mandate, %d faction "
                    "(%d MP(s) the roster no longer lists)", cycle,
                    len(changed.get("mandate") or []), len(changed.get("faction") or []),
                    len(departed))

    # One SPARQL query for the whole P4966 -> Wikidata/Wikipedia map; joined to
    # each MP by id below (EXT-2 — never by name). Degrades to {} on failure.
    wd_links = wikidata.fetch_mp_links(felicitas.http) if link_wikidata else {}
    known = {r["personID"]: r for r in (previous or {}).get("data", []) if r.get("personID")}
    reused = 0

    def enrich(rec: dict) -> None:
        """Fill one curated record: Wikidata link, per-MP details, CV, portrait."""
        nonlocal reused
        pid = rec["personID"]
        link = wd_links.get(pid) if pid else None
        if link:
            rec["wikidataId"] = link.get("wikidataId")
            if link.get("wikipediaUrl"):
                rec["wikipediaUrl"] = link["wikipediaUrl"]
            # Birth date + the signs derived from it (Wikidata P569). The roster
            # carries no birth date of its own, and both signs are pure functions
            # of the day, so all three are stamped here once.
            if link.get("dateOfBirth"):
                rec["dateOfBirth"] = link["dateOfBirth"]
                rec["zodiacSign"] = link.get("zodiacSign")
                rec["chineseZodiacSign"] = link.get("chineseZodiacSign")
        on_file = known.get(pid) if pid else None
        if details and pid and not on_file:
            fetched: dict[str, list[dict]] = {}
            for q in DETAIL_QUERIES:
                try:
                    fetched[q] = felicitas.representative_detail(q, pid)
                except Exception as e:
                    logger.warning("detail %s failed for %s: %s", q, pid, e)
                    fetched[q] = []
            apply_details(rec, fetched)
            apply_cv(felicitas, rec)
        elif on_file:
            # Whatever this run did fetch wins; the rest is carried over rather
            # than re-requested (and rather than lost).
            for key in _DETAIL_KEYS:
                if on_file.get(key) is not None:
                    rec.setdefault(key, on_file[key])
            reused += 1
        # A portrait already on file is not re-downloaded for a reused record.
        if photos_dir is not None and pid and not (on_file and rec.get("photoFile")):
            save_photo(felicitas, photos_dir, pid, rec)

    records: list[dict] = []
    for i, row in enumerate(roster):
        if limit is not None and i >= limit:
            break
        rec = _base_record(row)
        enrich(rec)
        records.append(rec)
        if (i + 1) % 25 == 0:
            logger.info("  …%d/%d MPs", i + 1, len(roster))
    for i, row in enumerate(departed):
        if limit is not None and i >= limit:
            break
        rec = _base_record_from_change(row)
        enrich(rec)
        records.append(rec)

    apply_mandates(records, changed.get("mandate") or [], start, rng.get("end"))

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
            "birthDatesLinked": sum(1 for r in records if r.get("dateOfBirth")),
            "withChanges": changes,
            # How many of the count are MPs the roster no longer lists (REP-14),
            # and how many records were served from the previous registry.
            "departed": sum(1 for r in records if r["mandate"]["terminated"]),
            "detailsReused": reused,
            "count": len(records),
        },
        # The cycle's composition changes as upstream reports them: the mandate
        # handovers (with the reason each ended) and the mid-cycle faction switches.
        # The faction half repeats what each MP's own faction history already dates
        # and is kept as a cross-check, not as a second source of truth (REP-14).
        "changes": {"mandate": changed.get("mandate") or [],
                    "faction": changed.get("faction") or []},
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
