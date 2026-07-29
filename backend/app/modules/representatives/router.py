"""Representatives & Statistics module API (§6) — /api/v1/representatives.

Self-contained slice (EXT-1) reading the shared `person`/`faction`/`membership`
core tables and the precomputed aggregate tables (REP-7). Statistics are served
straight from `person_stats`/`faction_stats`, never aggregated live (PERF-1).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ...config import settings
from ...db import (fold_text, get_db, like_contains, period_and, period_list,
                   period_sql)
from ...query_cache import cached_aggregate

router = APIRouter(prefix="/representatives", tags=["representatives"])


# Quorum-establishment "votes" (határozatképesség megállapítása) are procedural
# headcounts to confirm the House is quorate, not substantive decisions —
# parlament.hu's per-MP statistics don't count them as votes. We exclude them
# from every per-MP vote tally (the participation pie, the absence %, and the
# profile's grouped vote list) so our numbers match the official ones. They're
# identified by their result label, NOT the subject: the subject "egyéb
# szavazás" is also used for real votes, whereas the "Határozatképes"/
# "Határozatképtelen" results only ever mark a quorum check.
_QUORUM_RESULTS = ("Határozatképes", "Határozatképtelen")


def _exclude_quorum(alias: str = "v") -> str:
    """A ``WHERE``-appendable SQL fragment dropping the procedural quorum-check
    votes from a per-MP vote query. ``alias`` is the ``vote`` table alias (``""``
    when the query selects from ``vote`` directly with no alias)."""
    vals = ", ".join(f"'{r}'" for r in _QUORUM_RESULTS)
    col = f"{alias}.result" if alias else "result"
    return f" AND {col} NOT IN ({vals})"


def _has_advocate_columns(db: sqlite3.Connection) -> bool:
    """Whether the (regenerable) DB knows about nationality advocates — false only
    on a DB built before the advocate registry existed, so the endpoints keep
    working (MPs only) until the next loader run adds the columns."""
    return any(r["name"] == "is_advocate"
               for r in db.execute("PRAGMA table_info(person)"))


@router.get("")
def list_representatives(
    q: Optional[str] = None,
    faction_id: Optional[int] = None,
    period: Optional[List[int]] = Query(
        None, description="Electoral period number(s); repeat to scope to several cycles"),
    constituency: Optional[str] = None,
    role: str = Query("mp", pattern="^(mp|advocate|all)$",
                      description="mp (default) | advocate (nemzetiségi szószólók) | all"),
    nationality: Optional[str] = None,
    sort: str = Query("name", pattern="^(name|speeches|speaking_time)$"),
    limit: int = Query(60, ge=1, le=300),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
):
    """Browsable, filterable representative list (REP-1). Filters combine.

    ``role`` picks which mandate the list covers: MPs (the default, so an existing
    caller sees exactly what it did before), the **nationality advocates**
    (szószólók — they sit and speak but hold no mandate, REP-9), or both. On a DB
    predating the advocate registry the parameter degrades to MPs only."""
    advocates_known = _has_advocate_columns(db)
    where = []
    params: dict = {}
    if not advocates_known:
        # A DB predating the advocate registry knows only MPs, so an advocate-only
        # request comes back honestly empty instead of quietly listing MPs.
        where.append("0" if role == "advocate" else "p.is_mp = 1")
    elif role == "mp":
        where.append("p.is_mp = 1")
    elif role == "advocate":
        where.append("p.is_advocate = 1")
    else:
        where.append("(p.is_mp = 1 OR p.is_advocate = 1)")
    if q:
        where.append("fold(p.label) LIKE fold(:q) ESCAPE '\\'")
        params["q"] = like_contains(q.strip())
    if constituency:
        where.append("fold(p.constituency) LIKE fold(:con) ESCAPE '\\'")
        params["con"] = like_contains(constituency)
    if nationality and advocates_known:
        where.append("fold(p.nationality) LIKE fold(:nat) ESCAPE '\\'")
        params["nat"] = like_contains(nationality)
    mem_sql = period_sql(period, "m.period_number")
    if faction_id is not None:
        # One membership row must match both — two independent EXISTS would
        # list an MP who was in this faction only during a cycle out of scope.
        cond = "m.faction_id=:fid" + (f" AND {mem_sql}" if mem_sql else "")
        where.append("EXISTS (SELECT 1 FROM membership m "
                     f"WHERE m.person_id=p.person_id AND {cond})")
        params["fid"] = faction_id
    elif mem_sql:
        where.append("EXISTS (SELECT 1 FROM membership m "
                     f"WHERE m.person_id=p.person_id AND {mem_sql})")
    where_sql = " AND ".join(where)

    # fold() the name sort: BINARY collation puts accented Hungarian surnames
    # (Ágh, Árvay) after Z.
    order = {"name": "fold(p.lastname), fold(p.label)",
             "speeches": "stat.speech_count DESC",
             "speaking_time": "stat.speaking_seconds DESC"}[sort]

    # Scope the per-MP stats and the shown faction to the selected cycle(s) (§4A):
    # with `period` set, sum that cycle's (or those cycles') `person_stats` rows
    # and use the most recent membership within scope; otherwise the precomputed
    # all-cycles row (period_number IS NULL) and the most recent faction. This is
    # why the list never mixes an out-of-scope cycle's speech counts into another.
    stat_scope = (period_sql(period, "period_number")
                  or "period_number IS NULL")   # the precomputed all-cycles row
    stat_sub = ("SELECT person_id, SUM(speech_count) AS speech_count, "
                "SUM(speaking_seconds) AS speaking_seconds "
                f"FROM person_stats WHERE {stat_scope} GROUP BY person_id")
    faction_sub = ("SELECT m.faction_id FROM membership m WHERE m.person_id = p.person_id"
                   + (f" AND {mem_sql}" if mem_sql else "")
                   + " ORDER BY m.period_number DESC LIMIT 1")

    total = db.execute(f"SELECT COUNT(*) AS c FROM person p WHERE {where_sql}",
                       params).fetchone()["c"]
    # An advocate has no faction or constituency; their nationality is the
    # affiliation the card shows in its place.
    mandate_cols = ("p.is_advocate, p.nationality," if advocates_known
                    else "0 AS is_advocate, NULL AS nationality,")
    rows = db.execute(
        f"""SELECT p.person_id, p.label, p.firstname, p.lastname, p.photo_uri,
                   p.constituency, {mandate_cols}
                   COALESCE(stat.speech_count, 0) AS speech_count,
                   COALESCE(stat.speaking_seconds, 0) AS speaking_seconds,
                   f.id AS faction_id, f.label AS faction_label, f.color AS faction_color
            FROM person p
            LEFT JOIN ({stat_sub}) stat ON stat.person_id = p.person_id
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
                "is_advocate": bool(r["is_advocate"]),
                "nationality": r["nationality"],
                "speech_count": r["speech_count"],
                "speaking_seconds": r["speaking_seconds"],
                "faction": {"id": r["faction_id"], "label": r["faction_label"],
                            "color": r["faction_color"]} if r["faction_label"] else None,
            } for r in rows
        ],
    }


@router.get("/factions")
def list_factions(period: Optional[List[int]] = Query(
                      None, description="Electoral period number(s)"),
                  db: sqlite3.Connection = Depends(get_db)):
    """Factions with aggregate stats and consistent colours (REP-4).

    Scoped to the global cycle(s) (§4A): one selected cycle uses that cycle's
    precomputed aggregate row, "all cycles" the all-periods row
    (``period_number IS NULL``). A multi-cycle scope has no precomputed row —
    ``mp_count`` is a distinct-speaker count, so summing two cycles' rows would
    count an MP active in both twice — so it is derived from ``speech`` and
    memoized per scope (the scan is why the precomputed rows exist at all)."""
    nums = period_list(period)
    if len(nums) > 1:
        return cached_aggregate(
            "faction_stats_multi", tuple(nums),
            lambda: {"factions": _factions_across_cycles(db, nums),
                     "methodology": _FACTION_METHODOLOGY})
    join = ("fs.faction_id=f.id AND "
            + (period_sql(nums, "fs.period_number") or "fs.period_number IS NULL"))
    rows = db.execute(
        f"""SELECT f.id, f.label, f.color,
                  COALESCE(fs.speech_count, 0) AS speech_count,
                  COALESCE(fs.speaking_seconds, 0) AS speaking_seconds,
                  COALESCE(fs.mp_count, 0) AS mp_count
           FROM faction f
           LEFT JOIN faction_stats fs ON {join}
           ORDER BY fs.speaking_seconds DESC""").fetchall()
    return {"factions": [_faction_out(r) for r in rows],
            "methodology": _FACTION_METHODOLOGY}


def _faction_out(r: sqlite3.Row) -> dict:
    """Shape one faction aggregate row; averages are per speaking MP."""
    mp = r["mp_count"] or 0
    return {
        "id": r["id"], "label": r["label"], "color": r["color"],
        "speech_count": r["speech_count"],
        "speaking_seconds": r["speaking_seconds"],
        "mp_count": mp,
        "avg_speaking_seconds": (r["speaking_seconds"] / mp) if mp else 0,
        "avg_speeches": (r["speech_count"] / mp) if mp else 0,
    }


def _factions_across_cycles(db: sqlite3.Connection, nums: list[int]) -> list[dict]:
    """Faction aggregates over several cycles at once, computed from ``speech``.

    Mirrors what the loader precomputes per cycle (statistics-eligible speeches
    only — procedural/chairing excluded, STAT-1) but with ``mp_count`` counted
    DISTINCT across the whole scope, so an MP who spoke in both cycles counts
    once and the averages stay meaningful."""
    rows = db.execute(
        f"""SELECT f.id, f.label, f.color,
                   COALESCE(agg.speech_count, 0) AS speech_count,
                   COALESCE(agg.speaking_seconds, 0) AS speaking_seconds,
                   COALESCE(agg.mp_count, 0) AS mp_count
            FROM faction f
            LEFT JOIN (
                SELECT s.faction_id, COUNT(*) AS speech_count,
                       COALESCE(SUM(s.duration), 0) AS speaking_seconds,
                       COUNT(DISTINCT s.person_id) AS mp_count
                FROM speech s
                WHERE s.faction_id IS NOT NULL AND s.procedural = 0
                  AND {period_sql(nums, "s.period_number")}
                GROUP BY s.faction_id
            ) agg ON agg.faction_id = f.id
            ORDER BY agg.speaking_seconds DESC""").fetchall()
    return [_faction_out(r) for r in rows]


@router.get("/resolve")
def resolve_speakers(
    name: List[str] = Query(default=[]),
    db: sqlite3.Connection = Depends(get_db),
):
    """Resolve interjection speaker names to representatives (heckle attribution).

    The inline transcript lifts parenthetical heckles like
    ``Vitályos Eszter: Végrehajtod vagy nem?`` out of a speech; the caller passes
    the leading names here to attach a face + profile link. Matching is accent-
    and case-insensitive on the exact ``person.label`` (surname + given). Only a
    name mapping to a SINGLE person (MPs preferred on a tie) is returned — an
    ambiguous or unknown name is omitted, and the caller renders it as plain
    text. Keyed in the response by the requested spelling.

    Declared before ``/{person_id}`` so "resolve" isn't captured as an MP id."""
    names = [n.strip() for n in name if n and n.strip()][:40]
    resolved: dict = {}
    if not names:
        return {"resolved": resolved}
    # The person table is small (~450 rows); fold every label once and group so
    # a duplicate name (two people, same folded label) is detected as ambiguous.
    groups: dict = {}
    for r in db.execute(
            "SELECT person_id, label, photo_uri, is_mp FROM person").fetchall():
        groups.setdefault(fold_text(r["label"]), []).append(r)
    for n in names:
        rows = groups.get(fold_text(n))
        if not rows:
            continue
        mps = [r for r in rows if r["is_mp"]]
        cand = mps or rows
        if len(cand) == 1:
            r = cand[0]
            resolved[n] = {"person_id": r["person_id"], "label": r["label"],
                           "photo_uri": r["photo_uri"]}
    return {"resolved": resolved}


@router.get("/{person_id}")
def get_representative(person_id: str, period: Optional[List[int]] = Query(
                          None, description="Electoral period number(s)"),
                      db: sqlite3.Connection = Depends(get_db)):
    """Full MP profile (REP-2): bio, faction history, constituency, links.

    The shown ``current_faction`` is scoped to the selected cycle(s) (§4A): with
    ``period`` set it is the MP's faction in the most recent cycle *in scope*,
    otherwise their most recent faction overall. (``faction_history`` always
    lists every cycle — it IS the cross-cycle view.)"""
    p = db.execute("SELECT * FROM person WHERE person_id = ?", (person_id,)).fetchone()
    if not p:
        raise HTTPException(404, "Representative not found")
    current = db.execute(
        f"""SELECT f.id AS faction_id, f.label AS faction_label, f.color AS faction_color,
                   m.position
            FROM membership m LEFT JOIN faction f ON f.id = m.faction_id
            WHERE m.person_id = ?{period_and(period, "m.period_number")}
            ORDER BY m.period_number DESC LIMIT 1""",
        (person_id,)).fetchone()
    # Colour/id maps so each historical faction renders consistently (REP-4) and
    # its badge can link to the faction-filtered rep list (id keyed by label).
    faction_rows = db.execute("SELECT id, label, color FROM faction").fetchall()
    colors = {r["label"]: r["color"] for r in faction_rows}
    faction_ids = {r["label"]: r["id"] for r in faction_rows}
    faction_history = [
        {"cycle": h.get("cycle"), "start": h.get("start"), "end": h.get("end"),
         "faction": {"id": faction_ids.get(h.get("label")),
                     "label": h.get("label"), "color": colors.get(h.get("label"))}
                    if h.get("label") else None}
        for h in _loads(p["faction_history_json"])]
    offices = _loads(p["offices_json"])
    office = _current_office(db, person_id, period, offices)
    return {
        "person_id": p["person_id"], "label": p["label"],
        "label_full": p["label_full"], "firstname": p["firstname"],
        "lastname": p["lastname"], "photo_uri": p["photo_uri"],
        "wikidata_id": p["wikidata_id"], "wikipedia_url": p["wikipedia_url"],
        "kmonitor_url": p["kmonitor_url"],
        "constituency": p["constituency"],
        "seat": p["seat"], "email": p["email"], "website": p["website"],
        "highest_education": p["highest_education"], "active": p["active"],
        "is_mp": bool(p["is_mp"]),
        # Nationality advocate (nemzetiségi szószóló, REP-9): sits and speaks in
        # the House with no representative mandate — hence no faction, no
        # constituency and no vote — so the nationality they speak for is their
        # identifying affiliation, shown where an MP's faction badge goes.
        "is_advocate": bool(_col(p, "is_advocate")),
        "nationality": _col(p, "nationality"),
        # The speaker's government office (tisztség), e.g. "igazságügyi miniszter".
        # Present for office-holders (ministers/state secretaries); it identifies a
        # non-MP speaker — someone who spoke in the House but holds no mandate, so
        # has no faction or constituency — by their post (REP-2). Derived from their
        # speeches (see `_current_office`), scoped to the selected cycle (§4A).
        "office": office["title"] if office else None,
        # When that office applies (REP-2): {start, end, cycles, dates_from} — the
        # title alone reads as the person's current post even when it is one they
        # held cycles ago, so it is always shown dated.
        "office_term": {k: v for k, v in office.items() if k != "title"} if office else None,
        "current_faction": {"id": current["faction_id"], "label": current["faction_label"],
                            "color": current["faction_color"]} if current and current["faction_label"] else None,
        "faction_history": faction_history,
        "education": _loads(p["education_json"]),
        "committees": _loads(p["committees_json"]),
        "offices": _loads(p["offices_json"]),
        "election_history": _loads(p["election_history_json"]),
    }


@router.get("/{person_id}/statistics")
def get_statistics(person_id: str, period: Optional[List[int]] = Query(
                       None, description="Electoral period number(s)"),
                   db: sqlite3.Connection = Depends(get_db)):
    """Per-MP statistics (REP-3) with explicit scope + methodology (REP-5).

    Everything except ``by_period`` (the explicit per-cycle breakdown) is scoped
    to the selected cycle(s) (§4A): with ``period`` set, the headline totals,
    over-time chart and session count cover ONLY those cycles — the totals are
    the sum of their per-cycle aggregate rows; otherwise all cycles (the
    precomputed all-cycles row). The ``scope.description`` names the cycles so
    the scope is explicit."""
    p = db.execute("SELECT person_id, is_mp, external_stats_json, election_history_json "
                   "FROM person WHERE person_id=?", (person_id,)).fetchone()
    if not p:
        raise HTTPException(404, "Representative not found")

    periods = period_list(period)
    stat_scope = (period_sql(periods, "period_number")
                  or "period_number IS NULL")   # the precomputed all-cycles row
    sess_scope = period_and(periods, "s.period_number")
    totals = db.execute(
        f"""SELECT SUM(speech_count) AS speech_count,
                   SUM(speaking_seconds) AS speaking_seconds,
                   SUM(sentence_count) AS sentence_count
            FROM person_stats WHERE person_id=? AND {stat_scope}""",
        (person_id,)).fetchone()
    over_time = db.execute(
        f"""SELECT pss.session_id, pss.date, pss.speech_count, pss.speaking_seconds,
                   s.sitting, s.period_number
            FROM person_session_stats pss JOIN session s ON s.id=pss.session_id
            WHERE pss.person_id=?{sess_scope} ORDER BY pss.date""",
        (person_id,)).fetchall()
    sessions_covered = db.execute(
        f"""SELECT COUNT(DISTINCT pss.session_id) AS c FROM person_session_stats pss
            JOIN session s ON s.id=pss.session_id
            WHERE pss.person_id=?{sess_scope}""", (person_id,)).fetchone()["c"]
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
        bills_submitted = (_own_bills_for_cycles(bills_by_cycle, periods)
                           if periods else _latest_own_bills(bills_by_cycle))

    # Attendance (REP-3): on how many roll-call votes the MP cast no vote at all,
    # both nominally and as a share of the votes they could have cast in scope.
    # "No vote cast" covers every non-voting participation category — "jelen, nem
    # szavazott" (`value_code = 'novote'`), "igazoltan távol" (the upstream "Előre
    # bejelentett hiányzó", normalized to `'absent'`) and "nem volt jelen" (no
    # record at all) — i.e. exactly the pie's three non-voting slices; the
    # denominator is the pie's 100% base, so headline % and pie agree. Only
    # meaningful — and only queried — when the Votes module is live (EXT-6); its
    # tables may not exist otherwise.
    # Voting statistics are only shown for actual MPs (is_mp): a minister or other
    # non-representative has no mandate to attend roll calls, so the whole
    # participation section (the absence metric AND the pie) is suppressed for
    # them — otherwise every roll-call vote would read as "nem volt jelen 100%".
    is_mp = bool(p["is_mp"])
    votes_available = settings.module_enabled("votes")
    votes_total = votes_missed = 0
    votes_missed_pct = None
    vote_breakdown = None
    if votes_available and is_mp:
        extra = period_and(periods, "v.period_number")
        vparams: dict = {"pid": person_id}
        # One pass over the MP's roll-call records splits them into the four
        # participation categories shown in the profile pie: "szavazott" (a vote
        # was cast — igen/nem/tartózkodás all count as voting), "nem szavazott"
        # (present, no vote), "igazoltan távol" (pre-announced absence) and —
        # derived below — "nem volt jelen" (no record at all for a vote). `total`
        # counts every record.
        vrow = db.execute(
            f"""SELECT COUNT(*) AS total,
                       SUM(CASE WHEN vr.value_code IN ('yes','no','abstain') THEN 1 ELSE 0 END) AS voted,
                       SUM(CASE WHEN vr.value_code = 'novote'  THEN 1 ELSE 0 END) AS novote,
                       SUM(CASE WHEN vr.value_code = 'absent'  THEN 1 ELSE 0 END) AS absent
                FROM vote_record vr JOIN vote v ON v.id = vr.vote_id
                WHERE vr.person_id = :pid{extra}{_exclude_quorum()}""", vparams).fetchone()
        votes_total = vrow["total"] or 0
        votes_absent = vrow["absent"] or 0

        # The votes the MP has no record in at all split into two categories.
        # First, of all roll-call votes in scope (those with a per-MP list,
        # has_per_mp = 1 — voice/list votes are excluded so they don't inflate
        # everyone's absence, and quorum checks likewise) count the whole
        # universe.
        rc_where = ("has_per_mp = 1" + _exclude_quorum("")
                    + period_and(periods, "period_number"))
        rc_params: dict = {}
        total_rollcall = (db.execute(
            f"SELECT COUNT(*) AS n FROM vote WHERE {rc_where}",
            rc_params).fetchone()["n"] or 0)

        # Of that universe, the ones that fell WITHIN the MP's mandate window(s)
        # are the votes they could actually have taken part in ("eligible"). The
        # windows come from election_history_json's mandateStart/mandateEnd (ISO
        # UTC, so a lexicographic datetime compare is chronological); an MP who
        # took their seat mid-cycle (a replacement) or resigned early has votes
        # outside their mandate. Those are "nem volt képviselő" — kept separate
        # from a genuine absence and excluded from the participation denominator.
        windows = [(e.get("mandateStart"), e.get("mandateEnd"))
                   for e in (_loads(p["election_history_json"]) or [])
                   if e.get("mandateStart")]
        eligible_rollcall = total_rollcall
        if windows:
            conds, wparams = [], dict(rc_params)
            for i, (start, end) in enumerate(windows):
                wparams[f"ms{i}"] = start
                cond = f"vote_datetime >= :ms{i}"
                if end:
                    wparams[f"me{i}"] = end
                    cond += f" AND vote_datetime <= :me{i}"
                conds.append(f"({cond})")
            eligible_rollcall = (db.execute(
                f"SELECT COUNT(*) AS n FROM vote "
                f"WHERE {rc_where} AND ({' OR '.join(conds)})",
                wparams).fetchone()["n"] or 0)

        voted = vrow["voted"] or 0
        novote = vrow["novote"] or 0
        # Clamped at 0 for the rare case where the record count exceeds the
        # eligible universe (e.g. a vote right on the mandate boundary, or votes
        # lacking the flag in a test/partial import).
        not_present = max(0, eligible_rollcall - votes_total)
        not_mp = max(0, total_rollcall - eligible_rollcall)
        vote_breakdown = {
            "voted": voted,              # szavazott (igen + nem + tartózkodás)
            "novote": novote,            # jelen, nem szavazott
            "absent": votes_absent,      # igazoltan távol
            "not_present": not_present,  # nem volt jelen (MP, but no record)
            "not_mp": not_mp,            # nem volt képviselő (outside mandate)
            # `total` is the participation denominator (the 100% base) — it does
            # NOT include not_mp, so time before/after the mandate never counts.
            "total": voted + novote + votes_absent + not_present,
        }
        # Headline attendance metric: every occasion the MP cast no vote, over the
        # pie's 100% base. Equivalent to `total - voted`, but spelled out so the
        # link between the number and the three legend rows it sums stays obvious.
        vb_total = vote_breakdown["total"]
        votes_missed = novote + votes_absent + not_present
        votes_missed_pct = (round(100.0 * votes_missed / vb_total, 1)
                            if vb_total else None)

    if periods:
        labels = {r["number"]: r["label"] for r in db.execute(
            "SELECT number, label FROM electoral_period "
            f"WHERE {period_sql(periods, 'number')}")}
        cyc_label = ", ".join(labels.get(n) or f"{n}. ciklus" for n in periods)
        scope_desc = ("A Parlamonitor által feldolgozott ülésnapok alapján — "
                      f"{cyc_label}.")
    else:
        scope_desc = ("A Parlamonitor által feldolgozott ülésnapok alapján — "
                      "összes ciklus.")

    return {
        "person_id": person_id,
        # `period` stays scalar for a single-cycle scope (null for all cycles or a
        # multi-cycle one); `periods` carries the full scope either way.
        "scope": {"description": scope_desc,
                  "period": periods[0] if len(periods) == 1 else None,
                  "periods": periods,
                  "sessions_covered": sessions_covered},
        "totals": {
            # SUM over no rows yields NULL, so an MP with nothing in scope reads 0.
            "speech_count": (totals["speech_count"] if totals else 0) or 0,
            "speaking_seconds": (totals["speaking_seconds"] if totals else 0) or 0,
            "sentence_count": (totals["sentence_count"] if totals else 0) or 0,
            "bills_submitted": bills_submitted,  # null while Bills module is off
            "bills_available": bills_available,
            "bills_by_cycle": bills_by_cycle,
            "votes_available": votes_available,  # false while Votes module is off
            "votes_total": votes_total,          # roll-call votes in scope
            # Occasions with no vote cast: novote + absent + not_present, over
            # `vote_breakdown.total` (null when there is nothing in scope).
            "votes_missed": votes_missed,
            "votes_missed_pct": votes_missed_pct,
            "vote_breakdown": vote_breakdown,    # 5-way participation split, null while Votes off
        },
        "by_period": [dict(r) for r in by_period],
        "over_time": [dict(r) for r in over_time],
        "methodology": _MP_METHODOLOGY,
    }


@router.get("/{person_id}/activity")
def get_activity(person_id: str, period: Optional[List[int]] = Query(
                     None, description="Electoral period number(s)"),
                 db: sqlite3.Connection = Depends(get_db)):
    """Per-day activity for the contribution board (REP-8).

    Returns, for every calendar day on which the MP did anything, the count of
    their **statistics-eligible** speeches (procedural/chairing excluded — STAT-1,
    via the precomputed ``person_session_stats``) and the **irományok they
    submitted** that day (all types, via the Bills module). Scoped to the selected
    cycle (§4A) when ``period`` is set. Served straight from aggregates/indexed
    columns so it never blocks on live work (REP-7). The documents contribution is
    only counted when the Bills module is enabled (EXT-6) — its tables may not
    exist otherwise — and is then flagged ``documents_available`` so the UI can
    disclose, rather than fake, the metric."""
    if not db.execute("SELECT 1 FROM person WHERE person_id=?", (person_id,)).fetchone():
        raise HTTPException(404, "Representative not found")

    days: dict[str, dict] = {}

    def _day(date: str) -> dict:
        return days.setdefault(date, {"date": date, "speeches": 0,
                                      "documents": 0, "speaking_seconds": 0.0})

    # Speeches per day. `person_session_stats` already excludes procedural speeches
    # (STAT-1) and carries the sitting date; join `session` only to scope by cycle.
    sextra = period_and(period, "s.period_number")
    sparams: dict = {"pid": person_id}
    for r in db.execute(
        f"""SELECT substr(pss.date, 1, 10) AS day,
                   SUM(pss.speech_count) AS speeches,
                   SUM(pss.speaking_seconds) AS seconds
            FROM person_session_stats pss JOIN session s ON s.id = pss.session_id
            WHERE pss.person_id = :pid{sextra}
            GROUP BY day""", sparams).fetchall():
        d = _day(r["day"])
        d["speeches"] = r["speeches"] or 0
        d["speaking_seconds"] = r["seconds"] or 0.0

    # Irományok submitted per day — only when the Bills module is live (EXT-6).
    # Every iromány type the MP sponsored counts (BILL-9); a bill with several
    # sponsors is counted once for this MP (DISTINCT).
    documents_available = settings.module_enabled("bills")
    if documents_available:
        bextra = period_and(period, "b.period_number")
        bparams = {"pid": person_id}
        for r in db.execute(
            f"""SELECT substr(b.submitted_date, 1, 10) AS day,
                       COUNT(DISTINCT b.id) AS documents
                FROM bill b JOIN bill_sponsor bs ON bs.bill_id = b.id
                WHERE bs.person_id = :pid AND b.submitted_date IS NOT NULL{bextra}
                GROUP BY day""", bparams).fetchall():
            _day(r["day"])["documents"] = r["documents"] or 0

    out = sorted(days.values(), key=lambda d: d["date"])
    for d in out:
        d["total"] = d["speeches"] + d["documents"]
    return {
        "person_id": person_id,
        "scope": {"periods": period_list(period)},
        "documents_available": documents_available,
        "days": out,
        "totals": {
            "speeches": sum(d["speeches"] for d in out),
            "documents": sum(d["documents"] for d in out),
            "active_days": len(out),
        },
    }


@router.get("/{person_id}/speech-days")
def get_speech_days(person_id: str, period: Optional[List[int]] = Query(
                        None, description="Electoral period number(s)"),
                    db: sqlite3.Connection = Depends(get_db)):
    """The sitting days an MP spoke on, reverse-chronological, with a speech
    count per day (REP-2). Drives the grouped, lazy-loaded speech list on the
    profile: the speeches of a day are fetched on demand via ``/speeches``
    filtered by ``session_id``. Scoped to the selected cycle (§4A) when
    ``period`` is set. Counts cover ALL speeches (procedural included), matching
    what ``/speeches`` returns — not the procedural-excluded statistics."""
    extra = period_and(period, "ss.period_number")
    params: dict = {"pid": person_id}
    rows = db.execute(
        f"""SELECT ss.id AS session_id, ss.date, ss.sitting,
                   COUNT(*) AS count, SUM(sp.duration) AS seconds
            FROM speech sp
            JOIN session ss ON ss.id = sp.session_id
            WHERE sp.person_id = :pid{extra}
            GROUP BY ss.id
            ORDER BY ss.date DESC, ss.sitting DESC""", params).fetchall()
    return {
        "total": sum(r["count"] for r in rows),
        "days": [
            {"session_id": r["session_id"], "date": r["date"],
             "sitting": r["sitting"], "count": r["count"],
             "seconds": r["seconds"] or 0}
            for r in rows],
    }


@router.get("/{person_id}/speeches")
def get_speeches(person_id: str, period: Optional[List[int]] = Query(
                     None, description="Electoral period number(s)"),
                 session_id: Optional[str] = None,
                 limit: int = Query(50, ge=1, le=200),
                 offset: int = Query(0, ge=0),
                 db: sqlite3.Connection = Depends(get_db)):
    """Reverse-chronological list of an MP's speeches (REP-2), scoped to the
    selected cycle (§4A) when ``period`` is set, and to a single sitting day
    when ``session_id`` is set (used by the grouped, lazy-loaded list)."""
    extra = period_and(period, "ss.period_number")
    params: dict = {"pid": person_id}
    if session_id is not None:
        extra += " AND sp.session_id = :sid"
        params["sid"] = session_id
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


@router.get("/{person_id}/vote-days")
def get_vote_days(person_id: str, period: Optional[List[int]] = Query(
                      None, description="Electoral period number(s)"),
                  db: sqlite3.Connection = Depends(get_db)):
    """The sitting days an MP voted on, reverse-chronological, with a roll-call
    count per day (EXT-2). Drives the grouped, lazy-loaded vote list on the
    profile: a day's votes are fetched on demand via ``/votes`` filtered by
    ``date``. Scoped to the selected cycle (§4A) when ``period`` is set;
    procedural quorum checks are excluded (see ``_exclude_quorum``) so the count
    matches the participation stats. Empty when the Votes module is disabled
    (EXT-6) — guard before querying."""
    if not settings.module_enabled("votes"):
        return {"total": 0, "days": [], "available": False}
    extra = period_and(period, "v.period_number")
    params: dict = {"pid": person_id}
    rows = db.execute(
        f"""SELECT substr(v.vote_datetime, 1, 10) AS date, COUNT(*) AS count
            FROM vote_record vr JOIN vote v ON v.id = vr.vote_id
            WHERE vr.person_id = :pid{extra}{_exclude_quorum()}
            GROUP BY date ORDER BY date DESC""", params).fetchall()
    return {
        "total": sum(r["count"] for r in rows), "available": True,
        "days": [{"date": r["date"], "count": r["count"]} for r in rows],
    }


@router.get("/{person_id}/votes")
def get_votes(person_id: str, period: Optional[List[int]] = Query(
                  None, description="Electoral period number(s)"),
              date: Optional[str] = None,
              limit: int = Query(50, ge=1, le=200),
              offset: int = Query(0, ge=0),
              db: sqlite3.Connection = Depends(get_db)):
    """How an MP voted, reverse-chronologically (the reciprocal of the Votes
    module's per-MP roll call, EXT-2), scoped to the selected cycle (§4A) when
    ``period`` is set, and to a single sitting day when ``date`` (YYYY-MM-DD) is
    set (used by the grouped, lazy-loaded list). Procedural quorum checks are
    excluded (see ``_exclude_quorum``). Empty when the Votes module is disabled
    (EXT-6) — its tables may not exist, so guard before querying."""
    if not settings.module_enabled("votes"):
        return {"total": 0, "limit": limit, "offset": offset, "votes": [],
                "available": False}
    extra = period_and(period, "v.period_number")
    params: dict = {"pid": person_id}
    if date is not None:
        extra += " AND substr(v.vote_datetime, 1, 10) = :date"
        params["date"] = date
    total = db.execute(
        f"""SELECT COUNT(*) AS c FROM vote_record vr JOIN vote v ON v.id = vr.vote_id
            WHERE vr.person_id = :pid{extra}{_exclude_quorum()}""", params).fetchone()["c"]
    rows = db.execute(
        f"""SELECT v.id, v.vote_datetime, v.subject, v.result,
                  vr.value, vr.value_code
           FROM vote_record vr JOIN vote v ON v.id = vr.vote_id
           WHERE vr.person_id = :pid{extra}{_exclude_quorum()}
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


# Two spells of the same office separated by no more than this many days are one
# term: upstream splits a continuously held post at every electoral-cycle boundary
# (and at re-appointment after an election), so "államtitkár" held 2014→2022 arrives
# as 2014-06-14→2018-05-17 plus 2018-05-21→2022-05-24. Bridging those gaps reports
# the term a reader would recognise, while a real interruption (out of office for a
# cycle, back later) stays two separate terms.
_OFFICE_TERM_GAP_DAYS = 62


def _office_term(offices: list, title: str,
                 last_date: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """The upstream term boundaries (start, end) for the office named ``title``.

    ``offices`` is the person's upstream office list (``person.offices_json``:
    ``{title, start, end}`` entries, newest first) — the authoritative dates, which
    begin at the appointment rather than at the first speech. Back-to-back spells of
    the same title are merged (see ``_OFFICE_TERM_GAP_DAYS``); of the resulting
    terms we return the one covering ``last_date`` (the latest speech carrying the
    title), else the most recent one. ``(None, None)`` when the list names no such
    office, leaving the caller on its speech-derived span."""
    key = (title or "").strip().casefold()
    spells = sorted(
        ((o.get("start"), o.get("end")) for o in offices or []
         if isinstance(o, dict) and (o.get("title") or "").strip().casefold() == key
         and o.get("start")),
        key=lambda s: s[0])
    if not spells:
        return (None, None)
    terms: list[list] = []
    for start, end in spells:
        gap = _day_gap(terms[-1][1], start) if terms else None
        if gap is not None and gap <= _OFFICE_TERM_GAP_DAYS:
            # Same post, continued: keep the earlier start, take the later end.
            if not terms[-1][1] or (end or "") > terms[-1][1]:
                terms[-1][1] = end
            continue
        terms.append([start, end])
    if last_date:
        for start, end in terms:
            if start[:10] <= last_date and (not end or last_date <= end[:10]):
                return (start, end)
    return tuple(terms[-1])


def _day_gap(end: Optional[str], start: Optional[str]) -> Optional[int]:
    """Days between an office spell's ``end`` and the next spell's ``start``, or
    None when either timestamp is missing/unparseable (so no merge happens)."""
    if not end or not start:
        return None
    try:
        return (date.fromisoformat(start[:10]) - date.fromisoformat(end[:10])).days
    except ValueError:
        return None


def _current_office(db: sqlite3.Connection, person_id: str,
                    period: Optional[List[int]],
                    offices: Optional[list] = None) -> Optional[dict]:
    """The speaker's most recent government office (tisztség) in scope **with the
    time it refers to**, or None.

    A speaker's office is recorded per speech (``speech.speaker_office``, from the
    upstream *tisztség* — see the loader). Most office-holders keep one office, but
    a promotion mid-cycle (or a post held cycles ago and never held since) leaves
    several, so we take the **most recent** one (latest sitting date, then latest
    speech within the day). An undated title reads as the person's *current* post,
    which it often is not, so we also report **when it applies** (REP-2): the term
    from the upstream office list when it names the same post, else the span of the
    speeches carrying it, plus the cycles those speeches fall in. Scoped to the
    selected cycle (§4A) when ``period`` is set, matching the rest of the profile.
    Returns None for a speaker who never held one (every ordinary MP), so the UI
    shows it only for the ministers / state secretaries who do."""
    extra = period_and(period, "sp.period_number")
    params: dict = {"pid": person_id}
    row = db.execute(
        f"""SELECT sp.speaker_office AS title,
                   MIN(ss.date) AS first_date, MAX(ss.date) AS last_date,
                   GROUP_CONCAT(DISTINCT sp.period_number) AS cycles
            FROM speech sp JOIN session ss ON ss.id = sp.session_id
            WHERE sp.person_id = :pid
              AND sp.speaker_office IS NOT NULL AND sp.speaker_office <> ''{extra}
            GROUP BY sp.speaker_office
            ORDER BY MAX(ss.date || '#' || printf('%08d', sp.speech_index)) DESC
            LIMIT 1""", params).fetchone()
    if not row:
        return None
    start, end = _office_term(offices or [], row["title"], row["last_date"])
    cycles = sorted(int(c) for c in (row["cycles"] or "").split(",") if c.strip())
    return {
        "title": row["title"],
        # The term itself when upstream reports it, else the speeches that carry
        # the title — a narrower, but never wrong, "held at least between".
        "start": start or row["first_date"],
        "end": end or row["last_date"],
        # 'term' dates are the appointment/dismissal boundaries; 'speeches' dates
        # only bound the title from below, which the UI says differently.
        "dates_from": "term" if start else "speeches",
        # The cycles **in scope** in which the person spoke holding this title — the
        # cycles of the speech span above, which a longer upstream term can outrun.
        "cycles": cycles,
    }


def _own_bills_for_cycles(by_cycle: list, periods: list[int]) -> Optional[int]:
    """The own-bills count over the cycles in scope, from the upstream breakdown
    (summed when several are selected). None when the breakdown covers none of
    them — "not reported", which the UI shows differently from a real zero."""
    counts = [entry.get("ownBills") for entry in by_cycle or []
              if entry.get("cycle") in periods]
    known = [c for c in counts if c is not None]
    return sum(known) if known else None


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


def _col(row: sqlite3.Row, name: str):
    """A ``SELECT *`` column that may be absent on an older DB (see
    ``_has_advocate_columns``), read as ``None`` instead of raising."""
    return row[name] if name in row.keys() else None


_MP_METHODOLOGY = (
    "A beszédidő az adott képviselőhöz rendelt felszólalások becsült "
    "időtartamainak összege. A felszólalások száma a feldolgozott ülésnapokon "
    "elhangzott, e képviselőhöz kötött felszólalások darabszáma. A v1-es "
    "időbecslés pozícióalapú (karakterarányos), ezért közelítő — minden "
    "felszólalásnál átkattintva ellenőrizhető. Az „alkalommal nem szavazott” "
    "érték azokat a név szerinti szavazásokat számolja, amelyeken a képviselő "
    "nem adott le szavazatot: „jelen, nem szavazott”, „igazoltan távol” és "
    "„nem volt jelen” együtt; a százalék ezek aránya a képviselő által "
    "leadható összes (a vizsgált körbe eső) szavazathoz képest — ugyanahhoz a "
    "100%-os alaphoz, mint a szavazási részvétel diagram."
)
_FACTION_METHODOLOGY = (
    "A frakciószintű összesítések a frakcióhoz rendelt felszólalások alapján "
    "készülnek; az átlagok a frakcióban felszólaló képviselők számára vetítve."
)
