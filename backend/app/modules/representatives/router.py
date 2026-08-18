"""Representatives & Statistics module API (§6) — /api/v1/representatives.

Self-contained slice (EXT-1) reading the shared `person`/`faction`/`membership`
core tables and the precomputed aggregate tables (REP-7). Statistics are served
straight from `person_stats`/`faction_stats`, never aggregated live (PERF-1).
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from ... import valasztas
from ...analytics import search_analytics
from ...config import settings
from ...db import (fold_text, get_db, like_contains, local_instant, period_and,
                   period_bounds, period_list, period_sql)
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


def _person_offices(db: sqlite3.Connection, person_id: str,
                    fallback_json: list) -> list[dict]:
    """Every office (*tisztség*) term this person held, newest first (REP-2).

    Read from ``person_office``, which the loader fills from two sources: the
    all-time **office-holder registry** (the only one that covers a non-MP minister
    or state secretary) and the per-MP roster's office list. Both report the same
    upstream terms, so a term present in both is returned once — the registry's copy
    winning, as it is the whole-archive source. Falls back to the person's
    ``offices_json`` when the table is absent or holds nothing for them (a DB loaded
    before it existed), so no profile loses its office list waiting for a reload."""
    try:
        rows = db.execute(
            f"""SELECT title, {_category_col(db)} AS category, date_start, date_end
                FROM person_office
                WHERE person_id = ?
                ORDER BY source = 'registry' DESC, date_start DESC""",
            (person_id,)).fetchall()
    except sqlite3.OperationalError:      # no person_office yet
        rows = None
    if not rows:
        return [o for o in fallback_json if isinstance(o, dict)]
    out: list[dict] = []
    seen: set = set()
    for r in rows:
        key = (r["title"], r["date_start"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"title": r["title"], "category": r["category"],
                    "start": r["date_start"], "end": r["date_end"]})
    # `source` decided which duplicate won, so sort by date for display order.
    out.sort(key=lambda o: o["start"] or "", reverse=True)
    return out


# Consecutive spells in ONE faction — which upstream splits at every cycle boundary,
# so an unbroken career arrives as one row per cycle — read as a single run (REP-14),
# exactly as REP-2 already treats office terms. A wider gap is a real interruption (a
# cycle not served, a spell as an independent in between) and is never bridged. The
# boundary rows meet within a second; two days is slack, not a guess.
_FACTION_RUN_GAP = timedelta(days=2)


def _instant(value) -> Optional[datetime]:
    """Parse an upstream timestamp (``2018-05-07T22:00:00Z``) or a bare date/year to
    a naive UTC datetime, so two of them can be compared. ``None`` when it is neither
    — a history whose dates can't be read is left unmerged rather than guessed at."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _faction_runs(history: list) -> list[dict]:
    """A faction history as **runs**, newest first: one entry per continuous spell in
    a faction, with its real start/end dates and the cycles it covers (REP-14).

    An MP who left their faction mid-term and later rejoined it produces three
    upstream rows all labelled with the same cycle; dating them by that label alone
    renders the same span three times over, which reads as a bug rather than as the
    switch it records. So the dates are carried through, and only spells that
    actually *touch* are merged. A spell still running keeps an open end — a run is
    never closed with a date invented for it."""
    rows = [h for h in history or [] if isinstance(h, dict)]
    runs: list[dict] = []
    for h in sorted(rows, key=lambda r: str(r.get("start") or "")):
        prev = runs[-1] if runs else None
        gap = None
        if prev is not None and prev["label"] == h.get("label"):
            prev_end, start = _instant(prev["end"]), _instant(h.get("start"))
            # An open-ended run absorbs nothing: it has no end to be continuous with.
            gap = (start - prev_end) if (prev_end and start) else None
        if gap is not None and timedelta(days=-1) <= gap <= _FACTION_RUN_GAP:
            prev["end"] = h.get("end")
            if h.get("cycle") and h["cycle"] not in prev["cycles"]:
                prev["cycles"].append(h["cycle"])
            continue
        runs.append({"label": h.get("label"), "start": h.get("start"),
                     "end": h.get("end"),
                     "cycles": [h["cycle"]] if h.get("cycle") else []})
    runs.reverse()
    return runs


def _handover(db: sqlite3.Connection, person_id, label) -> Optional[dict]:
    """The MP on the other side of a mandate handover (REP-14). ``has_profile`` says
    whether they are in this DB at all — a successor seated after the last roster
    scrape, or anyone from a cycle it was never loaded with, is named but not linked,
    which beats both hiding the handover and offering a link that 404s."""
    if not person_id and not label:
        return None
    known = bool(person_id) and bool(db.execute(
        "SELECT 1 FROM person WHERE person_id = ?", (person_id,)).fetchone())
    return {"person_id": person_id, "label": label, "has_profile": known}


def _category_col(db: sqlite3.Connection) -> str:
    """``person_office.category``, or a NULL stand-in on a DB loaded before the
    column existed — so the office pages degrade to "uncategorised" rather than
    erroring until the next registry load."""
    try:
        cols = {r["name"] for r in db.execute("PRAGMA table_info(person_office)")}
    except sqlite3.OperationalError:
        cols = set()
    return "category" if "category" in cols else "NULL"


# The cycle-overlap helpers these pages need — office terms are dated, not
# numbered by cycle — now live in `app.db` beside the other period helpers,
# because the portfolios module (§6C) scopes its office holders the same way.
_local_instant = local_instant
_period_bounds = period_bounds


def _has_advocate_columns(db: sqlite3.Connection) -> bool:
    """Whether the (regenerable) DB knows about nationality advocates — false only
    on a DB built before the advocate registry existed, so the endpoints keep
    working (MPs only) until the next loader run adds the columns."""
    return any(r["name"] == "is_advocate"
               for r in db.execute("PRAGMA table_info(person)"))


def _has_mandates(db: sqlite3.Connection) -> bool:
    """Whether the DB carries per-cycle mandates (REP-14) — false on one built
    before the composition-changes registry was scraped, where the roster is still a
    snapshot. The list then simply offers no mandate state rather than claiming
    everyone served their full term."""
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                           "AND name='person_mandate'").fetchone())


def _mandate_out(row) -> Optional[dict]:
    """One mandate as the API shapes it, or ``None`` when there is no term on record
    (an advocate, a non-MP speaker, or a DB loaded before REP-14). ``terminated``
    means the seat was given up before the term ended — the end date is then a real
    departure, not the close of the electoral cycle. Callers alias the columns to
    ``mandate_*`` so the list's join and the profile's own row shape alike."""
    if row is None or (row["mandate_start"] is None and row["mandate_end"] is None
                       and not row["mandate_terminated"]):
        return None
    return {"start": row["mandate_start"], "end": row["mandate_end"],
            "terminated": bool(row["mandate_terminated"]),
            "end_reason": row["mandate_end_reason"]}


def _latest_mandate_sql(period, alias: str = "mand") -> str:
    """A joinable sub-select giving each person their mandate in the selected
    cycle(s) — the most recent one when several are in scope. SQLite's documented
    bare-column-with-MAX() behaviour makes the other columns come from that same
    row, so this stays one grouped scan rather than a correlated subquery per field."""
    scope = period_sql(period, "period_number")
    return (f"""(SELECT person_id, MAX(period_number) AS period_number,
                        date_start AS mandate_start, date_end AS mandate_end,
                        terminated AS mandate_terminated,
                        end_reason AS mandate_end_reason
                 FROM person_mandate
                 {("WHERE " + scope) if scope else ""}
                 GROUP BY person_id) {alias}""")


def _rollcall_where(periods: list[int]) -> str:
    """The universe of roll-call votes a per-MP participation figure is measured
    against, as a ``WHERE`` body over ``vote`` (no alias).

    Only votes with a per-MP list count (``has_per_mp``): voice and list votes have
    no per-person record, so counting them would read as everyone being absent from
    them. Procedural quorum checks are dropped for the same reason parlament.hu's
    own statistics drop them (see `_QUORUM_RESULTS`). Binds nothing — the period
    numbers are inlined by `period_and` — so it splices into any paramstyle."""
    return ("has_per_mp = 1" + _exclude_quorum("")
            + period_and(periods, "period_number"))


def _vote_participation(db: sqlite3.Connection, person_id: str,
                        election_history_json, periods: list[int],
                        total_rollcall: Optional[int] = None) -> dict:
    """One MP's roll-call participation in ``periods`` (REP-3).

    Returns the five-way split the profile pie draws and the headline "cast no
    vote" metric derived from it:

    * ``voted``       — *szavazott*: igen/nem/tartózkodás, all of them a vote cast;
    * ``novote``      — *jelen, nem szavazott*;
    * ``absent``      — *igazoltan távol* (upstream's *előre bejelentett hiányzó*);
    * ``not_present`` — *nem volt jelen*: no record at all for a vote inside their
      mandate;
    * ``not_mp``      — *nem volt képviselő*: roll calls that fell outside their
      mandate window(s) — a replacement seated mid-cycle, or an MP who resigned
      early. Kept separate from a genuine absence and **excluded from the
      denominator**, so time they could not have voted in never counts against
      them.

    ``vote_breakdown.total`` is that denominator (everything but ``not_mp``), and
    ``votes_missed`` = ``novote + absent + not_present`` over it — so the headline
    percentage and the pie always agree.

    The caller decides whether this is meaningful at all: it is queried only for
    actual MPs (a minister holds no mandate to attend roll calls, so every vote
    would read as "nem volt jelen") and only while the Votes module is live
    (EXT-6 — its tables may not exist otherwise). ``total_rollcall`` lets a caller
    comparing several people (REP-15) pass the person-independent universe count in
    once instead of re-counting it per column.
    """
    extra = period_and(periods, "v.period_number")
    # One pass over the MP's roll-call records splits them into the three recorded
    # participation categories; `total` counts every record they have.
    vrow = db.execute(
        f"""SELECT COUNT(*) AS total,
                   SUM(CASE WHEN vr.value_code IN ('yes','no','abstain') THEN 1 ELSE 0 END) AS voted,
                   SUM(CASE WHEN vr.value_code = 'novote'  THEN 1 ELSE 0 END) AS novote,
                   SUM(CASE WHEN vr.value_code = 'absent'  THEN 1 ELSE 0 END) AS absent
            FROM vote_record vr JOIN vote v ON v.id = vr.vote_id
            WHERE vr.person_id = :pid{extra}{_exclude_quorum()}""",
        {"pid": person_id}).fetchone()
    votes_total = vrow["total"] or 0
    voted = vrow["voted"] or 0
    novote = vrow["novote"] or 0
    votes_absent = vrow["absent"] or 0

    rc_where = _rollcall_where(periods)
    if total_rollcall is None:
        total_rollcall = (db.execute(
            f"SELECT COUNT(*) AS n FROM vote WHERE {rc_where}").fetchone()["n"] or 0)

    # Of that universe, the ones inside the MP's mandate window(s) are the votes
    # they could actually have taken part in. The windows come from
    # election_history_json's mandateStart/mandateEnd (ISO UTC, so a lexicographic
    # datetime compare is chronological).
    windows = [(e.get("mandateStart"), e.get("mandateEnd"))
               for e in (_loads(election_history_json) or [])
               if e.get("mandateStart")]
    eligible_rollcall = total_rollcall
    if windows:
        conds, wparams = [], {}
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

    # Clamped at 0 for the rare case where the record count exceeds the eligible
    # universe (a vote right on the mandate boundary, or votes lacking the flag in
    # a test/partial import).
    not_present = max(0, eligible_rollcall - votes_total)
    not_mp = max(0, total_rollcall - eligible_rollcall)
    breakdown = {
        "voted": voted,              # szavazott (igen + nem + tartózkodás)
        "novote": novote,            # jelen, nem szavazott
        "absent": votes_absent,      # igazoltan távol
        "not_present": not_present,  # nem volt jelen (MP, but no record)
        "not_mp": not_mp,            # nem volt képviselő (outside mandate)
        "total": voted + novote + votes_absent + not_present,
    }
    # Equivalent to `total - voted`, but spelled out so the link between the number
    # and the three legend rows it sums stays obvious.
    votes_missed = novote + votes_absent + not_present
    return {
        "votes_total": votes_total,
        "votes_missed": votes_missed,
        "votes_missed_pct": (round(100.0 * votes_missed / breakdown["total"], 1)
                             if breakdown["total"] else None),
        "vote_breakdown": breakdown,
    }


@router.get("")
def list_representatives(
    q: Optional[str] = None,
    faction_id: Optional[int] = None,
    period: Optional[List[int]] = Query(
        None, description="Electoral period number(s); repeat to scope to several cycles"),
    constituency: Optional[str] = None,
    role: str = Query("mp", pattern="^(mp|advocate|other|all)$",
                      description="mp (default) | advocate (nemzetiségi szószólók) "
                                  "| other (non-MP speakers) | all"),
    nationality: Optional[str] = None,
    mandate: str = Query("all", pattern="^(all|active|terminated)$",
                         description="all (default) | active (mandate not ended "
                                     "early) | terminated (ended early) — REP-14"),
    sort: str = Query("name", pattern="^(name|speeches|speaking_time)$"),
    limit: int = Query(60, ge=1, le=300),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
):
    """Browsable, filterable representative list (REP-1). Filters combine.

    ``role`` picks which mandate the list covers: MPs (the default, so an existing
    caller sees exactly what it did before), the **nationality advocates**
    (szószólók — they sit and speak but hold no mandate, REP-9), the **other
    speakers** (REP-12), or all three. On a DB predating the advocate registry the
    parameter degrades to MPs only.

    ``other`` is everyone who has actually spoken in the House holding neither
    mandate: ministers and state secretaries who are not MPs, the President of the
    Republic, invited guests. It is defined by *having spoken* rather than by "not
    an MP", because `person` also holds office-holders who never spoke a word here
    (the tisztségviselők registry, REP-11) — listing those as speakers would be a
    lie. Under a cycle scope it means having spoken in those cycles.

    A cycle's list covers **everyone who held a mandate in it** (REP-14), including
    the MPs whose mandate ended before the term did — ``mandate`` narrows it to
    either side. "Active" is read against the *cycle in scope*, not against today: in
    the running cycle it means still sitting, in a closed one that the mandate lasted
    to the end of the term. Only MPs have mandates, so the filter empties the
    advocate/other lists by construction — the site offers it on the MP list alone."""
    advocates_known = _has_advocate_columns(db)
    mandates_known = _has_mandates(db)
    where = []
    params: dict = {}
    # Speakers are counted from `person_stats`, the same precomputed aggregate the
    # list already reads for its speech counts — so "has spoken" and "N speeches"
    # can never disagree (both exclude procedural/chairing speeches, STAT-1).
    spoke_scope = period_sql(period, "ps.period_number") or "ps.period_number IS NULL"
    spoke = ("EXISTS (SELECT 1 FROM person_stats ps WHERE ps.person_id = p.person_id "
             f"AND {spoke_scope} AND ps.speech_count > 0)")
    if not advocates_known:
        # A DB predating the advocate registry knows only MPs, so an advocate-only
        # request comes back honestly empty instead of quietly listing MPs.
        where.append("0" if role == "advocate" else "p.is_mp = 1")
    elif role == "mp":
        where.append("p.is_mp = 1")
    elif role == "advocate":
        where.append("p.is_advocate = 1")
    elif role == "other":
        where.append("COALESCE(p.is_mp, 0) = 0 AND COALESCE(p.is_advocate, 0) = 0")
        where.append(spoke)
    else:
        where.append(f"(p.is_mp = 1 OR p.is_advocate = 1 OR {spoke})")
    if q:
        where.append("fold(p.label) LIKE fold(:q) ESCAPE '\\'")
        params["q"] = like_contains(q.strip())
    if constituency:
        where.append("fold(p.constituency) LIKE fold(:con) ESCAPE '\\'")
        params["con"] = like_contains(constituency)
    if nationality and advocates_known:
        where.append("fold(p.nationality) LIKE fold(:nat) ESCAPE '\\'")
        params["nat"] = like_contains(nationality)
    if mandate != "all" and mandates_known:
        # Scoped like everything else: a mandate that ended early in one cycle says
        # nothing about the one the reader is looking at, so the row must be in
        # scope AND in the asked-for state — two independent EXISTS would list an MP
        # on the strength of a different cycle's mandate (cf. the faction filter).
        mand_scope = period_sql(period, "pm.period_number")
        state = "pm.terminated = 1" if mandate == "terminated" else "pm.terminated = 0"
        where.append("EXISTS (SELECT 1 FROM person_mandate pm "
                     f"WHERE pm.person_id = p.person_id AND {state}"
                     + (f" AND {mand_scope}" if mand_scope else "") + ")")
    mem_sql = period_sql(period, "m.period_number")
    if faction_id is not None:
        # One membership row must match both — two independent EXISTS would
        # list an MP who was in this faction only during a cycle out of scope.
        cond = "m.faction_id=:fid" + (f" AND {mem_sql}" if mem_sql else "")
        where.append("EXISTS (SELECT 1 FROM membership m "
                     f"WHERE m.person_id=p.person_id AND {cond})")
        params["fid"] = faction_id
    elif mem_sql:
        # A mandate is held *for a cycle*, so a membership row in scope is what puts
        # an MP or an advocate in it. The other speakers hold no mandate and have no
        # membership row — they are scoped by the cycles they actually spoke in (the
        # `spoke` clause above), and demanding a membership too would empty the list.
        mem_exists = ("EXISTS (SELECT 1 FROM membership m "
                      f"WHERE m.person_id=p.person_id AND {mem_sql})")
        if role == "all":
            where.append(f"({mem_exists} OR {spoke})")
        elif role != "other":
            where.append(mem_exists)
    where_sql = " AND ".join(where)

    # fold() the name sort: BINARY collation puts accented Hungarian surnames
    # (Ágh, Árvay) after Z. The other speakers and the office holders come from
    # sources that may not split a name, so fall back to the label.
    order = {"name": "fold(COALESCE(p.lastname, p.label)), fold(p.label)",
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

    # Privacy-respecting analytics (PRIV-2): the typed name/keyword + the filters
    # it was combined with. A no-keyword browse of the list records nothing.
    search_analytics.record(
        source="representatives", query=q, period=period, sort=sort,
        faction_id=faction_id, role=role, constituency=constituency,
        nationality=nationality, mandate=mandate, results=total, offset=offset)

    # An advocate has no faction or constituency; their nationality is the
    # affiliation the card shows in its place.
    advocate_cols = ("p.is_advocate, p.nationality," if advocates_known
                     else "0 AS is_advocate, NULL AS nationality,")
    # ...and an "other" speaker has neither: what identifies a non-MP minister or
    # state secretary is the office they spoke in (REP-2/REP-12), so that goes in
    # the same slot on their card. Their most recently *begun* office in scope wins
    # (someone promoted mid-cycle is shown in the post they moved to), and an office
    # the registry dates wins over one their speeches merely carry — which is all
    # there is for a speaker the registry never lists: a guest, a commissioner.
    # Only computed where a card would show it, so the MP list's query is untouched.
    office_col = "NULL AS office,"
    if role == "other" or (role == "all" and advocates_known):
        # Scoped like everything else (§4A): the office they held *in these cycles*,
        # not the one they hold now — a state secretary in the cycle you are looking
        # at may since have moved on. Office terms are dated, not cycle-numbered, so
        # the scope is an overlap of instants (see `_period_bounds`).
        ostart, oend = _period_bounds(db, period_list(period)) or (None, None)
        overlap = ""
        if ostart:
            overlap += " AND (po.date_end IS NULL OR po.date_end >= :ostart)"
            params["ostart"] = ostart
        if oend:
            overlap += " AND po.date_start <= :oend"
            params["oend"] = oend
        office_sql = f"""(SELECT COALESCE(
              (SELECT po.title FROM person_office po
                WHERE po.person_id = p.person_id{overlap}
                ORDER BY po.date_start DESC, po.date_end IS NULL DESC LIMIT 1),
              (SELECT s.speaker_office FROM speech s
                WHERE s.person_id = p.person_id AND s.speaker_office IS NOT NULL
                  {period_and(period, "s.period_number")}
                ORDER BY s.session_id DESC, s.speech_index DESC LIMIT 1)
           ))"""
        if role == "all":
            # The merged list (the "all" chip of the Felszólalók page) mixes the
            # three, and a card must read the same there as under its own chip: an
            # MP is identified by their faction and an advocate by their
            # nationality, so the office fills the slot **only where those leave it
            # empty** — which is what it is there for (REP-2/REP-12). Asking the
            # faction subquery itself, rather than `is_mp`, is what keeps the two
            # in step: `is_mp` is a lifetime flag, so a former MP who spoke in this
            # cycle as a minister is neither shown a faction (they hold no
            # membership in scope) nor counted as a non-MP — and would otherwise
            # end up on a card carrying nothing but their name.
            office_sql = (f"CASE WHEN COALESCE(p.is_advocate, 0) = 0 "
                          f"AND ({faction_sub}) IS NULL THEN {office_sql} END")
        office_col = f"{office_sql} AS office,"
    # The mandate this person held in the cycle(s) in scope (REP-14), so a card can
    # say a seat was given up rather than presenting a former MP as a sitting one.
    # A DB predating the registry has no such table: the columns then read as "no
    # mandate on record", which is honest — not "served the full term".
    if mandates_known:
        mandate_join = f"LEFT JOIN {_latest_mandate_sql(period)} ON mand.person_id = p.person_id"
        mandate_sel = ("mand.mandate_start, mand.mandate_end, "
                       "mand.mandate_terminated, mand.mandate_end_reason,")
    else:
        mandate_join = ""
        mandate_sel = ("NULL AS mandate_start, NULL AS mandate_end, "
                       "0 AS mandate_terminated, NULL AS mandate_end_reason,")
    rows = db.execute(
        f"""SELECT p.person_id, p.label, p.firstname, p.lastname, p.photo_uri,
                   p.constituency, {advocate_cols} {office_col} {mandate_sel}
                   COALESCE(stat.speech_count, 0) AS speech_count,
                   COALESCE(stat.speaking_seconds, 0) AS speaking_seconds,
                   f.id AS faction_id, f.label AS faction_label, f.color AS faction_color
            FROM person p
            LEFT JOIN ({stat_sub}) stat ON stat.person_id = p.person_id
            {mandate_join}
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
                "office": r["office"],
                # The seat they held in the cycle in scope; `terminated` marks one
                # given up before the term ended (REP-14). None = no term on record.
                "mandate": _mandate_out(r),
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


# ---------------------------------------------------------------------------
# Office holders (tisztségviselők, REP-11)
#
# The parliament's own all-time listing of who held which government / House
# office and when (parlament.hu/web/guest/tisztsegviselok), served term by term:
# one row per (person, office, term), because a person holds several offices over
# a career and the term is the thing being listed.
#
# Declared before ``/{person_id}`` so "officials" isn't captured as a person id.
# ---------------------------------------------------------------------------

# The registry's office categories, in the order the portal groups them (most
# specific first). The keys are the slugs the scraper tags each term with — see
# `OFFICE_CATEGORIES` in the scraper's felicitas client; the UI holds the labels.
_OFFICE_CATEGORIES = ("pm", "minister", "state-secretary", "parliamentary",
                      "senior", "other")

_OFFICE_METHODOLOGY = (
    "A tisztségviselők listája az Országgyűlés hivatalos nyilvántartásából "
    "származik (parlament.hu, „Tisztségviselők”)."
    "Egy sor egy megbízatás: a kinevezés és a felmentés napja a nyilvántartás "
    "szerinti dátum, a nyitott vég azt jelenti, hogy a tisztséget a lekérdezés "
    "időpontjában is betöltötte. Ugyanaz a személy több megbízatással is "
    "szerepelhet – az egymás mellé kerülő megbízatásait a neve mellett, egy "
    "kártyán soroljuk fel. "
    "A ciklusra szűkített nézet azokat a megbízatásokat mutatja, amelyek a "
    "ciklus idejébe belenyúlnak – nem azokat, amelyek benne kezdődtek."
)


@router.get("/officials")
def list_officials(
    q: Optional[str] = None,
    category: Optional[str] = Query(
        None, description="Office category slug: " + " | ".join(_OFFICE_CATEGORIES)),
    status: str = Query("all", pattern="^(all|current|past)$",
                        description="all (default) | current (still in office) | past"),
    started: str = Query("all", pattern="^(all|in-cycle)$",
                         description="all (default) | in-cycle (term began within the "
                                     "selected cycles; no-op without `period`)"),
    period: Optional[List[int]] = Query(
        None, description="Electoral period number(s); scopes to terms overlapping them"),
    sort: str = Query("start", pattern="^(start|name|office)$"),
    limit: int = Query(60, ge=1, le=300),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
):
    """The office-holder listing (tisztségviselők, REP-11), one row per term.

    Reads the **registry** rows of ``person_office`` only. The per-MP roster
    reports the same terms but without a category, so mixing both sources in would
    duplicate rows and leave half of them unfilterable; the registry is the
    all-time source and covers every roster term (that is what makes it the one
    that can date a non-MP minister's office at all, REP-2).

    ``q`` matches the person's name **or** the office title — one box, because
    "Rétvári" and "államtitkár" are equally natural things to look for here.

    ``period`` scopes to terms that **overlap** those cycles rather than to terms
    that *began* in them: a minister appointed last cycle and still serving, and the
    outgoing government that governed into the new cycle's first days, both belong
    to the cycle a reader is asking about. That is also the surprising half — most
    of a cycle's rows can predate it — so the answer carries ``starts``: how many of
    the terms in scope began within it (``in_cycle``) against the total, and
    ``started=in-cycle`` narrows to those. Without a ``period`` there is no cycle to
    have begun in, and the parameter does nothing.

    ``categories`` counts the same filtered set with the category filter itself
    lifted, so the filter's own options never read zero for a choice that would
    return rows."""
    cat_col = _category_col(db)
    where = ["po.source = 'registry'"]
    params: dict = {}
    if q:
        where.append("(fold(p.label) LIKE fold(:q) ESCAPE '\\' "
                     "OR fold(po.title) LIKE fold(:q) ESCAPE '\\')")
        params["q"] = like_contains(q.strip())
    if status == "current":
        where.append("po.date_end IS NULL")
    elif status == "past":
        where.append("po.date_end IS NOT NULL")
    # Cycle scope (§4A): the term must **overlap** the cycles' span — start no later
    # than they end, and not have ended before they began. Both sides are instants
    # (see `_period_bounds`): a term running from the first midnight of a cycle
    # belongs to that cycle, not to the one that ended the moment before it.
    bounds = _period_bounds(db, period_list(period))
    if bounds is None:
        where.append("0")            # cycles this DB cannot date — nothing overlaps
    else:
        start_bound, end_bound = bounds
        if start_bound:
            where.append("(po.date_end IS NULL OR po.date_end >= :pstart)")
            params["pstart"] = start_bound
        if end_bound:
            where.append("po.date_start <= :pend")
            params["pend"] = end_bound

    # "Began within the selected cycles" — only meaningful with a cycle in scope,
    # and expressible only when that cycle could be dated.
    in_cycle_sql = (params.get("pstart") and "po.date_start >= :pstart") or None

    def _sql(extra_where: list) -> str:
        return (" FROM person_office po JOIN person p ON p.person_id = po.person_id "
                "WHERE " + " AND ".join(where + extra_where))

    cat_where = []
    if category:
        if category == "uncategorised":
            cat_where.append(f"{cat_col} IS NULL")
        else:
            cat_where.append(f"{cat_col} = :cat")
            params["cat"] = category
    started_where = [in_cycle_sql] if (started == "in-cycle" and in_cycle_sql) else []

    try:
        total = db.execute("SELECT COUNT(*) AS c" + _sql(cat_where + started_where),
                           params).fetchone()["c"]
    except sqlite3.OperationalError:
        # No person_office yet (a DB built before the registry stage) — an empty
        # listing is the honest answer, not a 500.
        return {"total": 0, "limit": limit, "offset": offset, "officials": [],
                "categories": [], "starts": {"all": 0, "in_cycle": None},
                "methodology": _OFFICE_METHODOLOGY}

    # Privacy-respecting analytics (PRIV-2): the typed name/keyword + the filters
    # it was combined with. A no-keyword browse of the registry records nothing.
    search_analytics.record(
        source="officials", query=q, period=period, sort=sort,
        category=category, status=status, started=started,
        results=total, offset=offset)

    # Each filter's own options are counted with **that** filter lifted (the others
    # applied), so neither can offer a choice that would land on an empty page.
    facet = {r["category"]: r["c"] for r in db.execute(
        f"SELECT {cat_col} AS category, COUNT(*) AS c" + _sql(started_where)
        + f" GROUP BY {cat_col}", params).fetchall()}
    categories = [{"key": k, "count": facet.get(k, 0)} for k in _OFFICE_CATEGORIES
                  if facet.get(k)]
    if facet.get(None):
        # Terms from a DB loaded before the categories existed; named rather than
        # dropped, so the counts on the page always add up to the total.
        categories.append({"key": "uncategorised", "count": facet[None]})

    # How much of the cycle's listing actually began in it. This is the number that
    # explains the page: most of a cycle's office terms can predate it and still be
    # held during it, which reads as a broken filter unless it is stated. `in_cycle`
    # is null when there is no cycle in scope — then there is nothing to have begun in.
    starts = {
        "all": db.execute("SELECT COUNT(*) AS c" + _sql(cat_where),
                          params).fetchone()["c"],
        "in_cycle": (db.execute(
            "SELECT COUNT(*) AS c" + _sql(cat_where + [in_cycle_sql]),
            params).fetchone()["c"] if in_cycle_sql else None),
    }

    # fold() the name sort: BINARY collation puts Ágh/Árvay after Z. Falling back
    # to the label covers the registry-only people whose name never came through a
    # roster (it is split in the scraper, but a single-token name has no surname).
    order = {
        "start": "po.date_start DESC, fold(COALESCE(p.lastname, p.label))",
        "name": "fold(COALESCE(p.lastname, p.label)), fold(p.label), po.date_start DESC",
        "office": "fold(po.title), po.date_start DESC",
    }[sort]
    rows = db.execute(
        f"""SELECT po.id, po.person_id, po.title, {cat_col} AS category,
                   po.date_start, po.date_end,
                   p.label, p.photo_uri, p.is_mp"""
        + _sql(cat_where + started_where)
        + f" ORDER BY {order} LIMIT :limit OFFSET :offset",
        {**params, "limit": limit, "offset": offset}).fetchall()
    return {
        "total": total, "limit": limit, "offset": offset,
        "officials": [
            {
                "id": r["id"], "person_id": r["person_id"], "label": r["label"],
                "photo_uri": r["photo_uri"], "is_mp": bool(r["is_mp"]),
                "title": r["title"], "category": r["category"],
                "start": r["date_start"], "end": r["date_end"],
                # Nothing downstream has to compare dates to know whether this is
                # someone's current post: an open end IS still in office (REP-2).
                "current": r["date_end"] is None,
            } for r in rows
        ],
        "categories": categories,
        "starts": starts,
        "methodology": _OFFICE_METHODOLOGY,
    }


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
    name mapping to a SINGLE person is returned — an ambiguous or unknown name is
    omitted, and the caller renders it as plain text. Keyed in the response by the
    requested spelling.

    Ties are broken by how likely the person is to be the one heckling in a
    chamber: an MP first, then anyone who has spoken here, then the rest — the
    last tier being the office-holder registry's people, most of whom never set
    foot in the House (REP-11) and must not make a name that used to resolve
    ambiguous.

    Declared before ``/{person_id}`` so "resolve" isn't captured as an MP id."""
    names = [n.strip() for n in name if n and n.strip()][:40]
    resolved: dict = {}
    if not names:
        return {"resolved": resolved}
    # The person table is small (~1 500 rows); fold every label once and group so
    # a duplicate name (two people, same folded label) is detected as ambiguous.
    groups: dict = {}
    for r in db.execute(
            """SELECT p.person_id, p.label, p.photo_uri, p.is_mp,
                      EXISTS (SELECT 1 FROM person_stats ps
                              WHERE ps.person_id = p.person_id
                                AND ps.period_number IS NULL) AS spoke
               FROM person p""").fetchall():
        groups.setdefault(fold_text(r["label"]), []).append(r)
    for n in names:
        rows = groups.get(fold_text(n))
        if not rows:
            continue
        cand = ([r for r in rows if r["is_mp"]]
                or [r for r in rows if r["spoke"]]
                or rows)
        if len(cand) == 1:
            r = cand[0]
            resolved[n] = {"person_id": r["person_id"], "label": r["label"],
                           "photo_uri": r["photo_uri"]}
    return {"resolved": resolved}


# ---------------------------------------------------------------------------
# "Which constituency am I in, and who represents it?" (REP-10)
#
# Declared before ``/{person_id}`` so "constituencies" isn't captured as an MP id.
# ---------------------------------------------------------------------------

# "Budapest 12. OEVK", "Szabolcs-Szatmár-Bereg 1. OEVK" — how parlament.hu names a
# single-member constituency in an MP's record. The county name and the number are
# what identify it; both are needed, since every county numbers from 1.
_OEVK_RE = re.compile(r"^(?P<county>.+?)\s+(?P<number>\d+)\.\s*OEVK$")


def _county_compatible(a: str, b: str) -> bool:
    """Whether two folded county names refer to the same county.

    Not equality, because the two sources disagree about renames: the election
    office says *Csongrád-Csanád* (the county's name since 2020) while
    parlament.hu's constituency labels still say *Csongrád*. One name being a
    prefix of the other covers that, and every rename of this shape, without a
    hard-coded alias table — and no two of the twenty county names are prefixes
    of each other, so it cannot conflate distinct counties.
    """
    return a.startswith(b) or b.startswith(a)


def _period_start_year(row: sqlite3.Row) -> str | None:
    return (row["date_start"] or "")[:4] or None


def _lookup_period(db: sqlite3.Connection, election_date: str | None) -> dict | None:
    """The electoral period the constituency map in hand elects.

    The boundaries are **redrawn between elections** — the 2026 map has 16 Budapest
    constituencies where the 2022 one had 18 — so the answer is only valid for one
    cycle, and it is this one: the first period that begins on or after the election
    the data describes. (An election is held weeks before the new House sits, so
    "the period containing the election date" would name the *outgoing* one.)
    Falls back to the newest period when the date is missing.
    """
    periods = db.execute(
        "SELECT number, label, date_start, date_end FROM electoral_period "
        "WHERE date_start IS NOT NULL ORDER BY date_start").fetchall()
    if not periods:
        return None
    chosen = None
    if election_date:
        day = election_date[:10]
        chosen = next((p for p in periods if (p["date_start"] or "") >= day), None)
    row = chosen or periods[-1]
    return {"number": row["number"], "date_start": row["date_start"],
            "date_end": row["date_end"]}


def _mps_by_constituency(db: sqlite3.Connection, period_number: int) -> dict:
    """``{number: [(folded county, mp), …]}`` — who held each single-member
    constituency in one electoral period.

    The per-cycle source is ``election_history_json``: it records the constituency
    the MP won **in each cycle**, so an MP who held a seat in 2022 and came in off the
    national list in 2026 is correctly absent from the seat in 2026. An entry belongs
    to the period whose start year matches its ``cycle`` span ("2022-2026" → the
    period beginning in 2022), which is exact and needs no timezone reasoning about
    the UTC mandate timestamps.

    ``person.constituency`` — a single, latest-cycle-only value — is the fallback for
    a DB whose roster predates the election history, gated on the MP actually sitting
    in the period so it can't attribute a seat across cycles.
    """
    period = db.execute(
        "SELECT number, label, date_start, date_end FROM electoral_period "
        "WHERE number = ?", (period_number,)).fetchone()
    start_year = _period_start_year(period) if period else None

    index: dict = {}

    def add(number: int, county: str, row: sqlite3.Row) -> None:
        # `email` is carried so the lookup can offer "write to your MP" directly —
        # the point of finding out who represents you is usually to contact them.
        # It is the MP's published parliamentary address (already public on their
        # profile, REP-2); an MP without one simply has None.
        mp = {"person_id": row["person_id"], "label": row["label"],
              "photo_uri": row["photo_uri"], "email": row["email"] or None}
        bucket = index.setdefault(number, [])
        if not any(existing["person_id"] == mp["person_id"]
                   for _, existing in bucket):
            bucket.append((fold_text(county), mp))

    rows = db.execute(
        """SELECT person_id, label, photo_uri, email, constituency,
                  election_history_json
           FROM person
           WHERE is_mp = 1
             AND (constituency LIKE '%OEVK%' OR election_history_json LIKE '%OEVK%')
        """).fetchall()
    for r in rows:
        history = []
        try:
            history = json.loads(r["election_history_json"] or "[]")
        except ValueError:
            history = []
        matched = False
        for entry in history:
            if not isinstance(entry, dict):
                continue
            m = _OEVK_RE.match((entry.get("constituency") or "").strip())
            if not m:
                continue
            cycle = str(entry.get("cycle") or "")
            if start_year and cycle[:4] == start_year:
                add(int(m.group("number")), m.group("county"), r)
                matched = True
        if matched or history:
            continue
        # No election history at all: fall back to the single stored constituency,
        # but only for an MP who actually sat in this period.
        m = _OEVK_RE.match((r["constituency"] or "").strip())
        if not m:
            continue
        sits = db.execute(
            "SELECT 1 FROM membership WHERE person_id = ? AND period_number = ? LIMIT 1",
            (r["person_id"], period_number)).fetchone()
        if sits:
            add(int(m.group("number")), m.group("county"), r)
    return index


def _faction_of(db: sqlite3.Connection, person_id: str, period_number: int) -> dict | None:
    row = db.execute(
        """SELECT f.id, f.label, f.color FROM membership m
           JOIN faction f ON f.id = m.faction_id
           WHERE m.person_id = ? AND m.period_number = ? LIMIT 1""",
        (person_id, period_number)).fetchone()
    return {"id": row["id"], "label": row["label"], "color": row["color"]} if row else None


def _require_lookup() -> None:
    """404 the lookup endpoints while the feature is off (OPS-4), the same way a
    disabled module's routes vanish (EXT-6) — ``/meta`` advertises the flag so the
    SPA hides the tab and never calls these at all."""
    if not settings.evk_lookup:
        raise HTTPException(404, "The constituency lookup is disabled")


@router.get("/constituencies/settlements")
def search_settlements(
    q: str = Query(..., min_length=1, max_length=80,
                   description="Settlement name; matched accent-insensitively"),
    limit: int = Query(25, ge=1, le=100),
):
    """Find a settlement by name, the first step of "who represents me?" (REP-10).

    Matching is accent- and case-insensitive (FOLD-1), and a Budapest district is
    also found by the spellings people actually use — ``V. kerület``, ``5. kerület``
    — not only the source's zero-padded ``Budapest 05. kerület``. Each row says how
    many constituencies the settlement spans: exactly one is already the answer,
    more than one needs the map (see the settlement endpoint).
    """
    _require_lookup()
    try:
        settlements, total = valasztas.search_settlements(q, limit)
    except valasztas.LookupUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"total": total, "limit": limit, "settlements": settlements}


@router.get("/constituencies/settlements/{maz}/{taz}")
def get_settlement_constituencies(
    maz: str = Path(..., pattern=r"^\d{2}$", description="County code (megye azonosító)"),
    taz: str = Path(..., pattern=r"^\d{3}$", description="Settlement code (település azonosító)"),
    db: sqlite3.Connection = Depends(get_db),
):
    """One settlement's single-member constituency (or constituencies) and the MP
    who holds each (REP-10).

    Most settlements sit wholly inside one constituency, and the answer is a single
    MP. The 23 that are split — 15 Budapest districts and 8 large cities — return
    every constituency they span **plus the boundaries as GeoJSON**, so the reader
    can pick the part they live in on a map; the constituency polygons partition the
    settlement, so the choice is unambiguous once seen.

    Deliberately **not** scoped by the global cycle selector (§4A). The constituency
    map is redrawn between elections, so these boundaries answer for exactly one
    cycle — the one the election office's data elects — and that cycle is reported
    in ``period`` rather than taken from the reader's scope. Answering an
    out-of-scope cycle from a map that didn't exist then would be a wrong answer,
    not a narrower one.
    """
    _require_lookup()
    try:
        found = valasztas.settlement(maz, taz)
    except valasztas.LookupUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    if found is None:
        raise HTTPException(404, "Settlement not found")

    period = _lookup_period(db, (found["source"] or {}).get("election_date"))
    if period is not None:
        # A pure function of the DB (and small), so it is memoized per DB file
        # rather than re-scanned for every settlement a reader tries.
        seats = cached_aggregate(
            "constituency_seats", (period["number"],),
            lambda: _mps_by_constituency(db, period["number"]))
        for part in found["constituencies"]:
            part["representatives"] = [
                {**mp, "faction": _faction_of(db, mp["person_id"], period["number"])}
                for county, mp in seats.get(part["number"], [])
                if _county_compatible(county, fold_text(part["county"]))
            ]
    else:  # a DB with no dated electoral periods yet — say so, don't invent MPs
        for part in found["constituencies"]:
            part["representatives"] = []
    found["period"] = period
    found["methodology"] = _LOOKUP_METHODOLOGY
    return found


_LOOKUP_METHODOLOGY = (
    "A település–választókerület megfeleltetés és a választókerületi határok a "
    "Nemzeti Választási Iroda adatai. Az egyéni választókerületek határai "
    "választásonként változhatnak, ezért a találat arra a ciklusra vonatkozik, "
    "amelyet ez a választás hozott létre. A képviselő azt a mandátumot jelöli, "
    "amelyet az adott egyéni választókerületben szerzett; az országos listáról "
    "bejutott képviselők nem választókerülethez kötődnek."
)


# How many people one comparison may hold (REP-15). Four columns is what a phone
# can still show side by side without either scrolling sideways or shrinking the
# figures out of legibility — and a comparison of more than four is a ranking,
# which the list pages already are.
_COMPARE_MAX = 4

# Main types (Felicitas *fotipus*) grouped exactly as the profile's three
# submitted-irományok sections group them (BILL-9), so a column's counts equal
# the section badges on that person's own page.
_DOC_BUCKETS = (("questions", ("K", "A", "I")), ("bills", ("T", "H")))


def _doc_counts(db: sqlite3.Connection, person_id: str,
                periods: list[int]) -> dict:
    """The irományok this person submitted in scope, bucketed by main type.

    One grouped count over `bill`/`bill_sponsor`, split into the same three buckets
    the profile shows as separate sections — questions (K/A/I), bills (T/H) and
    everything else — so the comparison column and the profile agree. A motion with
    several sponsors counts once for each of them (DISTINCT over the bill)."""
    rows = db.execute(
        f"""SELECT b.main_type AS main_type, COUNT(DISTINCT b.id) AS n
            FROM bill b JOIN bill_sponsor bs ON bs.bill_id = b.id
            WHERE bs.person_id = :pid{period_and(periods, "b.period_number")}
            GROUP BY b.main_type""", {"pid": person_id}).fetchall()
    out = {"questions": 0, "bills": 0, "other": 0, "total": 0}
    known = {t: key for key, types in _DOC_BUCKETS for t in types}
    for r in rows:
        n = r["n"] or 0
        out[known.get((r["main_type"] or "").strip(), "other")] += n
        out["total"] += n
    return out


@router.get("/compare")
def compare_representatives(
        ids: List[str] = Query(
            default=[], alias="id",
            description=f"Person id(s) to compare, repeated; at most {_COMPARE_MAX}"),
        period: Optional[List[int]] = Query(
            None, description="Electoral period number(s)"),
        db: sqlite3.Connection = Depends(get_db)):
    """Two to four representatives side by side (REP-15).

    One request per comparison rather than a fan-out of a dozen per-person calls:
    the page draws a spec sheet, so it needs every column's every figure before it
    can render a single row's bars. Everything is scoped to the selected cycle(s)
    (§4A) and read from the same precomputed aggregates the profile reads (REP-7,
    STAT-1) — via the same helpers, so a figure here can never disagree with the
    figure on that person's own page.

    Columns come back **in the order asked for** (a comparison's left-to-right
    order is the reader's), duplicates collapsed. Ids past the ``_COMPARE_MAX``
    limit are reported in ``dropped`` rather than silently truncated, and an id
    that resolves to nobody is reported in ``missing`` and skipped rather than
    failing the whole page: a link shared before a re-import must still open for
    the people it can still name.

    Values that are **not applicable** to a person come back as ``null``, never as
    a zero the reader would compare against a real one (TRUST-1): roll-call
    participation for someone holding no mandate (a minister has none to attend),
    a constituency for a nationality advocate. A genuine zero stays a zero.
    """
    periods = period_list(period)
    # Dedupe, keep the reader's order, and cap — `dict.fromkeys` does both at once.
    unique = list(dict.fromkeys(i for i in ids if i and i.strip()))
    wanted, dropped = unique[:_COMPARE_MAX], unique[_COMPARE_MAX:]

    stat_scope = (period_sql(periods, "period_number")
                  or "period_number IS NULL")   # the precomputed all-cycles row
    sess_scope = period_and(periods, "s.period_number")
    bills_available = settings.module_enabled("bills")
    votes_available = settings.module_enabled("votes")
    # The roll-call universe is the same for every column, so count it once instead
    # of once per person (each column then only needs its own mandate window).
    total_rollcall = None
    if votes_available:
        total_rollcall = (db.execute(
            f"SELECT COUNT(*) AS n FROM vote WHERE {_rollcall_where(periods)}"
        ).fetchone()["n"] or 0)

    people, missing = [], []
    for person_id in wanted:
        p = db.execute("SELECT * FROM person WHERE person_id = ?",
                       (person_id,)).fetchone()
        if not p:
            missing.append(person_id)
            continue

        totals = db.execute(
            f"""SELECT SUM(speech_count) AS speech_count,
                       SUM(speaking_seconds) AS speaking_seconds,
                       SUM(sentence_count) AS sentence_count
                FROM person_stats WHERE person_id=? AND {stat_scope}""",
            (person_id,)).fetchone()
        speech_count = (totals["speech_count"] if totals else 0) or 0
        speaking_seconds = (totals["speaking_seconds"] if totals else 0) or 0
        sentence_count = (totals["sentence_count"] if totals else 0) or 0
        # On how many sitting days they took the floor — the same count the profile
        # reports as its scope ("N ülésnap alapján"), and the divisor that turns
        # "many speeches" into "many speeches spread thin" or "concentrated".
        speaking_days = db.execute(
            f"""SELECT COUNT(DISTINCT pss.session_id) AS c
                FROM person_session_stats pss JOIN session s ON s.id = pss.session_id
                WHERE pss.person_id = ?{sess_scope}""",
            (person_id,)).fetchone()["c"] or 0

        current = db.execute(
            f"""SELECT f.id AS faction_id, f.label AS faction_label,
                       f.color AS faction_color
                FROM membership m LEFT JOIN faction f ON f.id = m.faction_id
                WHERE m.person_id = ?{period_and(periods, "m.period_number")}
                ORDER BY m.period_number DESC LIMIT 1""",
            (person_id,)).fetchone()

        offices = _person_offices(db, person_id, _loads(p["offices_json"]))
        office = _current_office(db, person_id, periods, offices)

        # Official own-motion count (parlament.hu's own statistic), for the cycle in
        # scope or the most recent on record under "all cycles" — exactly as REP-3
        # computes it for the profile tile. Null for anyone upstream reports none
        # for, which is not the same as zero.
        own_motions = None
        if bills_available:
            by_cycle = (_loads(p["external_stats_json"]) or {}).get("billsSubmitted") or []
            if settings.site_periods:
                by_cycle = [e for e in by_cycle
                            if e.get("cycle") in settings.site_periods]
            own_motions = (_own_bills_for_cycles(by_cycle, periods)
                           if periods else _latest_own_bills(by_cycle))

        is_mp = bool(p["is_mp"])
        participation = (_vote_participation(db, person_id,
                                            p["election_history_json"], periods,
                                            total_rollcall)
                         if votes_available and is_mp else None)

        declarations = _loads(_col(p, "asset_declarations_json")) or []
        committees = _loads(p["committees_json"]) or []

        people.append({
            # --- identity ---------------------------------------------------
            "person_id": p["person_id"], "label": p["label"],
            "photo_uri": p["photo_uri"],
            "is_mp": is_mp, "is_advocate": bool(_col(p, "is_advocate")),
            "nationality": _col(p, "nationality"),
            # Null rather than empty for someone the notion doesn't apply to: an
            # advocate and a non-MP minister hold no seat, so they have no
            # constituency — a blank cell would read as "unknown".
            "constituency": p["constituency"] if is_mp else None,
            "faction": ({"id": current["faction_id"], "label": current["faction_label"],
                         "color": current["faction_color"]}
                        if current and current["faction_label"] else None),
            "office": office["title"] if office else None,
            "highest_education": p["highest_education"],
            "wikipedia_url": p["wikipedia_url"], "kmonitor_url": p["kmonitor_url"],
            "website": p["website"],
            # --- what the corpus counts -------------------------------------
            "speech_count": speech_count,
            "speaking_seconds": speaking_seconds,
            "sentence_count": sentence_count,
            "speaking_days": speaking_days,
            # Mean length of one of their speeches: the shape of a career the two
            # totals above hide (many interjections vs. few long addresses).
            "avg_speech_seconds": (speaking_seconds / speech_count
                                   if speech_count else None),
            "own_motions": own_motions,
            "documents": _doc_counts(db, person_id, periods) if bills_available else None,
            # Biography, not cycle statistics (like the profile's own lists): the
            # whole career's committee seats and published declarations, whatever
            # cycle is selected — `_note` on the row says so in the UI.
            "committee_count": len(committees),
            "declaration_count": sum(1 for d in declarations if d.get("url")),
            # Null (not 0) for a non-MP: they have no mandate to attend roll calls,
            # so "missed none" would be as wrong as "missed all" (REP-3).
            "votes_total": participation["votes_total"] if participation else None,
            "votes_missed": participation["votes_missed"] if participation else None,
            "votes_missed_pct": (participation["votes_missed_pct"]
                                 if participation else None),
            "vote_breakdown": (participation["vote_breakdown"]
                               if participation else None),
        })

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
        "scope": {"description": scope_desc,
                  "period": periods[0] if len(periods) == 1 else None,
                  "periods": periods},
        "limit": _COMPARE_MAX,
        "people": people,
        # Told, not swallowed: the page says which ids it could not put in a column.
        "missing": missing,
        "dropped": dropped,
        # Which optional modules backed this comparison (EXT-6): the rows that
        # depend on one are left out rather than faked when it is off.
        "bills_available": bills_available,
        "votes_available": votes_available,
        "methodology": _MP_METHODOLOGY,
    }


@router.get("/{person_id}")
def get_representative(person_id: str, period: Optional[List[int]] = Query(
                          None, description="Electoral period number(s)"),
                      db: sqlite3.Connection = Depends(get_db)):
    """Full MP profile (REP-2): bio, faction history, constituency, links.

    The shown ``current_faction`` is scoped to the selected cycle(s) (§4A): with
    ``period`` set it is the MP's faction in the most recent cycle *in scope*,
    otherwise their most recent faction overall. (``faction_history`` always
    lists every cycle — it IS the cross-cycle view.) ``mandate``, by contrast, is
    cycle-scoped like the statistics: which seat this profile is about."""
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
    # Runs, not raw rows: upstream splits an unbroken faction membership at every
    # cycle boundary, and dates every spell of a mid-cycle switch with the same cycle
    # label (REP-14). `start`/`end` are the real ones, an open end meaning "still".
    faction_history = [
        {"cycle": (h["cycles"][-1] if h["cycles"] else None), "cycles": h["cycles"],
         "start": h["start"], "end": h["end"],
         "faction": {"id": faction_ids.get(h["label"]),
                     "label": h["label"], "color": colors.get(h["label"])}
                    if h["label"] else None}
        for h in _faction_runs(_loads(p["faction_history_json"]))]
    # The mandate held in the cycle(s) in scope, with the handovers on either side
    # (REP-14) — a former MP's profile is otherwise indistinguishable from a
    # sitting one. Absent on a DB loaded before the composition-changes registry.
    mandate = None
    if _has_mandates(db):
        row = db.execute(
            f"""SELECT date_start AS mandate_start, date_end AS mandate_end,
                       terminated AS mandate_terminated, end_reason AS mandate_end_reason,
                       period_number, constituency, predecessor_id, predecessor_label,
                       successor_id, successor_label
                FROM person_mandate
                WHERE person_id = ?{period_and(period, "period_number")}
                ORDER BY period_number DESC LIMIT 1""", (person_id,)).fetchone()
        mandate = _mandate_out(row)
        if mandate:
            mandate.update({
                "period_number": row["period_number"],
                "constituency": row["constituency"],
                "predecessor": _handover(db, row["predecessor_id"], row["predecessor_label"]),
                "successor": _handover(db, row["successor_id"], row["successor_label"]),
            })
    offices = _person_offices(db, person_id, _loads(p["offices_json"]))
    office = _current_office(db, person_id, period, offices)
    return {
        "person_id": p["person_id"], "label": p["label"],
        "label_full": p["label_full"], "firstname": p["firstname"],
        "lastname": p["lastname"], "photo_uri": p["photo_uri"],
        "wikidata_id": p["wikidata_id"], "wikipedia_url": p["wikipedia_url"],
        "kmonitor_url": p["kmonitor_url"],
        # The CV PDF they had published on parlament.hu, when there is one — only
        # while they sit, since the House takes it down when the mandate ends
        # (REP-13). Absent on a DB built before the column existed (`_col`).
        "cv_url": _col(p, "cv_url"),
        # Every asset declaration (vagyonnyilatkozat) on record, newest first,
        # each linking to its PDF on parlament.hu (REP-13). Biography, like the
        # office and committee history — deliberately NOT cycle-scoped, so the
        # series stays whole no matter which cycle is selected.
        "asset_declarations": _loads(_col(p, "asset_declarations_json")),
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
        # Whether this person ever spoke in the House — deliberately NOT cycle-
        # scoped, unlike the statistics. It answers "which kind of person is this",
        # which does not change with the scope: an office holder from the registry
        # who never spoke (REP-11) has a profile that is an office history and
        # nothing else, and the page must not present them as a silent speaker.
        "has_speeches": bool(db.execute(
            "SELECT 1 FROM person_stats WHERE person_id=? AND period_number IS NULL "
            "AND speech_count > 0", (person_id,)).fetchone()),
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
        # The seat held in the cycle in scope: its term, whether it ended before the
        # term did (and upstream's reason), and the MPs on either side of the
        # handover (REP-14). None when there is no mandate on record for that scope.
        "mandate": mandate,
        "faction_history": faction_history,
        "education": _loads(p["education_json"]),
        "committees": _loads(p["committees_json"]),
        # Their whole office history, newest first — every office (tisztség) they
        # ever held with its real term, historical ones included (REP-2). Not
        # cycle-scoped: it is biography, like `faction_history` and `committees`.
        "offices": offices,
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
    # The per-cycle breakdown deliberately ignores the reader's selection — it is
    # the "which cycles has this MP been active in" chart — but not the cycles the
    # site *serves* (§4A CYC-7): a bar for a cycle this deployment doesn't show
    # would invite a click into pages that answer 404.
    by_period = db.execute(
        """SELECT ep.number AS period, ep.label, ps.speech_count, ps.speaking_seconds,
                  ps.sentence_count
           FROM person_stats ps JOIN electoral_period ep ON ep.number=ps.period_number
           WHERE ps.person_id=? AND ps.period_number IS NOT NULL"""
        + period_and(settings.site_periods, "ps.period_number")
        + " ORDER BY ep.number", (person_id,)).fetchall()

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
        # Upstream reports this per cycle for the MP's whole career; keep the
        # served ones (CYC-7), so the breakdown covers the same span as the rest.
        if settings.site_periods:
            bills_by_cycle = [e for e in bills_by_cycle
                              if e.get("cycle") in settings.site_periods]
        bills_submitted = (_own_bills_for_cycles(bills_by_cycle, periods)
                           if periods else _latest_own_bills(bills_by_cycle))

    # Attendance (REP-3): the roll-call participation split and the headline
    # "cast no vote" metric, computed by the one helper the comparison page
    # (REP-15) also calls — the two pages must never report different numbers for
    # the same person and scope.
    votes_available = settings.module_enabled("votes")
    participation = (_vote_participation(db, person_id,
                                        p["election_history_json"], periods)
                     if votes_available and p["is_mp"] else None)
    votes_total = participation["votes_total"] if participation else 0
    votes_missed = participation["votes_missed"] if participation else 0
    votes_missed_pct = participation["votes_missed_pct"] if participation else None
    vote_breakdown = participation["vote_breakdown"] if participation else None

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
    speeches carrying it, plus the cycles those speeches fall in and whether the
    office is still held (``ongoing`` — then it has no end date, see below). Scoped
    to the selected cycle (§4A) when ``period`` is set, like the rest of the profile.
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
        # Nothing they said carries an office. For anyone who has spoken here that
        # settles it — they hold none, as every ordinary MP does, and an office they
        # held years ago must not be hoisted into the header as if it were current.
        # But the office-holder registry adds ~570 people who never spoke here at
        # all (REP-11): for them there is no speech to read an office off, and the
        # registry's newest term IS the identity this line exists to show. Upstream
        # dates it, so it is reported exactly like a matched term.
        if db.execute("SELECT 1 FROM speech WHERE person_id = :pid LIMIT 1",
                      {"pid": person_id}).fetchone():
            return None
        newest = next((o for o in (offices or [])
                       if isinstance(o, dict) and o.get("title")), None)
        if not newest:
            return None
        return {"title": newest["title"], "start": newest.get("start"),
                "end": newest.get("end"), "dates_from": "term",
                # Not cycle-scoped: there are no speeches to scope by, and the term
                # is always shown with its own dates.
                "ongoing": newest.get("end") is None, "cycles": []}
    start, end = _office_term(offices or [], row["title"], row["last_date"])
    cycles = sorted(int(c) for c in (row["cycles"] or "").split(",") if c.strip())
    # An upstream term is reported as-is, end included — and its end stays **null**
    # when the post is still held (upstream leaves it open), because substituting
    # any date there would announce a departure that never happened: the last
    # sitting day is not the day the prime minister stopped being prime minister.
    if start:
        return {"title": row["title"], "start": start, "end": end,
                # The appointment/dismissal boundaries; a null end = still in office.
                "dates_from": "term", "ongoing": end is None, "cycles": cycles}
    # No upstream term (the usual case for a non-MP minister, who gets no MP
    # enrichment at all): the speeches carrying the title are all we know, and they
    # bound the office only from below — "held at least since", which the UI says.
    # An end date is reported ONLY once we have seen the person speak *without* this
    # office since; while it is still the office on their latest speech, the office
    # is open-ended exactly as an upstream term would be, because the last sitting
    # day of the data says nothing about them having left.
    ongoing = row["last_date"] == db.execute(
        f"""SELECT MAX(ss.date)
            FROM speech sp JOIN session ss ON ss.id = sp.session_id
            WHERE sp.person_id = :pid{extra}""", params).fetchone()[0]
    return {
        "title": row["title"],
        "start": row["first_date"],
        "end": None if ongoing else row["last_date"],
        "dates_from": "speeches",
        "ongoing": ongoing,
        # The cycles **in scope** in which the person spoke holding this title.
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
