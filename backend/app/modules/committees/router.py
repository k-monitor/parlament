"""Committees module API (§6F / EXT-1) — /api/v1/committees.

A self-contained slice over the `committee` / `committee_member` /
`committee_term` / `committee_meeting` / `committee_document` tables, which the
loader fills from parlament.hu's own committee registry. Every cross-link goes
through the shared core entities (EXT-2): a seat's holder is a `person`, the
faction they hold it for a `faction`, and an iromány is resolved to a held `bill`
**at query time** — by the shared upstream id, never a hard FK — so re-ingesting
bills cannot break committees (EXT-1), exactly as `vote_subject` does.

The one thing worth knowing about the data model: a committee's membership comes
from two sources that answer different questions. `committee_member` is the
roster *as it stands* and `committee_term` the dated record of seats that began
or ended during the cycle. For a closed cycle the second is complete and the
first is its final state; for the running cycle it is the other way round. So
"the current roster" reads the first, "was this person ever on it" the union —
which is why the per-person endpoint below merges them and the committee sheet
serves them as separate blocks rather than pretending they are one list.
"""

from __future__ import annotations

import json
import sqlite3
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ...analytics import search_analytics
from ...db import (get_db, like_contains, period_in_scope, period_key,
                   period_list, period_sql)
from ...query_cache import cached_aggregate

router = APIRouter(prefix="/committees", tags=["committees"])

# Seniority order for a roster. Spliced into ORDER BY from this map only, never
# from request input. `other` sorts last so an unrecognised upstream title still
# renders, at the bottom, rather than being dropped (SCR-5).
_ROLE_RANK = ("chair", "deputy-chair", "member", "other")
_ROLE_ORDER_SQL = "CASE m.role " + " ".join(
    f"WHEN '{r}' THEN {i}" for i, r in enumerate(_ROLE_RANK)) + " ELSE 9 END"

# Whitelisted list orderings (cf. the votes list, SEA-10). Every one ends in a
# stable tiebreak so paging cannot repeat or skip a row: `ord` is upstream's own
# display order and unique within a cycle's level, and the id closes it.
_SORTS = {
    "official": "c.period_number DESC, c.ord, c.id",
    "name": "c.name COLLATE NOCASE, c.id",
    "meetings": "COALESCE(c.meetings, 0) DESC, c.ord, c.id",
    "duration": "COALESCE(c.total_minutes, 0) DESC, c.ord, c.id",
    "members": "member_count DESC, c.ord, c.id",
}

_KINDS = ("állandó", "eseti", "vizsgáló", "nemzetiségi", "törvényalkotási")

# End-of-term "reasons" that say nothing the end date does not already say: the
# seat ended because the Assembly's term did. They are two thirds of every
# recorded reason (1 216 of ~1 800 rows), so printed they would drown the ones
# that actually distinguish a departure — recalled by the faction, appointed
# state secretary, resigned, died. The date still shows; only the empty
# restatement is dropped.
_END_OF_TERM_REASONS = frozenset({
    "az országgyűlés megbízatása megszűnt",
    "ciklus vége",
})


def _reason(value: str | None) -> str | None:
    """A term's end reason, or ``None`` where it only restates the end date."""
    if not value or value.strip().lower() in _END_OF_TERM_REASONS:
        return None
    return value


def _table_exists(db: sqlite3.Connection, name: str) -> bool:
    return bool(db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone())


def _tables_present(db: sqlite3.Connection) -> bool:
    """Whether the loader has built the §6F tables. A DB loaded before this
    module existed simply has no committees — the endpoints answer empty (and
    the frontend hides the tab) rather than erroring (EXT-6, SCR-5)."""
    return _table_exists(db, "committee")


def _require_tables(db: sqlite3.Connection) -> None:
    if not _tables_present(db):
        raise HTTPException(
            status_code=503,
            detail="Committee tables not built yet; run the scraper's "
                   "`committees` command and reload the database.")


def _committee_row(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "name": r["name"],
        "period": r["period_number"],
        "parentId": r["parent_id"],
        "parentName": r["parent_name"] if "parent_name" in r.keys() else None,
        "kind": r["kind"],
        "standingCode": r["standing_code"],
        "isSubcommittee": r["parent_id"] is not None,
        "email": r["email"],
        "siteUrl": r["site_url"],
        "dateStart": r["date_start"],
        "dateEnd": r["date_end"],
        # Upstream's own meeting aggregates. `meetings` counts sittings the
        # per-meeting listing does not always include, so it can exceed the
        # number of rows the detail sheet shows; both are reported.
        "meetings": r["meetings"],
        "totalMinutes": r["total_minutes"],
        "quorate": r["quorate"],
        "inquorate": r["inquorate"],
    }


@router.get("")
def list_committees(
    db: sqlite3.Connection = Depends(get_db),
    period: Optional[List[int]] = Query(None),
    q: Optional[str] = Query(None, max_length=200),
    kind: Optional[str] = Query(None),
    include_subcommittees: bool = Query(False),
    sort: str = Query("official"),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """The cycle scope's committees, with each one's seat count.

    Subcommittees are **off by default**: they are a level down (a body of three
    inside a body of fifteen), and mixed into one flat list they treble its
    length while burying the committees a reader came for. Asked for, they come
    back as ordinary rows carrying `parentId`, so the page can nest them under
    the committee they belong to.
    """
    if not _tables_present(db):
        return {"total": 0, "items": [], "kinds": [], "tablesReady": False}
    if sort not in _SORTS:
        raise HTTPException(400, f"Unknown sort '{sort}'")

    where = ["1=1"]
    params: dict = {}
    scope = period_sql(period, "c.period_number")
    if scope:
        where.append(scope)
    if not include_subcommittees:
        where.append("c.parent_id IS NULL")
    if kind:
        if kind not in _KINDS:
            raise HTTPException(400, f"Unknown kind '{kind}'")
        where.append("c.kind = :kind")
        params["kind"] = kind
    if q and q.strip():
        where.append("c.name LIKE :q ESCAPE '\\'")
        params["q"] = like_contains(q.strip())
    cond = " AND ".join(where)

    total = db.execute(
        f"SELECT COUNT(*) AS n FROM committee c WHERE {cond}", params)\
        .fetchone()["n"]

    # Privacy-respecting analytics (PRIV-2): the typed keyword and the filters
    # it was combined with. A no-keyword browse records nothing.
    search_analytics.record(
        source="committees", query=q, period=period, sort=sort, kind=kind,
        include_subcommittees=include_subcommittees,
        results=total, offset=offset)

    rows = db.execute(f"""
        SELECT c.*, p.name AS parent_name,
               (SELECT COUNT(*) FROM committee_member m
                 WHERE m.committee_id = c.id) AS member_count
        FROM committee c
        LEFT JOIN committee p ON p.id = c.parent_id
        WHERE {cond}
        ORDER BY {_SORTS[sort]}
        LIMIT :limit OFFSET :offset
    """, {**params, "limit": limit, "offset": offset}).fetchall()

    items = [dict(_committee_row(r), memberCount=r["member_count"], chairs=[])
             for r in rows]
    # The chairs of the whole page in one query rather than one per row: this
    # list runs to 500 bodies at the width the list view asks for, and a
    # per-row lookup would be 500 round trips for a name apiece.
    by_id = {it["id"]: it for it in items}
    if by_id:
        marks = ",".join("?" * len(by_id))
        for m in db.execute(
                f"""SELECT committee_id, person_id, name, faction_name
                      FROM committee_member
                     WHERE role = 'chair' AND committee_id IN ({marks})
                     ORDER BY ord""", tuple(by_id)):
            by_id[m["committee_id"]]["chairs"].append(
                {"personId": m["person_id"], "name": m["name"],
                 "faction": m["faction_name"]})

    return {"total": total, "items": items, "kinds": _kinds_in_scope(db, period),
            "tablesReady": True}


def _kinds_in_scope(db: sqlite3.Connection,
                    period: Optional[List[int]]) -> list[dict]:
    """The committee kinds actually present in the scope, with their counts —
    the filter chips. Built from the data rather than from `_KINDS` so a scope
    is never offered a filter that would return nothing."""
    def _build():
        scope = period_sql(period, "period_number")
        rows = db.execute(
            "SELECT kind, COUNT(*) AS n FROM committee "
            "WHERE parent_id IS NULL AND kind IS NOT NULL"
            f"{' AND ' + scope if scope else ''} GROUP BY kind")
        counts = {r["kind"]: r["n"] for r in rows}
        return [{"kind": k, "count": counts[k]} for k in _KINDS if k in counts]
    return cached_aggregate("committee-kinds", period_key(period), _build)


@router.get("/upcoming")
def upcoming_meetings(
    db: sqlite3.Connection = Depends(get_db),
    period: Optional[List[int]] = Query(None),
    limit: int = Query(30, ge=1, le=200),
):
    """The committee meetings that are **scheduled but have not happened** —
    the committee-side counterpart of the plenary's order paper (NR-5).

    Declared before `/{committee_id}` so "upcoming" is never taken for a
    committee id. Rows already in the past are dropped rather than trusted:
    the source states the schedule, and a meeting it forgot to clear would
    otherwise sit at the top of the list for ever. A cancelled sitting is kept
    and flagged — that a committee called a meeting off is worth seeing.
    """
    if not _tables_present(db) or not _table_exists(db, "committee_upcoming"):
        return {"items": []}
    scope = period_sql(period, "u.period_number")
    rows = db.execute(f"""
        SELECT u.*, c.id AS held FROM committee_upcoming u
        LEFT JOIN committee c ON c.id = u.committee_id
        WHERE u.starts_on >= date('now', '-1 day')
              {' AND ' + scope if scope else ''}
        -- The time is stored as published ("9:00", not "09:00"), so ordering
        -- on it as a string puts the 9am sitting after the half-twelve one.
        -- Left-padding to five characters sorts it as a clock reads it without
        -- rewriting what the House published.
        ORDER BY u.starts_on, substr('0' || u.starts_at, -5), u.committee_name
        LIMIT :limit
    """, {"limit": limit}).fetchall()
    return {"items": [{
        "id": r["id"],
        # Linked only where we hold the body: an eseti bizottság can be
        # scheduled before it is constituted (SCR-5).
        "committeeId": r["held"],
        "name": r["committee_name"],
        "date": r["starts_on"], "time": r["starts_at"], "venue": r["venue"],
        "cancelled": bool(r["cancelled"]),
    } for r in rows]}


@router.get("/{committee_id}")
def get_committee(
    committee_id: str,
    db: sqlite3.Connection = Depends(get_db),
):
    """One committee's sheet: its roster, its subcommittees, and the counts the
    page's sections are worth opening for.

    Addressed by id and so carrying no `period` param, it is clamped against the
    served cycle window like every other detail page (CYC-7)."""
    _require_tables(db)
    r = db.execute("""
        SELECT c.*, p.name AS parent_name FROM committee c
        LEFT JOIN committee p ON p.id = c.parent_id WHERE c.id = ?
    """, (committee_id,)).fetchone()
    if not r or not period_in_scope(r["period_number"]):
        raise HTTPException(404, "Committee not found")

    out = _committee_row(r)
    out["members"] = [_member(m) for m in db.execute(f"""
        SELECT * FROM committee_member m WHERE m.committee_id = ?
        ORDER BY {_ROLE_ORDER_SQL}, m.ord, m.name COLLATE NOCASE
    """, (committee_id,))]
    out["subcommittees"] = [{
        "id": s["id"], "name": s["name"], "dateStart": s["date_start"],
        "dateEnd": s["date_end"], "memberCount": s["n"],
    } for s in db.execute("""
        SELECT c.id, c.name, c.date_start, c.date_end,
               (SELECT COUNT(*) FROM committee_member m
                 WHERE m.committee_id = c.id) AS n
        FROM committee c WHERE c.parent_id = ? ORDER BY c.ord, c.name
    """, (committee_id,))]
    # The former members: a term that has ended and whose holder is not on the
    # current roster. Shown as its own block rather than mixed into the roster —
    # "who sits on this" and "who used to" are different questions, and merging
    # them would make a committee of nine look like a committee of thirty.
    # Both kinds of term count here. Someone who only ever appears as an office
    # term — the registry records a departing chair that way often enough — left
    # the committee just as surely as someone with a `membership` row, and
    # filtering to memberships dropped them from this page while their own
    # profile went on reporting the seat. One row per person, latest departure
    # winning, which is also the order the block reads in.
    former: dict = {}
    for t in db.execute("""
        SELECT t.* FROM committee_term t
        WHERE t.committee_id = ? AND t.date_end IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM committee_member m
                           WHERE m.committee_id = t.committee_id
                             AND ((m.person_id IS NOT NULL
                                   AND m.person_id = t.person_id)
                                  OR m.name = t.name))
        ORDER BY t.date_end, t.name COLLATE NOCASE
    """, (committee_id,)):
        former[t["person_id"] or t["name"]] = {
            "personId": t["person_id"], "name": t["name"],
            "faction": t["faction_name"], "role": t["role"],
            "roleLabel": t["role_label"], "dateStart": t["date_start"],
            "dateEnd": t["date_end"], "reason": _reason(t["reason"]),
            "replacing": t["replacing"],
        }
    out["formerMembers"] = sorted(
        former.values(), key=lambda f: (f["dateEnd"] or "", f["name"] or ""),
        reverse=True)

    counts = db.execute("""
        SELECT (SELECT COUNT(*) FROM committee_meeting WHERE committee_id = :c)
                   AS meetings,
               (SELECT COUNT(*) FROM committee_meeting
                 WHERE committee_id = :c AND minutes_url IS NOT NULL) AS minutes,
               (SELECT COUNT(*) FROM committee_document
                 WHERE committee_id = :c AND role = 'discussed') AS discussed,
               (SELECT COUNT(*) FROM committee_document
                 WHERE committee_id = :c AND role IN ('own','motion')) AS tabled
    """, {"c": committee_id}).fetchone()
    out["counts"] = dict(counts)
    return out


def _outlasts(row: sqlite3.Row, seat: dict) -> bool:
    """Whether ``row``'s term describes the seat better than what it already
    holds: it ran later (an open term runs latest of all), or ended at the same
    moment and is the more senior of the two."""
    theirs, ours = row["date_end"], seat["dateEnd"]
    if theirs is None or ours is None:
        # An open term outlasts a closed one; two open ones fall back to rank.
        return ours is not None or _seniority(row["role"]) < _seniority(seat["role"])
    if theirs != ours:
        return theirs > ours
    return _seniority(row["role"]) < _seniority(seat["role"])


def _seniority(role: str | None) -> int:
    """Rank of a committee role, most senior first; an unrecognised one sorts
    last rather than being dropped (SCR-5)."""
    return _ROLE_RANK.index(role) if role in _ROLE_RANK else len(_ROLE_RANK)


def _member(m: sqlite3.Row) -> dict:
    return {
        "personId": m["person_id"], "name": m["name"], "role": m["role"],
        "roleLabel": m["role_label"], "faction": m["faction_name"],
        "factionId": m["faction_id"], "governing": bool(m["governing"]),
    }


@router.get("/{committee_id}/meetings")
def committee_meetings(
    committee_id: str,
    db: sqlite3.Connection = Depends(get_db),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """The committee's meetings, newest first.

    A meeting with no published minutes keeps its row and `minutesUrl` is null:
    that a committee met and left no record is itself worth seeing, and hiding
    such rows would make the published minutes look like the full history."""
    _require_tables(db)
    _check_committee(db, committee_id)
    total = db.execute("SELECT COUNT(*) AS n FROM committee_meeting "
                       "WHERE committee_id = ?", (committee_id,)).fetchone()["n"]
    rows = db.execute("""
        SELECT * FROM committee_meeting WHERE committee_id = ?
        -- `id` closes every ordering here, as it does in `_SORTS`: two meetings
        -- can share a date (a committee sitting twice in a morning) and paging
        -- over an unstable order repeats or skips rows.
        ORDER BY held_at DESC, number DESC, id LIMIT ? OFFSET ?
    """, (committee_id, limit, offset))
    return {"total": total, "items": [{
        "id": r["id"], "number": r["number"], "numberInYear": r["number_in_year"],
        "heldAt": r["held_at"], "kind": r["kind"], "quorum": r["quorum"],
        "durationS": r["duration_s"], "minutesUrl": r["minutes_url"],
    } for r in rows]}


@router.get("/{committee_id}/documents")
def committee_documents(
    committee_id: str,
    db: sqlite3.Connection = Depends(get_db),
    role: str = Query("discussed", pattern="^(discussed|tabled)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """The irományok the committee dealt with (`discussed`) or tabled itself
    (`tabled` — its own motions and the reports it filed on other bills).

    Each row's `billId` is the upstream iromány id; `held` says whether this
    deployment actually holds that document, so the UI links only where the link
    resolves and shows a plain label otherwise (SCR-5)."""
    _require_tables(db)
    _check_committee(db, committee_id)
    roles = ("discussed",) if role == "discussed" else ("own", "motion")
    marks = ",".join("?" * len(roles))
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM committee_document "
        f"WHERE committee_id = ? AND role IN ({marks})",
        (committee_id, *roles)).fetchone()["n"]
    rows = db.execute(f"""
        SELECT d.*, b.id AS held FROM committee_document d
        LEFT JOIN bill b ON b.id = d.bill_id
        WHERE d.committee_id = ? AND d.role IN ({marks})
        ORDER BY COALESCE(d.referred_at, '') DESC, d.id
        LIMIT ? OFFSET ?
    """, (committee_id, *roles, limit, offset))
    return {"total": total, "items": [{
        "role": r["role"], "billId": r["bill_id"], "billNumber": r["bill_number"],
        "title": r["title"], "docType": r["doc_type"], "status": r["status"],
        "referredAt": r["referred_at"], "textUrl": r["text_url"],
        "held": r["held"] is not None,
        "sponsors": json.loads(r["sponsors"]) if r["sponsors"] else [],
    } for r in rows]}


def _check_committee(db: sqlite3.Connection, committee_id: str) -> None:
    r = db.execute("SELECT period_number FROM committee WHERE id = ?",
                   (committee_id,)).fetchone()
    if not r or not period_in_scope(r["period_number"]):
        raise HTTPException(404, "Committee not found")


@router.get("/representative/{person_id}")
def representative_committees(
    person_id: str,
    db: sqlite3.Connection = Depends(get_db),
    period: Optional[List[int]] = Query(None),
):
    """One person's committee seats — the block on their profile.

    The union of the two membership sources: a seat they hold now
    (`committee_member`) and every dated term the registry recorded
    (`committee_term`). A seat present in both is reported once, as current,
    keeping the term's dates. Ordered current-first, then most recently ended.

    **One row per (person, committee)**, not per role. Sitting on a committee
    and chairing it are one seat described two ways, and upstream records them
    as two terms — a `membership` span (which carries no title, so it reads as
    plain "member") and an `office` span. Keyed by role they would come back as
    two seats on the same body, one of them a past "member" the person never
    stopped being. So the spans are merged into the one the seat actually ran
    for, and the role is **the one the seat ended in**, not the most senior it
    ever carried: an MP who gave up a chairmanship and stayed on as a member
    left as a member, and calling that seat "elnök" for its whole span would
    assert an office they resigned.
    """
    if not _tables_present(db):
        return {"items": [], "coverage": [], "covers_scope": False}
    scope = period_sql(period, "c.period_number")
    cond = (" AND " + scope) if scope else ""

    seats: dict[tuple, dict] = {}

    # `c.name` is aliased throughout: both `committee_member` and
    # `committee_term` have a `name` of their own (the person's), and an
    # unaliased `c.name` in the same row would be shadowed by it.
    #
    # A roster seat is only **current** while the committee itself still is.
    # The roster is a snapshot, and for a closed cycle that snapshot is its
    # state on the last day of the term — so every seat of every archive cycle
    # would otherwise be reported as still held, which is how a 2018 seat comes
    # to read "current". `committee.date_end` is the right test and not merely a
    # cycle comparison: a committee dissolved mid-term ended its seats then too.
    for r in db.execute(f"""
        SELECT m.*, c.name AS committee_name, c.period_number, c.parent_id,
               c.kind, c.date_end AS committee_end, p.name AS parent_name
        FROM committee_member m
        JOIN committee c ON c.id = m.committee_id
        LEFT JOIN committee p ON p.id = c.parent_id
        WHERE m.person_id = ?{cond}
    """, (person_id,)):
        running = r["committee_end"] is None
        seats[r["committee_id"]] = {
            "committeeId": r["committee_id"], "name": r["committee_name"],
            "period": r["period_number"], "parentName": r["parent_name"],
            "kind": r["kind"], "role": r["role"], "roleLabel": r["role_label"],
            "faction": r["faction_name"], "current": running,
            "dateStart": None,
            # The seat ran to the end of the body it was on; the registry does
            # not repeat that date on every seat, so it is filled in here rather
            # than left blank next to a term that reads as open.
            "dateEnd": None if running else r["committee_end"],
            "reason": None,
        }

    for r in db.execute(f"""
        SELECT t.*, c.name AS committee_name, c.period_number, c.parent_id,
               c.kind, p.name AS parent_name
        FROM committee_term t
        JOIN committee c ON c.id = t.committee_id
        LEFT JOIN committee p ON p.id = c.parent_id
        WHERE t.person_id = ?{cond}
        ORDER BY COALESCE(t.date_start, '')
    """, (person_id,)):
        key = r["committee_id"]
        seat = seats.get(key)
        if seat is None:
            seat = seats[key] = {
                "committeeId": r["committee_id"], "name": r["committee_name"],
                "period": r["period_number"], "parentName": r["parent_name"],
                "kind": r["kind"], "role": r["role"],
                "roleLabel": r["role_label"], "faction": r["faction_name"],
                "current": False, "dateStart": None, "dateEnd": None,
                "reason": None,
            }
        elif not seat["current"] and _outlasts(r, seat):
            # A chair's own `membership` term says only "member", so the office
            # term is usually the one that describes the seat — but only while
            # it is the one that ran longest. A resigned chairmanship ends
            # before the membership that outlived it, and then "member" is the
            # truthful label. A roster seat is never relabelled: it is today's
            # own answer, and these terms are its history.
            seat["role"], seat["roleLabel"] = r["role"], r["role_label"]
        # The dates the roster snapshot cannot carry. Widest span wins, so a
        # seat recorded as several consecutive terms reads as one.
        if r["date_start"] and (not seat["dateStart"]
                                or r["date_start"] < seat["dateStart"]):
            seat["dateStart"] = r["date_start"]
        if not seat["current"]:
            # The latest end wins, so a seat recorded as several consecutive
            # terms reads as one span ending when the last of them did.
            if r["date_end"] and (not seat["dateEnd"]
                                  or r["date_end"] > seat["dateEnd"]):
                seat["dateEnd"] = r["date_end"]
                seat["reason"] = _reason(r["reason"])

    def _rank(seat: dict) -> tuple:
        return (-(seat["period"] or 0), _seniority(seat["role"]),
                seat["name"] or "")

    current = sorted((s for s in seats.values() if s["current"]), key=_rank)
    # Past seats most recently ended first, so a profile opens on what the
    # person did last rather than on what they did in 2014.
    past = sorted((s for s in seats.values() if not s["current"]),
                  key=lambda s: (s["dateEnd"] or "", ), reverse=True)

    # Which cycles this module actually holds committees for, and whether they
    # cover the scope asked about. The scraper is run per cycle, so a
    # deployment can hold 40–43 while the corpus goes back to 34 — and a
    # profile that swapped its career-wide biography list for this one would
    # then silently lose every seat before 2014. The caller needs to know that,
    # and cannot tell from an answer that merely looks complete.
    coverage = _coverage(db)
    asked = period_list(period) or sorted(
        r["p"] for r in db.execute(
            "SELECT DISTINCT period_number AS p FROM membership "
            "WHERE period_number IS NOT NULL") if r["p"] is not None)
    return {"items": current + past, "coverage": coverage,
            "covers_scope": bool(coverage) and all(p in coverage for p in asked)}


def _coverage(db: sqlite3.Connection) -> list[int]:
    """The cycles the committee registry has been scraped for."""
    return cached_aggregate("committee-coverage", (), lambda: sorted(
        r["p"] for r in db.execute(
            "SELECT DISTINCT period_number AS p FROM committee "
            "WHERE period_number IS NOT NULL") if r["p"] is not None))
