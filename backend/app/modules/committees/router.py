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


@router.get("/meetings")
def list_meetings(
    db: sqlite3.Connection = Depends(get_db),
    period: Optional[List[int]] = Query(None),
    q: Optional[str] = Query(None, max_length=200),
    committee: Optional[str] = Query(None),
    readable: bool = Query(False),
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """**Every committee sitting in the cycle scope**, newest first — the
    committee-side counterpart of `/proceedings/sessions` (§6F / BIZ-24).

    The plenary's sitting days have had a list of their own since v1; committee
    sittings only ever existed *inside* one committee's page, which answers
    "when did this body meet" and never "what met this week". They are the same
    kind of object — a dated sitting with a record — and the sittings page now
    carries both.

    Rows are shaped like the plenary list's so one page can render either:
    a date, a number, and what is available for it. Subcommittee sittings are
    included (they are sittings), but each row names its body, so a reader can
    tell a subcommittee of three from the committee it hangs off.

    `readable` narrows to the sittings whose minutes this deployment has
    actually read — the ones with something to open.
    """
    if not _tables_present(db):
        return {"total": 0, "limit": limit, "offset": offset, "meetings": [],
                "tablesReady": False}
    where = ["1=1"]
    params: dict = {}
    scope = period_sql(period, "m.period_number")
    if scope:
        where.append(scope)
    if committee:
        where.append("m.committee_id = :committee")
        params["committee"] = committee
    if q and q.strip():
        where.append("c.name LIKE :q ESCAPE '\\'")
        params["q"] = like_contains(q.strip())
    has_minutes = _table_exists(db, "committee_minutes")
    if readable and has_minutes:
        where.append("cm.speeches > 0")
    cond = " AND ".join(where)
    join = (" LEFT JOIN committee_minutes cm ON cm.meeting_id = m.id"
            if has_minutes else "")
    cols = ", cm.speeches AS n_speeches" if has_minutes else ""

    total = db.execute(
        f"""SELECT COUNT(*) AS n FROM committee_meeting m
              JOIN committee c ON c.id = m.committee_id{join}
             WHERE {cond}""", params).fetchone()["n"]

    search_analytics.record(source="committee-meetings", query=q, period=period,
                            readable=readable, results=total, offset=offset)

    rows = db.execute(f"""
        SELECT m.*, c.name AS committee_name, c.parent_id{cols}
          FROM committee_meeting m
          JOIN committee c ON c.id = m.committee_id{join}
         WHERE {cond}
         -- `id` closes the ordering, as everywhere in this module: two bodies
         -- routinely sit on the same day and paging over an unstable order
         -- repeats or skips rows.
         ORDER BY m.held_at DESC, c.name COLLATE NOCASE, m.id
         LIMIT :limit OFFSET :offset
    """, {**params, "limit": limit, "offset": offset}).fetchall()

    videos: dict[str, list] = {}
    if _table_exists(db, "committee_video") and rows:
        marks = ",".join("?" * len(rows))
        for v in db.execute(
                f"""SELECT meeting_id, video_id, url, title, thumbnail,
                           duration_s, continued
                      FROM committee_video
                     WHERE meeting_id IN ({marks})
                     ORDER BY continued, published_at""",
                tuple(r["id"] for r in rows)):
            videos.setdefault(v["meeting_id"], []).append(_video_row(v))

    return {
        "total": total, "limit": limit, "offset": offset, "tablesReady": True,
        "meetings": [{
            "id": r["id"], "committeeId": r["committee_id"],
            "committeeName": r["committee_name"],
            "isSubcommittee": r["parent_id"] is not None,
            "number": r["number"], "numberInYear": r["number_in_year"],
            "heldAt": r["held_at"], "kind": r["kind"], "quorum": r["quorum"],
            "durationS": r["duration_s"], "minutesUrl": r["minutes_url"],
            "speeches": (r["n_speeches"] if has_minutes else None),
            "videos": videos.get(r["id"], []),
        } for r in rows],
    }


@router.get("/meetings/{meeting_id}/minutes")
def meeting_minutes(
    meeting_id: str,
    db: sqlite3.Connection = Depends(get_db),
):
    """One sitting's jegyzőkönyv as the site reads it (§6F / BIZ-15).

    The whole document in one response — cover, agenda, attendance, and every
    speech under the heading it was made under. It is served whole rather than
    paged because it *is* one document: a sitting averages 43 kB of text and the
    longest in the corpus is under 250 kB, the reader arrives wanting to read it
    end to end, and paging a transcript breaks both in-page search and the
    ability to link a speech.

    Declared before `/{committee_id}` and its sub-paths, as `/upcoming` is, so
    the static `meetings` segment can never be read as a committee id.

    A sitting we hold **no record of but a recording of** is served too, from
    the meeting row alone (BIZ-27): same shape, every collection empty,
    `hasMinutes` false. The recording is up the same day and the jegyzőkönyv
    follows weeks later (BIZ-22), so on the sittings a reader is most likely to
    open it is the only thing there is — and there is no second page to put it
    on, the sitting being one object with two records.
    """
    _require_tables(db)
    has_minutes = _table_exists(db, "committee_minutes")
    row = db.execute("""
        SELECT cm.*, m.number, m.number_in_year, m.held_at, m.kind, m.quorum,
               m.duration_s, c.name AS committee_name, c.parent_id
        FROM committee_minutes cm
        JOIN committee_meeting m ON m.id = cm.meeting_id
        JOIN committee c ON c.id = cm.committee_id
        WHERE cm.meeting_id = ?
    """, (meeting_id,)).fetchone() if has_minutes else None
    if row is None:
        recorded = _recording_only_sitting(db, meeting_id)
        if recorded is not None:
            return recorded
        if not has_minutes:
            raise HTTPException(
                status_code=503,
                detail="Committee minutes are not built yet; run the scraper's "
                       "`committee-minutes` command and reload the database.")
    if not row or not period_in_scope(row["period_number"]):
        raise HTTPException(404, "Minutes not found")

    out = {
        "meetingId": row["meeting_id"],
        "committeeId": row["committee_id"],
        "committeeName": row["committee_name"],
        "period": row["period_number"],
        "number": row["number"], "numberInYear": row["number_in_year"],
        "heldAt": row["held_at"], "heldOn": row["held_on"],
        "weekday": row["weekday"], "meetingKind": row["kind"],
        "quorum": row["quorum"], "durationS": row["duration_s"],
        "venue": row["venue"], "openedAt": row["opened_at"],
        "closedAt": row["closed_at"], "closedSession": bool(row["closed_session"]),
        "registryNumber": row["registry_number"],
        "meetingLabel": row["meeting_label"], "termLabel": row["term_label"],
        "url": row["url"],
        "speeches": row["speeches"], "speakers": row["speakers"],
        "chars": row["chars"],
        # Whether this is the sitting's record or only its recording (BIZ-27).
        # The page is the same page either way, so it has to be told which.
        "hasMinutes": True,
        # Why the document could not be read, where it could not. The row exists
        # either way (BIZ-9): "we could not read this" and "there is nothing to
        # read" are different findings and the page says which.
        "error": row["error"],
    }
    out["agenda"] = [{
        "ordinal": a["ordinal"], "title": a["title"],
        "billNumber": a["bill_number"], "billId": a["bill_id"],
        # Linked only where this deployment holds the document, like every other
        # iromány reference in the module (BIZ-11 / SCR-5).
        "held": a["held"] is not None,
        "notes": json.loads(a["notes"]) if a["notes"] else [],
    } for a in db.execute("""
        SELECT i.*, b.id AS held FROM committee_minutes_item i
        LEFT JOIN bill b ON b.id = i.bill_id
        WHERE i.meeting_id = ? ORDER BY i.ord""", (meeting_id,))]

    people: dict[str, list] = {"chair": [], "present": [], "proxy": [],
                               "staff": [], "guest": []}
    for r in db.execute(
            "SELECT * FROM committee_minutes_person WHERE meeting_id = ? "
            "ORDER BY role, ord", (meeting_id,)):
        people.setdefault(r["role"], []).append({
            "personId": r["person_id"], "name": r["name"],
            "faction": r["faction_name"], "title": r["title"], "org": r["org"],
            "proxyPersonId": r["proxy_person_id"], "proxyName": r["proxy_name"],
        })
    out["participants"] = people

    out["sections"] = [{
        "ord": s["ord"], "title": s["title"], "level": s["level"],
        "preamble": s["preamble"], "speeches": s["speeches"],
    } for s in db.execute(
        "SELECT * FROM committee_minutes_section WHERE meeting_id = ? "
        "ORDER BY ord", (meeting_id,))]

    out["transcript"] = [{
        "ord": s["ord"], "section": s["section_ord"],
        "personId": s["person_id"], "name": s["name"],
        "faction": s["faction_name"], "role": s["role"], "org": s["org"],
        "chair": bool(s["chair"]),
        # The same speaker carrying on past an agenda heading without their name
        # being printed again. Flagged so the page can run the two together
        # rather than repeating the name as if someone had taken the floor.
        "continued": bool(s["continued"]),
        "text": s["text"],
    } for s in db.execute(
        "SELECT * FROM committee_speech WHERE meeting_id = ? ORDER BY ord",
        (meeting_id,))]

    out["videos"] = []
    if _table_exists(db, "committee_video"):
        out["videos"] = [_video_row(v) for v in db.execute(
            "SELECT * FROM committee_video WHERE meeting_id = ? "
            "ORDER BY continued, published_at", (meeting_id,))]

    # Who did the talking, so the page can open on the same question a sitting
    # day's does. Ranked by **characters spoken**, not by number of
    # contributions: the chair takes the floor between every speaker and would
    # otherwise top every sitting on a count of two-word interjections. There
    # are no durations to rank by — committee minutes carry no timings at all,
    # which is exactly why the plenary's "speaking time" toplist cannot be
    # reused here.
    out["topSpeakers"] = [{
        "personId": r["person_id"], "name": r["name"], "faction": r["faction"],
        # The portrait the shared `person` row already carries, so the block
        # reads like a sitting day's toplist rather than a bare list of names.
        "photoUri": r["photo_uri"],
        "chair": bool(r["chair"]), "speeches": r["n"], "chars": r["chars"],
        "words": r["words"],
    } for r in db.execute("""
        SELECT cs.person_id, cs.name, MAX(cs.faction_name) AS faction,
               MAX(cs.chair) AS chair, COUNT(*) AS n,
               SUM(LENGTH(COALESCE(cs.text, ''))) AS chars,
               -- Words, so the figure beside the bar is one a reader can weigh.
               -- Counted as separators + 1 per speech, which is exact for this
               -- text: the parser rejoins every speech on single spaces and
               -- separates its paragraphs with one blank line, so there are no
               -- runs to collapse.
               SUM(LENGTH(COALESCE(cs.text, '')) -
                   LENGTH(REPLACE(REPLACE(COALESCE(cs.text, ''), ' ', ''),
                                  char(10), '')) + 1) AS words,
               MAX(p.photo_uri) AS photo_uri
          FROM committee_speech cs
          LEFT JOIN person p ON p.person_id = cs.person_id
         WHERE cs.meeting_id = ? AND cs.name IS NOT NULL
         GROUP BY cs.person_id, cs.name
         -- Ranked by the same measure the page prints and sizes its bars by.
         -- Ordering on characters instead put a speaker with longer words above
         -- one who had said more, and the list then read as mis-sorted.
         ORDER BY words DESC, n DESC, cs.name COLLATE NOCASE
         LIMIT 15""", (meeting_id,))]

    # The same body's previous and next sitting, so a reader can walk a
    # committee's record the way they walk the plenary's sitting days. Scoped to
    # the committee rather than to the date: "the next committee sitting" across
    # all bodies is a different question, and the list page answers it.
    out["neighbours"] = {
        "prev": _adjacent(db, row["committee_id"], row["held_at"], "<", "DESC"),
        "next": _adjacent(db, row["committee_id"], row["held_at"], ">", "ASC"),
    }
    return out


@router.get("/meetings/{meeting_id}/agenda")
def meeting_agenda(
    meeting_id: str,
    db: sqlite3.Connection = Depends(get_db),
    limit: int = Query(6, ge=1, le=50),
):
    """**What one sitting was about**, in a payload small enough to hover over
    (BIZ-4b) — the agenda headings alone, without the transcript hanging off
    them.

    The same rows `/minutes` serves under `agenda`, but that response is the
    whole document (43 kB on average, 250 kB at worst) and a list page that
    previewed its rows through it would download a cycle of transcripts to show
    a dozen titles. Hence a route of its own, paged by a `limit` the caller sets
    to however many it means to show.

    `total` is the count the limit cut from, so a preview can say that a sitting
    had more points than it is showing rather than implying the list is the
    whole agenda.

    A sitting we hold no *read* record of has no agenda to serve and comes back
    empty — that is an answer, not an error (BIZ-9). Only a meeting this
    deployment does not have, or one outside the served cycle window (CYC-7),
    is a 404.
    """
    _require_tables(db)
    m = db.execute("SELECT period_number FROM committee_meeting WHERE id = ?",
                   (meeting_id,)).fetchone()
    if m is None or not period_in_scope(m["period_number"]):
        raise HTTPException(404, "Meeting not found")
    if not _table_exists(db, "committee_minutes_item"):
        return {"meetingId": meeting_id, "total": 0, "items": []}
    total = db.execute("SELECT COUNT(*) AS n FROM committee_minutes_item "
                       "WHERE meeting_id = ?", (meeting_id,)).fetchone()["n"]
    return {
        "meetingId": meeting_id,
        "total": total,
        # `ord` is the order the points stood in the document; `ordinal` is the
        # number the document gave them, which repeats across sittings and is
        # missing from the unnumbered ones ("Egyebek"), so it labels a row but
        # never orders one.
        "items": [{
            "ordinal": i["ordinal"], "title": i["title"],
            "billNumber": i["bill_number"],
        } for i in db.execute(
            """SELECT ordinal, title, bill_number
                 FROM committee_minutes_item
                WHERE meeting_id = ?
                ORDER BY ord, ordinal, id LIMIT ?""", (meeting_id, limit))],
    }


def _recording_only_sitting(db: sqlite3.Connection,
                            meeting_id: str) -> dict | None:
    """One sitting served for its **recording** alone (BIZ-27), or None.

    A meeting whose jegyzőkönyv we have not read — usually because the House has
    not published it yet — but which was streamed. The payload is the minutes
    payload with every record-derived collection empty, so the viewer renders it
    without a second code path: what it has is the cover the meeting row already
    carries (which body, when, how long, and the PDF link if one is published)
    and the videos.

    None means there is nothing to open: no such meeting, out of the cycle
    scope, or a sitting with neither a record nor a recording. The caller then
    404s exactly as before — this widens what the endpoint serves, never what it
    claims to have.
    """
    if not _table_exists(db, "committee_video"):
        return None
    m = db.execute("""
        SELECT m.*, c.name AS committee_name
          FROM committee_meeting m
          JOIN committee c ON c.id = m.committee_id
         WHERE m.id = ?""", (meeting_id,)).fetchone()
    if not m or not period_in_scope(m["period_number"]):
        return None
    videos = [_video_row(v) for v in db.execute(
        "SELECT * FROM committee_video WHERE meeting_id = ? "
        "ORDER BY continued, published_at", (meeting_id,))]
    if not videos:
        return None
    return {
        "meetingId": m["id"],
        "committeeId": m["committee_id"],
        "committeeName": m["committee_name"],
        "period": m["period_number"],
        "number": m["number"], "numberInYear": m["number_in_year"],
        "heldAt": m["held_at"], "heldOn": (m["held_at"] or "")[:10] or None,
        "weekday": None, "meetingKind": m["kind"],
        "quorum": m["quorum"], "durationS": m["duration_s"],
        "venue": None, "openedAt": None, "closedAt": None,
        # Not "we know it was open": we know nothing, the cover being in the
        # document we have not read. `kind` says zárt where the registry does.
        "closedSession": False,
        "registryNumber": None, "meetingLabel": None, "termLabel": None,
        # The published PDF if the House has one up, so a page reached this way
        # still offers the record where it exists (BIZ-12: linked, not mirrored).
        "url": m["minutes_url"],
        "speeches": None, "speakers": None, "chars": None, "error": None,
        "hasMinutes": False,
        "agenda": [], "sections": [], "transcript": [], "topSpeakers": [],
        "participants": {"chair": [], "present": [], "proxy": [],
                         "staff": [], "guest": []},
        "videos": videos,
        "neighbours": {
            "prev": _adjacent(db, m["committee_id"], m["held_at"], "<", "DESC"),
            "next": _adjacent(db, m["committee_id"], m["held_at"], ">", "ASC"),
        },
    }


def _adjacent(db: sqlite3.Connection, committee_id: str, held_at: str | None,
              op: str, direction: str) -> dict | None:
    """The committee's sitting immediately before or after this one.

    Only a sitting whose minutes we have **read** is offered: the link goes to
    the minutes viewer, and pointing it at a sitting that has none would walk
    the reader into a 404. A meeting with no record is still on the committee's
    own meeting list, which is where it belongs."""
    if not held_at or not _table_exists(db, "committee_minutes"):
        return None
    r = db.execute(f"""
        SELECT m.id, m.held_at, m.number_in_year
          FROM committee_meeting m
          JOIN committee_minutes cm ON cm.meeting_id = m.id
         WHERE m.committee_id = ? AND m.held_at {op} ? AND cm.speeches > 0
         ORDER BY m.held_at {direction} LIMIT 1
    """, (committee_id, held_at)).fetchone()
    return {"meetingId": r["id"], "heldAt": r["held_at"],
            "numberInYear": r["number_in_year"]} if r else None


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
    # The portrait comes off the shared `person` row, as the minutes' speaker
    # list already takes it: the roster is a list of people, and a page that
    # shows them as faces reads like the rest of the site's people lists rather
    # than like a registry dump. A seat that resolved to nobody (SCR-5) simply
    # has none.
    out["members"] = [_member(m) for m in db.execute(f"""
        SELECT m.*, p.photo_uri FROM committee_member m
        LEFT JOIN person p ON p.person_id = m.person_id
        WHERE m.committee_id = ?
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
        SELECT t.*, p.photo_uri FROM committee_term t
        LEFT JOIN person p ON p.person_id = t.person_id
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
            "photoUri": t["photo_uri"],
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
        "photoUri": m["photo_uri"],
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
    such rows would make the published minutes look like the full history.

    Each row also says whether this deployment holds a **parsed** record of the
    sitting (`speeches`, BIZ-15) and whether a **recording** of it exists
    (`videos`, BIZ-16). Those are three independent states — the PDF can be
    published without us having read it, and a video can exist for a sitting
    whose minutes are still weeks away — so the row carries all three rather
    than one "do we have it" flag that would conflate them."""
    _require_tables(db)
    _check_committee(db, committee_id)
    total = db.execute("SELECT COUNT(*) AS n FROM committee_meeting "
                       "WHERE committee_id = ?", (committee_id,)).fetchone()["n"]
    has_minutes = _table_exists(db, "committee_minutes")
    has_videos = _table_exists(db, "committee_video")
    # A left join rather than a correlated sub-select, and only for a table this
    # DB actually has: the module has to answer on a deployment loaded before
    # the minutes stage existed (EXT-6).
    cols = ", cm.speeches AS n_speeches, cm.error AS minutes_error" \
        if has_minutes else ""
    join = " LEFT JOIN committee_minutes cm ON cm.meeting_id = m.id" \
        if has_minutes else ""
    rows = db.execute(f"""
        SELECT m.*{cols}
        FROM committee_meeting m{join}
        WHERE m.committee_id = ?
        -- `id` closes every ordering here, as it does in `_SORTS`: two meetings
        -- can share a date (a committee sitting twice in a morning) and paging
        -- over an unstable order repeats or skips rows.
        ORDER BY m.held_at DESC, m.number DESC, m.id LIMIT ? OFFSET ?
    """, (committee_id, limit, offset)).fetchall()

    videos: dict[str, list] = {}
    if has_videos and rows:
        marks = ",".join("?" * len(rows))
        for v in db.execute(
                f"""SELECT meeting_id, video_id, url, title, thumbnail,
                           duration_s, continued
                      FROM committee_video
                     WHERE meeting_id IN ({marks})
                     ORDER BY continued, published_at""",
                tuple(r["id"] for r in rows)):
            videos.setdefault(v["meeting_id"], []).append(_video_row(v))

    return {"total": total, "items": [{
        "id": r["id"], "number": r["number"], "numberInYear": r["number_in_year"],
        "heldAt": r["held_at"], "kind": r["kind"], "quorum": r["quorum"],
        "durationS": r["duration_s"], "minutesUrl": r["minutes_url"],
        "speeches": (r["n_speeches"] if has_minutes else None),
        "minutesError": (r["minutes_error"] if has_minutes else None),
        "videos": videos.get(r["id"], []),
    } for r in rows]}


def _video_row(v: sqlite3.Row) -> dict:
    keys = v.keys()
    return {
        "videoId": v["video_id"], "url": v["url"], "title": v["title"],
        "thumbnail": v["thumbnail"], "durationS": v["duration_s"],
        "continued": bool(v["continued"]),
        **({"heldOn": v["held_on"]} if "held_on" in keys else {}),
        **({"views": v["views"]} if "views" in keys else {}),
        **({"meetingId": v["meeting_id"]} if "meeting_id" in keys else {}),
    }


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


@router.get("/{committee_id}/videos")
def committee_videos(
    committee_id: str,
    db: sqlite3.Connection = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """The committee's recordings on the House's YouTube channel (BIZ-16).

    Newest sitting first. A video whose meeting this deployment holds carries
    `meetingId`, and one whose meeting is not (yet) in the registry does not —
    the recording is up the same day and the meeting listing follows weeks
    later, so on the day of a sitting the second is the normal case.

    `coverage` is what the page needs to explain an empty list honestly. The
    channel's first upload is in February 2024, so cycles 40 and 41 have no
    recordings *to* find; a committee of 2016 showing "no videos" with no
    further word would read as a gap in the site rather than a fact about the
    House's own channel.
    """
    _require_tables(db)
    _check_committee(db, committee_id)
    if not _table_exists(db, "committee_video"):
        return {"total": 0, "items": [], "coverage": None}
    total = db.execute("SELECT COUNT(*) AS n FROM committee_video "
                       "WHERE committee_id = ?", (committee_id,)).fetchone()["n"]
    # Whether the sitting a video belongs to also has a *readable* record, which
    # is a third state again: a recording is up the same day and the minutes PDF
    # follows weeks later, so for every recent sitting there is a meeting and a
    # video and no transcript. Offering the link anyway would send the reader to
    # a 404 on exactly the sittings they are most likely to be looking at.
    joined = _table_exists(db, "committee_minutes")
    cols = ", cm.meeting_id AS has_minutes" if joined else ""
    join = (" LEFT JOIN committee_minutes cm ON cm.meeting_id = v.meeting_id"
            if joined else "")
    rows = db.execute(f"""
        SELECT v.*{cols}
        FROM committee_video v{join}
        WHERE v.committee_id = ?
        ORDER BY v.held_on DESC, v.continued, v.published_at DESC, v.video_id
        LIMIT ? OFFSET ?""", (committee_id, limit, offset))
    return {"total": total,
            "items": [dict(_video_row(v),
                           hasMinutes=bool(joined and v["has_minutes"]))
                      for v in rows],
            "coverage": _video_coverage(db)}


def _video_coverage(db: sqlite3.Connection) -> dict | None:
    """The window the recordings registry can speak for at all — the channel's
    own first and last upload."""
    if not _table_exists(db, "committee_video"):
        return None
    def _build():
        r = db.execute("SELECT MIN(held_on) AS lo, MAX(held_on) AS hi, "
                       "COUNT(*) AS n FROM committee_video "
                       "WHERE held_on IS NOT NULL").fetchone()
        if not r or not r["lo"]:
            return None
        return {"from": r["lo"], "to": r["hi"], "videos": r["n"]}
    return cached_aggregate("committee-video-coverage", (), _build)


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
