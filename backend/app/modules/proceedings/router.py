"""Proceedings module API (§5) — search + viewer, under /api/v1/proceedings.

A self-contained vertical slice (EXT-1): it owns these routes and reads only the
proceedings tables plus the shared core entities (person, faction, session).
"""

from __future__ import annotations

import html
import json
import sqlite3
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ...analytics import search_analytics
from ...config import settings
from ...db import (QueryBudgetExceeded, get_db, like_contains, period_and,
                   period_in_scope, period_key, period_list, period_sql,
                   query_budget, sentence_id_range)
from ...media import per_speech_clip
from ...nlp import LINKABLE_LABELS
# How completely a held day is published (SIT-2). Shared with the Bluesky
# announcer, whose "fully processed" MUST mean what the site's badge means.
from ...publication import processing_state
from ...query_cache import cached_aggregate
from ... import parlacap, readability
from ...search import build_match
from ...wordfreq import count_words, tfidf_scores

router = APIRouter(prefix="/proceedings", tags=["proceedings"])


def _is_estimated(align_method: str | None) -> bool:
    """Whether sentence timing is an estimate the UI should disclose (VIE-6).

    The positional methods estimate the *sentence* position (whole-day, or within a
    speech's real offsets); only a word-precise method — Whisper forced alignment —
    is exact and needs no disclosure."""
    return (align_method or "") not in (
        "", "none", "forced-alignment", "whisper-forced-alignment")


# ---------------------------------------------------------------------------
# Search (SEA-*)
# ---------------------------------------------------------------------------

def _mark_html(s: str | None) -> str | None:
    """Make FTS5 highlight/snippet output safe for the SPA's ``v-html``: the
    match markers are emitted as control-char sentinels (which cannot occur in
    transcript text), the sentence text is HTML-escaped, and only then do the
    sentinels become ``<mark>`` tags — so a transcript containing ``<`` or
    scraped markup renders literally instead of as HTML (stored-XSS surface)."""
    if s is None:
        return None
    return (html.escape(s, quote=False)
            .replace("\x02", "<mark>").replace("\x03", "</mark>"))


# How many transcript sentences of surrounding context to attach to each search
# hit on either side (SEA-4). Kept small so the result list stays scannable and
# the per-page context fetch stays cheap; the window spills into the neighbouring
# speech when the match sits at a speech boundary, so context is drawn from the
# speeches *before/after*, not only the one the hit lands in.
_SEARCH_CONTEXT_WINDOW = 2

# Whitelisted result orderings (SEA-10). The value is spliced straight into the
# ORDER BY, so it MUST come from this map — never from the raw request — and each
# tie-breaks down to the sentence so paging is stable. `relevance` is the bm25
# rank (best first); the date orderings read the sitting date then reading order.
_SEARCH_SORTS = {
    "relevance": "rank",
    "date_desc": "ss.date DESC, sp.speech_index DESC, se.ord DESC",
    "date_asc": "ss.date ASC, sp.speech_index ASC, se.ord ASC",
}


def _context_side(db, session_id, speech_index, ord, direction, spill=True):
    """Up to ``_SEARCH_CONTEXT_WINDOW`` transcript sentences flanking a matched
    sentence on one side, in reading order (SEA-4).

    ``direction`` is ``+1`` (the sentences *after* the hit) or ``-1`` (*before*).
    The walk starts inside the hit's own speech and, when the hit sits at a speech
    boundary, spills into the neighbouring speech(es) — so the context genuinely
    comes "from the speeches before/after", not just the one the hit is in. Each
    sentence carries its speaker so the UI can mark where the speaker changes.

    ``spill=False`` keeps the walk inside the one speech: a filter-only result is
    a *speech* shown by its opening (see ``_search_where``), and running on into
    the next speaker would be padding its preview with somebody else's words."""
    cmp, order = (">", "ASC") if direction > 0 else ("<", "DESC")
    out: list[dict] = []
    idx, cursor = speech_index, ord
    while len(out) < _SEARCH_CONTEXT_WINDOW:
        rows = db.execute(
            f"""SELECT se.text, sp.person_id,
                       COALESCE(p.label, sp.speaker_label) AS speaker
                FROM sentence se
                JOIN speech sp ON sp.uid = se.speech_id
                LEFT JOIN person p ON p.person_id = sp.person_id
                WHERE sp.session_id = :sid AND sp.speech_index = :idx
                      AND se.ord {cmp} :cursor
                ORDER BY se.ord {order} LIMIT :need""",
            {"sid": session_id, "idx": idx, "cursor": cursor,
             "need": _SEARCH_CONTEXT_WINDOW - len(out)}).fetchall()
        out.extend({"text": r["text"], "speaker": r["speaker"],
                    "person_id": r["person_id"]} for r in rows)
        if len(out) >= _SEARCH_CONTEXT_WINDOW or not spill:
            break
        # Window not full — hop to the adjacent speech and keep going from its edge.
        nb = db.execute(
            f"""SELECT speech_index FROM speech
                WHERE session_id = :sid AND speech_index {cmp} :idx
                ORDER BY speech_index {order} LIMIT 1""",
            {"sid": session_id, "idx": idx}).fetchone()
        if not nb:
            break
        idx = nb["speech_index"]
        cursor = -1 if direction > 0 else 1 << 62   # start at the new speech's edge
    if direction < 0:
        out.reverse()                               # collected outward → reading order
    return out


def _search_where(db, q, date_from, date_to, period, person_id, faction_id,
                  agenda_type):
    """Build the shared FTS-match + filter clause for the search endpoints.

    `/search`, `/search/trend` (SEA-8) and `/search/breakdown` (SEA-9) all
    describe the *same* result set, so they apply an identical WHERE — only the
    projection/grouping differs. Returns ``(where_sql, params, needs)`` where
    ``needs`` names the optional joins the filters reference (``'speech'`` for a
    ``sp.*`` filter, ``'session'`` for a date filter, ``'agenda'`` for an
    agenda-type filter) so a caller can assemble a minimal FROM.

    With **no searchable term** the search is filter-only: a speaker's speeches,
    listed without a keyword (SEA-3). There is then no doclist to match, so the
    unit of a result changes from the matched sentence to the **speech**, stood
    for by its first sentence (``se.ord = 0``) — "everything they said" is a list
    of speeches, not one card per sentence inside them. Only a **speaker** may
    stand alone like that: it is the one filter narrow enough to bound the scan
    (even the most prolific MP has a few thousand speeches), where a bare faction
    or agenda-type filter would be a read of the whole corpus. Callers tell the
    two modes apart by whether ``params`` carries a ``match``. Raises 400 when
    neither a usable term nor a speaker is given."""
    match = build_match(q) if q else None
    where: list[str] = []
    params: dict = {}
    needs: set[str] = set()
    if match:
        where.append("sentence_fts MATCH :match")
        params["match"] = match
    elif person_id:
        where.append("se.ord = 0")   # one row per speech, not per sentence
        needs.add("speech")
    else:
        raise HTTPException(
            400, "Query contains no searchable terms — give a term, or pick a "
                 "speaker to list their speeches")
    if date_from:
        where.append("ss.date >= :date_from"); params["date_from"] = date_from; needs.add("session")
    if date_to:
        where.append("ss.date <= :date_to"); params["date_to"] = date_to; needs.add("session")
    per_sql = period_sql(period, "sp.period_number")
    if per_sql:
        where.append(per_sql); needs.add("speech")
        # Bound the same filter as a rowid range on the FTS index, where it can be
        # pushed into the doclist scan instead of costing a `sentence` + `speech`
        # row fetch per match (db.sentence_id_range; schema.sql). Added ALONGSIDE
        # the predicate above, never instead of it, so a loose or stale bound
        # costs speed and nothing else — and needs no join of its own, since
        # `sentence_fts` is always the FROM's first table.
        span = sentence_id_range(db, period) if match else None
        if span:
            where.append("sentence_fts.rowid BETWEEN :sid_lo AND :sid_hi")
            params["sid_lo"], params["sid_hi"] = span
    if person_id:
        where.append("sp.person_id = :person_id"); params["person_id"] = person_id; needs.add("speech")
    if faction_id is not None:
        where.append("sp.faction_id = :faction_id"); params["faction_id"] = faction_id; needs.add("speech")
    if agenda_type:
        where.append("ai.type = :agenda_type"); params["agenda_type"] = agenda_type; needs.add("agenda")
    return " AND ".join(where), params, needs


def _budgeted(db, run):
    """Run one search endpoint's whole body under the wall-clock budget,
    answering 503 rather than letting it hold a worker thread
    (``settings.search_timeout``).

    Wrapped around the *endpoint*, not around each statement: a search runs a
    count, a ranked page and a handful of per-hit context lookups, and a
    per-statement budget would let one request spend the full allowance several
    times over. One deadline covers the request.

    A search can be made arbitrarily expensive from the query string alone — a
    very short prefix term expands to millions of matches, and bm25 has to score
    every one of them to find the top twenty. `build_match`'s length floor and the
    cycle range above make the *reachable* worst case small, but neither is a
    bound: this is the bound. 503 + Retry-After is the honest answer — the query
    is valid and might well succeed with a narrower scope, so it is the origin
    declining the work, not the request being wrong."""
    try:
        with query_budget(db, settings.search_timeout):
            return run()
    except QueryBudgetExceeded:
        raise HTTPException(
            503,
            "Search took too long and was stopped. Try a longer or more specific "
            "term, or narrow the cycle/date range.",
            # Retrying the identical query costs the same and fails the same,
            # so the backoff is a real one rather than an invitation.
            headers={"Retry-After": "30"})


def _assemble_from(se: bool, speech: bool, session: bool, agenda: bool,
                   *, fts: bool = True) -> str:
    """Assemble the minimal FTS join chain for a search query. Each table bridges
    to the next through the previous one's key (``sentence_fts.rowid`` →
    ``sentence.id``; ``sentence.speech_id`` → ``speech.uid``; ``speech`` →
    ``session``/``agenda_item``), so session/agenda/speech joins all require the
    ``sentence`` row (``se``). Person/faction are never here: the WHERE never
    filters on them, so they are joined only against the ranked page (see
    ``search``), not across the whole match set.

    ``fts=False`` is the filter-only chain (see ``_search_where``): with no
    doclist to drive the scan the filters have to, so it starts at ``speech`` —
    where person, faction and cycle are all indexed — and reaches through it to
    the sentence that stands for each speech. Both tables are always in it, so
    ``se``/``speech`` are ignored in that mode."""
    if fts:
        parts = ["FROM sentence_fts"]
        if se:
            parts.append("JOIN sentence se ON se.id = sentence_fts.rowid")
        if speech:
            parts.append("JOIN speech sp ON sp.uid = se.speech_id")
    else:
        parts = ["FROM speech sp", "JOIN sentence se ON se.speech_id = sp.uid"]
    if session:
        parts.append("JOIN session ss ON ss.id = sp.session_id")
    if agenda:
        parts.append("LEFT JOIN agenda_item ai ON ai.id = sp.agenda_item_id")
    return "\n        ".join(parts)


def _aggregate_from(fts: bool, *, people: bool = False) -> str:
    """The whole join chain, for the two aggregates (SEA-8/SEA-9). They group over
    the entire match set rather than a page, so a minimal FROM buys them nothing
    and every table is joined; ``people`` adds the person/faction rows the
    breakdown groups by."""
    parts = [_assemble_from(True, True, True, True, fts=fts)]
    if people:
        parts.append("LEFT JOIN person p ON p.person_id = sp.person_id")
        parts.append("LEFT JOIN faction f ON f.id = sp.faction_id")
    return "\n        ".join(parts)


@router.get("/search")
def search(
    q: Optional[str] = Query(
        None, description="Free-text query; \"…\" = exact phrase. Optional when "
                          "`person_id` is given — the search then lists that "
                          "speaker's speeches"),
    date_from: Optional[str] = Query(None, description="ISO date lower bound"),
    date_to: Optional[str] = Query(None, description="ISO date upper bound"),
    period: Optional[List[int]] = Query(
        None, description="Electoral period number(s); repeat to scope to several cycles"),
    person_id: Optional[str] = None,
    faction_id: Optional[int] = None,
    agenda_type: Optional[str] = None,
    sort: str = Query("relevance", description="relevance | date_desc | date_asc"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
):
    """Sentence-level full-text search with combinable filters (SEA-1/3).

    Ordered by relevance by default, or by sitting date (SEA-10, `sort`). Each hit
    carries the matched sentence (with `<mark>` highlights), a few sentences of
    surrounding transcript context — spilling into the adjacent speeches at a
    speech boundary — speaker/faction/date/agenda metadata, and the timing needed
    to open the viewer at that moment (SEA-4).

    With no query but a `person_id`, it lists that speaker's speeches instead —
    one row per speech, shown by its opening (SEA-3)."""
    q = (q or "").strip()
    where_sql, params, needs = _search_where(db, q, date_from, date_to, period,
                                              person_id, faction_id, agenda_type)
    sort_name = sort if sort in _SEARCH_SORTS else "relevance"

    # The result list is the most-requested and most expensive thing the API
    # serves, and — unlike its trend/breakdown companions, which were already
    # memoized — it was recomputed in full every time. It is a pure function of
    # (query, filters, sort, page) over a read-only DB, and the SPA re-fires it
    # with an identical argument set on every page flip and every sort change, so
    # memoize the payload exactly as the aggregates are. `page` is part of the key
    # (unlike trend/breakdown, which are page-independent), which makes the key
    # space wider — paging through one search fills entries rather than reusing
    # one — so a busy deployment may want a larger PARLAMONITOR_QUERY_CACHE_SIZE
    # than the aggregates need.
    key = (q, date_from, date_to, period_key(period), person_id, faction_id,
           agenda_type, sort_name, limit, offset)
    payload = cached_aggregate("search", key, lambda: _budgeted(
        db, lambda: _search_compute(
            db, q, where_sql, params, needs, sort_name, limit, offset)))

    # Privacy-respecting analytics (PRIV-1): count this search — its keyword, its
    # filters, how many hits it found and which page was asked for — into the
    # current hour's aggregate. No IP / no exact timestamp; the request's network
    # metadata is never touched. Only `/search` is instrumented (not
    # trend/breakdown/suggest, which the SPA fires for the same query), so one
    # user search is one recorded event; the result the reader then opens is
    # counted onto this same bucket by the click ping (SEA-12, main.py).
    #
    # A filter-only search (no keyword) records nothing: the analytics table is
    # keyed on the keyword, and `record` drops an empty one — there is no term
    # whose ranking could regress.
    #
    # Deliberately OUTSIDE the cache above: this counts *searches people ran*, so
    # a repeat search must be counted again even when its payload was served from
    # memory. The figure recorded is the one the reader is shown and pages
    # through — already capped in the payload. Best-effort; never affects the
    # response.
    search_analytics.record(
        source="proceedings", query=q, date_from=date_from, date_to=date_to,
        # Multi-cycle scope is recorded as one canonical "43,44" key, so the same
        # selection always aggregates onto the same row.
        period=period_list(period),
        person_id=person_id, faction_id=faction_id, agenda_type=agenda_type,
        sort=sort_name, results=payload["total"], offset=offset)
    return payload


def _search_compute(db, q, where_sql, params, needs, sort_name, limit, offset):
    """Run one page of the search and build its payload (see ``search``)."""
    # Filter-only search (no term): rows are speeches stood for by their first
    # sentence, not matched sentences — see `_search_where`.
    fts = "match" in params
    if not fts and sort_name == "relevance":
        sort_name = "date_desc"      # nothing to rank: bm25 needs a match
    order_by = _SEARCH_SORTS[sort_name]
    is_relevance = order_by == "rank"

    # Joins the FILTERS reference. A date filter needs session, an agenda filter
    # agenda, any sp.* filter speech; session/agenda both reach through speech, and
    # all of them need the sentence row to bridge the FTS rowid → speech.
    f_speech = bool(needs)
    f_session = "session" in needs
    f_agenda = "agenda" in needs
    f_se = f_speech or f_session or f_agenda

    # Capped total: only the filters' joins matter (person/faction are never
    # filtered on), and the LIMIT stops the scan at the cap + 1.
    count_from = _assemble_from(f_se, f_speech, f_session, f_agenda, fts=fts)
    total_row = db.execute(
        f"SELECT COUNT(*) AS c FROM (SELECT 1 {count_from} WHERE {where_sql} "
        f"LIMIT {settings.max_search_total + 1})", params).fetchone()
    total = total_row["c"]
    capped = total > settings.max_search_total

    # Rank + page over the FTS/filter joins ALONE, then join the <=`limit`
    # survivors for their display metadata. A pure-relevance, unfiltered search
    # never touches speech/session in this phase — the win is not scanning the
    # whole match set through those joins just to return one page (measured
    # ~2.3x on common terms). highlight()/snippet() live in the CTE because they
    # need the FTS MATCH context; there SQLite computes them for the page only,
    # not for every match. A date sort needs session/speech in the ranking phase
    # for its sort key; relevance needs neither. Results are byte-identical to
    # the pre-CTE query across every sort/filter/paging combination.
    c_speech = f_speech or not is_relevance
    c_session = f_session or not is_relevance
    c_agenda = f_agenda
    c_se = c_speech or c_session or c_agenda
    hits_from = _assemble_from(c_se, c_speech, c_session, c_agenda, fts=fts)
    rank_col = ", bm25(sentence_fts) AS rank" if is_relevance else ""
    outer_order = "hits.rank" if is_relevance else order_by
    # Nothing is highlighted in the filter-only mode: the sentence is not a match
    # but the speech's opening line, and it is shown as it was said.
    hit_cols = (f"""sentence_fts.rowid AS sid,
                   highlight(sentence_fts, 0, char(2), char(3)) AS hl,
                   snippet(sentence_fts, 0, char(2), char(3), '…', 18) AS sn{rank_col}"""
                if fts else "se.id AS sid, se.text AS hl, NULL AS sn")

    rows = db.execute(
        f"""
        WITH hits AS (
            SELECT {hit_cols}
            {hits_from}
            WHERE {where_sql}
            ORDER BY {order_by}
            LIMIT :limit OFFSET :offset
        )
        SELECT se.id AS sentence_id, se.ord AS sentence_ord, se.time_start,
               se.time_end,
               hits.hl AS highlighted,
               hits.sn AS snippet,
               sp.uid AS speech_uid, sp.origin_id, sp.speaker_label,
               sp.person_id, sp.confidence, sp.align_method, sp.speech_index,
               ai.title AS agenda_title, ai.type AS agenda_type,
               ss.id AS session_id, ss.date, ss.sitting, sp.period_number,
               p.label AS person_label, p.photo_uri,
               f.label AS faction_label, f.color AS faction_color
        FROM hits
        JOIN sentence se ON se.id = hits.sid
        JOIN speech sp ON sp.uid = se.speech_id
        JOIN session ss ON ss.id = sp.session_id
        LEFT JOIN agenda_item ai ON ai.id = sp.agenda_item_id
        LEFT JOIN person p ON p.person_id = sp.person_id
        LEFT JOIN faction f ON f.id = sp.faction_id
        ORDER BY {outer_order}
        """,
        {**params, "limit": limit, "offset": offset}).fetchall()

    return {
        # A filter-only search has no keyword to echo, and the UI reads this to
        # tell the two kinds of result list apart.
        "query": q if fts else "",
        "match": params.get("match"),
        "sort": sort_name,
        "total": min(total, settings.max_search_total),
        "total_is_capped": capped,
        "limit": limit,
        "offset": offset,
        "results": [
            {
                "sentence_id": r["sentence_id"],
                "sentence_ord": r["sentence_ord"],
                "highlighted": _mark_html(r["highlighted"]),
                "snippet": _mark_html(r["snippet"]),
                "context": {
                    # A speech shown by its opening has no "before" that belongs
                    # to it — what precedes it is the previous speaker — and its
                    # preview stops at the end of the speech (`spill`).
                    "before": _context_side(db, r["session_id"], r["speech_index"],
                                            r["sentence_ord"], -1) if fts else [],
                    "after": _context_side(db, r["session_id"], r["speech_index"],
                                           r["sentence_ord"], +1, spill=fts),
                },
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


# The electoral mandate is four years (Fundamental Law) — the widest an ongoing
# cycle's trend axis ever grows.
_TERM_YEARS = 4


def _plus_years(iso: str, n: int) -> str:
    """ISO date `n` years later, clamping Feb 29 → Feb 28 in non-leap years."""
    d = date.fromisoformat(iso)
    try:
        return d.replace(year=d.year + n).isoformat()
    except ValueError:  # 29 Feb landing on a common year
        return d.replace(year=d.year + n, day=28).isoformat()


def _ongoing_axis_end(start_iso: str, today_iso: str) -> str:
    """Synthesised end date for the *ongoing* cycle's trend axis (it has no real
    end yet). The window is a whole number of years wide — enough to cover the time
    elapsed so far, at least one year and at most the full mandate. So a young cycle
    shows a ~1-year axis with its data left-aligned and room to grow, and the window
    widens a year at a time as the cycle ages rather than sitting mostly empty."""
    elapsed = (date.fromisoformat(today_iso) - date.fromisoformat(start_iso)).days
    years = min(_TERM_YEARS, max(1, -(-elapsed // 365)))  # ceil(elapsed/365), 1..term
    return _plus_years(start_iso, years)


@router.get("/search/trend")
def search_trend(
    q: Optional[str] = Query(
        None, description="Free-text query; \"…\" = exact phrase. Optional when "
                          "`person_id` is given — the search then lists that "
                          "speaker's speeches"),
    date_from: Optional[str] = Query(None, description="ISO date lower bound"),
    date_to: Optional[str] = Query(None, description="ISO date upper bound"),
    period: Optional[List[int]] = Query(
        None, description="Electoral period number(s); repeat to scope to several cycles"),
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
    q = (q or "").strip()
    where_sql, params, _needs = _search_where(db, q, date_from, date_to, period,
                                               person_id, faction_id, agenda_type)

    # This aggregate re-scans the whole match set on every call and the SPA fires
    # it alongside every search (identical across pagination), so memoize the
    # payload per (query+filters) with a short TTL, invalidated on DB swap. The
    # ongoing cycle's axis end is anchored to *today* (_ongoing_axis_end), so today
    # is part of the key — the axis is never stale by more than a day (and TTL
    # keeps it far fresher). The 400 for an empty query is raised above the cache.
    key = (q, date_from, date_to, period_key(period), person_id, faction_id,
           agenda_type, date.today().isoformat())
    return cached_aggregate("search_trend", key, lambda: _budgeted(
        db, lambda: _search_trend_compute(
            db, q, where_sql, params, date_from, date_to, period)))


def _search_trend_compute(db, q, where_sql, params, date_from, date_to, period):
    fts = "match" in params
    base_from = _aggregate_from(fts)
    q = q if fts else ""      # filter-only: speeches per bucket, no keyword

    span = db.execute(
        f"SELECT MIN(ss.date) AS lo, MAX(ss.date) AS hi {base_from} WHERE {where_sql}",
        params).fetchone()
    if not span or not span["lo"]:
        return {"query": q, "granularity": "month", "buckets": []}

    # Anchor the chart's time axis to the *scope*, not to this query's own first
    # and last hit, so every card sharing a scope draws one identical timeline
    # (otherwise a rare term and a common one show visibly different axes). With
    # cycles in scope the axis spans them end to end: a finished cycle runs
    # cycle-start → cycle-end; the ongoing one runs cycle-start → a synthesised end
    # that grows a year at a time (see `_ongoing_axis_end`), so its data sits
    # left-aligned with the not-yet-happened remainder empty on the right and the
    # axis widens as the term progresses. Several cycles selected at once give one
    # continuous axis from the earliest start to the latest end — including the gap
    # between two non-adjacent cycles, which is honest: nothing was said there *in
    # scope*. The all-cycles view runs earliest-sitting → today. Explicit date
    # filters tighten these bounds, and real hits are never clipped.
    today = date.today().isoformat()
    nums = period_list(period)
    if nums:
        prows = db.execute(
            "SELECT date_start, date_end FROM electoral_period "
            f"WHERE {period_sql(nums, 'number')}").fetchall()
        starts = [r["date_start"] for r in prows if r["date_start"]]
        ends = [min(r["date_end"], today) if r["date_end"]
                else _ongoing_axis_end(r["date_start"], today)  # ongoing → growing
                for r in prows if r["date_start"] or r["date_end"]]
        dom_lo = min(starts) if starts else None
        dom_hi = max(ends) if ends else None
    else:
        dom_lo = db.execute("SELECT MIN(date) AS lo FROM session").fetchone()["lo"]
        dom_hi = today
    if date_from:
        dom_lo = max(dom_lo, date_from) if dom_lo else date_from
    if date_to:
        dom_hi = min(dom_hi, date_to) if dom_hi else date_to
    lo = min(dom_lo, span["lo"]) if dom_lo else span["lo"]
    hi = max(dom_hi, span["hi"]) if dom_hi else span["hi"]

    # Pick the interval from the (anchored) span so the histogram stays dense at
    # every zoom: daily up to a few months, weekly up to a couple of years,
    # monthly beyond. Each `period_expr` yields a sortable key the client steps over.
    days = (date.fromisoformat(hi) - date.fromisoformat(lo)).days + 1
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
        "start": lo,   # axis domain (scope-anchored) so sibling charts align
        "end": hi,
        "buckets": [{"period": r["period"], "hits": r["hits"]} for r in rows],
    }


@router.get("/search/breakdown")
def search_breakdown(
    q: Optional[str] = Query(
        None, description="Free-text query; \"…\" = exact phrase. Optional when "
                          "`person_id` is given — the search then lists that "
                          "speaker's speeches"),
    date_from: Optional[str] = Query(None, description="ISO date lower bound"),
    date_to: Optional[str] = Query(None, description="ISO date upper bound"),
    period: Optional[List[int]] = Query(
        None, description="Electoral period number(s); repeat to scope to several cycles"),
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
    q = (q or "").strip()
    where_sql, params, _needs = _search_where(db, q, date_from, date_to, period,
                                               person_id, faction_id, agenda_type)

    # Two grouped top-N over the whole match set, fired alongside every search and
    # identical across pagination — memoize per (query+filters+limit), invalidated
    # on DB swap. The 400 for an empty query is raised above, before the cache.
    key = (q, date_from, date_to, period_key(period), person_id, faction_id,
           agenda_type, limit)
    return cached_aggregate("search_breakdown", key, lambda: _budgeted(
        db, lambda: _search_breakdown_compute(db, q, where_sql, params, limit)))


def _search_breakdown_compute(db, q, where_sql, params, limit):
    fts = "match" in params
    base_from = _aggregate_from(fts, people=True)
    q = q if fts else ""

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
    like = like_contains(q.strip())
    # Anyone who actually spoke in plenary is suggestible, not just MPs: nationality
    # advocates (szószólók, REP-9) speak too, as do non-MP ministers and state
    # secretaries (REP-12) — leaving them out means filtering the transcript by a
    # minister silently finds nothing. "Has spoken" is `speeches > 0` below rather
    # than a mandate flag, which also keeps the office-holder registry's people
    # (REP-11, most of whom never spoke here) out of the suggestions.
    spoke = "COALESCE(ps.speech_count, 0) > 0"
    mandate = (f"(p.is_mp = 1 OR p.is_advocate = 1 OR {spoke})"
               if any(r["name"] == "is_advocate"
                      for r in db.execute("PRAGMA table_info(person)"))
               else f"(p.is_mp = 1 OR {spoke})")
    # Rank name matches by activity using the precomputed all-periods aggregate
    # (person_stats, period_number IS NULL) instead of a correlated COUNT over
    # `speech` per matched person — this endpoint fires on every keystroke. The
    # aggregate counts statistics-eligible speeches (procedural/chairing excluded,
    # STAT-1), which is the same "how active a speaker" signal the profile shows.
    people = db.execute(
        """SELECT p.person_id, p.label, p.photo_uri,
                  COALESCE(ps.speech_count, 0) AS speeches
           FROM person p
           LEFT JOIN person_stats ps
                  ON ps.person_id = p.person_id AND ps.period_number IS NULL
           WHERE """ + mandate + """ AND fold(p.label) LIKE fold(:like) ESCAPE '\\'
           ORDER BY speeches DESC LIMIT :limit""",
        {"like": like, "limit": limit}).fetchall()
    factions = db.execute(
        "SELECT id, label, color FROM faction "
        "WHERE fold(label) LIKE fold(:like) ESCAPE '\\' LIMIT :limit",
        {"like": like, "limit": limit}).fetchall()
    return {
        "speakers": [dict(r) for r in people],
        "factions": [dict(r) for r in factions],
    }


# ---------------------------------------------------------------------------
# Sittings list / browse (use case 2)
# ---------------------------------------------------------------------------

@router.get("/sessions")
def list_sessions(period: Optional[List[int]] = Query(
                      None, description="Electoral period number(s)"),
                  limit: int = Query(50, ge=1, le=200),
                  offset: int = Query(0, ge=0),
                  db: sqlite3.Connection = Depends(get_db)):
    per_sql = period_sql(period, "s.period_number")
    where = f"WHERE {per_sql}" if per_sql else ""
    params: dict = {}
    total = db.execute(
        f"SELECT COUNT(*) AS c FROM session s {where}", params).fetchone()["c"]
    # `status` distinguishes an announced upcoming sitting ('scheduled') from a held
    # one ('published'); COALESCE keeps a pre-migration DB (default column value not
    # yet backfilled) reporting 'published' rather than NULL.
    status_col = "COALESCE(s.status, 'published')" if _has_session_status(db) else "'published'"
    # The two completeness counts are what `processing` (SIT-2) is derived from, and
    # are reported alongside it so a client can say *what* is still missing. Three
    # correlated counts over idx_speech_session cost ~1.5 ms per page of 50 days —
    # a grouped join over the whole speech table is 30× that.
    rows = db.execute(
        f"""SELECT s.id, s.period_number, s.sitting, s.date, s.date_start,
                   s.date_end, s.video_duration, {status_col} AS status,
                   (SELECT COUNT(*) FROM speech sp WHERE sp.session_id=s.id) AS speeches,
                   (SELECT COUNT(*) FROM speech sp WHERE sp.session_id=s.id
                     AND sp.has_text=1) AS speeches_with_text,
                   (SELECT COUNT(*) FROM speech sp WHERE sp.session_id=s.id
                     AND sp.video_start IS NOT NULL) AS speeches_with_video,
                   (SELECT COUNT(*) FROM agenda_item ai WHERE ai.session_id=s.id) AS agenda_items
            FROM session s {where} ORDER BY s.date DESC, s.sitting DESC
            LIMIT :limit OFFSET :offset""",
        {**params, "limit": limit, "offset": offset}).fetchall()
    return {"total": total, "limit": limit, "offset": offset,
            "sessions": [{**dict(r),
                          "processing": processing_state(
                              r["status"], r["date"], r["speeches"],
                              r["speeches_with_text"], r["speeches_with_video"])}
                         for r in rows]}


# ---------------------------------------------------------------------------
# The sitting that is coming: the Aktuális page's order paper (NR-5)
# ---------------------------------------------------------------------------


def _has_agenda_doc(db: sqlite3.Connection) -> bool:
    """Whether this DB carries the NR tables — false on one built before the
    stage existed, which is an empty answer rather than an error."""
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agenda_doc'"
    ).fetchone() is not None


def _json_or_none(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


@router.get("/upcoming")
def upcoming_agenda(db: sqlite3.Connection = Depends(get_db)):
    """The order paper for the sitting that is coming (NR-5).

    Everything the House has announced about its next sitting: the napirend's
    days with their timetables and their items, the other documents the Aktuális
    page links (the month's ülésterv, the submission deadlines), and the House
    Committee's next meeting.

    This is a **plan**, not a record: items get dropped, reordered and re-timed
    between the order paper and the sitting, so every field here is what the
    House *said* it would do, and the response carries the document and the
    moment it was issued (``statusLabel`` / ``statusAt``) so a caller can say so.
    Cycle-less by nature — there is only ever one next sitting.
    """
    if not _has_agenda_doc(db):
        return {"agenda": None, "documents": [], "houseCommittee": None,
                "source": None}

    def compute():
        docs = [dict(r) for r in db.execute(
            """SELECT slug, kind, url, label, grp, doc_date, title, first_date,
                      last_date, extraordinary, term_year, term_season,
                      status_label, status_at, item_count, parse_error
                 FROM agenda_doc ORDER BY ord""")]
        agenda = next((d for d in docs if d["kind"] == "agenda"), None)
        days = []
        if agenda:
            items_by_day: dict[int, list] = {}
            bill_ids: list[str] = []
            for r in db.execute(
                    """SELECT i.day_ord, i.ordinal, i.ref, i.bill_code, i.bill_id,
                              i.section, i.title, i.submitter, i.stage,
                              i.time_window, i.flags, i.notes, i.detail,
                              b.title AS bill_title, b.status AS bill_status
                         FROM agenda_doc_item i
                         LEFT JOIN bill b ON b.id = i.bill_id
                        WHERE i.doc_slug = ?
                        ORDER BY i.day_ord, i.id""", (agenda["slug"],)):
                items_by_day.setdefault(r["day_ord"], []).append({
                    "ordinal": r["ordinal"], "ref": r["ref"],
                    "billCode": r["bill_code"], "billId": r["bill_id"],
                    "billTitle": r["bill_title"], "billStatus": r["bill_status"],
                    "section": r["section"], "title": r["title"],
                    "submitter": r["submitter"], "stage": r["stage"],
                    "timeWindow": r["time_window"],
                    "flags": (r["flags"] or "").split(",") if r["flags"] else [],
                    "notes": (r["notes"] or "").split("\n") if r["notes"] else [],
                    "detail": _json_or_none(r["detail"]),
                })
                if r["bill_id"]:
                    bill_ids.append(r["bill_id"])
            # What the model read the iromány's own text as being about (TOPIC-8).
            # An order paper's titles are the least readable on the site — routinely
            # a citation of the law being amended ("Az egészségügyről szóló 1997.
            # évi CLIV. törvény … módosításáról") — so the topic is doing more work
            # here than on the bills list. It is the *same* chip: the same label,
            # the same read-time threshold, resolved from the iromány we link to,
            # so an item whose number resolves to no bill (or whose document was
            # never mirrored) simply carries no topic rather than a guess from its
            # title.
            topics = parlacap.topics_for(db, bill_ids, "bill")
            for day_items in items_by_day.values():
                for it in day_items:
                    it["topic"] = topics.get(it["billId"])
            for r in db.execute(
                    """SELECT ord, date, weekday, starts_at, decisions_from,
                              ends_note, break_note
                         FROM agenda_doc_day WHERE doc_slug = ? ORDER BY ord""",
                    (agenda["slug"],)):
                days.append({
                    "date": r["date"], "weekday": r["weekday"],
                    "startsAt": r["starts_at"],
                    "decisionsFrom": ((r["decisions_from"] or "").split(",")
                                      if r["decisions_from"] else []),
                    "endsNote": r["ends_note"], "breakNote": r["break_note"],
                    "items": items_by_day.get(r["ord"], []),
                })

        meta = {r["key"]: _json_or_none(r["value"])
                for r in db.execute("SELECT key, value FROM agenda_meta")}
        page = meta.get("page") or {}
        return {
            "agenda": ({"slug": agenda["slug"], "url": agenda["url"],
                        "title": agenda["title"], "label": agenda["label"],
                        "firstDate": agenda["first_date"],
                        "lastDate": agenda["last_date"],
                        "extraordinary": bool(agenda["extraordinary"]),
                        "termYear": agenda["term_year"],
                        "termSeason": agenda["term_season"],
                        "statusLabel": agenda["status_label"],
                        "statusAt": agenda["status_at"],
                        "itemCount": agenda["item_count"],
                        "parseError": agenda["parse_error"],
                        "days": days} if agenda else None),
            "documents": [{"slug": d["slug"], "kind": d["kind"], "url": d["url"],
                           "label": d["label"], "group": d["grp"],
                           "date": d["doc_date"]}
                          for d in docs if d["kind"] != "agenda"],
            "houseCommittee": meta.get("house_committee"),
            "source": {"page": page.get("url"), "scrapedAt": page.get("scrapedAt")},
        }

    return cached_aggregate("proceedings_upcoming", (), compute)


def _has_session_status(db: sqlite3.Connection) -> bool:
    """Whether the (regenerable) DB has the ``session.status`` column — false only
    on a DB built before the upcoming-sittings feature, so those endpoints keep
    working until the next loader run adds it."""
    return any(r["name"] == "status"
               for r in db.execute("PRAGMA table_info(session)"))


# ---------------------------------------------------------------------------
# Per-speech readability + lexical diversity (READ-1..7)
# ---------------------------------------------------------------------------

# The stored columns, in the order the serializer reads them.
_METRIC_COLUMNS = ("lix", "rix", "words", "sentences", "long_words",
                   "avg_sentence", "long_share", "ttr", "mattr", "types",
                   "tokens", "lemma_model")


def _metric_cuts(db: sqlite3.Connection) -> dict:
    """The corpus quantile cut points each score is banded against (READ-6),
    as ``{"lix": {20: …, 40: …}, "mattr": {…}}``.

    Eighteen rows, read once per request and handed to the serializer, so banding
    hundreds of speeches on a sitting-day page costs one extra query. Empty on a DB
    built before the metrics pass existed — the serializer then omits the band
    rather than inventing one."""
    try:
        rows = db.execute("SELECT metric, q, value FROM metric_distribution").fetchall()
    except sqlite3.OperationalError:
        return {}
    cuts: dict = {}
    for r in rows:
        cuts.setdefault(r["metric"], {})[int(r["q"])] = r["value"]
    return cuts


def _metrics_dict(row, cuts: dict) -> dict | None:
    """The ``metrics`` object hung off a speech, or ``None`` when it has none.

    A speech has no metrics when it is procedural, has no transcript, or is below
    the minimum length — the score would be noise — so the absence is meaningful
    and the UI shows nothing rather than a zero. ``lix_band`` / ``mattr_band`` place
    the speech in its corpus quintile: Björnsson's absolute difficulty labels are
    calibrated for Swedish at long-word threshold 6 and do not survive the
    Hungarian threshold, so the honest comparison is against the House's own
    speeches (see ``app/readability.py``). ``mattr`` is ``None`` for a speech
    shorter than the sliding window, and the whole diversity half is ``None`` when
    the build had no lemmatizer — never faked from surface forms."""
    if row is None or all(row[c] is None for c in _METRIC_COLUMNS):
        return None
    out = {c: row[c] for c in _METRIC_COLUMNS}
    out["lix_band"] = readability.band_for(row["lix"], cuts.get("lix", {}),
                                           readability.LIX_BANDS)
    out["mattr_band"] = readability.band_for(row["mattr"], cuts.get("mattr", {}),
                                             readability.MATTR_BANDS)
    return out


_METRICS_SELECT = ", ".join(f"m.{c} AS metric_{c}" for c in _METRIC_COLUMNS)


class _MetricView:
    """Adapts a joined query row (``metric_lix``, …) to the plain column names
    :func:`_metrics_dict` reads, so one serializer works for both the joined
    sitting-day query and a standalone ``speech_metrics`` lookup."""

    __slots__ = ("_row",)

    def __init__(self, row):
        self._row = row

    def __getitem__(self, key):
        return self._row[f"metric_{key}"]


def _has_speech_metrics(db: sqlite3.Connection) -> bool:
    """Whether the (regenerable) DB carries the metrics table — false only on a DB
    built before this feature, so the viewer keeps working unannotated until the
    next loader run adds it."""
    return bool(db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='speech_metrics'"
    ).fetchone())


def _speech_metrics(db: sqlite3.Connection, uid: str) -> dict | None:
    """One speech's metrics object, banded against the corpus. Degrades to ``None``
    on a DB without the table."""
    try:
        row = db.execute(
            "SELECT " + ", ".join(_METRIC_COLUMNS)
            + " FROM speech_metrics WHERE speech_id = ?", (uid,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return _metrics_dict(row, _metric_cuts(db)) if row else None


# ---------------------------------------------------------------------------
# CAP policy topics (TOPIC-1..7)
# ---------------------------------------------------------------------------

def _has_speech_topics(db: sqlite3.Connection) -> bool:
    """Whether the (regenerable) DB carries the topic table — false on a DB built
    before this feature, or on one whose build had neither the cache nor a model."""
    return parlacap.has_topics(db, "speech")


def _topics_for(db: sqlite3.Connection, uids: list[str]) -> dict[str, dict]:
    """Speech-level topics for a batch of speeches, ``{uid: topic}``.

    Aggregated **on read** rather than looked up, by the shared
    :func:`parlacap.topics_for`: nothing about a speech's topic is stored, because
    the confidence threshold that decides which paragraphs count is an operator
    setting (``PARLAMONITOR_PARLACAP_THRESHOLD``) that must be retunable without
    reclassifying — or even rebuilding — the corpus. The cost is one indexed query
    over the day's block rows (a sitting day of 200 speeches is roughly a thousand
    of them) and a fold in Python, which is cheaper than the join it replaces.

    Speeches with no confident block are simply absent from the result, which
    is how they end up with ``topic: null`` and no badge.
    """
    return parlacap.topics_for(db, uids, "speech")


# ---------------------------------------------------------------------------
# The floor's topic mix (TOPIC-9) — the Témák analysis's speech half
# ---------------------------------------------------------------------------
# Everything below aggregates the *same* stored blocks the chip on a speech is
# read off, through the *same* threshold and the *same* tie-break
# (`parlacap.aggregate`, expressed in SQL as `parlacap.dominant_topic_sql`). That
# is the point: a page that said the House spent 6 % of its words on health while
# the speeches it links to carried a different label would be worse than no page.
#
# Nothing here is materialised. A stored topic table would have to bake in the
# confidence threshold, and the threshold is a read-time policy an operator must
# be able to retune with a restart (TOPIC-6) — so the aggregation is redone per
# request and memoised per (scope, threshold, DB) like every other aggregate on
# this site.
#
# What that costs, measured against a synthetic **fully**-classified five-cycle
# archive (300k speeches, 1.5M blocks) with the query cache switched off: 4.3 s
# for the all-cycles mix and 0.8 s for one cycle; 1.4 s / 0.4 s for a topic's
# detail. Two passes over the block table, ~2 s each, and the scan *is* the cost —
# dropping the `session` join changes nothing measurable (2.42 s against 2.46 s),
# which is also why no covering index is carried for it. Two things keep that
# tolerable: the cycle selector defaults to a single cycle, so the sub-second
# request is the ordinary one, and the archive as actually classified today is
# ~600k blocks, not 1.5M. Revisit if the whole archive is ever classified and the
# all-cycles view becomes a common entry point.

# How many people the per-topic panel names. A top list is a way in, not a
# ranking to read to the end: twelve fills the panel at every width and keeps the
# cached matrix (every label's top list, computed in one pass) small.
TOPIC_TOP_SPEAKERS = 12


def _topic_scope(period: Optional[List[int]], column: str = "session_id") -> str:
    """Cycle scope for a `speech_topic` query, as a bare SQL predicate ("" = all).

    A block row carries the sitting it was spoken on, not a cycle number, so the
    scope is a subquery over `session` rather than a join: the same fragment has to
    go inside `parlacap.dominant_topic_sql`, which is a window query with nothing
    to join to. `column` is the sitting-id column to test, qualified where the
    caller's query has more than one (`speech` carries a `session_id` too).

    A query that already joins `session` should scope on `s.period_number` instead
    and skip this — cheaper, and this exists for the ones that cannot.
    """
    per = period_sql(period, "period_number")
    return f"{column} IN (SELECT id FROM session WHERE {per})" if per else ""


def _topic_scope_and(period: Optional[List[int]], column: str = "session_id") -> str:
    """:func:`_topic_scope` prefixed with " AND ", for appending to a WHERE that
    already has a condition (mirrors `db.period_and`)."""
    sql = _topic_scope(period, column)
    return f" AND {sql}" if sql else ""


@router.get("/topics")
def topic_mix(period: Optional[List[int]] = Query(None),
              db: sqlite3.Connection = Depends(get_db)):
    """What the House talks about: the CAP topic mix of the floor (TOPIC-9).

    Two measures per topic, because they answer different questions and neither
    stands alone:

    * **`words`/`share`** — how much of the classified policy text is about it.
      This is the agenda measure, and it is the one the aggregation already uses
      (a speech is about what it spends its words on, TOPIC-4).
    * **`speeches`** — how many speeches it is the *subject* of, resolved by the
      dominant-topic rule, i.e. exactly the speeches whose chip shows this label.
      A topic can be the subject of few speeches while running through many, and
      the difference between the two columns is worth seeing.

    `coverage` is what keeps the chart honest (TRUST-1): the pass only ever ran on
    non-procedural speeches with a transcript, and at the threshold in force about
    a third of the blocks it did classify are left unlabelled. A reader is told how
    much of the House's speech the picture is drawn from before they read it.

    `trend` is the same mix cut by calendar year — the shape of the agenda over
    time. It rides along with the mix rather than being an endpoint of its own
    because it comes out of the same scan.
    """
    if not _has_speech_topics(db):
        raise HTTPException(
            status_code=503,
            detail="No topic classification in this database (TOPIC-7).")
    return _floor_mix(db, period)


def _floor_mix(db: sqlite3.Connection, period: Optional[List[int]]) -> dict:
    """The mix above, as a function rather than a response.

    It has two readers now — the Témák page and every representative profile,
    which plots a member's own mix against the floor's (TOPIC-10) — and they
    share the one memoised result. That is not only thrift: a profile that drew
    its reference line from a second, separately-computed floor mix could show a
    member above a bar the analysis page draws below them.
    """

    def compute():
        params = parlacap.topic_params()
        per = period_sql(period, "s.period_number")
        # One pass over the blocks answers the mix, the coverage and the trend.
        # Grouping by `conf` rather than filtering on it is what makes the
        # unlabelled share visible: a topic's words and the words the model was
        # not sure enough about come out of the same scan.
        rows = db.execute(
            f"""SELECT substr(s.date, 1, 4) AS y, t.label AS label,
                       t.score >= :topic_threshold AS conf,
                       SUM(t.words) AS words, COUNT(*) AS blocks
                FROM speech_topic t
                JOIN session s ON s.id = t.session_id
                {('WHERE ' + per) if per else ''}
                GROUP BY y, label, conf""", params).fetchall()

        scope = _topic_scope_and(period)
        classified = db.execute(
            f"SELECT COUNT(DISTINCT speech_id) FROM speech_topic WHERE 1=1{scope}"
        ).fetchone()[0]
        dominant = dict(db.execute(
            "SELECT label, COUNT(*) FROM ("
            + parlacap.dominant_topic_sql(scope, "speech") + ") GROUP BY label",
            params).fetchall())

        # The population the pass covers, so `labelled` can be read as a share of
        # something: chairing speeches are never classified (STAT-1) and a speech
        # with no transcript has nothing to classify.
        speech_per = period_sql(period, "period_number")
        speeches = db.execute(
            "SELECT COUNT(*) FROM speech WHERE procedural = 0 AND has_text = 1"
            + ((" AND " + speech_per) if speech_per else "")).fetchone()[0]

        return parlacap.topic_mix(
            [(r["y"], r["label"], r["conf"], r["words"] or 0, r["blocks"])
             for r in rows],
            dominant,
            {"speeches": speeches, "classified": classified,
             "labelled": sum(dominant.values())})

    # The threshold rides in the key, not only in the payload: it is a read-time
    # policy an operator can retune (TOPIC-6), and a cached mix that outlived the
    # change would disagree with the chips on the speeches it links to.
    return cached_aggregate(
        "topic_mix",
        (period_key(period), parlacap.topic_params()["topic_threshold"]), compute)


@router.get("/topics/{label}")
def topic_detail(label: str,
                 period: Optional[List[int]] = Query(None),
                 db: sqlite3.Connection = Depends(get_db)):
    """One topic's owners: which factions spend their floor time on it, and who
    speaks most about it (TOPIC-9).

    Two shares per faction, because "who owns this topic" and "who is this topic's
    House" are different claims and the bigger faction wins the first by arithmetic
    alone:

    * `share_of_topic` — this faction's slice of everything said about it;
    * `share_of_own` — how much of *its own* classified policy speech goes here,
      which is what makes a small faction's specialism visible at all.

    Computed for **every** topic in one pass and cached per scope, then sliced:
    the scan is the cost, not the grouping, so a reader clicking through topics
    pays for it once.
    """
    if label not in parlacap.LABELS or label == parlacap.NON_POLICY:
        raise HTTPException(404, "Unknown policy topic")
    if not _has_speech_topics(db):
        raise HTTPException(
            status_code=503,
            detail="No topic classification in this database (TOPIC-7).")

    def compute():
        params = parlacap.topic_params()
        # `speech` carries the cycle itself, so this half needs no session subquery.
        per = period_and(period, "sp.period_number")
        factions: dict[str, list] = {}
        totals: dict[int | None, int] = {}
        rows = db.execute(
            f"""SELECT t.label AS label, sp.faction_id AS faction_id,
                       f.label AS faction_label, f.color AS color,
                       SUM(t.words) AS words
                FROM speech_topic t
                JOIN speech sp ON sp.uid = t.speech_id
                LEFT JOIN faction f ON f.id = sp.faction_id
                WHERE t.score >= :topic_threshold
                  AND t.label <> :topic_non_policy{per}
                GROUP BY t.label, sp.faction_id""", params).fetchall()
        for r in rows:
            totals[r["faction_id"]] = totals.get(r["faction_id"], 0) + (r["words"] or 0)
            factions.setdefault(r["label"], []).append(r)

        speakers: dict[str, list] = {}
        top = db.execute(
            f"""SELECT label, person_id, name, photo_uri, faction_label, color, words
                FROM (
                  SELECT t.label AS label, sp.person_id AS person_id,
                         -- A speaker the person registry does not carry (a guest,
                         -- an office-holder from before the roster) still spoke:
                         -- fall back to the name the transcript wrote.
                         COALESCE(p.label, sp.speaker_label) AS name,
                         p.photo_uri AS photo_uri,
                         f.label AS faction_label, f.color AS color,
                         SUM(t.words) AS words,
                         ROW_NUMBER() OVER (
                           PARTITION BY t.label
                           ORDER BY SUM(t.words) DESC, sp.person_id ASC) AS rk
                  FROM speech_topic t
                  JOIN speech sp ON sp.uid = t.speech_id
                  LEFT JOIN person p ON p.person_id = sp.person_id
                  LEFT JOIN faction f ON f.id = sp.faction_id
                  WHERE t.score >= :topic_threshold
                    AND t.label <> :topic_non_policy
                    AND sp.person_id IS NOT NULL{per}
                  GROUP BY t.label, sp.person_id
                ) WHERE rk <= {TOPIC_TOP_SPEAKERS}""", params).fetchall()
        for r in top:
            speakers.setdefault(r["label"], []).append(r)

        out = {}
        for lab in parlacap.LABELS:
            if lab == parlacap.NON_POLICY:
                continue
            frows = factions.get(lab, [])
            topic_words = sum(r["words"] or 0 for r in frows)
            out[lab] = {
                "label": lab,
                "code": parlacap.CAP_CODES.get(lab),
                "words": topic_words,
                "factions": sorted(
                    ({"id": r["faction_id"],
                      "label": r["faction_label"],
                      "color": r["color"],
                      "words": r["words"] or 0,
                      "share_of_topic": ((r["words"] or 0) / topic_words
                                         if topic_words else 0.0),
                      "share_of_own": ((r["words"] or 0) / totals[r["faction_id"]]
                                       if totals.get(r["faction_id"]) else 0.0)}
                     for r in frows),
                    key=lambda d: (-d["words"], d["label"] or "")),
                "speakers": [
                    {"person_id": r["person_id"],
                     "name": r["name"],
                     "photo_uri": r["photo_uri"],
                     "faction": ({"label": r["faction_label"], "color": r["color"]}
                                 if r["faction_label"] else None),
                     "words": r["words"] or 0,
                     "share_of_topic": ((r["words"] or 0) / topic_words
                                        if topic_words else 0.0)}
                    for r in speakers.get(lab, [])],
            }
        return out

    everything = cached_aggregate(
        "topic_detail",
        (period_key(period), parlacap.topic_params()["topic_threshold"]), compute)
    return everything[label]


# ---------------------------------------------------------------------------
# One member's own agenda (TOPIC-10) — the figure on a representative profile
# ---------------------------------------------------------------------------

@router.get("/topics/representative/{person_id}")
def representative_topic_mix(
        person_id: str,
        period: Optional[List[int]] = Query(None),
        db: sqlite3.Connection = Depends(get_db)):
    """What one member spends their floor time on (TOPIC-10).

    The Témák analysis asks what the House talks about; a profile asks the same
    question of one person. It *is* the same question, so it is the same scan
    through the same fold (`parlacap.topic_mix`) at the same read-time threshold,
    restricted to this speaker's speeches — a member's health figure is their
    health words over their own classified policy words, exactly as the floor's is.
    Nothing new is derived here and nothing is stored (TOPIC-6).

    **`house_share` on every row is what keeps the figure from being read as a
    personality test.** A member who gave 12 % of their words to health care in a
    term when the whole House gave 11 % to it is not a health specialist, and a
    bar chart of their 12 % alone would say they were: most of what any member
    talks about is simply what was on the agenda. The reference is the floor's own
    mix for the same scope, read off the Témák page's cached aggregate rather than
    rescanned (see :func:`_floor_mix`). It is deliberately the House's *share of
    words* and not an average over members — the latter would let a handful of
    talkative back-benchers define the norm.

    Coverage travels with it for the reason it does on the analysis page
    (TRUST-1): chairing speeches are never classified at all (STAT-1), a speech
    with no transcript cannot be, and at the threshold in force roughly a third of
    the blocks that were classified say nothing. `speeches` is the population,
    `labelled` the part of it this picture is actually drawn from.
    """
    # A speaker the registry does not carry has no profile to put this on, so the
    # unknown id is answered as the profile endpoints answer it.
    if not db.execute("SELECT 1 FROM person WHERE person_id = ?",
                      (person_id,)).fetchone():
        raise HTTPException(404, "Representative not found")
    if not _has_speech_topics(db):
        raise HTTPException(
            status_code=503,
            detail="No topic classification in this database (TOPIC-7).")

    def compute():
        params = {**parlacap.topic_params(), "pid": person_id}
        # `speech` carries the cycle number itself, so the person filter and the
        # cycle scope are one indexed lookup (`idx_speech_person`) and the block
        # rows follow by primary key — one member's figure never scans
        # `speech_topic` whole, which is what makes it cheap enough for a profile.
        per = period_and(period, "sp.period_number")
        rows = db.execute(
            f"""SELECT substr(s.date, 1, 4) AS y, t.label AS label,
                       t.score >= :topic_threshold AS conf,
                       SUM(t.words) AS words, COUNT(*) AS blocks
                FROM speech_topic t
                JOIN speech sp ON sp.uid = t.speech_id
                JOIN session s ON s.id = t.session_id
                WHERE sp.person_id = :pid{per}
                GROUP BY y, label, conf""", params).fetchall()

        # The dominant-topic rule is a window query with nothing to join to, so
        # "this member's speeches" reaches it as a subquery over `speech` rather
        # than as a join (`parlacap.dominant_topic_sql`). The counts it yields are
        # the speeches whose own chip carries the label — the same rule, so a
        # count here is the size of a set the reader could go and read.
        scope = (" AND speech_id IN (SELECT uid FROM speech WHERE person_id = :pid"
                 + period_and(period, "period_number") + ")")
        dominant = dict(db.execute(
            "SELECT label, COUNT(*) FROM ("
            + parlacap.dominant_topic_sql(scope, "speech") + ") GROUP BY label",
            params).fetchall())

        classified = db.execute(
            f"""SELECT COUNT(DISTINCT t.speech_id) FROM speech_topic t
                JOIN speech sp ON sp.uid = t.speech_id
                WHERE sp.person_id = :pid{per}""", params).fetchone()[0]
        # The population the pass could have covered: their non-procedural
        # speeches that have a transcript at all.
        speeches = db.execute(
            "SELECT COUNT(*) FROM speech WHERE person_id = :pid"
            " AND procedural = 0 AND has_text = 1"
            + period_and(period, "period_number"), params).fetchone()[0]

        mix = parlacap.topic_mix(
            [(r["y"], r["label"], r["conf"], r["words"] or 0, r["blocks"])
             for r in rows],
            dominant,
            {"speeches": speeches, "classified": classified,
             "labelled": sum(dominant.values())})

        # Read off the floor's cached mix; never written back into it.
        house = {t["label"]: t["share"] for t in _floor_mix(db, period)["topics"]}
        for topic in mix["topics"]:
            topic["house_share"] = house.get(topic["label"], 0.0)
        return {"person_id": person_id, **mix}

    # Keyed by the person as well as the scope and the threshold, for the same
    # reason the floor mix is keyed by the last two (TOPIC-6).
    return cached_aggregate(
        "rep_topic_mix",
        (person_id, period_key(period),
         parlacap.topic_params()["topic_threshold"]), compute)


@router.get("/sessions/{session_id}")
def get_session(session_id: str, db: sqlite3.Connection = Depends(get_db)):
    """A sitting day: agenda items in order, each with its speeches (use case 2)."""
    s = db.execute("SELECT * FROM session WHERE id = ?", (session_id,)).fetchone()
    # A sitting of a cycle this deployment does not serve (CYC-7) is answered as
    # not found — same answer as an id that isn't in the DB, since within this
    # window it isn't. The same guard sits on every by-id page below.
    if not s or not period_in_scope(s["period_number"]):
        raise HTTPException(404, "Session not found")
    agenda = db.execute(
        "SELECT * FROM agenda_item WHERE session_id = ? ORDER BY ord", (session_id,)
    ).fetchall()
    # Readability / lexical diversity travel with the speech list rather than as a
    # second request: they are eleven precomputed numbers joined on the primary
    # key, so annotating the day costs one join, not a round trip per speech.
    metrics = _has_speech_metrics(db)
    speeches = db.execute(
        f"""SELECT sp.uid, sp.origin_id, sp.agenda_item_id, sp.speech_index,
                  sp.speaker_label, sp.person_id, sp.speaker_status,
                  sp.felszolalas_tipus, sp.procedural, sp.time_start,
                  sp.time_end, sp.duration, sp.has_text, sp.video_start,
                  sp.confidence,
                  sp.align_method, p.label AS person_label, p.photo_uri,
                  f.label AS faction_label, f.color AS faction_color
                  {(', ' + _METRICS_SELECT) if metrics else ''}
           FROM speech sp
           LEFT JOIN person p ON p.person_id = sp.person_id
           LEFT JOIN faction f ON f.id = sp.faction_id
           {'LEFT JOIN speech_metrics m ON m.speech_id = sp.uid' if metrics else ''}
           WHERE sp.session_id = ? ORDER BY sp.speech_index""",
        (session_id,)).fetchall()
    cuts = _metric_cuts(db) if metrics else {}
    # Topics travel with the list too, but as one extra query rather than a join:
    # they live at paragraph granularity and are folded up per speech on read (see
    # `_topics_for`), so there is no one row per speech to join against.
    topics = _topics_for(db, [sp["uid"] for sp in speeches if not sp["procedural"]])
    by_agenda: dict = {a["id"]: [] for a in agenda}
    for sp in speeches:
        by_agenda.setdefault(sp["agenda_item_id"], []).append(
            _speech_brief(sp, _metrics_dict(_MetricView(sp), cuts) if metrics else None,
                          topics.get(sp["uid"])))
    # Same completeness signal the sittings list carries (SIT-2), counted off the
    # speech rows already fetched rather than re-queried: the page heading marks a
    # day whose transcript or per-speech video is still arriving.
    processing = processing_state(
        (s["status"] if "status" in s.keys() else None) or "published", s["date"],
        len(speeches), sum(1 for sp in speeches if sp["has_text"]),
        sum(1 for sp in speeches if sp["video_start"] is not None))
    return {
        "session": {**_session_dict(s), "processing": processing},
        "neighbours": _session_neighbours(db, s),
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
    if not s or not period_in_scope(s["period_number"]):
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
    s = db.execute("SELECT id, date, period_number FROM session WHERE id = ?",
                   (session_id,)).fetchone()
    if not s or not period_in_scope(s["period_number"]):
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
    s = db.execute("SELECT id, date, period_number FROM session WHERE id = ?",
                   (session_id,)).fetchone()
    if not s or not period_in_scope(s["period_number"]):
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

# SQL literal list of the mention kinds that can carry a link. Built from the one
# definition in `app.nlp`; the values are our own fixed labels, never user input.
_LINKED_KINDS_SQL = ",".join(f"'{k}'" for k in sorted(LINKABLE_LABELS))


def _speech_entities(db, uid: str) -> list[dict]:
    """Resolved person/institution links occurring in a speech's transcript (NEL, §10).

    One row per distinct recognized surface form → its ordered list of destinations
    (`links`): an internal MP `profile`, K-Monitor tag pages (the primary source),
    or Wikipedia (the fallback). The frontend matches these surfaces in the rendered
    text and wraps each as the name plus a cluster of destination badges; `ambiguous`
    marks a name that resolved to more than one candidate (shown as alternatives).
    Degrades to [] on a pre-NEL DB (no entity tables).

    `entity` also holds LOC/MISC mentions, which are stored for analysis only — the
    kind filter keeps them out, so a place whose normalized name happens to equal a
    linked org's can never light up an extra stretch of text."""
    try:
        rows = db.execute(
            f"""SELECT DISTINCT e.surface, el.kind, el.ambiguous, el.links_json
               FROM entity e
               JOIN sentence se ON se.id = e.sentence_id
               JOIN entity_link el ON el.entity_key = e.entity_key
               WHERE se.speech_id = ? AND e.kind IN ({_LINKED_KINDS_SQL})
                 AND el.links_json IS NOT NULL""",
            (uid,)).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in rows:
        try:
            links = json.loads(r["links_json"]) or []
        except (ValueError, TypeError):
            links = []
        if not links:
            continue
        out.append({"surface": r["surface"], "kind": r["kind"],
                    "ambiguous": bool(r["ambiguous"]), "links": links})
    return out


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
    if not sp or not period_in_scope(sp["period_number"]):
        raise HTTPException(404, "Speech not found")
    session = db.execute("SELECT * FROM session WHERE id = ?",
                         (sp["session_id"],)).fetchone()
    sentences = db.execute(
        "SELECT id, ord, text, time_start, time_end FROM sentence "
        "WHERE speech_id = ? ORDER BY ord", (uid,)).fetchall()
    nb = _speech_neighbours(db, sp["session_id"], sp["speech_index"])
    speech = _speech_full(sp, _speech_metrics(db, uid), _topics_for(db, [uid]).get(uid))
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
        "entities": _speech_entities(db, uid),
        "neighbours": nb,
    }


@router.get("/speeches/{uid}/text")
def get_speech_text(uid: str, db: sqlite3.Connection = Depends(get_db)):
    """The transcript sentences of one speech, for inline reading on the
    sitting-day page (the spoiler drop-down).

    A deliberately lightweight companion to `/speeches/{uid}` (which also derives
    the video clip and prev/next neighbours): expanding a speech in the day list
    only needs its text, so this returns just the sentences and never pulls the
    whole viewer payload. `has_text` distinguishes a genuinely empty transcript
    (video-only speech, VIE-8) from one still being read."""
    sp = db.execute("SELECT uid, has_text, period_number FROM speech WHERE uid = ?",
                    (uid,)).fetchone()
    if not sp or not period_in_scope(sp["period_number"]):
        raise HTTPException(404, "Speech not found")
    # `paragraph` groups the flat sentence list back into the transcript's
    # original paragraphs (NULL on a pre-migration DB → the reader falls back to
    # one block). Select it defensively so an old DB without the column works.
    try:
        rows = db.execute(
            "SELECT ord, text, paragraph FROM sentence WHERE speech_id = ? ORDER BY ord",
            (uid,)).fetchall()
        sentences = [{"ord": r["ord"], "text": r["text"], "paragraph": r["paragraph"]}
                     for r in rows]
    except sqlite3.OperationalError:
        rows = db.execute(
            "SELECT ord, text FROM sentence WHERE speech_id = ? ORDER BY ord",
            (uid,)).fetchall()
        sentences = [{"ord": r["ord"], "text": r["text"], "paragraph": None}
                     for r in rows]
    return {
        "uid": uid,
        "has_text": bool(sp["has_text"]),
        "sentences": sentences,
        "entities": _speech_entities(db, uid),
    }


# Upper bound on an exportable clip's length (seconds). The client-side exporter
# (VIE-10) buffers every segment of the window into memory before muxing, so an
# unbounded window could try to hold hours of video — cap it defensively. A
# single speech is minutes long; 30 min is generous headroom for a sub-range that
# spans an adjourned/rejoined stretch.
_MAX_CLIP_SECONDS = 30 * 60


@router.get("/speeches/{uid}/clip")
def get_speech_clip(
    uid: str,
    start: Optional[float] = Query(None, description="window start, day-absolute seconds"),
    end: Optional[float] = Query(None, description="window end, day-absolute seconds"),
    db: sqlite3.Connection = Depends(get_db),
):
    """A cropped HLS clip URL for an arbitrary ``[start, end]`` window of the
    speech, for the client-side exporter (VIE-10).

    Generalises the per-speech clip (VIE-9): where ``/speeches/{uid}`` always
    crops to the whole speech, this crops the day recording to any sub-window the
    user selected (a range of sentences). ``start``/``end`` are day-absolute
    seconds (the same coordinate as ``sentence.time_start`` and the speech's
    ``video_start``); omitting them falls back to the whole speech. The heavy
    lifting is the streaming server's on-demand smil cropping — this endpoint only
    shifts the day stream's offsets, exactly as the viewer's clip does, so it adds
    no scraping and no stored artefact.
    """
    sp = db.execute(
        "SELECT session_id, period_number, video_start, video_end "
        "FROM speech WHERE uid = ?", (uid,)).fetchone()
    if not sp or not period_in_scope(sp["period_number"]):
        raise HTTPException(404, "Speech not found")
    session = db.execute("SELECT video_uri, video_playseq FROM session WHERE id = ?",
                         (sp["session_id"],)).fetchone()
    if not session or not session["video_uri"]:
        raise HTTPException(404, "No recording for this speech")

    # Default to the speech's own window; clamp a requested sub-range into it so a
    # client can't crop outside the speech it is viewing.
    sp_start, sp_end = sp["video_start"], sp["video_end"]
    lo = start if start is not None else sp_start
    hi = end if end is not None else sp_end
    if lo is None or hi is None:
        raise HTTPException(422, "Speech has no usable video offsets")
    if sp_start is not None and sp_end is not None and sp_end > sp_start:
        lo = max(sp_start, min(lo, sp_end))
        hi = max(sp_start, min(hi, sp_end))
    if hi <= lo:
        raise HTTPException(422, "Empty clip window")
    if hi - lo > _MAX_CLIP_SECONDS:
        raise HTTPException(422, f"Clip window exceeds {_MAX_CLIP_SECONDS}s cap")

    clip = per_speech_clip(session["video_uri"], session["video_playseq"], lo, hi)
    if not clip:
        raise HTTPException(422, "Could not derive a clip for this window")
    return {
        "uid": uid,
        "start": lo, "end": hi, "duration": hi - lo,
        "video_uri": clip["video_uri"],
        "video_playseq": clip["video_playseq"],
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


def _session_neighbours(db, s) -> dict:
    """The chronologically adjacent sitting days within the same electoral cycle,
    for prev/next-day navigation on the sitting page. Ordered by (date, sitting);
    None at the cycle's first/last sitting so navigation never crosses cycles
    (matching the cycle-scoped sittings list)."""
    status_col = "COALESCE(status, 'published')" if _has_session_status(db) else "'published'"
    params = {"per": s["period_number"], "date": s["date"], "sitting": s["sitting"]}
    prev = db.execute(
        f"SELECT id, date, sitting, {status_col} AS status FROM session "
        "WHERE period_number = :per AND (date, sitting) < (:date, :sitting) "
        "ORDER BY date DESC, sitting DESC LIMIT 1", params).fetchone()
    nxt = db.execute(
        f"SELECT id, date, sitting, {status_col} AS status FROM session "
        "WHERE period_number = :per AND (date, sitting) > (:date, :sitting) "
        "ORDER BY date ASC, sitting ASC LIMIT 1", params).fetchone()

    def brief(r):
        return {"id": r["id"], "date": r["date"], "sitting": r["sitting"],
                "status": r["status"]} if r else None
    return {"prev": brief(prev), "next": brief(nxt)}


# ---------------------------------------------------------------------------
# serializers
# ---------------------------------------------------------------------------

def _session_dict(s) -> dict:
    if s is None:
        return None
    return {
        "id": s["id"], "period": s["period_number"], "sitting": s["sitting"],
        "date": s["date"], "date_start": s["date_start"], "date_end": s["date_end"],
        # 'published' | 'scheduled'; defensive for a pre-migration DB without the column.
        "status": (s["status"] if "status" in s.keys() else None) or "published",
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


def _speech_brief(sp, metrics: dict | None = None,
                  topic: dict | None = None) -> dict:
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
        # Readability + lexical diversity (READ-1..7); None when the speech is not
        # measurable (procedural, no transcript, or below the length floor).
        "metrics": metrics,
        # CAP policy topic (TOPIC-1..7); None when no paragraph of the speech was
        # classified confidently enough to name one — which is the intended answer
        # for roughly a third of speeches, not a gap to be filled.
        "topic": topic,
    }


def _speech_full(sp, metrics: dict | None = None,
                 topic: dict | None = None) -> dict:
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
        # Readability + lexical diversity (READ-1..7); None when not measurable.
        "metrics": metrics,
        # CAP policy topic (TOPIC-1..7); None when nothing cleared the threshold.
        "topic": topic,
    }
