"""Interjections module API (§6E / EXT-1) — /api/v1/interjections.

Who shouts over whom. The transcript records the loudest heckling verbatim, in
parentheses inside the interrupted speech, and the loader turns those into rows of
``interjection`` — a directed relation between two representatives (§6E/INT-2). This
module serves the two shapes that relation is read in:

- **The graph** (``/graph``) — one arrow per ordered pair, thick with its count,
  narrowed to the ``top`` most involved people so the picture stays a picture. It
  owns no aggregate table: the whole corpus is 99 000 rows, so grouping them per
  request is a few milliseconds' work, and one fewer derived table is one fewer
  thing that can fall out of step with the transcript it summarises (INT-5).
- **The interjections themselves** (``/list``) — because an arrow that cannot be
  opened is an assertion. Every count on this page leads back to the words, the
  sitting day and the video moment they were shouted at (INT-7).
- **One member's counterparts** (``/partners``) — who shouts at them and whom they
  shout at, each way with its count, which is what fills the two choosers the
  panel's one arrow hangs between (INT-11).

``/graph`` and ``/list`` also answer for **one sitting day** (``session``), which is
what the day page's "who heckled whom today" dialog asks for. A day is one day of
one cycle, so it replaces the cycle scope rather than narrowing it (see
:func:`_scope`).

Two exclusions are applied to everything counted here, and to nothing stored:

- **chairing speeches** (``procedural``), as in every other representative statistic
  (STAT-1) — see :func:`app.loader.rebuild_interjections` for why it dominates this
  measure in particular;
- **a name that resolved to nobody, or to several members of the same House**, which
  buys no arrow (INT-3). ``/graph`` reports both as coverage rather than hiding
  them, so the reader can see what the picture is drawn from (TRUST-1).

Disabling the module removes these routes and the analysis page; nothing else
notices (EXT-6).
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ...db import get_db, period_and, period_key, period_sql
from ...query_cache import cached_aggregate

router = APIRouter(prefix="/interjections", tags=["interjections"])

# How many people the graph may be asked to draw. The lower bound is two because one
# node has nobody to shout at; the upper is where a circular diagram stops being
# readable — beyond it the labels collide and every pair is a hairline.
MIN_TOP = 2
MAX_TOP = 40
DEFAULT_TOP = 12
DEFAULT_RANK = "total"

# What a pair must be for the graph to draw an arrow between them: both ends known,
# not the same person (the transcript occasionally attributes an aside to the member
# already holding the floor — an editing artefact, and a self-loop says nothing on a
# circular diagram), and not shouted over the chair running the sitting (STAT-1).
_GRAPH_WHERE = ("i.speaker_id IS NOT NULL AND i.target_id IS NOT NULL "
                "AND i.speaker_id <> i.target_id AND i.procedural = 0")


def _tables_present(db: sqlite3.Connection) -> bool:
    """Whether the loader has built the §6E table. A DB loaded before this module
    existed simply has no interjections — the endpoints say "not built" and the
    frontend says so, rather than erroring (EXT-6, SCR-5)."""
    return bool(db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='interjection'"
    ).fetchone())


def _require_tables(db: sqlite3.Connection) -> None:
    if not _tables_present(db):
        raise HTTPException(
            status_code=503,
            detail="Interjection table not built yet; run the loader to derive it.")


def _scope(db: sqlite3.Connection, period: Optional[List[int]],
           session: Optional[str]) -> tuple[str, list, Optional[List[int]]]:
    """Which interjections a request is about: a WHERE fragment (led by ``AND``)
    with its parameters, and the cycle scope a member's faction is read in.

    A sitting day *replaces* the cycle scope rather than narrowing it: the day page
    asks for its own interjections whatever cycle the reader has chosen elsewhere,
    and its members wear the faction they sat in on that day's cycle."""
    if not session:
        return period_and(period, "i.period_number"), [], period
    row = db.execute("SELECT period_number FROM session WHERE id = ?",
                     (session,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown sitting day.")
    return " AND i.session_id = ?", [session], [row["period_number"]]


# The faction shown for a node: the most recent membership *within the scope in
# view*, exactly as the representatives list resolves it (§4A), so one person wears
# the same colour on both pages.
def _faction_sub(period: Optional[List[int]]) -> str:
    return ("SELECT m.faction_id FROM membership m WHERE m.person_id = p.person_id"
            + (f" AND {period_sql(period, 'm.period_number')}"
               if period_sql(period, "m.period_number") else "")
            + " ORDER BY m.period_number DESC LIMIT 1")


def _people(db: sqlite3.Connection, ids: List[str],
            period: Optional[List[int]]) -> dict:
    """Name, portrait and faction for the people the graph draws."""
    if not ids:
        return {}
    holes = ",".join("?" * len(ids))
    rows = db.execute(
        f"""SELECT p.person_id, p.label, p.photo_uri,
                   f.id AS faction_id, f.label AS faction_label, f.color AS faction_color
            FROM person p
            LEFT JOIN faction f ON f.id = ({_faction_sub(period)})
            WHERE p.person_id IN ({holes})""", ids).fetchall()
    return {r["person_id"]: {
        "person_id": r["person_id"], "label": r["label"],
        "photo_uri": r["photo_uri"],
        "faction": ({"id": r["faction_id"], "label": r["faction_label"],
                     "color": r["faction_color"]} if r["faction_label"] else None),
    } for r in rows}


@router.get("/graph")
def interjection_graph(
    period: Optional[List[int]] = Query(None),
    top: int = Query(DEFAULT_TOP, ge=MIN_TOP, le=MAX_TOP),
    rank: str = Query(DEFAULT_RANK, pattern="^(total|made|received)$"),
    session: Optional[str] = Query(None, max_length=16),
    db: sqlite3.Connection = Depends(get_db),
):
    """Who interjects over whose speeches, as a directed graph (INT-5, INT-10).

    The cut is always the same shape — the ``top`` highest-ranked people, and the
    arrows *between them* — so the figure holds exactly ``top`` members whatever
    is asked for. Only the ranking changes, and ``rank`` picks it:

    - ``total`` (the default) — interjections **made plus received**, because the
      graph is by default about the exchange and a member who only ever gets
      shouted at belongs in it as much as one who only ever shouts.
    - ``made`` — who interjects the *most*, i.e. "közbeszólt".
    - ``received`` — who is interjected at the most, i.e. "közbeszóltak neki".

    The three pick genuinely different people: the House's loudest hecklers are
    not its most-interrupted members, and `total` hides both behind their sum.

    Arrows are kept only where *both* ends survive the cut, so every arrow drawn
    is complete. A node's `out`/`in` are always the person's true totals in
    scope; `shown_out`/`shown_in` are what the drawn arrows account for, so the
    page can say how much of a person's cross-talk is on screen rather than
    implying the picture is all of it.

    ``session`` draws one sitting day instead of the cycle scope (``period`` is
    then ignored).

    The pair counts are grouped per request (no aggregate table, INT-5) and the
    payload memoized per scope + ``top`` + ``rank`` like the other aggregates.
    """
    _require_tables(db)
    where_scope, scope_params, faction_period = _scope(db, period, session)

    def compute():
        pairs = db.execute(
            f"""SELECT i.speaker_id, i.target_id, COUNT(*) AS n
                FROM interjection i
                WHERE {_GRAPH_WHERE}{where_scope}
                GROUP BY i.speaker_id, i.target_id""", scope_params).fetchall()

        out_total: dict[str, int] = {}
        in_total: dict[str, int] = {}
        for speaker_id, target_id, n in pairs:
            out_total[speaker_id] = out_total.get(speaker_id, 0) + n
            in_total[target_id] = in_total.get(target_id, 0) + n

        everyone = set(out_total) | set(in_total)

        if rank == "total":
            metric = {p: out_total.get(p, 0) + in_total.get(p, 0) for p in everyone}
        elif rank == "made":
            metric = out_total
        else:
            metric = in_total
        # Ranked by the chosen metric, then by name-independent id so a tie is
        # stable across requests (a slider dragged back and forth must not
        # reshuffle).
        ranked = sorted(everyone, key=lambda p: (-metric.get(p, 0), p))[:top]
        keep = set(ranked)

        links = [{"source": s, "target": t, "count": n}
                 for s, t, n in pairs if s in keep and t in keep]
        shown_out: dict[str, int] = {}
        shown_in: dict[str, int] = {}
        for link in links:
            shown_out[link["source"]] = shown_out.get(link["source"], 0) + link["count"]
            shown_in[link["target"]] = shown_in.get(link["target"], 0) + link["count"]

        people = _people(db, ranked, faction_period)
        # Order the nodes by faction, then by involvement inside it: the circular
        # layout draws them in the order given, so grouping here is what makes the
        # picture legible — an arrow crossing the circle is then a cross-bench one.
        # A person with no faction in scope sorts last, together.
        nodes = [{
            **people.get(p, {"person_id": p, "label": p, "photo_uri": None,
                             "faction": None}),
            "out": out_total.get(p, 0), "in": in_total.get(p, 0),
            "shown_out": shown_out.get(p, 0), "shown_in": shown_in.get(p, 0),
        } for p in ranked]
        nodes.sort(key=lambda n: (
            (n["faction"] or {}).get("label") is None,
            (n["faction"] or {}).get("label") or "",
            -metric.get(n["person_id"], 0), n["person_id"]))

        # Links reference node *indices*, as the other figures' payloads do.
        at = {n["person_id"]: i for i, n in enumerate(nodes)}
        for link in links:
            link["source"], link["target"] = at[link["source"]], at[link["target"]]
        links.sort(key=lambda l: -l["count"])

        total = sum(n for _s, _t, n in pairs)
        return {
            "top": top,
            "rank": rank,
            "nodes": nodes,
            "links": links,
            # What the drawn picture leaves out, said out loud (TRUST-1): how many
            # people the relation has at all, and how much of it the top `top` hold.
            "people_total": len(everyone),
            "total": total,
            "shown": sum(link["count"] for link in links),
            "coverage": _coverage(db, where_scope, scope_params),
        }

    return cached_aggregate(
        "interjection_graph",
        (session or period_key(period), top, rank), compute)


def _coverage(db: sqlite3.Connection, where_scope: str, scope_params: list) -> dict:
    """What the extraction found in scope and how much of it the graph can use —
    the module's methodology note in numbers (INT-8)."""
    row = db.execute(
        f"""SELECT COUNT(*) AS extracted,
                   SUM(i.speaker_id IS NOT NULL) AS attributed,
                   SUM(i.procedural) AS procedural
            FROM interjection i
            WHERE 1=1{where_scope}""", scope_params).fetchone()
    return {"extracted": row["extracted"] or 0,
            "attributed": row["attributed"] or 0,
            "procedural": row["procedural"] or 0}


@router.get("/partners")
def interjection_partners(
    person: str = Query(..., max_length=32),
    period: Optional[List[int]] = Query(None),
    db: sqlite3.Connection = Depends(get_db),
):
    """Everyone one member exchanged interjections with, each way (INT-11).

    The panel is one arrow — who shouted, who was interrupted — and either end
    can be swapped for anyone the other end actually exchanged interjections
    with. These two lists are that offer: each entry carries its count, so
    picking a counterpart is an informed choice, the shape of someone's
    cross-talk is legible before any of it is opened, and a chooser can never
    land on an empty list.

    The same exclusions as the graph apply (``_GRAPH_WHERE``), so a count here
    always matches the list that opening that counterpart gives.
    """
    _require_tables(db)

    def compute():
        def counterparts(fixed: str, other: str) -> list[dict]:
            rows = db.execute(
                f"""SELECT i.{other} AS person_id, p.label, COUNT(*) AS count
                    FROM interjection i
                    JOIN person p ON p.person_id = i.{other}
                    WHERE {_GRAPH_WHERE} AND i.{fixed} = ?
                          {period_and(period, 'i.period_number')}
                    GROUP BY i.{other}
                    ORDER BY count DESC, p.label""", [person]).fetchall()
            return [{"person_id": r["person_id"], "label": r["label"],
                     "count": r["count"]} for r in rows]

        return {
            "person_id": person,
            # Who interjected at them, and whom they interjected at.
            "received_from": counterparts("target_id", "speaker_id"),
            "made_to": counterparts("speaker_id", "target_id"),
        }

    return cached_aggregate(
        "interjection_partners", (period_key(period), person), compute)


@router.get("/list")
def interjection_list(
    period: Optional[List[int]] = Query(None),
    speaker: Optional[str] = Query(None, max_length=32),
    target: Optional[str] = Query(None, max_length=32),
    person: Optional[str] = Query(None, max_length=32),
    session: Optional[str] = Query(None, max_length=16),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
):
    """The interjections behind one arrow, or all of one person's.

    The person filters are ids, and one of them or a ``session`` is required — an
    unfiltered dump of a hundred thousand shouts is not a view of anything, while
    one sitting day's is (a few dozen, read in the order they were shouted, which
    is how a day is listed; any other scope lists newest first). ``speaker`` +
    ``target`` together are one arrow; either alone is one end of it against
    everyone, which is what the page opens a clicked member with (INT-11).
    ``person`` is both of their directions at once — what a clicked node opened
    before the panel became a single reversible arrow; the page no longer asks for
    it, and the filter stays part of the API.

    Each row carries the words, the sitting date, the agenda item they interrupted
    and the speech + sentence the viewer deep-links by (VIE-5), so the count on the
    graph opens onto the moment it was shouted (INT-7).
    """
    _require_tables(db)
    if not speaker and not target and not person and not session:
        raise HTTPException(
            status_code=400,
            detail="Pass speaker, target, person (ids) and/or session.")

    where_scope, scope_params, _ = _scope(db, period, session)
    where = [_GRAPH_WHERE]
    params: list = []
    if speaker:
        where.append("i.speaker_id = ?")
        params.append(speaker)
    if target:
        where.append("i.target_id = ?")
        params.append(target)
    if person:
        where.append("(i.speaker_id = ? OR i.target_id = ?)")
        params.extend((person, person))
    where_sql = " AND ".join(where) + where_scope
    params += scope_params
    order = ("sp.speech_index, i.speech_uid, i.ord" if session
             else "ss.date DESC, i.speech_uid DESC, i.ord DESC")

    total = db.execute(
        f"SELECT COUNT(*) AS c FROM interjection i WHERE {where_sql}",
        params).fetchone()["c"]
    rows = db.execute(
        f"""SELECT i.id, i.text, i.speech_uid, i.session_id, i.sentence_id,
                   i.speaker_id, i.target_id,
                   sp.speaker_label, se.ord AS sentence_ord, se.time_start, ss.date,
                   who.label AS speaker_label_name, whom.label AS target_label_name,
                   ai.title AS agenda_title
            FROM interjection i
            JOIN speech sp   ON sp.uid = i.speech_uid
            JOIN session ss  ON ss.id = i.session_id
            LEFT JOIN sentence se   ON se.id = i.sentence_id
            LEFT JOIN person who    ON who.person_id = i.speaker_id
            LEFT JOIN person whom   ON whom.person_id = i.target_id
            LEFT JOIN agenda_item ai ON ai.id = sp.agenda_item_id
            WHERE {where_sql}
            ORDER BY {order}
            LIMIT ? OFFSET ?""", [*params, limit, offset]).fetchall()
    return {
        "total": total, "limit": limit, "offset": offset,
        "interjections": [{
            "id": r["id"], "text": r["text"],
            "speech_uid": r["speech_uid"], "session_id": r["session_id"],
            "sentence_id": r["sentence_id"], "sentence_ord": r["sentence_ord"],
            "time_start": r["time_start"],
            "date": r["date"], "agenda_title": r["agenda_title"],
            "speaker": {"person_id": r["speaker_id"],
                        "label": r["speaker_label_name"]},
            "target": {"person_id": r["target_id"],
                       "label": r["target_label_name"] or r["speaker_label"]},
        } for r in rows],
    }
