"""Bills module API (§7 / EXT-1) — /api/v1/bills.

A self-contained slice over the `bill` / `bill_sponsor` tables. Sponsorship is
expressed through the shared `person`/`faction` core entities (EXT-2): a sponsor
who is a known MP links to their profile, while government/committee submitters
keep their display label. The list is filterable and paginated; every bill links
back to its parlament.hu source text (LEGAL-1).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ...db import get_db
from ...media import per_speech_clip

router = APIRouter(prefix="/bills", tags=["bills"])

# A question-type iromány (kérdés / interpelláció / azonnali kérdés) is answered
# orally in plenary by the responsible minister or state secretary; that answer
# is a recorded plenary speech. These event names mark that oral answer (written
# answers — "kérdés írásban megválaszolva" — carry no speech and are excluded),
# so the bill page can embed the answer video (VIE-9).
_ANSWER_EVENTS = ("kérdés megválaszolva", "interpelláció szóban megválaszolva")

# Debate brackets: a plenary debate is opened and closed by a pair of bill
# events, each tied (via the shared speech UUID, EXT-2) to the plenary speech
# that announced it. The speeches *between* those two anchors are the debate
# itself, surfaced as a panel on the bill page. Only the two debate kinds whose
# events actually resolve to plenary speeches are bracketed here (általános /
# összevont vita); committee-phase "részletes vita" events carry no speech link.
_DEBATE_STARTS = {
    "általános vita megkezdve": {"end": "általános vita lezárva", "label": "általános vita"},
    "összevont vita megkezdve": {"end": "összevont vita lezárva", "label": "összevont vita"},
}


def _stages(stages_json: Optional[str]) -> list[dict]:
    """Parse the stored legislative-stage diagram and flag the current stage
    (the furthest reached) so the UI can split the timeline into past/future."""
    try:
        stages = json.loads(stages_json) if stages_json else []
    except (ValueError, TypeError):
        return []
    last_done = -1
    for i, s in enumerate(stages):
        if s.get("done"):
            last_done = i
    for i, s in enumerate(stages):
        s["current"] = (i == last_done)
    return stages


def _sponsors_for(db: sqlite3.Connection, bill_ids: list[str]) -> dict[str, list]:
    """Sponsor rows grouped by bill id (one query for a page of bills)."""
    if not bill_ids:
        return {}
    placeholders = ",".join("?" * len(bill_ids))
    rows = db.execute(
        f"""SELECT bs.bill_id, bs.person_id, bs.label, bs.ord,
                   p.label AS person_label,
                   f.id AS faction_id, f.label AS faction_label, f.color AS faction_color
            FROM bill_sponsor bs
            LEFT JOIN person p ON p.person_id = bs.person_id
            LEFT JOIN faction f ON f.id = bs.faction_id
            WHERE bs.bill_id IN ({placeholders})
            ORDER BY bs.bill_id, bs.ord""", bill_ids).fetchall()
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r["bill_id"], []).append({
            "person_id": r["person_id"],
            "label": r["label"],
            "name": r["person_label"] or r["label"],
            "faction": {"id": r["faction_id"], "label": r["faction_label"],
                        "color": r["faction_color"]} if r["faction_label"] else None,
        })
    return out


@router.get("")
def list_bills(
    q: Optional[str] = None,
    period: Optional[int] = None,
    main_type: Optional[str] = None,
    main_type_not: Optional[str] = None,     # exclude a fotipus, e.g. T (bills)
    main_type_in: Optional[str] = None,      # include any of these fotipusok (CSV)
    main_type_not_in: Optional[str] = None,  # exclude any of these fotipusok (CSV)
    type: Optional[str] = None,              # exact iromány type (category)
    status: Optional[str] = None,
    sponsor: Optional[str] = None,           # person_id — bills by this MP
    sort: str = Query("number", pattern="^(number|date)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
):
    """Browsable, filterable bill list. Filters combine."""
    where = ["1=1"]
    params: dict = {}
    if q:
        where.append("fold(b.title) LIKE fold(:q)"); params["q"] = f"%{q.strip()}%"
    if period is not None:
        where.append("b.period_number = :per"); params["per"] = period
    if main_type:
        where.append("b.main_type = :mt"); params["mt"] = main_type
    if main_type_not:
        where.append("(b.main_type IS NULL OR b.main_type != :mtn)")
        params["mtn"] = main_type_not
    if main_type_in:
        codes = [c.strip() for c in main_type_in.split(",") if c.strip()]
        if codes:
            keys = [f"mti{i}" for i in range(len(codes))]
            where.append("b.main_type IN (" + ",".join(":" + k for k in keys) + ")")
            params.update(dict(zip(keys, codes)))
    if main_type_not_in:
        codes = [c.strip() for c in main_type_not_in.split(",") if c.strip()]
        if codes:
            keys = [f"mtni{i}" for i in range(len(codes))]
            where.append("(b.main_type IS NULL OR b.main_type NOT IN ("
                         + ",".join(":" + k for k in keys) + "))")
            params.update(dict(zip(keys, codes)))
    if type:
        where.append("b.type = :ty"); params["ty"] = type
    if status:
        where.append("b.status = :st"); params["st"] = status
    if sponsor:
        where.append("EXISTS (SELECT 1 FROM bill_sponsor bs WHERE bs.bill_id=b.id "
                     "AND bs.person_id=:sp)"); params["sp"] = sponsor
    where_sql = " AND ".join(where)

    order = {"number": "b.number_sort DESC",
             "date": "b.submitted_date DESC"}[sort]

    total = db.execute(f"SELECT COUNT(*) AS c FROM bill b WHERE {where_sql}",
                       params).fetchone()["c"]
    rows = db.execute(
        f"""SELECT b.id, b.bill_number, b.title, b.type, b.main_type, b.status,
                   b.submitted_date, b.text_url, b.source_url, b.period_number
            FROM bill b WHERE {where_sql}
            ORDER BY {order} LIMIT :limit OFFSET :offset""",
        {**params, "limit": limit, "offset": offset}).fetchall()
    sponsors = _sponsors_for(db, [r["id"] for r in rows])
    return {
        "total": total, "limit": limit, "offset": offset,
        "bills": [
            {
                "id": r["id"], "bill_number": r["bill_number"], "title": r["title"],
                "type": r["type"], "main_type": r["main_type"], "status": r["status"],
                "submitted_date": r["submitted_date"], "text_url": r["text_url"],
                "source_url": r["source_url"], "period_number": r["period_number"],
                "sponsors": sponsors.get(r["id"], []),
            } for r in rows
        ],
    }


@router.get("/facets")
def bill_facets(period: Optional[int] = None,
                main_type: Optional[str] = None,
                main_type_not: Optional[str] = None,
                sponsor: Optional[str] = None,       # restrict to one MP's irományok
                db: sqlite3.Connection = Depends(get_db)):
    """Distinct statuses and types for filter controls (optionally scoped to a
    period, a fotipus include/exclude — e.g. ``main_type=T`` for the bills page,
    ``main_type_not=T`` for the other-irományok page — and a sponsor, so a
    profile can list only the document types that MP actually submitted)."""
    where, params = ["1=1"], []
    if period is not None:
        where.append("b.period_number = ?"); params.append(period)
    if main_type:
        where.append("b.main_type = ?"); params.append(main_type)
    if main_type_not:
        where.append("(b.main_type IS NULL OR b.main_type != ?)"); params.append(main_type_not)
    if sponsor:
        where.append("EXISTS (SELECT 1 FROM bill_sponsor bs "
                     "WHERE bs.bill_id=b.id AND bs.person_id=?)"); params.append(sponsor)
    where_sql = "WHERE " + " AND ".join(where)
    statuses = [r["status"] for r in db.execute(
        f"SELECT DISTINCT b.status FROM bill b {where_sql} "
        f"AND b.status IS NOT NULL ORDER BY b.status", params)]
    types = [{"main_type": r["main_type"], "type": r["type"]} for r in db.execute(
        f"SELECT DISTINCT b.main_type, b.type FROM bill b {where_sql} "
        f"ORDER BY b.main_type, b.type", params)]
    return {"statuses": statuses, "types": types}


def _rows(db: sqlite3.Connection, sql: str, bill_id: str) -> list[dict]:
    return [dict(r) for r in db.execute(sql, (bill_id,)).fetchall()]


def _motions_for(db: sqlite3.Connection, bill_id: str) -> list[dict]:
    """The bill's non-self-standing motions, each with its submitters grouped in
    (MP sponsors linked to their profile via person_id, EXT-2)."""
    motions = [dict(r) for r in db.execute(
        """SELECT id, iromany_id, bill_number, main_type, type, submitted_date,
                  text_url, text_caption, no_text, has_vote, note
           FROM bill_motion WHERE bill_id = ? ORDER BY ord""", (bill_id,)).fetchall()]
    if not motions:
        return []
    by_id = {m["id"]: m for m in motions}
    for m in motions:
        m["no_text"] = bool(m["no_text"])
        m["has_vote"] = bool(m["has_vote"])
        m["sponsors"] = []
    rows = db.execute(
        f"""SELECT ms.motion_id, ms.person_id, ms.label,
                   p.label AS person_label,
                   f.id AS faction_id, f.label AS faction_label, f.color AS faction_color
            FROM bill_motion_sponsor ms
            LEFT JOIN person p ON p.person_id = ms.person_id
            LEFT JOIN faction f ON f.id = ms.faction_id
            WHERE ms.motion_id IN ({",".join("?" * len(motions))})
            ORDER BY ms.motion_id, ms.ord""",
        [m["id"] for m in motions]).fetchall()
    for r in rows:
        by_id[r["motion_id"]]["sponsors"].append({
            "person_id": r["person_id"],
            "label": r["label"],
            "name": r["person_label"] or r["label"],
            "faction": {"id": r["faction_id"], "label": r["faction_label"],
                        "color": r["faction_color"]} if r["faction_label"] else None,
        })
    for m in motions:
        m.pop("id", None)
    return motions


def _debate_speeches(db: sqlite3.Connection, start: sqlite3.Row,
                     end: sqlite3.Row) -> list[dict]:
    """The plenary speeches from the debate-opening anchor through the closing
    one (inclusive), in proceedings order. Ordering is global by sitting date
    then per-session speech index, so a debate adjourned and resumed on another
    day still reads end to end. Each speaker who is a known MP links to their
    profile via the shared `person` entity (EXT-2)."""
    rows = db.execute(
        """SELECT sp.uid, sp.speech_index, sp.speaker_label, sp.person_id,
                  sp.speaker_status, sp.duration, sp.has_text,
                  p.label AS person_label, p.photo_uri,
                  f.label AS faction_label, f.color AS faction_color,
                  ss.date AS session_date, ss.sitting
           FROM speech sp
           JOIN session ss ON ss.id = sp.session_id
           LEFT JOIN person p ON p.person_id = sp.person_id
           LEFT JOIN faction f ON f.id = sp.faction_id
           WHERE (ss.date > :d1 OR (ss.date = :d1 AND sp.speech_index >= :i1))
             AND (ss.date < :d2 OR (ss.date = :d2 AND sp.speech_index <= :i2))
           ORDER BY ss.date, sp.speech_index""",
        {"d1": start["sdate"], "i1": start["speech_index"],
         "d2": end["sdate"], "i2": end["speech_index"]}).fetchall()
    return [{
        "uid": r["uid"],
        "speaker": {"person_id": r["person_id"],
                    "label": r["person_label"] or r["speaker_label"],
                    "photo_uri": r["photo_uri"], "status": r["speaker_status"]},
        "faction": {"label": r["faction_label"], "color": r["faction_color"]}
                   if r["faction_label"] else None,
        "duration": r["duration"], "has_text": bool(r["has_text"]),
        "date": r["session_date"], "sitting": r["sitting"],
    } for r in rows]


def _debates_for(db: sqlite3.Connection, bill_id: str) -> list[dict]:
    """Reconstruct each plenary debate as the run of speeches between its
    opening and closing events (BILL-10). Events come in chronological order;
    a debate start is matched to the next event with its paired closing name.
    A bracket whose anchors don't both resolve to ingested speeches is skipped
    (graceful degradation, SCR-5)."""
    evs = db.execute(
        """SELECT e.ord, e.name, e.event_date, e.speech_number,
                  s.uid AS speech_uid, s.speech_index, ss.date AS sdate
           FROM bill_event e
           LEFT JOIN speech s ON s.uid = (
               SELECT s2.uid FROM speech s2 WHERE s2.speech_uuid = e.speech_id
               ORDER BY s2.speech_index LIMIT 1)
           LEFT JOIN session ss ON ss.id = s.session_id
           WHERE e.bill_id = ? ORDER BY e.ord""", (bill_id,)).fetchall()

    pending: dict[str, tuple] = {}   # closing-event name -> (start row, label, start name)
    debates: list[dict] = []
    for e in evs:
        info = _DEBATE_STARTS.get(e["name"])
        if info:
            pending[info["end"]] = (e, info["label"], e["name"])
            continue
        match = pending.pop(e["name"], None)
        if not match:
            continue
        start, label, start_name = match
        if start["speech_uid"] is None or e["speech_uid"] is None:
            continue
        speeches = _debate_speeches(db, start, e)
        if not speeches:
            continue
        debates.append({
            "label": label,
            "start_event": start_name, "end_event": e["name"],
            "start_date": start["event_date"], "end_date": e["event_date"],
            "start_speech_uid": start["speech_uid"],
            "end_speech_uid": e["speech_uid"],
            "speeches": speeches,
        })
    return debates


def _video_answer(db: sqlite3.Connection, bill_id: str) -> Optional[dict]:
    """The oral answer to a question-type iromány, with a per-speech video clip
    ready to embed (VIE-9). Resolves the first answer event (`_ANSWER_EVENTS`)
    to its plenary speech via the shared speech UUID, then crops the day stream
    to that speech. Returns ``None`` for a written-only answer (no speech) or a
    bill with no answer event — the bill page then shows no player."""
    ph = ",".join("?" * len(_ANSWER_EVENTS))
    row = db.execute(
        f"""SELECT e.name AS event_name, e.related_label, e.event_date,
                   s.uid, s.speaker_label, s.person_id, s.speaker_status,
                   s.video_start, s.video_end, s.has_text, s.duration,
                   p.label AS person_label, p.photo_uri,
                   f.label AS faction_label, f.color AS faction_color,
                   ss.video_uri AS day_uri, ss.video_playseq AS day_playseq,
                   ss.date AS session_date, ss.sitting
            FROM bill_event e
            JOIN speech s ON s.uid = (
                SELECT s2.uid FROM speech s2 WHERE s2.speech_uuid = e.speech_id
                ORDER BY s2.speech_index LIMIT 1)
            JOIN session ss ON ss.id = s.session_id
            LEFT JOIN person p ON p.person_id = s.person_id
            LEFT JOIN faction f ON f.id = s.faction_id
            WHERE e.bill_id = ? AND e.name IN ({ph})
            ORDER BY e.ord LIMIT 1""",
        (bill_id, *_ANSWER_EVENTS)).fetchone()
    if not row:
        return None
    clip = per_speech_clip(row["day_uri"], row["day_playseq"],
                           row["video_start"], row["video_end"])
    video_uri = clip["video_uri"] if clip else row["day_uri"]
    if not video_uri:
        return None
    return {
        "speech_uid": row["uid"],
        "event_name": row["event_name"],
        "responder_label": row["related_label"],
        "speaker": {"person_id": row["person_id"],
                    "label": row["person_label"] or row["speaker_label"],
                    "photo_uri": row["photo_uri"], "status": row["speaker_status"]},
        "faction": {"label": row["faction_label"], "color": row["faction_color"]}
                   if row["faction_label"] else None,
        "video_uri": video_uri,
        "video_playseq": clip["video_playseq"] if clip else row["day_playseq"],
        "using_clip": bool(clip),
        "has_text": bool(row["has_text"]),
        "duration": row["duration"],
        "date": row["session_date"], "sitting": row["sitting"],
    }


@router.get("/{bill_id}")
def get_bill(bill_id: str, db: sqlite3.Connection = Depends(get_db)):
    """A single bill with its full sponsor list and detail sections (events,
    votes, committees, deadlines, documents, non-self-standing motions)."""
    b = db.execute("SELECT * FROM bill WHERE id = ?", (bill_id,)).fetchone()
    if not b:
        raise HTTPException(404, "Bill not found")
    sponsors = _sponsors_for(db, [bill_id]).get(bill_id, [])

    events = _rows(db,
        """SELECT e.event_date, e.name, e.person_id, e.related_label,
                  e.speech_number, e.vote_id, e.remark, p.label AS person_name,
                  (SELECT s.uid FROM speech s WHERE s.speech_uuid = e.speech_id
                   ORDER BY s.speech_index LIMIT 1) AS speech_uid
           FROM bill_event e LEFT JOIN person p ON p.person_id = e.person_id
           WHERE e.bill_id = ? ORDER BY e.ord""", bill_id)
    committee_events = _rows(db,
        """SELECT c.event_date, c.name, c.committee, c.person_id, c.person_label,
                  c.amendment, c.overreaching_amendment, c.report,
                  p.label AS person_name
           FROM bill_committee_event c LEFT JOIN person p ON p.person_id = c.person_id
           WHERE c.bill_id = ? ORDER BY c.ord""", bill_id)
    # Each bill vote carries the upstream szavazasId (vote_id). Where the Votes
    # module has ingested that vote, resolve a `vote_ref` so the bill page can
    # link into the full roll call (EXT-2); otherwise it stays a plain tally.
    votes = _rows(db,
        """SELECT bv.vote_date, bv.subject, bv.yes, bv.no, bv.abstain, bv.result,
                  bv.vote_id,
                  (SELECT v.id FROM vote v WHERE v.id = bv.vote_id) AS vote_ref
           FROM bill_vote bv WHERE bv.bill_id = ? ORDER BY bv.ord""", bill_id)
    deadlines = _rows(db,
        "SELECT name, deadline, reference, remark FROM bill_deadline "
        "WHERE bill_id = ? ORDER BY ord", bill_id)
    committees = _rows(db,
        "SELECT committee, role, reference, parts FROM bill_committee "
        "WHERE bill_id = ? ORDER BY ord", bill_id)
    documents = _rows(db,
        "SELECT kind, title, url, doc_date, published FROM bill_document "
        "WHERE bill_id = ? ORDER BY ord", bill_id)
    motion_summary = _rows(db,
        "SELECT type, valid, withdrawn, total FROM bill_motion_summary "
        "WHERE bill_id = ? ORDER BY ord", bill_id)
    motions = _motions_for(db, bill_id)
    debates = _debates_for(db, bill_id)
    video_answer = _video_answer(db, bill_id)

    return {
        "id": b["id"], "bill_number": b["bill_number"], "title": b["title"],
        "type": b["type"], "main_type": b["main_type"], "status": b["status"],
        "submitted_date": b["submitted_date"], "text_url": b["text_url"],
        "text_caption": b["text_caption"], "source_url": b["source_url"],
        "no_text": bool(b["no_text"]), "period_number": b["period_number"],
        "stages": _stages(b["stages_json"]),
        "sponsors": sponsors,
        # extra header fields from the detail sheet
        "subtype": b["subtype"], "character": b["character"],
        "negotiation_mode": b["negotiation_mode"], "status_type": b["status_type"],
        "current_event": b["current_event"],
        "promulgation_number": b["promulgation_number"], "mk_number": b["mk_number"],
        "promulgation_date": b["promulgation_date"], "remark": b["remark"],
        "last_modifier": b["last_modifier"],
        "kozlony_url": b["kozlony_url"], "kozlony_doc_url": b["kozlony_doc_url"],
        # detail sections
        "events": events, "committee_events": committee_events, "votes": votes,
        "deadlines": deadlines, "committees": committees, "documents": documents,
        "motion_summary": motion_summary, "motions": motions,
        "debates": debates, "video_answer": video_answer,
    }
