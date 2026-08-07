"""Scrape the **office-holder registry** (*tisztségviselők*) — who held which
government / House office, and exactly when.

parlament.hu publishes this as its own page (``/web/guest/tisztsegviselok``),
backed by a single Felicitas listing query (see
:meth:`parlamonitor.felicitas.FelicitasClient.office_holders`). One row per
(person, office, term), each carrying the **real appointment and dismissal
timestamps** — with an open end while the office is still held.

Why this stage exists at all, given the MP roster already reports each MP's
offices (``kepviselo-tisztseg-query``):

* a **minister or state secretary who is not an MP** appears in no roster — not
  the MP one, not the advocates one — so nothing dated their office. They show up
  in our corpus only as speakers, and their office was until now only datable from
  their own speeches, which bounds it from below and never says when it ended (a
  sitting minister looked like they left on the last sitting day of the data);
* it covers **every** office category in one query, historical terms included, so
  a profile can list a person's whole office history rather than the current post.

Ids are ``kepvId`` — the same person id space the transcripts already carry (EXT-2),
so a term joins to a speaker with no name matching.

Output is one **cycle-less** file (``processed/officeholders.json``): the registry
is a single all-time listing, so there is nothing per-cycle to key it by. It is
additive like the advocates file — dropping it in is enough for the loader's
incremental update to pick it up (SCR-2 / ING-5); no other stage is invalidated.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

from ..config import Paths
from ..felicitas import FelicitasClient
from ..names import split_name

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _term(row: dict) -> dict:
    """One office term from a registry row, in the same ``{title, start, end}``
    shape the MP roster's ``offices`` uses — so the loader has one code path.

    ``category`` is the portal's own office grouping, which the client tags each
    row with (the row itself carries only the free-text title); absent on a row
    from an older cache, in which case the term is simply uncategorised."""
    return {
        "title": (row.get("tisztseg") or "").strip() or None,
        "start": row.get("tol"),
        # Null while the office is still held; kept null rather than filled in, so
        # nothing downstream invents a departure date.
        "end": row.get("ig"),
        "category": row.get("category"),
    }


def _sort_key(term: dict) -> str:
    """Newest term first; a term with no start sorts last."""
    return term.get("start") or ""


def group_by_person(rows: list[dict]) -> list[dict]:
    """Group registry rows into one record per person, newest office first.

    A person is identified by ``kepvId``; a row without one is dropped (it could
    not be joined to a speaker anyway) and counted by the caller.

    The split first/last name is carried too: most of this registry never appears
    in an MP roster, so for an office-holder who is not (and never was) an MP this
    file is the only place their name is broken up for name-ordered listings."""
    people: dict[str, dict] = {}
    for row in rows:
        pid = (row.get("kepvId") or "").strip()
        term = _term(row)
        if not pid or not term["title"]:
            continue
        if pid not in people:
            label = (row.get("nevElonevNelkul") or row.get("nev") or "").strip()
            firstname, lastname = split_name(label)
            people[pid] = {
                "personID": pid,
                "label": label,
                "labelFull": (row.get("nev") or "").strip() or None,
                "firstname": firstname or None,
                "lastname": lastname or None,
                "offices": [],
            }
        people[pid]["offices"].append(term)
    for rec in people.values():
        rec["offices"].sort(key=_sort_key, reverse=True)
    return [people[pid] for pid in sorted(people)]


def fetch_office_holders(felicitas: FelicitasClient, *,
                         as_of: str | None = None) -> dict:
    """Build the office-holder registry: every recorded office term, by person.

    ``as_of`` (``YYYY-MM-DD``, default today) is the listing's upper date bound —
    terms starting after it are excluded. Costs a handful of paged requests for the
    whole archive, so this stage is cheap enough to re-run on every sync."""
    as_of = as_of or date.today().isoformat()
    rows = felicitas.office_holders(as_of=as_of)
    records = group_by_person(rows)
    terms = sum(len(r["offices"]) for r in records)
    # Rows with no person id or no office name are unusable (nothing to join to,
    # nothing to show) — reported rather than silently dropped.
    dropped = len(rows) - terms
    if dropped:
        logger.info("Office holders: skipped %d row(s) with no person id or title",
                    dropped)
    # Per-category term counts: the categories come from *which listing* a row was
    # returned by (see the client), so a category silently going empty is a change
    # upstream rather than in the data — worth having in the file to compare runs.
    by_category: dict[str, int] = {}
    for rec in records:
        for office in rec["offices"]:
            key = office.get("category") or "uncategorised"
            by_category[key] = by_category.get(key, 0) + 1
    logger.info("Office holders: %d term(s) over %d people (as of %s) — %s",
                terms, len(records), as_of,
                ", ".join(f"{k}: {v}" for k, v in sorted(by_category.items())))
    return {
        "meta": {
            "scrapedAt": _now_iso(),
            "asOf": as_of,
            "source": "felicitas-tisztsegviselok-api",
            "count": len(records),
            "terms": terms,
            "rows": len(rows),
            "skippedRows": dropped,
            "categories": by_category,
        },
        "data": records,
    }


def save_office_holders(paths: Paths, registry: dict) -> None:
    out = paths.officeholders_file()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(registry, indent=2, ensure_ascii=False))
    tmp.replace(out)
    logger.info("Wrote %s (%d people, %d office terms)", out,
                registry["meta"]["count"], registry["meta"]["terms"])
