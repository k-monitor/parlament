"""Votes module API (§7 / EXT-1) — /api/v1/votes.

A self-contained slice over the `vote` / `vote_subject` / `vote_record` /
`vote_faction_stat` tables. Every cross-link goes through the shared core
entities (EXT-2): a vote subject links to a held `bill`, each per-MP roll-call
record links to that representative's `person` profile, and each faction
breakdown row to the shared `faction`. A vote's id is the upstream szavazasId,
the same key a bill's vote tally references, so the bill ↔ vote link is free.
The list is filterable and paginated.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ...db import get_db, like_contains
from ...parlament_links import vote_page_url

router = APIRouter(prefix="/votes", tags=["votes"])

# Whitelisted orderings. The ORDER BY is spliced from this map only, never from
# raw request input (cf. the proceedings search sort, SEA-10).
_VOTE_SORTS = {
    "date_desc": "v.vote_datetime DESC",
    "date_asc": "v.vote_datetime ASC",
}
_DEFAULT_SORT = "date_desc"


def _subjects_for(db: sqlite3.Connection, vote_ids: list[str]) -> dict[str, list]:
    """Vote subjects (the bills/motions decided) grouped by vote id."""
    if not vote_ids:
        return {}
    ph = ",".join("?" * len(vote_ids))
    rows = db.execute(
        f"""SELECT vs.vote_id, b.id AS bill_id, vs.bill_number, vs.title
            FROM vote_subject vs LEFT JOIN bill b ON b.id = vs.iromany_id
            WHERE vs.vote_id IN ({ph})
            ORDER BY vs.vote_id, vs.ord""", vote_ids).fetchall()
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r["vote_id"], []).append({
            "bill_id": r["bill_id"], "bill_number": r["bill_number"],
            "title": r["title"],
        })
    return out


def _vote_brief(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"], "vote_datetime": r["vote_datetime"],
        "voting_mode": r["voting_mode"], "subject": r["subject"],
        "result": r["result"], "yes": r["yes"], "no": r["no"],
        "abstain": r["abstain"], "has_per_mp": bool(r["has_per_mp"]),
        "period_number": r["period_number"],
    }


@router.get("")
def list_votes(
    q: Optional[str] = None,
    period: Optional[int] = None,
    result: Optional[str] = None,
    voting_mode: Optional[str] = None,      # szavazasiMod ("type" of vote)
    date_from: Optional[str] = None,        # ISO date lower bound (inclusive)
    date_to: Optional[str] = None,          # ISO date upper bound (inclusive)
    bill: Optional[str] = None,             # bill id — votes deciding this bill
    sort: str = "date_desc",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
):
    """Browsable, filterable vote list. Filters combine."""
    where = ["1=1"]
    params: dict = {}
    if q:
        where.append("(fold(v.subject) LIKE fold(:q) ESCAPE '\\' "
                     "OR EXISTS (SELECT 1 FROM vote_subject vs "
                     "WHERE vs.vote_id=v.id AND (fold(vs.bill_number) LIKE fold(:q) ESCAPE '\\' "
                     "OR fold(vs.title) LIKE fold(:q) ESCAPE '\\')))")
        params["q"] = like_contains(q.strip())
    if period is not None:
        where.append("v.period_number = :per"); params["per"] = period
    if result:
        where.append("v.result = :res"); params["res"] = result
    if voting_mode:
        where.append("v.voting_mode = :vmode"); params["vmode"] = voting_mode
    # vote_datetime is a full ISO timestamp ("…T10:00:51Z"); compare on the date
    # part so an upper bound is inclusive of the whole day.
    if date_from:
        where.append("substr(v.vote_datetime, 1, 10) >= :date_from")
        params["date_from"] = date_from
    if date_to:
        where.append("substr(v.vote_datetime, 1, 10) <= :date_to")
        params["date_to"] = date_to
    if bill:
        where.append("EXISTS (SELECT 1 FROM vote_subject vs WHERE vs.vote_id=v.id "
                     "AND vs.iromany_id=:bill)"); params["bill"] = bill
    where_sql = " AND ".join(where)

    order = _VOTE_SORTS.get(sort, _VOTE_SORTS[_DEFAULT_SORT])
    total = db.execute(f"SELECT COUNT(*) AS c FROM vote v WHERE {where_sql}",
                       params).fetchone()["c"]
    rows = db.execute(
        f"""SELECT * FROM vote v WHERE {where_sql}
            ORDER BY {order} LIMIT :limit OFFSET :offset""",
        {**params, "limit": limit, "offset": offset}).fetchall()
    subjects = _subjects_for(db, [r["id"] for r in rows])
    return {
        "total": total, "limit": limit, "offset": offset,
        "sort": sort if sort in _VOTE_SORTS else _DEFAULT_SORT,
        "votes": [{**_vote_brief(r), "subjects": subjects.get(r["id"], [])}
                  for r in rows],
    }


@router.get("/facets")
def vote_facets(period: Optional[int] = None,
                db: sqlite3.Connection = Depends(get_db)):
    """Distinct results and voting modes for the filter controls (within a period)."""
    where, par = ("WHERE period_number = ?", (period,)) if period is not None else ("", ())
    kw = "AND" if where else "WHERE"
    results = [r["result"] for r in db.execute(
        f"SELECT DISTINCT result FROM vote {where} "
        f"{kw} result IS NOT NULL ORDER BY result", par)]
    voting_modes = [r["voting_mode"] for r in db.execute(
        f"SELECT DISTINCT voting_mode FROM vote {where} "
        f"{kw} voting_mode IS NOT NULL ORDER BY voting_mode", par)]
    return {"results": results, "voting_modes": voting_modes}


@router.get("/{vote_id}")
def get_vote(vote_id: str, db: sqlite3.Connection = Depends(get_db)):
    """A single vote with its subjects (bill links), per-faction breakdown and
    the full per-MP roll call (each MP linked to their profile, EXT-2)."""
    v = db.execute("SELECT * FROM vote WHERE id = ?", (vote_id,)).fetchone()
    if not v:
        raise HTTPException(404, "Vote not found")

    subjects = _subjects_for(db, [vote_id]).get(vote_id, [])

    faction_stats = [dict(r) for r in db.execute(
        """SELECT fs.faction_name, fs.total, fs.yes, fs.no, fs.abstain,
                  fs.absent, fs.not_voting, fs.against_faction,
                  f.id AS faction_id, f.color AS faction_color
           FROM vote_faction_stat fs LEFT JOIN faction f ON f.id = fs.faction_id
           WHERE fs.vote_id = ? ORDER BY fs.ord""", (vote_id,)).fetchall()]

    # The per-MP roll call, each record linked to a profile where the MP is known.
    records = [dict(r) for r in db.execute(
        """SELECT vr.person_id, vr.name, vr.faction_name, vr.value, vr.value_code,
                  p.label AS person_name,
                  f.id AS faction_id, f.color AS faction_color
           FROM vote_record vr
           LEFT JOIN person p ON p.person_id = vr.person_id
           LEFT JOIN faction f ON f.label = vr.faction_name
           WHERE vr.vote_id = ?
           ORDER BY vr.faction_name, vr.name""", (vote_id,)).fetchall()]
    for r in records:
        r["name"] = r["person_name"] or r["name"]
        r.pop("person_name", None)

    # Tally the roll call by normalized code (for the summary + a11y).
    tally: dict[str, int] = {}
    for r in records:
        tally[r["value_code"]] = tally.get(r["value_code"], 0) + 1

    return {
        **_vote_brief(v), "total_votes": v["total_votes"], "remark": v["remark"],
        "source_url": vote_page_url(v["id"]),      # deep link to the parlament.hu adatlap
        "subjects": subjects,
        "faction_stats": faction_stats,
        "records": records,
        "tally": tally,
    }
