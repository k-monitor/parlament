"""Proceedings module API (§5) — search + viewer, under /api/v1/proceedings.

A self-contained vertical slice (EXT-1): it owns these routes and reads only the
proceedings tables plus the shared core entities (person, faction, session).
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ...config import settings
from ...db import get_db
from ...media import per_speech_clip
from ...search import build_match
from ...wordfreq import count_words, tfidf_scores

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

def _search_where(q, date_from, date_to, period, person_id, faction_id, agenda_type):
    """Build the shared FTS-match + filter clause for the search endpoints.

    `/search`, `/search/trend` (SEA-8) and `/search/breakdown` (SEA-9) all
    describe the *same* result set, so they apply an identical WHERE — only the
    projection/grouping differs. Returns ``(where_sql, params)``; raises 400 when
    the query holds no searchable terms."""
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
    return " AND ".join(where), params


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
    where_sql, params = _search_where(q, date_from, date_to, period,
                                      person_id, faction_id, agenda_type)

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
        "match": params["match"],
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

    The interval adapts to the span so the chart always has enough thin bars to
    read as a histogram — daily for a few months, weekly for a couple of years,
    monthly across the full multi-decade corpus. Only the periods that actually
    have hits are returned; the client fills the gaps with zeros so the timeline
    is continuous and honest."""
    where_sql, params = _search_where(q, date_from, date_to, period,
                                      person_id, faction_id, agenda_type)

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

    # Pick the interval from the span so the histogram stays dense at every
    # zoom: daily up to a few months, weekly up to a couple of years, monthly
    # beyond. Each `period_expr` yields a sortable key the client can step over.
    days = (date.fromisoformat(span["hi"]) - date.fromisoformat(span["lo"])).days + 1
    if days <= 120:
        granularity = "day"
        period_expr = "strftime('%Y-%m-%d', ss.date)"
    elif days <= 900:
        granularity = "week"  # key = the Monday of each week
        period_expr = "date(ss.date, '-' || ((strftime('%w', ss.date) + 6) % 7) || ' days')"
    else:
        granularity = "month"
        period_expr = "strftime('%Y-%m', ss.date)"

    rows = db.execute(
        f"""SELECT {period_expr} AS period, COUNT(*) AS hits
            {base_from} WHERE {where_sql}
            GROUP BY period ORDER BY period""",
        params).fetchall()
    return {
        "query": q,
        "granularity": granularity,
        "buckets": [{"period": r["period"], "hits": r["hits"]} for r in rows],
    }


@router.get("/search/breakdown")
def search_breakdown(
    q: str = Query(..., min_length=1, description="Free-text query; \"…\" = exact phrase"),
    date_from: Optional[str] = Query(None, description="ISO date lower bound"),
    date_to: Optional[str] = Query(None, description="ISO date upper bound"),
    period: Optional[int] = Query(None, description="Electoral period number"),
    person_id: Optional[str] = None,
    faction_id: Optional[int] = None,
    agenda_type: Optional[str] = None,
    limit: int = Query(12, ge=1, le=50, description="Top-N rows per group"),
    db: sqlite3.Connection = Depends(get_db),
):
    """Who a query's matches come from (SEA-9): matching-sentence counts grouped
    by faction and by representative, honouring the *same* filters as `/search`
    so the chart describes the very result set being browsed.

    Each group is a top-N (the busiest factions/speakers) computed over **all**
    matches, not just the current page; it is a separate aggregate so it never
    slows the result list. Factions carry their consistent colour and each
    representative its `person_id` so the UI can link to the profile (REP-1).
    Speeches with no resolved representative are not attributed to a person row;
    those with no faction are not attributed to a faction row."""
    where_sql, params = _search_where(q, date_from, date_to, period,
                                      person_id, faction_id, agenda_type)

    base_from = """
        FROM sentence_fts
        JOIN sentence se ON se.id = sentence_fts.rowid
        JOIN speech sp ON sp.uid = se.speech_id
        JOIN session ss ON ss.id = sp.session_id
        LEFT JOIN agenda_item ai ON ai.id = sp.agenda_item_id
        LEFT JOIN person p ON p.person_id = sp.person_id
        LEFT JOIN faction f ON f.id = sp.faction_id
    """

    factions = db.execute(
        f"""SELECT f.id AS faction_id, f.label, f.color, COUNT(*) AS hits
            {base_from} WHERE {where_sql} AND sp.faction_id IS NOT NULL
            GROUP BY f.id ORDER BY hits DESC, f.label LIMIT :limit""",
        {**params, "limit": limit}).fetchall()

    speakers = db.execute(
        f"""SELECT sp.person_id, p.label, p.photo_uri, COUNT(*) AS hits
            {base_from} WHERE {where_sql} AND sp.person_id IS NOT NULL
            GROUP BY sp.person_id ORDER BY hits DESC, p.label LIMIT :limit""",
        {**params, "limit": limit}).fetchall()

    return {
        "query": q,
        "factions": [
            {"faction_id": r["faction_id"], "label": r["label"],
             "color": r["color"], "hits": r["hits"]}
            for r in factions
        ],
        "speakers": [
            {"person_id": r["person_id"], "label": r["label"],
             "photo_uri": r["photo_uri"], "hits": r["hits"]}
            for r in speakers
        ],
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


# A word must be said at least this often on the day to qualify, so a single
# rare token (a name, a typo) cannot top the cloud on IDF alone.
_WORDCLOUD_MIN_TF = 2


@router.get("/sessions/{session_id}/wordcloud")
def session_wordcloud(session_id: str, limit: int = Query(80, ge=1, le=200),
                      db: sqlite3.Connection = Depends(get_db)):
    """Word cloud for a sitting day, ranked by what is *distinctive* to it (WCLOUD).

    Built over the sitting's non-procedural sentence text, lemmatized with HuSpaCy
    (inflected forms collapsed to their dictionary form) and with named entities
    kept as single multi-word terms — all precomputed at load time into
    `session_word_count`, with Hungarian stop-words and very short tokens removed
    (WCLOUD-2). Words are scored by TF·IDF against the rest of the electoral cycle
    (precomputed `word_doc_freq`), so a word frequent on this day but rare on other
    days ranks high while the ubiquitous parliamentary vocabulary that appears
    every day is suppressed — surfacing the day's actual topics. `count` is the raw
    occurrences (shown to the user); `weight` is the TF·IDF score the cloud sizes
    by; `kind` is `entity` for a recognized named entity, else `term`. A separate,
    lightweight request so it never slows the sitting-day load (WCLOUD-5)."""
    s = db.execute("SELECT id, date, period_number FROM session WHERE id = ?",
                   (session_id,)).fetchone()
    if not s:
        raise HTTPException(404, "Session not found")

    tf, kinds = _session_term_freqs(db, session_id)
    if not tf:
        return {"session_id": session_id, "date": s["date"], "words": []}

    # Candidates: words said at least twice; relax to all words on a sparse day
    # so a short sitting still yields a cloud.
    candidates = [w for w, c in tf.items() if c >= _WORDCLOUD_MIN_TF]
    if len(candidates) < limit:
        candidates = list(tf.keys())

    df, n_docs = _doc_freqs(db, s["period_number"], candidates)
    if n_docs and df:                       # TF·IDF: distinctive-to-this-day
        scores = tfidf_scores({w: tf[w] for w in candidates}, df, n_docs)
    else:                                   # no corpus stats → raw frequency
        scores = {w: float(tf[w]) for w in candidates}

    top = sorted(candidates, key=lambda w: (-scores[w], w))[:limit]
    return {
        "session_id": session_id,
        "date": s["date"],
        "words": [{"text": w, "count": tf[w], "weight": round(scores[w], 4),
                   "kind": kinds.get(w, "term")}
                  for w in top],
    }


def _session_term_freqs(db, session_id):
    """The sitting's precomputed term frequencies + each term's kind.

    Reads ``session_word_count`` (lemmatized / entity-aware, built at load time).
    Falls back to live regex tokenization of the day's sentences when the table is
    absent (a pre-migration DB) so the endpoint still works on an old build."""
    try:
        rows = db.execute(
            "SELECT word, count, kind FROM session_word_count WHERE session_id = ?",
            (session_id,)).fetchall()
    except sqlite3.OperationalError:
        rows = None
    # Use precomputed rows when present. An empty result falls through to live
    # tokenization: that covers both a pre-migration DB (no table) and a sitting
    # not yet processed (e.g. a long lemmatization pass still in flight), so the
    # cloud degrades to raw forms rather than rendering empty.
    if rows:
        tf = {r["word"]: r["count"] for r in rows}
        kinds = {r["word"]: r["kind"] for r in rows}
        return tf, kinds
    sents = db.execute(
        """SELECT se.text FROM sentence se
           JOIN speech sp ON sp.uid = se.speech_id
           WHERE sp.session_id = ? AND sp.procedural = 0""",
        (session_id,)).fetchall()
    return dict(count_words(r["text"] for r in sents)), {}


@router.get("/sessions/{session_id}/top-speakers")
def session_top_speakers(session_id: str, limit: int = Query(10, ge=1, le=50),
                         db: sqlite3.Connection = Depends(get_db)):
    """Representatives who spoke the most on a sitting day (TOPSPK).

    Ranked by total speaking time (sum of speech durations), with the speech
    count alongside. Only statistics-eligible speeches count — procedural /
    chairing speeches (STAT-1) are excluded, like the per-MP statistics and the
    word cloud — and only known representatives (resolved ``person_id``) are
    listed (TOPSPK-2). A separate, lightweight request so it never slows the
    sitting-day load (TOPSPK-5)."""
    s = db.execute("SELECT id, date FROM session WHERE id = ?",
                   (session_id,)).fetchone()
    if not s:
        raise HTTPException(404, "Session not found")
    rows = db.execute(
        """SELECT sp.person_id, sp.speaker_status,
                  p.label AS person_label, p.photo_uri,
                  f.label AS faction_label, f.color AS faction_color,
                  COUNT(*) AS speeches,
                  COALESCE(SUM(sp.duration), 0) AS seconds
           FROM speech sp
           LEFT JOIN person p ON p.person_id = sp.person_id
           LEFT JOIN faction f ON f.id = sp.faction_id
           WHERE sp.session_id = ? AND sp.procedural = 0
                 AND sp.person_id IS NOT NULL
           GROUP BY sp.person_id
           ORDER BY seconds DESC, speeches DESC, person_label
           LIMIT ?""",
        (session_id, limit)).fetchall()
    return {
        "session_id": session_id,
        "date": s["date"],
        "speakers": [
            {
                "person_id": r["person_id"],
                "label": r["person_label"],
                "photo_uri": r["photo_uri"],
                "status": r["speaker_status"],
                "faction": {"label": r["faction_label"], "color": r["faction_color"]}
                           if r["faction_label"] else None,
                "speeches": r["speeches"],
                "seconds": r["seconds"],
            }
            for r in rows
        ],
    }


# A "new word" must be a single clean lexical token: any of these punctuation
# marks makes it a compound / abbreviation / hyphenated artefact (`rtl-es`, `dr.`,
# `elmesélte‑e`) rather than a genuine new word. Covers the ASCII forms the user
# named (`/ - . '`) plus the typographic hyphen/dash/apostrophe variants HuSpaCy
# lemmas actually contain.
_NEW_WORD_BANNED_CHARS = frozenset("/-.'’‘‑–—")


@router.get("/sessions/{session_id}/new-words")
def session_new_words(session_id: str, limit: int = Query(80, ge=1, le=400),
                      db: sqlite3.Connection = Depends(get_db)):
    """Words that *debuted* on a sitting day — never spoken before in parliament
    (NEW-1).

    A word counts as new on this day when this is the earliest sitting day (by
    date) on which it was ever said, across **all** electoral cycles, previous
    ones included. The unit is a HuSpaCy lemma (inflected forms collapsed), taken
    from the same precomputed, stop-word-filtered `session_word_count` as the word
    cloud and restricted to non-procedural speeches (STAT-1); first-appearance is
    precomputed into `word_first_seen`. **Named entities, capitalized words and
    words containing punctuation (`/ - . '` and typographic variants) are
    excluded** — a "new" proper noun is almost always just a name/place that
    happens not to have come up before, and a punctuated token (`rtl-es`,
    `elmesélte‑e`, `dr.`) is a compound/abbreviation artefact rather than a genuine
    new word — so only clean lower-case common terms remain. `count` is how many
    times the word was said on its debut day; ranked by count so words that
    arrived and were actually discussed lead over one-off mentions. A separate,
    lightweight request so it never slows the sitting-day load.

    Note the novelty is only ever relative to the transcripts loaded: the very
    earliest sitting in the corpus will show almost all of its words as "new"."""
    s = db.execute("SELECT id, date FROM session WHERE id = ?",
                   (session_id,)).fetchone()
    if not s:
        raise HTTPException(404, "Session not found")
    try:
        # Drop named entities here; drop capitalized / punctuated lemmas in Python
        # (SQLite's upper/lower is ASCII-only and would misjudge Hungarian accented
        # capitals like "Á"/"Ő"). Fetch all qualifying rows (a day has at most a
        # couple thousand) so the limit applies *after* those filters.
        rows = db.execute(
            """SELECT w.word, w.kind, swc.count
               FROM word_first_seen w
               JOIN session_word_count swc
                 ON swc.session_id = w.session_id AND swc.word = w.word
               WHERE w.session_id = ? AND w.kind != 'entity'
               ORDER BY swc.count DESC, w.word""",
            (session_id,)).fetchall()
    except sqlite3.OperationalError:
        rows = []          # pre-migration DB without word_first_seen
    words = [{"text": r["word"], "count": r["count"], "kind": r["kind"]}
             for r in rows
             if not r["word"][:1].isupper()
             and not (_NEW_WORD_BANNED_CHARS & set(r["word"]))][:limit]
    return {"session_id": session_id, "date": s["date"], "words": words}


def _doc_freqs(db, period, words):
    """Per-period document frequencies for ``words`` + the period's day count.

    Returns ``({}, 0)`` when the corpus table is absent (older DB) or the period
    has no precomputed stats, so the endpoint falls back to raw frequency."""
    if period is None:
        return {}, 0
    try:
        tot = db.execute("SELECT n_docs FROM word_doc_total WHERE period_number = ?",
                         (period,)).fetchone()
    except sqlite3.OperationalError:
        return {}, 0          # table not in this (pre-migration) DB
    if not tot:
        return {}, 0
    df: dict = {}
    words = list(words)
    for i in range(0, len(words), 800):     # stay well under SQLite's var limit
        chunk = words[i:i + 800]
        ph = ",".join("?" * len(chunk))
        for r in db.execute(
                f"SELECT word, doc_count FROM word_doc_freq "
                f"WHERE period_number = ? AND word IN ({ph})",
                (period, *chunk)):
            df[r["word"]] = r["doc_count"]
    return df, tot["n_docs"]


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
