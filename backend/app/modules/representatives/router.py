"""Representatives & Statistics module API (§6) — /api/v1/representatives.

Self-contained slice (EXT-1) reading the shared `person`/`faction`/`membership`
core tables and the precomputed aggregate tables (REP-7). Statistics are served
straight from `person_stats`/`faction_stats`, never aggregated live (PERF-1).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ...config import settings
from ...db import get_db

router = APIRouter(prefix="/representatives", tags=["representatives"])


@router.get("")
def list_representatives(
    q: Optional[str] = None,
    faction_id: Optional[int] = None,
    period: Optional[int] = None,
    constituency: Optional[str] = None,
    sort: str = Query("name", pattern="^(name|speeches|speaking_time)$"),
    limit: int = Query(60, ge=1, le=300),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
):
    """Browsable, filterable MP list (REP-1). Filters combine."""
    where = ["p.is_mp = 1"]
    params: dict = {}
    if q:
        where.append("fold(p.label) LIKE fold(:q)"); params["q"] = f"%{q.strip()}%"
    if constituency:
        where.append("fold(p.constituency) LIKE fold(:con)"); params["con"] = f"%{constituency}%"
    if faction_id is not None:
        where.append("EXISTS (SELECT 1 FROM membership m WHERE m.person_id=p.person_id "
                     "AND m.faction_id=:fid)"); params["fid"] = faction_id
    if period is not None:
        where.append("EXISTS (SELECT 1 FROM membership m WHERE m.person_id=p.person_id "
                     "AND m.period_number=:per)"); params["per"] = period
    where_sql = " AND ".join(where)

    order = {"name": "p.lastname, p.label",
             "speeches": "stat.speech_count DESC",
             "speaking_time": "stat.speaking_seconds DESC"}[sort]

    # Scope the per-MP stats and the shown faction to the selected cycle (§4A):
    # with `period` set, use that cycle's `person_stats` row and that cycle's
    # membership; otherwise the all-cycles row and the most recent faction. This
    # is why the list never mixes a previous cycle's speech counts into another.
    if period is not None:
        stat_join = "stat.person_id = p.person_id AND stat.period_number = :per"
        faction_sub = ("SELECT m.faction_id FROM membership m WHERE m.person_id = p.person_id "
                       "AND m.period_number = :per ORDER BY m.period_number DESC LIMIT 1")
    else:
        stat_join = "stat.person_id = p.person_id AND stat.period_number IS NULL"
        faction_sub = ("SELECT m.faction_id FROM membership m WHERE m.person_id = p.person_id "
                       "ORDER BY m.period_number DESC LIMIT 1")

    total = db.execute(f"SELECT COUNT(*) AS c FROM person p WHERE {where_sql}",
                       params).fetchone()["c"]
    rows = db.execute(
        f"""SELECT p.person_id, p.label, p.firstname, p.lastname, p.photo_uri,
                   p.constituency,
                   COALESCE(stat.speech_count, 0) AS speech_count,
                   COALESCE(stat.speaking_seconds, 0) AS speaking_seconds,
                   f.id AS faction_id, f.label AS faction_label, f.color AS faction_color
            FROM person p
            LEFT JOIN person_stats stat ON {stat_join}
            LEFT JOIN faction f ON f.id = ({faction_sub})
            WHERE {where_sql}
            ORDER BY {order}
            LIMIT :limit OFFSET :offset""",
        {**params, "limit": limit, "offset": offset}).fetchall()
    return {
        "total": total, "limit": limit, "offset": offset,
        "representatives": [
            {
                "person_id": r["person_id"], "label": r["label"],
                "firstname": r["firstname"], "lastname": r["lastname"],
                "photo_uri": r["photo_uri"], "constituency": r["constituency"],
                "speech_count": r["speech_count"],
                "speaking_seconds": r["speaking_seconds"],
                "faction": {"id": r["faction_id"], "label": r["faction_label"],
                            "color": r["faction_color"]} if r["faction_label"] else None,
            } for r in rows
        ],
    }


@router.get("/factions")
def list_factions(period: Optional[int] = None,
                  db: sqlite3.Connection = Depends(get_db)):
    """Factions with aggregate stats and consistent colours (REP-4).

    Scoped to the global cycle (§4A): with ``period`` set, the per-period
    aggregate row is used; otherwise the all-periods row (``period_number IS NULL``)."""
    if period is not None:
        join = "fs.faction_id=f.id AND fs.period_number = :per"
        params = {"per": period}
    else:
        join = "fs.faction_id=f.id AND fs.period_number IS NULL"
        params = {}
    rows = db.execute(
        f"""SELECT f.id, f.label, f.color,
                  COALESCE(fs.speech_count, 0) AS speech_count,
                  COALESCE(fs.speaking_seconds, 0) AS speaking_seconds,
                  COALESCE(fs.mp_count, 0) AS mp_count
           FROM faction f
           LEFT JOIN faction_stats fs ON {join}
           ORDER BY fs.speaking_seconds DESC""", params).fetchall()
    factions = []
    for r in rows:
        mp = r["mp_count"] or 0
        factions.append({
            "id": r["id"], "label": r["label"], "color": r["color"],
            "speech_count": r["speech_count"],
            "speaking_seconds": r["speaking_seconds"],
            "mp_count": mp,
            "avg_speaking_seconds": (r["speaking_seconds"] / mp) if mp else 0,
            "avg_speeches": (r["speech_count"] / mp) if mp else 0,
        })
    return {"factions": factions, "methodology": _FACTION_METHODOLOGY}


@router.get("/{person_id}")
def get_representative(person_id: str, period: Optional[int] = None,
                      db: sqlite3.Connection = Depends(get_db)):
    """Full MP profile (REP-2): bio, faction history, constituency, links.

    The shown ``current_faction`` is scoped to the selected cycle (§4A): with
    ``period`` set it is the MP's faction in that cycle, otherwise their most
    recent faction. (``faction_history`` always lists every cycle — it IS the
    cross-cycle view.)"""
    p = db.execute("SELECT * FROM person WHERE person_id = ?", (person_id,)).fetchone()
    if not p:
        raise HTTPException(404, "Representative not found")
    if period is not None:
        current = db.execute(
            """SELECT f.id AS faction_id, f.label AS faction_label, f.color AS faction_color,
                      m.position
               FROM membership m LEFT JOIN faction f ON f.id = m.faction_id
               WHERE m.person_id = ? AND m.period_number = ?
               ORDER BY m.period_number DESC LIMIT 1""",
            (person_id, period)).fetchone()
    else:
        current = db.execute(
            """SELECT f.id AS faction_id, f.label AS faction_label, f.color AS faction_color,
                      m.position
               FROM membership m LEFT JOIN faction f ON f.id = m.faction_id
               WHERE m.person_id = ? ORDER BY m.period_number DESC LIMIT 1""",
            (person_id,)).fetchone()
    # Colour map so each historical faction renders consistently (REP-4).
    colors = {r["label"]: r["color"]
              for r in db.execute("SELECT label, color FROM faction")}
    faction_history = [
        {"cycle": h.get("cycle"), "start": h.get("start"), "end": h.get("end"),
         "faction": {"label": h.get("label"), "color": colors.get(h.get("label"))}
                    if h.get("label") else None}
        for h in _loads(p["faction_history_json"])]
    return {
        "person_id": p["person_id"], "label": p["label"],
        "label_full": p["label_full"], "firstname": p["firstname"],
        "lastname": p["lastname"], "photo_uri": p["photo_uri"],
        "wikidata_id": p["wikidata_id"], "constituency": p["constituency"],
        "seat": p["seat"], "email": p["email"], "website": p["website"],
        "highest_education": p["highest_education"], "active": p["active"],
        "is_mp": bool(p["is_mp"]),
        "current_faction": {"id": current["faction_id"], "label": current["faction_label"],
                            "color": current["faction_color"]} if current and current["faction_label"] else None,
        "faction_history": faction_history,
        "education": _loads(p["education_json"]),
        "committees": _loads(p["committees_json"]),
        "offices": _loads(p["offices_json"]),
        "election_history": _loads(p["election_history_json"]),
    }


@router.get("/{person_id}/statistics")
def get_statistics(person_id: str, period: Optional[int] = None,
                   db: sqlite3.Connection = Depends(get_db)):
    """Per-MP statistics (REP-3) with explicit scope + methodology (REP-5).

    Everything except ``by_period`` (the explicit per-cycle breakdown) is scoped
    to the selected cycle (§4A): with ``period`` set, the headline totals,
    over-time chart and session count cover ONLY that cycle; otherwise all
    cycles. The ``scope.description`` names the cycle so the scope is explicit."""
    p = db.execute("SELECT person_id, external_stats_json FROM person WHERE person_id=?",
                   (person_id,)).fetchone()
    if not p:
        raise HTTPException(404, "Representative not found")

    if period is not None:
        totals = db.execute(
            "SELECT speech_count, speaking_seconds, sentence_count FROM person_stats "
            "WHERE person_id=? AND period_number=?", (person_id, period)).fetchone()
        over_time = db.execute(
            """SELECT pss.session_id, pss.date, pss.speech_count, pss.speaking_seconds,
                      s.sitting, s.period_number
               FROM person_session_stats pss JOIN session s ON s.id=pss.session_id
               WHERE pss.person_id=? AND s.period_number=? ORDER BY pss.date""",
            (person_id, period)).fetchall()
        sessions_covered = db.execute(
            "SELECT COUNT(DISTINCT pss.session_id) AS c FROM person_session_stats pss "
            "JOIN session s ON s.id=pss.session_id "
            "WHERE pss.person_id=? AND s.period_number=?", (person_id, period)).fetchone()["c"]
    else:
        totals = db.execute(
            "SELECT speech_count, speaking_seconds, sentence_count FROM person_stats "
            "WHERE person_id=? AND period_number IS NULL", (person_id,)).fetchone()
        over_time = db.execute(
            """SELECT pss.session_id, pss.date, pss.speech_count, pss.speaking_seconds,
                      s.sitting, s.period_number
               FROM person_session_stats pss JOIN session s ON s.id=pss.session_id
               WHERE pss.person_id=? ORDER BY pss.date""", (person_id,)).fetchall()
        sessions_covered = db.execute(
            "SELECT COUNT(DISTINCT session_id) AS c FROM person_session_stats "
            "WHERE person_id=?", (person_id,)).fetchone()["c"]
    by_period = db.execute(
        """SELECT ep.number AS period, ep.label, ps.speech_count, ps.speaking_seconds,
                  ps.sentence_count
           FROM person_stats ps JOIN electoral_period ep ON ep.number=ps.period_number
           WHERE ps.person_id=? AND ps.period_number IS NOT NULL
           ORDER BY ep.number""", (person_id,)).fetchall()

    # REP-3: "bills submitted" stays hidden (not faked) until the Bills module
    # is live (EXT-6). When it is, surface the official parlament.hu own-bill
    # count — for the selected cycle, or the most recent on record when scope is
    # "all cycles" — plus the per-cycle breakdown.
    bills_available = settings.module_enabled("bills")
    bills_submitted = None
    bills_by_cycle = []
    if bills_available:
        ext = _loads(p["external_stats_json"]) or {}
        bills_by_cycle = (ext or {}).get("billsSubmitted") or []
        bills_submitted = (_own_bills_for_cycle(bills_by_cycle, period)
                           if period is not None else _latest_own_bills(bills_by_cycle))

    if period is not None:
        ep = db.execute("SELECT label FROM electoral_period WHERE number=?",
                        (period,)).fetchone()
        cyc_label = ep["label"] if ep else f"{period}. ciklus"
        scope_desc = ("A Parlamonitor által feldolgozott ülésnapok alapján — "
                      f"{cyc_label}.")
    else:
        scope_desc = ("A Parlamonitor által feldolgozott ülésnapok alapján — "
                      "összes ciklus.")

    return {
        "person_id": person_id,
        "scope": {"description": scope_desc, "period": period,
                  "sessions_covered": sessions_covered},
        "totals": {
            "speech_count": totals["speech_count"] if totals else 0,
            "speaking_seconds": totals["speaking_seconds"] if totals else 0,
            "sentence_count": totals["sentence_count"] if totals else 0,
            "bills_submitted": bills_submitted,  # null while Bills module is off
            "bills_available": bills_available,
            "bills_by_cycle": bills_by_cycle,
        },
        "by_period": [dict(r) for r in by_period],
        "over_time": [dict(r) for r in over_time],
        "methodology": _MP_METHODOLOGY,
    }


@router.get("/{person_id}/speeches")
def get_speeches(person_id: str, period: Optional[int] = None,
                 limit: int = Query(50, ge=1, le=200),
                 offset: int = Query(0, ge=0),
                 db: sqlite3.Connection = Depends(get_db)):
    """Reverse-chronological list of an MP's speeches (REP-2), scoped to the
    selected cycle (§4A) when ``period`` is set."""
    extra = " AND ss.period_number = :per" if period is not None else ""
    params: dict = {"pid": person_id}
    if period is not None:
        params["per"] = period
    total = db.execute(
        f"""SELECT COUNT(*) AS c FROM speech sp
            JOIN session ss ON ss.id = sp.session_id
            WHERE sp.person_id = :pid{extra}""", params).fetchone()["c"]
    rows = db.execute(
        f"""SELECT sp.uid, sp.origin_id, sp.duration, sp.time_start, sp.has_text,
                  ss.id AS session_id, ss.date, ss.sitting,
                  ai.title AS agenda_title, ai.type AS agenda_type,
                  (SELECT se.text FROM sentence se WHERE se.speech_id=sp.uid
                   ORDER BY se.ord LIMIT 1) AS first_sentence
           FROM speech sp
           JOIN session ss ON ss.id = sp.session_id
           LEFT JOIN agenda_item ai ON ai.id = sp.agenda_item_id
           WHERE sp.person_id = :pid{extra}
           ORDER BY ss.date DESC, sp.speech_index DESC
           LIMIT :limit OFFSET :offset""",
        {**params, "limit": limit, "offset": offset}).fetchall()
    return {
        "total": total, "limit": limit, "offset": offset,
        "speeches": [
            {"uid": r["uid"], "origin_id": r["origin_id"], "duration": r["duration"],
             "time_start": r["time_start"], "has_text": bool(r["has_text"]),
             "session_id": r["session_id"], "date": r["date"], "sitting": r["sitting"],
             "agenda_title": r["agenda_title"], "agenda_type": r["agenda_type"],
             "excerpt": (r["first_sentence"] or "")[:200]}
            for r in rows],
    }


@router.get("/{person_id}/votes")
def get_votes(person_id: str, period: Optional[int] = None,
              limit: int = Query(50, ge=1, le=200),
              offset: int = Query(0, ge=0),
              db: sqlite3.Connection = Depends(get_db)):
    """How an MP voted, reverse-chronologically (the reciprocal of the Votes
    module's per-MP roll call, EXT-2), scoped to the selected cycle (§4A) when
    ``period`` is set. Empty when the Votes module is disabled (EXT-6) — its
    tables may not exist, so guard before querying."""
    if not settings.module_enabled("votes"):
        return {"total": 0, "limit": limit, "offset": offset, "votes": [],
                "available": False}
    extra = " AND v.period_number = :per" if period is not None else ""
    params: dict = {"pid": person_id}
    if period is not None:
        params["per"] = period
    total = db.execute(
        f"""SELECT COUNT(*) AS c FROM vote_record vr JOIN vote v ON v.id = vr.vote_id
            WHERE vr.person_id = :pid{extra}""", params).fetchone()["c"]
    rows = db.execute(
        f"""SELECT v.id, v.vote_datetime, v.subject, v.result,
                  vr.value, vr.value_code
           FROM vote_record vr JOIN vote v ON v.id = vr.vote_id
           WHERE vr.person_id = :pid{extra}
           ORDER BY v.vote_datetime DESC LIMIT :limit OFFSET :offset""",
        {**params, "limit": limit, "offset": offset}).fetchall()
    # The bills each of those votes decided (for context on the profile).
    ids = [r["id"] for r in rows]
    subjects: dict[str, list] = {}
    if ids:
        ph = ",".join("?" * len(ids))
        for s in db.execute(
            f"""SELECT vs.vote_id, b.id AS bill_id, vs.bill_number, vs.title
                FROM vote_subject vs LEFT JOIN bill b ON b.id = vs.iromany_id
                WHERE vs.vote_id IN ({ph}) ORDER BY vs.vote_id, vs.ord""", ids).fetchall():
            subjects.setdefault(s["vote_id"], []).append(
                {"bill_id": s["bill_id"], "bill_number": s["bill_number"],
                 "title": s["title"]})
    return {
        "total": total, "limit": limit, "offset": offset, "available": True,
        "votes": [{
            "id": r["id"], "vote_datetime": r["vote_datetime"],
            "subject": r["subject"], "result": r["result"],
            "value": r["value"], "value_code": r["value_code"],
            "subjects": subjects.get(r["id"], []),
        } for r in rows],
    }


def _own_bills_for_cycle(by_cycle: list, period: int) -> Optional[int]:
    """The own-bills count for a specific cycle in the upstream breakdown."""
    for entry in by_cycle or []:
        if entry.get("cycle") == period:
            return entry.get("ownBills")
    return None


def _latest_own_bills(by_cycle: list) -> Optional[int]:
    """The own-bills count for the most recent cycle in the upstream breakdown."""
    best = None
    for entry in by_cycle or []:
        cyc = entry.get("cycle")
        if cyc is None:
            continue
        if best is None or cyc > best[0]:
            best = (cyc, entry.get("ownBills"))
    return best[1] if best else None


def _loads(s):
    if not s:
        return []
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return []


_MP_METHODOLOGY = (
    "A beszédidő az adott képviselőhöz rendelt felszólalások becsült "
    "időtartamainak összege. A felszólalások száma a feldolgozott ülésnapokon "
    "elhangzott, e képviselőhöz kötött felszólalások darabszáma. A v1-es "
    "időbecslés pozícióalapú (karakterarányos), ezért közelítő — minden "
    "felszólalásnál átkattintva ellenőrizhető."
)
_FACTION_METHODOLOGY = (
    "A frakciószintű összesítések a frakcióhoz rendelt felszólalások alapján "
    "készülnek; az átlagok a frakcióban felszólaló képviselők számára vetítve."
)
