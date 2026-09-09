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
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ... import parlacap
from ... import portfolios as portfolio_map
from ...analytics import search_analytics
from ...db import (get_db, like_contains, period_in_scope, period_key,
                   period_list, period_sql)
from ...media import per_speech_clip
from ...query_cache import cached_aggregate
from ...vote_stats import (NO_ATTENDANCE, NO_CROSSVOTING, attendance_for,
                           crossvoting_for)

router = APIRouter(prefix="/bills", tags=["bills"])

# A question-type iromány (kérdés / interpelláció / azonnali kérdés) is answered
# orally in plenary by the responsible minister or state secretary; that answer
# is a recorded plenary speech. These event names mark that oral answer (written
# answers — "kérdés írásban megválaszolva" — carry no speech and are excluded),
# so the bill page can embed the answer video (VIE-9).
_ANSWER_EVENTS = ("kérdés megválaszolva", "interpelláció szóban megválaszolva")

# The asking MP's verdict on that oral answer, recorded as its own bill event.
# In practice only interpellációk carry it (the House then votes on an answer the
# MP rejected), and a question gets at most one of the two — so they make a clean
# two-way filter on the documents list: did the MP who asked accept the reply?
_VERDICT_EVENTS = {"accepted": "képviselő elfogadta a választ",
                   "rejected": "képviselő elutasította a választ"}

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


# ---------------------------------------------------------------------------
# CAP policy topics for irományok (TOPIC-8)
# ---------------------------------------------------------------------------

def _has_bill_topics(db: sqlite3.Connection) -> bool:
    """Whether the (regenerable) DB carries the iromány topic table — false on one
    built before this feature, or whose build had neither a document mirror nor a
    shipped cache to replay."""
    return bool(db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bill_topic'"
    ).fetchone())


def _topics_for(db: sqlite3.Connection, bill_ids: list[str]) -> dict[str, dict]:
    """Document-level topics for a batch of irományok, ``{bill_id: topic}``.

    Aggregated **on read**, through the very same :func:`parlacap.aggregate` the
    speech list uses, for the very same reason: the confidence threshold that
    decides which blocks count is an operator setting that must be retunable with
    a restart rather than a reclassification (TOPIC-6). The payload shape is
    identical too, so one badge component renders both.

    An iromány with no confident block is simply absent from the result and ends
    up with ``topic: null`` — which is the honest answer for the ~10 % of cycle-43
    documents whose text is too short, too procedural or too scanned to place.
    """
    if not bill_ids or not _has_bill_topics(db):
        return {}
    out: dict[str, dict] = {}
    # Chunked to stay under SQLite's variable limit on a full page of results.
    for start in range(0, len(bill_ids), 400):
        chunk = bill_ids[start:start + 400]
        rows = db.execute(
            "SELECT bill_id, block, label, score, runner_up, runner_score, "
            "words FROM bill_topic WHERE bill_id IN ("
            + ",".join("?" * len(chunk)) + ") ORDER BY bill_id, block",
            chunk).fetchall()
        by_bill: dict[str, list] = {}
        for r in rows:
            by_bill.setdefault(r["bill_id"], []).append(
                (r["block"], r["label"], r["score"], r["runner_up"],
                 r["runner_score"], r["words"]))
        for bid, block_rows in by_bill.items():
            agg = parlacap.aggregate(block_rows)
            if agg:
                out[bid] = agg
    return out


def _any_of(where: list, params: dict, column: str, values: list[str], prefix: str) -> None:
    """Restrict ``column`` to any of ``values``, in-place on a WHERE/params pair.

    Blank entries are dropped and an empty list adds no condition at all, so a
    multi-value filter left unset (``?type=`` or simply omitted) means "all"
    rather than "nothing" — the same "empty selection is the whole set" rule the
    cycle scope follows."""
    vals = [v for v in values if v]
    if not vals:
        return
    keys = [f"{prefix}{i}" for i in range(len(vals))]
    where.append(f"{column} IN (" + ",".join(":" + k for k in keys) + ")")
    params.update(dict(zip(keys, vals)))


def _has_portfolios(db: sqlite3.Connection) -> bool:
    """Whether the loader has derived the §6C portfolio tables into this DB."""
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                           "AND name='portfolio_bill'").fetchone())


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


def _responders_for(db: sqlite3.Connection, bill_ids: list[str]) -> dict[str, dict]:
    """Who *answered* each question-type iromány, grouped by bill id (one query
    for a page). The responder is the speaker of the oral answer speech, resolved
    from the answer event through the shared speech UUID (EXT-2) — the same
    person the bill page's answer video shows. A question answered only in
    writing names a responding portfolio upstream but no person, so it gets no
    entry; neither does a non-question iromány."""
    if not bill_ids:
        return {}
    ph = ",".join("?" * len(bill_ids))
    aph = ",".join("?" * len(_ANSWER_EVENTS))
    rows = db.execute(
        f"""SELECT e.bill_id, s.person_id, s.speaker_label, s.speaker_office,
                   p.label AS person_label,
                   f.label AS faction_label, f.color AS faction_color
            FROM bill_event e
            JOIN speech s ON s.speech_uuid = e.speech_id
            LEFT JOIN person p ON p.person_id = s.person_id
            LEFT JOIN faction f ON f.id = s.faction_id
            WHERE e.bill_id IN ({ph}) AND e.name IN ({aph})
            ORDER BY e.bill_id, e.ord, s.speech_index""",
        (*bill_ids, *_ANSWER_EVENTS)).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        name = r["person_label"] or r["speaker_label"]
        if not name or r["bill_id"] in out:   # first answer event per bill wins
            continue
        out[r["bill_id"]] = {
            "person_id": r["person_id"],
            "name": name,
            "office": r["speaker_office"],
            "faction": {"label": r["faction_label"], "color": r["faction_color"]}
                       if r["faction_label"] else None,
        }
    return out


@router.get("")
def list_bills(
    q: Optional[str] = None,
    period: Optional[List[int]] = Query(
        None, description="Electoral period number(s); repeat to scope to several cycles"),
    main_type: Optional[str] = None,
    main_type_not: Optional[str] = None,     # exclude a fotipus, e.g. T (bills)
    main_type_in: Optional[str] = None,      # include any of these fotipusok (CSV)
    main_type_not_in: Optional[str] = None,  # exclude any of these fotipusok (CSV)
    type: Optional[List[str]] = Query(
        None, description="Iromány type (category); repeat to match any of several"),
    status: Optional[List[str]] = Query(
        None, description="Status; repeat to match any of several"),
    sponsor: Optional[str] = None,           # person_id — bills by this MP
    portfolio: Optional[str] = None,         # portfolio slug (§6C) — see below
    portfolio_role: str = Query(
        "any", pattern="^(any|answered|submitted)$",
        description="With `portfolio`: irományok the tárca answered, ones the "
                    "government submitted through it, or either"),
    portfolio_period: Optional[List[int]] = Query(
        None, description="With `portfolio`: scope by the cycle the tárca *acted* "
                          "in (answered / submitted), rather than the cycle the "
                          "iromány itself is filed under — see §6C"),
    answer_verdict: Optional[str] = Query(
        None, pattern="^(accepted|rejected)$",
        description="Question-type irományok where the asking MP accepted / "
                    "rejected the answer given to them"),
    sort: str = Query("number", pattern="^(number|date)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
):
    """Browsable, filterable bill list. Filters combine."""
    where = ["1=1"]
    params: dict = {}
    if q:
        # Readers look an iromány up by its number ("T/438") as often as by
        # title, so the free-text filter matches both (BILL-1) — as the votes
        # list already does for its bill numbers.
        where.append("(fold(b.title) LIKE fold(:q) ESCAPE '\\' "
                     "OR fold(b.bill_number) LIKE fold(:q) ESCAPE '\\')")
        params["q"] = like_contains(q.strip())
    per_sql = period_sql(period, "b.period_number")
    if per_sql:
        where.append(per_sql)
    if main_type:
        where.append("b.main_type = :mt"); params["mt"] = main_type
    if main_type_not:
        where.append("(b.main_type IS NULL OR b.main_type != :mtn)")
        params["mtn"] = main_type_not
    if main_type_in:
        _any_of(where, params, "b.main_type",
                [c.strip() for c in main_type_in.split(",")], "mti")
    if main_type_not_in:
        codes = [c.strip() for c in main_type_not_in.split(",") if c.strip()]
        if codes:
            keys = [f"mtni{i}" for i in range(len(codes))]
            where.append("(b.main_type IS NULL OR b.main_type NOT IN ("
                         + ",".join(":" + k for k in keys) + "))")
            params.update(dict(zip(keys, codes)))
    # Type and status are multi-value: the documents list lets a reader tick
    # several categories at once (kérdés *and* interpelláció), and each arrives
    # as a repeated query param. A single value still works unchanged.
    if type:
        _any_of(where, params, "b.type", type, "ty")
    if status:
        _any_of(where, params, "b.status", status, "st")
    if sponsor:
        where.append("EXISTS (SELECT 1 FROM bill_sponsor bs WHERE bs.bill_id=b.id "
                     "AND bs.person_id=:sp)"); params["sp"] = sponsor
    if portfolio:
        # The tárca filter (MIN-7). Resolved through the loader's derived link
        # table rather than by matching the free-text office labels here, so the
        # list and the portfolio pages can never disagree about what belongs to a
        # ministry. On a DB built before §6C the table is absent — the filter then
        # matches nothing rather than erroring, exactly as a disabled module's
        # metric is hidden rather than faked (EXT-6).
        if _has_portfolios(db):
            role_sql = "" if portfolio_role == "any" else " AND role = :pfrole"
            # `portfolio_period` scopes by the cycle the tárca *acted* in, which is
            # not always the cycle the iromány is filed under: parlament.hu
            # re-lists an iromány still in progress under the new cycle, so a bill
            # submitted in 2024 comes back as a cycle-43 row. The tárca pages count
            # by the link's own cycle, and this is how their document lists stay
            # the same set as their counts (cf. VOTE-6).
            per_sql = period_sql(portfolio_period, "period_number")
            where.append("b.id IN (SELECT bill_id FROM portfolio_bill "
                         f"WHERE portfolio_slug = :pfslug{role_sql}"
                         + (f" AND {per_sql}" if per_sql else "") + ")")
            params["pfslug"] = portfolio
            if portfolio_role != "any":
                params["pfrole"] = portfolio_role
        else:
            where.append("1=0")
    if answer_verdict:
        # `IN (subquery)` rather than a correlated EXISTS: the verdict events are
        # a thin slice of bill_event with no index on `name`, so one scan of the
        # small table beats an index probe per candidate bill (~5× here).
        where.append("b.id IN (SELECT bill_id FROM bill_event WHERE name = :av)")
        params["av"] = _VERDICT_EVENTS[answer_verdict]
    where_sql = " AND ".join(where)

    # number_sort restarts every cycle, so rank by period first — otherwise an
    # unscoped list pages through the previous cycle's high numbers before any
    # current-cycle bill appears.
    order = {"number": "b.period_number DESC, b.number_sort DESC",
             "date": "b.submitted_date DESC"}[sort]
    if q:
        # A reader who typed a whole iromány number wants *that* iromány first,
        # not the longer numbers it is a prefix of ("T/438" before "T/4388").
        order = "(fold(b.bill_number) = fold(:q_exact)) DESC, " + order
        params["q_exact"] = q.strip()

    total = db.execute(f"SELECT COUNT(*) AS c FROM bill b WHERE {where_sql}",
                       params).fetchone()["c"]

    # Privacy-respecting analytics (PRIV-2): count the typed keyword and the
    # filters it was combined with. A no-keyword browse of the list records
    # nothing. The bills page and the "egyéb irományok" page are this one
    # endpoint under different main_type filters — those filters are part of the
    # key, so the two stay distinguishable in the aggregates.
    search_analytics.record(
        source="bills", query=q, period=period_list(period), sort=sort,
        main_type=main_type, main_type_not=main_type_not,
        main_type_in=main_type_in, main_type_not_in=main_type_not_in,
        type=type, status=status, sponsor=sponsor, portfolio=portfolio,
        portfolio_role=portfolio_role, portfolio_period=portfolio_period,
        answer_verdict=answer_verdict, results=total, offset=offset)

    rows = db.execute(
        f"""SELECT b.id, b.bill_number, b.title, b.type, b.main_type, b.status,
                   b.submitted_date, b.text_url, b.source_url, b.period_number
            FROM bill b WHERE {where_sql}
            ORDER BY {order} LIMIT :limit OFFSET :offset""",
        {**params, "limit": limit, "offset": offset}).fetchall()
    sponsors = _sponsors_for(db, [r["id"] for r in rows])
    responders = _responders_for(db, [r["id"] for r in rows])
    topics = _topics_for(db, [r["id"] for r in rows])
    return {
        "total": total, "limit": limit, "offset": offset,
        "bills": [
            {
                "id": r["id"], "bill_number": r["bill_number"], "title": r["title"],
                "type": r["type"], "main_type": r["main_type"], "status": r["status"],
                "submitted_date": r["submitted_date"], "text_url": r["text_url"],
                "source_url": r["source_url"], "period_number": r["period_number"],
                "sponsors": sponsors.get(r["id"], []),
                "responder": responders.get(r["id"]),
                "topic": topics.get(r["id"]),
            } for r in rows
        ],
    }


@router.get("/facets")
def bill_facets(period: Optional[List[int]] = Query(
                    None, description="Electoral period number(s)"),
                main_type: Optional[str] = None,
                main_type_not: Optional[str] = None,
                sponsor: Optional[str] = None,       # restrict to one MP's irományok
                db: sqlite3.Connection = Depends(get_db)):
    """Distinct statuses and types for filter controls (optionally scoped to a
    period, a fotipus include/exclude — e.g. ``main_type=T`` for the bills page,
    ``main_type_not=T`` for the other-irományok page — and a sponsor, so a
    profile can list only the document types that MP actually submitted)."""
    # Distinct-value scan over the whole bill table; static per deploy and hit by
    # every filter UI, so memoize per filter-combination (invalidated on DB swap).
    key = (period_key(period), main_type, main_type_not, sponsor)

    def _compute():
        where, params = ["1=1"], []
        per_sql = period_sql(period, "b.period_number")
        if per_sql:
            where.append(per_sql)
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

    return cached_aggregate("bill_facets", key, _compute)


# Question-type irományok (kérdés / interpelláció / azonnali kérdés) and the
# events that record how each was answered. The Kérdések sub-page (BILL-11) turns
# these into a three-column Sankey — question type → asker faction → answerer —
# where the answerer is the responding portfolio (both oral and written answer
# events carry it in `related_label`), or an "unanswered" node when the question
# drew no answer.
_QUESTION_TYPES = ("A", "I", "K")
_ORAL_ANSWER_EVENTS = ("kérdés megválaszolva", "interpelláció szóban megválaszolva")
_WRITTEN_ANSWER_EVENT = "kérdés írásban megválaszolva"
# Colours for the question-type column of the Sankey (§6A). Muted tones distinct
# from the faction palette; the type node colours its outgoing (stage-1) ribbons.
_TYPE_COLORS = {
    "I": "#6b7fb0",   # interpelláció
    "K": "#8a9b6e",   # kérdés (answered orally)
    "A": "#c08a55",   # azonnali kérdés
    "W": "#9d84a8",   # írásbeli kérdés (answered in writing)
}


def _refine_type(main_type: Optional[str], type_str: Optional[str]) -> str:
    """The Sankey type-column code for a question. A written question — an
    ``írásbeli kérdés`` (main_type K answered in writing rather than orally) — is
    split out from the plain ``kérdés`` into its own ``W`` category."""
    if main_type == "K" and "írásbeli" in (type_str or "").lower():
        return "W"
    return main_type or ""


def _classify_questions(db: sqlite3.Connection, period: Optional[List[int]], top: int,
                        expand_other: bool = False):
    """Shared engine for the Kérdések Sankey and its drill-down: classify every
    question-type iromány in scope by asker faction, by question type and by
    answerer, applying the same top-``top`` ministry ranking so the diagram and
    the per-flow list agree. With ``expand_other`` the ranking is dropped and
    every named responder keeps its own node ("ungroup the other bucket").

    Returns ``(order_ids, factions, faction_key, type_key, answerer_key)`` where
    ``order_ids`` is the question bill ids newest-first, ``faction_key(bid)`` is
    the asker's faction id (or ``None``), ``type_key(bid)`` is the question's
    ``main_type`` code (A/I/K), and ``answerer_key(bid)`` is a ``(kind, label)``
    tuple (``label`` empty for the non-ministry nodes)."""
    from collections import Counter

    qph = ",".join("?" * len(_QUESTION_TYPES))
    where = [f"b.main_type IN ({qph})"]
    params: list = list(_QUESTION_TYPES)
    per_sql = period_sql(period, "b.period_number")
    if per_sql:
        where.append(per_sql)
    where_sql = " AND ".join(where)

    order_ids: list[str] = []
    bill_type: dict[str, str] = {}   # bill -> refined type code (A/I/K/W)
    for r in db.execute(
        f"SELECT b.id, b.main_type, b.type FROM bill b WHERE {where_sql} "
        f"ORDER BY b.period_number DESC, b.number_sort DESC",  # number_sort is per-cycle
        params):
        order_ids.append(r["id"])
        bill_type[r["id"]] = _refine_type(r["main_type"], r["type"])
    factions = {r["id"]: r for r in
                db.execute("SELECT id, label, color FROM faction")}

    # Asker = the primary (lowest-ord) sponsor's faction, one per question.
    asker_faction: dict[str, Optional[int]] = {}
    for r in db.execute(
        f"""SELECT bs.bill_id, bs.faction_id FROM bill_sponsor bs
            JOIN bill b ON b.id = bs.bill_id
            WHERE {where_sql} ORDER BY bs.bill_id, bs.ord""", params):
        asker_faction.setdefault(r["bill_id"], r["faction_id"])

    # Answer classification from the bill's answer events. A question is answered
    # either orally in plenary or in writing, and BOTH kinds of event name the
    # responding portfolio in `related_label` (the same label strings are reused
    # for oral and written answers), so every answered question resolves to its
    # actual responder — there is no separate "answered in writing" bucket. Oral
    # wins per bill when both events exist (the plenary answer is the substantive
    # one).
    answer_events = _ORAL_ANSWER_EVENTS + (_WRITTEN_ANSWER_EVENT,)
    aph = ",".join("?" * len(answer_events))
    oral_ministry: dict[str, Optional[str]] = {}     # bill -> responder label (or None)
    written_ministry: dict[str, Optional[str]] = {}  # bill -> responder label (or None)
    slug_of: dict[str, Optional[str]] = {}           # responder label -> tárca slug
    for r in db.execute(
        f"""SELECT e.bill_id, e.name, e.related_label
            FROM bill_event e JOIN bill b ON b.id = e.bill_id
            WHERE {where_sql} AND e.name IN ({aph})""",
        params + list(answer_events)):
        target = oral_ministry if r["name"] in _ORAL_ANSWER_EVENTS else written_ministry
        # The responder is named by *office*, so one ministry arrives under
        # several labels — "Emberi Erőforrások Minisztériumának államtitkára" and
        # "emberi erőforrások minisztere" are the same tárca answering, and left
        # raw they occupy two nodes and split one ministry's flow in half. Collate
        # them through the portfolio table (§6C), which is also what makes a node
        # clickable through to that tárca's page (MIN-7). A label the table does
        # not cover keeps its own node under its own name (MIN-3).
        label = r["related_label"]
        if label:
            tarca = portfolio_map.resolve(label)
            label = tarca.name if tarca else portfolio_map.normalize_label(label)
            slug_of.setdefault(label, tarca.slug if tarca else None)
        # mark the bill as answered, keeping the first non-empty responder label
        # seen for it (None until/unless one appears)
        if not target.get(r["bill_id"]):
            target[r["bill_id"]] = label

    def responder_label(bid: str) -> Optional[str]:
        """The responding portfolio for an answered question: the oral answer's
        label when it was answered in plenary, else the written answer's."""
        return oral_ministry[bid] if bid in oral_ministry else written_ministry.get(bid)

    # Rank responders (oral and written together) so only the busiest keep their
    # own node; the rest pool into "other". `expand_other` keeps every responder.
    ministry_totals: Counter = Counter()
    for bid in oral_ministry.keys() | written_ministry.keys():
        m = responder_label(bid)
        if m:
            ministry_totals[m] += 1
    # Tie-break by name, not by `most_common`'s insertion order: ministries with
    # equal counts are common in a short scope, and insertion order here is the
    # order SQLite happened to return the answer events in. Left to chance, which
    # of two tied ministries keeps its own node and which is pooled into "other"
    # can differ between two identical requests — a diagram that reshuffles on
    # reload, and a drill-down that disagrees with the diagram it was clicked from.
    ranked = sorted(ministry_totals.items(), key=lambda kv: (-kv[1], kv[0]))
    top_ministries = (set(ministry_totals) if expand_other
                      else {m for m, _ in ranked[:top]})

    def faction_key(bid: str) -> Optional[int]:
        fid = asker_faction.get(bid)
        return fid if (fid is not None and fid in factions) else None

    def type_key(bid: str) -> str:
        """The question's refined type code (A/I/K/W) — the first Sankey column;
        W is a written question split out from the plain kérdés (K)."""
        return bill_type.get(bid, "")

    def answerer_key(bid: str) -> tuple[str, str]:
        """(kind, label) for the answerer side; label is '' for special nodes."""
        answered_orally = bid in oral_ministry
        if not answered_orally and bid not in written_ministry:
            return ("unanswered", "")
        m = responder_label(bid)
        if not m:
            # answered, but the responder isn't named upstream: keep the
            # "answered orally" node for plenary answers; pool the (rare)
            # unnamed written answers into "other" — with the pool ungrouped
            # they are all that would be left in it, so name them for what they
            # are ("unnamed") instead of "other ministry".
            if answered_orally:
                return ("oral", "")
            return ("unnamed", "") if expand_other else ("other", "")
        return ("ministry", m) if m in top_ministries else ("other", "")

    return order_ids, factions, faction_key, type_key, answerer_key, slug_of


@router.get("/questions/sankey")
def questions_sankey(
    period: Optional[List[int]] = Query(
        None, description="Electoral period number(s); repeat to scope to several cycles"),
    limit: int = Query(12, ge=1, le=40),   # top-N responding ministries, rest pooled
    include_type: bool = True,             # prepend the question-type column
    expand_other: bool = False,            # ungroup the pool: one node per responder
    db: sqlite3.Connection = Depends(get_db),
):
    """Sankey data for the Kérdések page (BILL-11): every question-type iromány
    flows from its asker's faction to whoever answered it (the responding
    portfolio whether the answer came orally in plenary or in writing, both
    naming the responder in `related_label`, or an "unanswered" node otherwise).
    Faction colours (§4.1) carry through the faction → answerer stage, so the
    right half stays split by party. When ``include_type`` is set (the default) a
    question-type column (interpelláció / kérdés / azonnali kérdés / írásbeli) is
    prepended, giving type → faction → answerer; clearing it collapses the
    diagram to just faction → answerer. Derived at query time from the shared
    bill / bill_event / bill_sponsor data (EXT-2); honours the global cycle
    (§4A). Only the top-``limit`` ministries stay glanceable — the rest are
    pooled into one "other" node; ``expand_other`` ungroups that pool, giving
    every responder its own node (a taller but complete answerer column). Each
    node carries the identity a click needs to drill into ``/questions/list``
    (asker nodes their ``faction_id``, type nodes their ``main_type``, ministry
    nodes their label)."""
    from collections import Counter

    bill_ids, factions, faction_key, type_key, answerer_key, slug_of = _classify_questions(
        db, period, limit, expand_other)
    if not bill_ids:
        return {"period": period_list(period), "total": 0, "nodes": [], "links": []}

    def asker_of(bid):
        fid = faction_key(bid)
        if fid is not None:
            return (f"f{fid}", {"side": "asker", "kind": "faction", "faction_id": fid,
                                "label": factions[fid]["label"],
                                "color": factions[fid]["color"]})
        return ("__nofaction__", {"side": "asker", "kind": "nofaction",
                                  "faction_id": None, "label": None, "color": None})

    # The faction → answerer stage is always present. The question-type stage
    # (type → faction) is prepended only when include_type is set, shifting the
    # faction and answerer columns one to the right.
    fa: Counter = Counter()   # (asker_key, answerer_key) -> count  [faction → answerer]
    tf: Counter = Counter()   # (type_code, asker_key) -> count     [type → faction]
    asker_meta: dict[str, dict] = {}
    for bid in bill_ids:
        akey, meta = asker_of(bid)
        asker_meta[akey] = meta
        fa[(akey, answerer_key(bid))] += 1
        if include_type:
            tf[(type_key(bid), akey)] += 1

    # Column volumes drive node ordering (busiest first) within each column.
    type_vol: Counter = Counter()
    asker_vol: Counter = Counter()
    answerer_vol: Counter = Counter()
    for (ak, ans), n in fa.items():
        asker_vol[ak] += n; answerer_vol[ans] += n
    for (tc, ak), n in tf.items():
        type_vol[tc] += n

    faction_col = 1 if include_type else 0
    answerer_col = faction_col + 1

    nodes: list[dict] = []
    index: dict = {}
    if include_type:
        for tc, _ in type_vol.most_common():
            index[("t", tc)] = len(nodes)
            nodes.append({"column": 0, "side": "type", "kind": "type",
                          "main_type": tc, "label": None,
                          "color": _TYPE_COLORS.get(tc)})
    for ak, _ in asker_vol.most_common():
        index[("a", ak)] = len(nodes)
        nodes.append({"column": faction_col, **asker_meta[ak]})
    for ans, _ in answerer_vol.most_common():
        kind, label = ans
        index[("s", ans)] = len(nodes)
        nodes.append({"column": answerer_col, "side": "answerer", "kind": kind,
                      "label": label or None, "color": None,
                      # A named tárca links through to its own page (§6C / MIN-7);
                      # the pooled and unanswered nodes have nowhere to go.
                      "slug": slug_of.get(label) if kind == "ministry" else None})

    # Type → faction ribbons take the question type's colour; faction → answerer
    # ribbons take the asking faction's colour, so the right half stays split and
    # coloured by party.
    links: list[dict] = []
    if include_type:
        links += [{
            "source": index[("t", tc)], "target": index[("a", ak)], "value": n,
            "color": _TYPE_COLORS.get(tc),
        } for (tc, ak), n in tf.items()]
    links += [{
        "source": index[("a", ak)], "target": index[("s", ans)], "value": n,
        "color": asker_meta[ak]["color"],
    } for (ak, ans), n in fa.items()]

    return {"period": period_list(period), "total": len(bill_ids),
            "nodes": nodes, "links": links}


@router.get("/questions/list")
def questions_list(
    period: Optional[List[int]] = Query(
        None, description="Electoral period number(s); repeat to scope to several cycles"),
    faction: Optional[str] = None,     # asker faction id, or "none" for the no-faction node
    main_type: Optional[str] = None,   # question type code (A/I/K) for the middle column
    answerer: Optional[str] = None,    # ministry | oral | other | unnamed | unanswered
    ministry: Optional[str] = None,    # the ministry label when answerer == "ministry"
    top: int = Query(12, ge=1, le=40),  # MUST match the Sankey's `limit` so nodes agree
    expand_other: bool = False,        # MUST match the Sankey's `expand_other`
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
):
    """The individual questions behind one Sankey flow or node (BILL-11): the
    question-type irományok whose asker faction is ``faction``, whose question
    type is ``main_type`` and/or whose answerer is ``answerer`` (a specific
    ``ministry`` when ``answerer == "ministry"``). A clicked stage-1 ribbon fixes
    faction+type, a stage-2 ribbon fixes type+answerer, a node fixes just itself.
    Uses the same classification as the diagram (same ``top`` ranking and
    ``expand_other`` setting) so a clicked flow lists exactly its questions.
    Paginated, newest-first; each links back to its detail view. Omitting the
    filters lists every question."""
    order_ids, _factions, faction_key, type_key, answerer_key, _slugs = _classify_questions(
        db, period, top, expand_other)

    want = (answerer, ministry or "") if answerer == "ministry" else (answerer, "")

    def fmatch(bid: str) -> bool:
        fk = faction_key(bid)
        if faction == "none":
            return fk is None
        if faction is None:
            return True
        return fk is not None and str(fk) == str(faction)

    matched = [bid for bid in order_ids
               if fmatch(bid)
               and (main_type is None or type_key(bid) == main_type)
               and (answerer is None or answerer_key(bid) == want)]
    total = len(matched)
    page_ids = matched[offset:offset + limit]
    if not page_ids:
        return {"total": total, "limit": limit, "offset": offset, "bills": []}

    ph = ",".join("?" * len(page_ids))
    rows = {r["id"]: r for r in db.execute(
        f"""SELECT id, bill_number, title, type, main_type, status, submitted_date
            FROM bill WHERE id IN ({ph})""", page_ids)}
    sponsors = _sponsors_for(db, page_ids)
    responders = _responders_for(db, page_ids)
    bills = [{
        "id": r["id"], "bill_number": r["bill_number"], "title": r["title"],
        "type": r["type"], "main_type": r["main_type"], "status": r["status"],
        "submitted_date": r["submitted_date"], "sponsors": sponsors.get(bid, []),
        "responder": responders.get(bid),
    } for bid in page_ids if (r := rows.get(bid))]
    return {"total": total, "limit": limit, "offset": offset, "bills": bills}


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
                     end: sqlite3.Row, session_ids: list[str]) -> list[dict]:
    """The plenary speeches from the debate-opening anchor through the closing
    one (inclusive), in proceedings order, restricted to the sittings that
    actually record a debate event for this bill (the anchors' sittings plus
    any "… folytatása" continuation days). A date-only window would sweep in
    whole unrelated sittings falling between the anchors and interleave two
    sittings sharing a calendar date. Each speaker who is a known MP links to
    their profile via the shared `person` entity (EXT-2)."""
    ph = ",".join("?" * len(session_ids))
    rows = db.execute(
        f"""SELECT sp.uid, sp.speech_index, sp.speaker_label, sp.person_id,
                  sp.speaker_status, sp.duration, sp.has_text,
                  p.label AS person_label, p.photo_uri,
                  f.label AS faction_label, f.color AS faction_color,
                  ss.date AS session_date, ss.sitting
           FROM speech sp
           JOIN session ss ON ss.id = sp.session_id
           LEFT JOIN person p ON p.person_id = sp.person_id
           LEFT JOIN faction f ON f.id = sp.faction_id
           WHERE sp.session_id IN ({ph})
             AND (sp.session_id <> ? OR sp.speech_index >= ?)
             AND (sp.session_id <> ? OR sp.speech_index <= ?)
           ORDER BY ss.date, ss.id, sp.speech_index""",
        [*session_ids,
         start["session_id"], start["speech_index"],
         end["session_id"], end["speech_index"]]).fetchall()
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
                  s.uid AS speech_uid, s.speech_index, s.session_id,
                  ss.date AS sdate
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
        # The debate's sittings: the anchors' own, plus any day the source
        # marks as a continuation ("<phase> folytatása") between them. A
        # multi-day debate resumed on a later sitting still reads end to end,
        # but sittings that merely fall between the anchor dates stay out.
        cont_name = f"{label} folytatása"
        session_ids = {start["session_id"], e["session_id"]}
        session_ids.update(
            ev["session_id"] for ev in evs
            if start["ord"] < ev["ord"] < e["ord"]
            and ev["name"] == cont_name and ev["session_id"] is not None)
        speeches = _debate_speeches(db, start, e, sorted(session_ids))
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
    votes, committees, deadlines, documents, non-self-standing motions).

    Each vote carries the same derived figures the Votes list shows on its cards:
    attendance (`present` / `seats` / `attendance`) and cross-voting (`defectors`,
    `defector_share`, `defector_factions`), for the votes the Votes module has
    ingested (`vote_ref`)."""
    b = db.execute("SELECT * FROM bill WHERE id = ?", (bill_id,)).fetchone()
    # An iromány of a cycle this deployment does not serve (CYC-7) is not found here.
    if not b or not period_in_scope(b["period_number"]):
        raise HTTPException(404, "Bill not found")
    sponsors = _sponsors_for(db, [bill_id]).get(bill_id, [])
    topic = _topics_for(db, [bill_id]).get(bill_id)

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
    try:
        votes = _rows(db,
            """SELECT bv.vote_date, bv.subject, bv.yes, bv.no, bv.abstain,
                      bv.result, bv.vote_id,
                      (SELECT v.id FROM vote v WHERE v.id = bv.vote_id) AS vote_ref
               FROM bill_vote bv WHERE bv.bill_id = ? ORDER BY bv.ord""", bill_id)
    except sqlite3.OperationalError:
        # Pre-votes-migration DB: the `vote` table may not exist (the other
        # consumers guard the same way) — degrade to plain tallies, no links.
        votes = _rows(db,
            """SELECT bv.vote_date, bv.subject, bv.yes, bv.no, bv.abstain,
                      bv.result, bv.vote_id, NULL AS vote_ref
               FROM bill_vote bv WHERE bv.bill_id = ? ORDER BY bv.ord""", bill_id)
    # Attendance and cross-voting for the votes the Votes module has ingested, so
    # a bill's tallies read like the cards in that list (same numbers, same
    # source). A vote we hold no per-faction breakdown for keeps the null shape.
    _ingested = [v["vote_ref"] for v in votes if v["vote_ref"]]
    _attendance = attendance_for(db, _ingested)
    _crossvoting = crossvoting_for(db, _ingested)
    for v in votes:
        v.update(_attendance.get(v["vote_ref"], NO_ATTENDANCE))
        v.update(_crossvoting.get(v["vote_ref"], NO_CROSSVOTING))
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
        # What the model read the iromány's own document text as being about
        # (TOPIC-8); null where it had nothing confident to say.
        "topic": topic,
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
