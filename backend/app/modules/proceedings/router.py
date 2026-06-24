"""Proceedings module API (§5) — search + viewer, under /api/v1/proceedings.

A self-contained vertical slice (EXT-1): it owns these routes and reads only the
proceedings tables plus the shared core entities (person, faction, session).
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ...config import settings
from ...db import get_db
from ...media import per_speech_clip
from ...search import build_match

router = APIRouter(prefix="/proceedings", tags=["proceedings"])


def _is_estimated(align_method: str | None) -> bool:
    """Whether sentence timing is an estimate the UI should disclose (VIE-6).

    Both v1 methods estimate the *sentence* position (whole-day, or within a
    speech's real offsets); only a future word-precise method (forced alignment)
    would be exact."""
    return (align_method or "") not in ("", "none", "forced-alignment")


# ---------------------------------------------------------------------------
# Search (SEA-*)
# ---------------------------------------------------------------------------

@router.get("/search")
def search(
    q: str = Query(..., min_length=1, description="Free-text query; \"…\" = exact phrase"),
    date_from: Optional[str] = Query(None, description="ISO date lower bound"),
    date_to: Optional[str] = Query(None, description="ISO date upper bound"),
    period: Optional[int] = Query(None, description="Electoral period number"),
    person_id: Optional[str] = None,
    faction_id: Optional[int] = None,
    agenda_type: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
):
    """Ranked sentence-level full-text search with combinable filters (SEA-1/3).

    Each hit carries the matched sentence (with `<mark>` highlights), a context
    snippet, speaker/faction/date/agenda metadata, and the timing needed to open
    the viewer at that moment (SEA-4)."""
    match = build_match(q)
    if not match:
        raise HTTPException(400, "Query contains no searchable terms")

    where = ["sentence_fts MATCH :match"]
    params: dict = {"match": match}
    if date_from:
        where.append("ss.date >= :date_from"); params["date_from"] = date_from
    if date_to:
        where.append("ss.date <= :date_to"); params["date_to"] = date_to
    if period is not None:
        where.append("sp.period_number = :period"); params["period"] = period
    if person_id:
        where.append("sp.person_id = :person_id"); params["person_id"] = person_id
    if faction_id is not None:
        where.append("sp.faction_id = :faction_id"); params["faction_id"] = faction_id
    if agenda_type:
        where.append("ai.type = :agenda_type"); params["agenda_type"] = agenda_type
    where_sql = " AND ".join(where)

    base_from = """
        FROM sentence_fts
        JOIN sentence se ON se.id = sentence_fts.rowid
        JOIN speech sp ON sp.uid = se.speech_id
        JOIN session ss ON ss.id = sp.session_id
        LEFT JOIN agenda_item ai ON ai.id = sp.agenda_item_id
        LEFT JOIN person p ON p.person_id = sp.person_id
        LEFT JOIN faction f ON f.id = sp.faction_id
    """

    total_row = db.execute(
        f"SELECT COUNT(*) AS c FROM (SELECT se.id {base_from} WHERE {where_sql} "
        f"LIMIT {settings.max_search_total + 1})", params).fetchone()
    total = total_row["c"]
    capped = total > settings.max_search_total

    rows = db.execute(
        f"""
        SELECT se.id AS sentence_id, se.ord AS sentence_ord, se.time_start,
               se.time_end,
               highlight(sentence_fts, 0, '<mark>', '</mark>') AS highlighted,
               snippet(sentence_fts, 0, '<mark>', '</mark>', '…', 18) AS snippet,
               sp.uid AS speech_uid, sp.origin_id, sp.speaker_label,
               sp.person_id, sp.confidence, sp.align_method,
               ai.title AS agenda_title, ai.type AS agenda_type,
               ss.id AS session_id, ss.date, ss.sitting, sp.period_number,
               p.label AS person_label, p.photo_uri,
               f.label AS faction_label, f.color AS faction_color,
               bm25(sentence_fts) AS rank
        {base_from}
        WHERE {where_sql}
        ORDER BY rank
        LIMIT :limit OFFSET :offset
        """,
        {**params, "limit": limit, "offset": offset}).fetchall()

    return {
        "query": q,
        "match": match,
        "total": min(total, settings.max_search_total),
        "total_is_capped": capped,
        "limit": limit,
        "offset": offset,
        "results": [
            {
                "sentence_id": r["sentence_id"],
                "sentence_ord": r["sentence_ord"],
                "highlighted": r["highlighted"],
                "snippet": r["snippet"],
                "time_start": r["time_start"],
                "time_end": r["time_end"],
                "speech_uid": r["speech_uid"],
                "origin_id": r["origin_id"],
                "session_id": r["session_id"],
                "date": r["date"],
                "sitting": r["sitting"],
                "period": r["period_number"],
                "agenda_title": r["agenda_title"],
                "agenda_type": r["agenda_type"],
                "speaker": {
                    "person_id": r["person_id"],
                    "label": r["person_label"] or r["speaker_label"],
                    "photo_uri": r["photo_uri"],
                },
                "faction": {"label": r["faction_label"], "color": r["faction_color"]}
                           if r["faction_label"] else None,
                "timing": {  # VIE-6 / TRUST-1: disclose estimated precision
                    "confidence": r["confidence"],
                    "align_method": r["align_method"],
                    "estimated": _is_estimated(r["align_method"]),
                },
            }
            for r in rows
        ],
    }


@router.get("/search/trend")
def search_trend(
    q: str = Query(..., min_length=1, description="Free-text query; \"…\" = exact phrase"),
    date_from: Optional[str] = Query(None, description="ISO date lower bound"),
    date_to: Optional[str] = Query(None, description="ISO date upper bound"),
    period: Optional[int] = Query(None, description="Electoral period number"),
    person_id: Optional[str] = None,
    faction_id: Optional[int] = None,
    agenda_type: Optional[str] = None,
    db: sqlite3.Connection = Depends(get_db),
):
    """Popularity of a query over time (SEA-8): matching-sentence counts bucketed
    by calendar period, honouring the *same* filters as `/search` so the chart
    describes the very result set being browsed.

    Granularity adapts to the span — monthly for a few years, yearly for the full
    historical corpus (PERF-3) — so the timeline stays readable. Buckets are the
    ones that actually have hits; the client fills the gaps with zeros so the
    timeline is continuous and honest."""
    match = build_match(q)
    if not match:
        raise HTTPException(400, "Query contains no searchable terms")

    where = ["sentence_fts MATCH :match"]
    params: dict = {"match": match}
    if date_from:
        where.append("ss.date >= :date_from"); params["date_from"] = date_from
    if date_to:
        where.append("ss.date <= :date_to"); params["date_to"] = date_to
    if period is not None:
        where.append("sp.period_number = :period"); params["period"] = period
    if person_id:
        where.append("sp.person_id = :person_id"); params["person_id"] = person_id
    if faction_id is not None:
        where.append("sp.faction_id = :faction_id"); params["faction_id"] = faction_id
    if agenda_type:
        where.append("ai.type = :agenda_type"); params["agenda_type"] = agenda_type
    where_sql = " AND ".join(where)

    base_from = """
        FROM sentence_fts
        JOIN sentence se ON se.id = sentence_fts.rowid
        JOIN speech sp ON sp.uid = se.speech_id
        JOIN session ss ON ss.id = sp.session_id
        LEFT JOIN agenda_item ai ON ai.id = sp.agenda_item_id
    """

    span = db.execute(
        f"SELECT MIN(ss.date) AS lo, MAX(ss.date) AS hi {base_from} WHERE {where_sql}",
        params).fetchone()
    if not span or not span["lo"]:
        return {"query": q, "granularity": "month", "buckets": []}

    # Roughly how many months the hits span (dates are ISO 'YYYY-MM-DD').
    lo_y, lo_m = int(span["lo"][:4]), int(span["lo"][5:7])
    hi_y, hi_m = int(span["hi"][:4]), int(span["hi"][5:7])
    months = (hi_y - lo_y) * 12 + (hi_m - lo_m) + 1
    granularity = "year" if months > 36 else "month"
    fmt = "%Y" if granularity == "year" else "%Y-%m"

    rows = db.execute(
        f"""SELECT strftime('{fmt}', ss.date) AS period, COUNT(*) AS hits
            {base_from} WHERE {where_sql}
            GROUP BY period ORDER BY period""",
        params).fetchall()
    return {
        "query": q,
        "granularity": granularity,
        "buckets": [{"period": r["period"], "hits": r["hits"]} for r in rows],
    }


@router.get("/suggest")
def suggest(q: str = Query(..., min_length=1), limit: int = Query(8, ge=1, le=20),
            db: sqlite3.Connection = Depends(get_db)):
    """Search-as-you-type suggestions for speakers and factions (SEA-7)."""
    like = f"%{q.strip()}%"
    people = db.execute(
        """SELECT p.person_id, p.label, p.photo_uri,
                  (SELECT COUNT(*) FROM speech s WHERE s.person_id=p.person_id) AS speeches
           FROM person p WHERE p.is_mp = 1 AND fold(p.label) LIKE fold(:like)
           ORDER BY speeches DESC LIMIT :limit""",
        {"like": like, "limit": limit}).fetchall()
    factions = db.execute(
        "SELECT id, label, color FROM faction WHERE fold(label) LIKE fold(:like) LIMIT :limit",
        {"like": like, "limit": limit}).fetchall()
    return {
        "speakers": [dict(r) for r in people],
        "factions": [dict(r) for r in factions],
    }


# ---------------------------------------------------------------------------
# Sittings list / browse (use case 2)
# ---------------------------------------------------------------------------

@router.get("/sessions")
def list_sessions(period: Optional[int] = None,
                  db: sqlite3.Connection = Depends(get_db)):
    where = ""
    params: dict = {}
    if period is not None:
        where = "WHERE s.period_number = :period"; params["period"] = period
    rows = db.execute(
        f"""SELECT s.id, s.period_number, s.sitting, s.date, s.date_start,
                   s.date_end, s.video_duration,
                   (SELECT COUNT(*) FROM speech sp WHERE sp.session_id=s.id) AS speeches,
                   (SELECT COUNT(*) FROM agenda_item ai WHERE ai.session_id=s.id) AS agenda_items
            FROM session s {where} ORDER BY s.date DESC, s.sitting DESC""",
        params).fetchall()
    return {"sessions": [dict(r) for r in rows]}


@router.get("/sessions/{session_id}")
def get_session(session_id: str, db: sqlite3.Connection = Depends(get_db)):
    """A sitting day: agenda items in order, each with its speeches (use case 2)."""
    s = db.execute("SELECT * FROM session WHERE id = ?", (session_id,)).fetchone()
    if not s:
        raise HTTPException(404, "Session not found")
    agenda = db.execute(
        "SELECT * FROM agenda_item WHERE session_id = ? ORDER BY ord", (session_id,)
    ).fetchall()
    speeches = db.execute(
        """SELECT sp.uid, sp.origin_id, sp.agenda_item_id, sp.speech_index,
                  sp.speaker_label, sp.person_id, sp.speaker_status,
                  sp.felszolalas_tipus, sp.procedural, sp.time_start,
                  sp.time_end, sp.duration, sp.has_text, sp.confidence,
                  sp.align_method, p.label AS person_label, p.photo_uri,
                  f.label AS faction_label, f.color AS faction_color
           FROM speech sp
           LEFT JOIN person p ON p.person_id = sp.person_id
           LEFT JOIN faction f ON f.id = sp.faction_id
           WHERE sp.session_id = ? ORDER BY sp.speech_index""",
        (session_id,)).fetchall()
    by_agenda: dict = {a["id"]: [] for a in agenda}
    for sp in speeches:
        by_agenda.setdefault(sp["agenda_item_id"], []).append(_speech_brief(sp))
    return {
        "session": _session_dict(s),
        "agenda": [
            {**_agenda_dict(a), "speeches": by_agenda.get(a["id"], [])}
            for a in agenda
        ],
    }


# ---------------------------------------------------------------------------
# Viewer: a single speech with its sentences (VIE-1/3/5)
# ---------------------------------------------------------------------------

@router.get("/speeches/{uid}")
def get_speech(uid: str, db: sqlite3.Connection = Depends(get_db)):
    sp = db.execute(
        """SELECT sp.*, p.label AS person_label, p.photo_uri,
                  f.label AS faction_label, f.color AS faction_color,
                  ai.title AS agenda_title, ai.official_title, ai.type AS agenda_type,
                  ai.native_type
           FROM speech sp
           LEFT JOIN person p ON p.person_id = sp.person_id
           LEFT JOIN faction f ON f.id = sp.faction_id
           LEFT JOIN agenda_item ai ON ai.id = sp.agenda_item_id
           WHERE sp.uid = ?""", (uid,)).fetchone()
    if not sp:
        raise HTTPException(404, "Speech not found")
    session = db.execute("SELECT * FROM session WHERE id = ?",
                         (sp["session_id"],)).fetchone()
    sentences = db.execute(
        "SELECT id, ord, text, time_start, time_end FROM sentence "
        "WHERE speech_id = ? ORDER BY ord", (uid,)).fetchall()
    nb = _speech_neighbours(db, sp["session_id"], sp["speech_index"])
    speech = _speech_full(sp)
    # Source the player on a clip of just this speech (VIE-9), derived from the
    # day stream + the speech's real offsets; falls back to the whole-day stream.
    clip = per_speech_clip(session["video_uri"], session["video_playseq"],
                           sp["video_start"], sp["video_end"]) if session else None
    speech["video_uri"] = clip["video_uri"] if clip else None
    speech["video_playseq"] = clip["video_playseq"] if clip else None
    return {
        "speech": speech,
        "session": _session_dict(session),
        "sentences": [dict(r) for r in sentences],
        "neighbours": nb,
    }


def _speech_neighbours(db, session_id, idx):
    prev = db.execute(
        "SELECT uid FROM speech WHERE session_id=? AND speech_index<? "
        "ORDER BY speech_index DESC LIMIT 1", (session_id, idx)).fetchone()
    nxt = db.execute(
        "SELECT uid FROM speech WHERE session_id=? AND speech_index>? "
        "ORDER BY speech_index ASC LIMIT 1", (session_id, idx)).fetchone()
    return {"prev": prev["uid"] if prev else None,
            "next": nxt["uid"] if nxt else None}


# ---------------------------------------------------------------------------
# serializers
# ---------------------------------------------------------------------------

def _session_dict(s) -> dict:
    if s is None:
        return None
    return {
        "id": s["id"], "period": s["period_number"], "sitting": s["sitting"],
        "date": s["date"], "date_start": s["date_start"], "date_end": s["date_end"],
        "video_uri": s["video_uri"], "video_playseq": s["video_playseq"],
        "video_duration": s["video_duration"],
        "video_license": s["video_license"], "video_creator": s["video_creator"],
        "source": s["source"], "source_page": s["source_page"],
        "timing_method": s["timing_method"],
    }


def _agenda_dict(a) -> dict:
    return {"id": a["id"], "ord": a["ord"], "title": a["title"],
            "official_title": a["official_title"], "type": a["type"],
            "native_type": a["native_type"]}


def _speech_brief(sp) -> dict:
    return {
        "uid": sp["uid"], "origin_id": sp["origin_id"],
        "speech_index": sp["speech_index"],
        "speaker": {"person_id": sp["person_id"],
                    "label": sp["person_label"] or sp["speaker_label"],
                    "photo_uri": sp["photo_uri"], "status": sp["speaker_status"]},
        "faction": {"label": sp["faction_label"], "color": sp["faction_color"]}
                   if sp["faction_label"] else None,
        "speech_type": sp["felszolalas_tipus"],
        "procedural": bool(sp["procedural"]),
        "time_start": sp["time_start"], "time_end": sp["time_end"],
        "duration": sp["duration"], "has_text": bool(sp["has_text"]),
        "timing": {"confidence": sp["confidence"], "align_method": sp["align_method"],
                   "estimated": _is_estimated(sp["align_method"])},
    }


def _speech_full(sp) -> dict:
    return {
        "uid": sp["uid"], "origin_id": sp["origin_id"],
        "session_id": sp["session_id"], "period": sp["period_number"],
        "speech_index": sp["speech_index"],
        "agenda": {"title": sp["agenda_title"], "official_title": sp["official_title"],
                   "type": sp["agenda_type"], "native_type": sp["native_type"]},
        "speaker": {"person_id": sp["person_id"],
                    "label": sp["person_label"] or sp["speaker_label"],
                    "photo_uri": sp["photo_uri"], "status": sp["speaker_status"]},
        "faction": {"label": sp["faction_label"], "color": sp["faction_color"]}
                   if sp["faction_label"] else None,
        "speech_type": sp["felszolalas_tipus"],
        "procedural": bool(sp["procedural"]),
        "time_start": sp["time_start"], "time_end": sp["time_end"],
        "video_start": sp["video_start"], "video_end": sp["video_end"],
        "duration": sp["duration"], "has_text": bool(sp["has_text"]),
        "source_uri": sp["source_uri"], "source_page": sp["source_page"],
        "timing": {"confidence": sp["confidence"], "align_method": sp["align_method"],
                   "estimated": _is_estimated(sp["align_method"])},
    }
