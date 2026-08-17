"""Settlements module API (§6D / EXT-1) — /api/v1/settlements.

The local dimension of the corpus: which of Hungary's 3 177 settlements the House
names, how often, by whom — and which it has never named at all (TEL-7). It owns no
source of its own: every count is a precomputed aggregate over the `settlement*`
tables the loader derives from the shared `sentence`, `speech`, `person` and `entity`
rows (EXT-2), and the geography is the constituency lookup's own National Election
Office data (TEL-5/REP-10). Disabling the module removes these routes and the
profile panel; nothing else notices (EXT-6).

Two deliberate shapes here:

- **The map is its own request** (`/map`), independent of the ranked list beside it
  and cached per cycle scope, so a page that draws 3 177 points never waits on
  pagination and vice versa (WCLOUD-5's rule).
- **A count is never a dead end.** Every settlement's page carries the sentences
  behind its number (`/mentions`), so a reader lands on the speech, the speaker and
  the video moment (TEL-4). A blind spot has its own page too, saying so plainly —
  that page *is* the finding.
"""

from __future__ import annotations

import json
import sqlite3
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from ... import settlements, valasztas
from ...analytics import search_analytics
from ...config import settings
from ...db import (get_db, like_contains, period_and, period_key, period_list,
                   period_sql)
from ...query_cache import cached_aggregate

router = APIRouter(prefix="/settlements", tags=["settlements"])

# How a mention count is scoped. `settlement_stats` carries one row per cycle plus
# an all-cycles row (period_number IS NULL), so "all cycles" reads the single NULL
# row while a multi-cycle scope sums its cycles (CYC-4).
def _stats_join(period: Optional[List[int]], alias: str = "st") -> tuple[str, str]:
    """``(join predicate, group column)`` for the stats table under this scope."""
    scope = period_list(period)
    if not scope:
        return f"{alias}.period_number IS NULL", ""
    return period_sql(scope, f"{alias}.period_number"), ""


def _has_rows(db: sqlite3.Connection, table: str, where: str = "1=1") -> bool:
    """Whether an optional table exists **and** holds a row matching `where`. The
    segmented views hang off tables that a given deployment may simply not have
    (no h3 library, no reachable geometry), and the rule throughout is that such a
    view is never offered rather than offered and then failing (EXT-6)."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                      (table,)).fetchone():
        return False
    return bool(db.execute(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1").fetchone())


# The binnings the map can be read in, beyond the per-settlement points (TEL-15,
# TEL-16), in the order the UI offers them: the constituency first because it is a
# territory the reader already knows and can be held to account, the hexagon after it
# because it is the abstract, equal-area one.
def _segment_bins(db: sqlite3.Connection) -> list[str]:
    bins = []
    if _has_rows(db, "constituency", "boundary IS NOT NULL"):
        bins.append("oevk")
    if settlements.h3_available() and _has_rows(db, "settlement_h3"):
        bins.append("h3")
    return bins


def _tables_present(db: sqlite3.Connection) -> bool:
    """Whether the loader has built the §6D tables. A DB loaded before this module
    existed simply has no settlements — the endpoints answer "not built" and the
    frontend says so, rather than erroring (EXT-6, SCR-5)."""
    return bool(db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='settlement'"
    ).fetchone())


def _require_tables(db: sqlite3.Connection) -> None:
    if not _tables_present(db):
        raise HTTPException(
            status_code=503,
            detail="Settlement tables not built yet; run the loader (or "
                   "migrate_settlements.py) to derive them.")


def _source(db: sqlite3.Connection) -> dict:
    """The provenance line every page of this module must show (TRUST-1/TEL-12):
    the mentions are ours, the geography is the election office's."""
    return {
        "geography": "Nemzeti Választási Iroda (valasztas.hu)",
        "settlements": db.execute("SELECT COUNT(*) FROM settlement").fetchone()[0],
        "with_coordinates": db.execute(
            "SELECT COUNT(*) FROM settlement WHERE lat IS NOT NULL").fetchone()[0],
    }


@router.get("")
def list_settlements(
    q: Optional[str] = None,
    county: Optional[str] = None,
    mentioned: Optional[str] = Query(
        None, pattern="^(yes|no)$",
        description="'yes' = named at least once in scope; 'no' = the blind spots"),
    sort: str = Query("mentions", pattern="^(mentions|name|electorate)$"),
    period: Optional[List[int]] = Query(
        None, description="Electoral period number(s); repeat to scope to several"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
):
    """The settlement listing — the ranked half of the module's front page, and the
    blind-spot list when ``mentioned=no`` (TEL-6/TEL-7).

    The join is a LEFT JOIN throughout, deliberately: a settlement with no mention
    row is not missing data, it is a settlement nobody named, and it has to be
    listable and countable as such. `q` matches the name accent-insensitively (§4B
    FOLD-1).
    """
    if not _tables_present(db):
        return {"settlements": [], "total": 0, "limit": limit, "offset": offset,
                "built": False}

    scope, params = _stats_join(period)[0], {}
    where = ["1=1"]
    if q:
        where.append("fold(s.name) LIKE fold(:q) ESCAPE '\\'")
        params["q"] = like_contains(q.strip())
    if county:
        where.append("s.county = :county")
        params["county"] = county
    if mentioned == "yes":
        where.append("COALESCE(m.mention_count, 0) > 0")
    elif mentioned == "no":
        where.append("COALESCE(m.mention_count, 0) = 0")

    order = {
        "mentions": "COALESCE(m.mention_count, 0) DESC, s.name COLLATE NOCASE",
        "name": "s.name COLLATE NOCASE",
        "electorate": "COALESCE(s.electorate, 0) DESC, s.name COLLATE NOCASE",
    }[sort]     # a fixed whitelist, never spliced from raw input (cf. SEA-10)

    # The per-settlement totals for this scope, summed once and joined — a
    # multi-cycle scope sums its cycles' rows, "all cycles" reads the NULL row.
    base = f"""
        FROM settlement s
        LEFT JOIN (SELECT st.settlement_id,
                          SUM(st.mention_count) AS mention_count,
                          SUM(st.speech_count)  AS speech_count,
                          MAX(st.last_date)     AS last_date
                   FROM settlement_stats st WHERE {scope}
                   GROUP BY st.settlement_id) m ON m.settlement_id = s.id
        WHERE {' AND '.join(where)}"""
    total = db.execute(f"SELECT COUNT(*) AS c {base}", params).fetchone()["c"]
    rows = db.execute(
        f"""SELECT s.id, s.name, s.county, s.lat, s.lon, s.electorate, s.ambiguity,
                   COALESCE(m.mention_count, 0) AS mentions,
                   COALESCE(m.speech_count, 0) AS speeches, m.last_date
            {base} ORDER BY {order} LIMIT :limit OFFSET :offset""",
        {**params, "limit": limit, "offset": offset}).fetchall()

    # Privacy-respecting analytics (PRIV-2/SEA-11): the typed keyword plus the
    # filters it was combined with, nothing about the reader.
    search_analytics.record(source="settlements", query=q, period=period,
                            county=county, mentioned=mentioned, sort=sort,
                            results=total, offset=offset)
    return {
        "settlements": [{
            "id": r["id"], "name": r["name"], "county": r["county"],
            "lat": r["lat"], "lon": r["lon"], "electorate": r["electorate"],
            "mentions": r["mentions"], "speeches": r["speeches"],
            "last_date": r["last_date"],
            # Whether this name needed corroboration to count at all (TEL-3), so a
            # reader can see why a homonym village reads low.
            "ambiguous": bool(r["ambiguity"]),
        } for r in rows],
        "total": total, "limit": limit, "offset": offset, "built": True,
    }


@router.get("/map")
def settlement_map(
    period: Optional[List[int]] = Query(None),
    db: sqlite3.Connection = Depends(get_db),
):
    """Every settlement with a coordinate, and how often it was named in scope
    (TEL-6) — one cached aggregate, independent of the list beside it.

    Includes the settlements with **zero** mentions: they are the blind spots, and
    the map's whole point is that they are visible (TEL-7). The payload is
    deliberately flat and short-keyed — 3 177 rows go over the wire on every load —
    and the response carries the scale's own extremes so the client can build a
    legend without a second pass.
    """
    _require_tables(db)

    def compute():
        scope = _stats_join(period)[0]
        rows = db.execute(
            f"""SELECT s.id, s.name, s.county, s.lat, s.lon,
                       COALESCE(SUM(st.mention_count), 0) AS n
                FROM settlement s
                LEFT JOIN settlement_stats st
                       ON st.settlement_id = s.id AND {scope}
                WHERE s.lat IS NOT NULL AND s.lon IS NOT NULL
                GROUP BY s.id ORDER BY n DESC""").fetchall()
        counts = [r["n"] for r in rows if r["n"] > 0]
        return {
            # [id, name, county, lat, lon, mentions] — positional, because the key
            # names would otherwise be three quarters of the payload.
            "columns": ["id", "name", "county", "lat", "lon", "mentions"],
            "points": [[r["id"], r["name"], r["county"], r["lat"], r["lon"], r["n"]]
                       for r in rows],
            "max": max(counts) if counts else 0,
            "named": len(counts),
            "blind": len(rows) - len(counts),
        }

    # The basemap config rides outside the cached payload: it comes from deployment
    # settings (OPS-4), not from the data, so it must not be frozen into a cache
    # entry that outlives a config change.
    bins = _segment_bins(db)
    return {
        **cached_aggregate("settlement_map", (period_key(period),), compute),
        "map": {
            "tile_url": settings.map_tile_url,
            "tile_attribution": settings.map_tile_attribution,
            "max_zoom": settings.map_max_zoom,
        },
        # Whether this map can also be asked for **in segments** (TEL-15/TEL-16), in
        # which binnings, and at which H3 resolutions. Carried here so the page knows
        # what to offer without a second round trip — and so it offers nothing at all
        # where a binning cannot be built, rather than a control that fails when used
        # (EXT-6).
        "segments": {
            "available": bool(bins),
            "bins": bins,
            "default": settlements.H3_DEFAULT_RESOLUTION,
            "resolutions": [{"resolution": r, "area_km2": settlements.h3_cell_area(r)}
                            for r in settlements.H3_RESOLUTIONS],
        },
    }


@router.get("/map/segments")
def settlement_map_segments(
    bins: str = Query(
        "h3", pattern="^(h3|oevk)$",
        description="'h3' = equal-area hexagons (TEL-15); "
                    "'oevk' = the single-member constituencies (TEL-16)"),
    resolution: Optional[int] = Query(
        None, ge=0, le=12,
        description="H3 resolution; defaults to the middle of the offered set. "
                    "Ignored when bins=oevk"),
    period: Optional[List[int]] = Query(None),
    db: sqlite3.Connection = Depends(get_db),
):
    """The map read **in segments** — the settlements aggregated into an area and the
    area shaded, rather than a mark drawn per place. Two binnings, because they answer
    different questions:

    - ``h3`` (TEL-15) bins into **equal-area hexagons**, so a cell is never loud merely
      for being large: the honest picture of *where* attention falls.
    - ``oevk`` (TEL-16) bins into the **single-member constituencies** — near-equal in
      *electorate* rather than in area, and each with a member who can be asked about
      it: the picture of *whose* places get named.

    Neither replaces `/map`: a point map answers *which places*, and stays the default.
    Both are answered as **GeoJSON**, the format the constituency picker already
    consumes, so the frontend needs no geometry library of its own. A segmented payload
    is not automatically the cheaper one — a polygon costs many coordinate pairs where
    a point costs one — which is one more reason the binning is the reader's choice.

    Declared before ``/{maz}/{taz}`` — both are two-segment paths and the first
    declaration wins.
    """
    _require_tables(db)
    if bins not in _segment_bins(db):
        # An honest "unavailable", never an empty country (SCR-5): the UI hides the
        # option rather than showing a blank map.
        raise HTTPException(
            status_code=503,
            detail=("The segmented map needs the h3 package; the point map is "
                    "unaffected." if bins == "h3" else
                    "No constituency boundaries are stored; the point map is "
                    "unaffected."))
    if bins == "oevk":
        return _constituency_segments(db, period)
    offered = settlements.H3_RESOLUTIONS
    if resolution is None:
        resolution = settlements.H3_DEFAULT_RESOLUTION
    elif resolution not in offered:
        raise HTTPException(
            status_code=400,
            detail=f"resolution must be one of {list(offered)} "
                   "(PARLAMONITOR_H3_RESOLUTIONS)")

    def compute():
        scope = _stats_join(period)[0]
        rows = db.execute(
            f"""SELECT h.cell AS cell,
                       COUNT(*) AS settlements,
                       COALESCE(SUM(t.n), 0) AS mentions,
                       SUM(CASE WHEN t.n > 0 THEN 1 ELSE 0 END) AS named
                FROM settlement_h3 h
                JOIN settlement s ON s.id = h.settlement_id
                LEFT JOIN (SELECT st.settlement_id AS sid, SUM(st.mention_count) AS n
                           FROM settlement_stats st WHERE {scope}
                           GROUP BY st.settlement_id) t ON t.sid = h.settlement_id
                WHERE h.resolution = ?
                GROUP BY h.cell""", (resolution,)).fetchall()

        # The places behind each cell, so a segment can name what it is made of — a
        # hexagon with no names in it is a shape, not a finding. Top three by
        # mentions, then alphabetically, so a cell of all-zeroes still names someone.
        top: dict[str, list[dict]] = {}
        for r in db.execute(
                f"""SELECT h.cell AS cell, s.name AS name, s.id AS id,
                           COALESCE(t.n, 0) AS n
                    FROM settlement_h3 h
                    JOIN settlement s ON s.id = h.settlement_id
                    LEFT JOIN (SELECT st.settlement_id AS sid,
                                      SUM(st.mention_count) AS n
                               FROM settlement_stats st WHERE {scope}
                               GROUP BY st.settlement_id) t ON t.sid = h.settlement_id
                    WHERE h.resolution = ?
                    ORDER BY h.cell, n DESC, s.name COLLATE NOCASE""",
                (resolution,)):
            bucket = top.setdefault(r["cell"], [])
            if len(bucket) < 3:
                bucket.append({"id": r["id"], "name": r["name"],
                               "mentions": r["n"]})

        features = []
        for r in rows:
            geometry = settlements.h3_polygon(r["cell"])
            if geometry is None:
                continue
            named = r["named"] or 0
            count = r["settlements"]
            features.append({
                "type": "Feature", "id": r["cell"],
                "properties": {
                    "cell": r["cell"],
                    "mentions": r["mentions"],
                    "settlements": count,
                    "named": named,
                    "blind": count - named,
                    # The share of the cell's settlements never named. Reported with
                    # `settlements` beside it because a share over one place is not a
                    # share — the finer the resolution, the more cells hold exactly
                    # one, and the reader has to be able to see that (TEL-15).
                    "blind_share": round((count - named) / count, 4) if count else None,
                    "top": top.get(r["cell"], []),
                },
                "geometry": geometry,
            })
        mentions = [f["properties"]["mentions"] for f in features]
        return {
            "type": "FeatureCollection", "features": features,
            "bins": "h3",
            "resolution": resolution,
            "max_mentions": max(mentions) if mentions else 0,
            # The floor as well as the ceiling, so the client can stretch its ramp over
            # the range the data actually occupies — see the constituency binning, where
            # no cell is anywhere near zero and a ramp anchored there wastes half of
            # itself. Zero here (as it usually is for hexagons) changes nothing.
            "min_mentions": min(mentions) if mentions else 0,
            "cells": len(features),
        }

    return {
        **cached_aggregate("settlement_segments",
                           (resolution, period_key(period)), compute),
        # Static, so outside the cached payload (as on /map): the offered set and each
        # option's cell size, which is what lets the UI label a resolution with what
        # it means instead of a bare number.
        "resolutions": [{"resolution": r, "area_km2": settlements.h3_cell_area(r)}
                        for r in offered],
        "map": {
            "tile_url": settings.map_tile_url,
            "tile_attribution": settings.map_tile_attribution,
            "max_zoom": settings.map_max_zoom,
        },
    }


def _constituency_segments(db: sqlite3.Connection,
                           period: Optional[List[int]]) -> dict:
    """The map binned into the 106 **single-member constituencies** (TEL-16).

    The unit is deliberately not equal-*area* like the hexagons but equal-*electorate*:
    a constituency holds roughly the same number of voters as any other by law, which
    makes "these voters' places were named N times" comparable across cells in a way an
    administrative region never is. And unlike a hexagon it has a **member**, so each
    cell carries who holds it and how much of it they have named — the map answer to
    TEL-9's question.

    Two disclosures ride on the payload because the geometry forces them, and stating
    them is the difference between a map and a wrong map:

    - **The cells overlap.** 23 settlements — Debrecen, Szeged, Pécs and the split
      Budapest districts among them — lie in more than one constituency, and a mention
      names the *place*, never the part of it. Their mentions are therefore counted in
      **each** constituency the place belongs to: for the question "are my
      constituency's places talked about" that is the true answer, and it is what TEL-9
      already does for the MPs who share such a city. The consequence is that the cells
      do **not** sum to the national total, so the response reports the overlap rather
      than leaving a reader to add them up.
    - **The capital is in none of them.** *Budapest* is its own entity beside its
      districts (TEL-5) and spans 16 constituencies, so its mentions — the largest
      single figure in the corpus — are attributed to no cell at all. Reported as
      `unattributed` so the page can say so, since a quiet Budapest would otherwise be
      the map's most visible and most wrong claim.
    """
    def compute():
        scope = _stats_join(period)[0]
        # `settlement_constituency` is the many-to-many, so a split settlement joins to
        # each of its constituencies — see the overlap disclosure above.
        rows = db.execute(
            f"""SELECT c.id, c.label, c.number, c.county, c.seat,
                       c.electorate, c.boundary,
                       COUNT(sc.settlement_id) AS settlements,
                       COALESCE(SUM(t.n), 0) AS mentions,
                       SUM(CASE WHEN t.n > 0 THEN 1 ELSE 0 END) AS named,
                       SUM(CASE WHEN sh.parts > 1 THEN 1 ELSE 0 END) AS shared
                FROM constituency c
                LEFT JOIN settlement_constituency sc ON sc.label = c.label
                LEFT JOIN (SELECT settlement_id, COUNT(*) AS parts
                           FROM settlement_constituency
                           GROUP BY settlement_id) sh
                       ON sh.settlement_id = sc.settlement_id
                LEFT JOIN (SELECT st.settlement_id AS sid, SUM(st.mention_count) AS n
                           FROM settlement_stats st WHERE {scope}
                           GROUP BY st.settlement_id) t ON t.sid = sc.settlement_id
                WHERE c.boundary IS NOT NULL
                GROUP BY c.id
                ORDER BY c.label COLLATE NOCASE""").fetchall()

        # The places behind each cell, so a segment can name what it is made of.
        top: dict[str, list[dict]] = {}
        for r in db.execute(
                f"""SELECT sc.label AS label, s.id AS id, s.name AS name,
                           COALESCE(t.n, 0) AS n
                    FROM settlement_constituency sc
                    JOIN settlement s ON s.id = sc.settlement_id
                    LEFT JOIN (SELECT st.settlement_id AS sid,
                                      SUM(st.mention_count) AS n
                               FROM settlement_stats st WHERE {scope}
                               GROUP BY st.settlement_id) t ON t.sid = sc.settlement_id
                    ORDER BY sc.label, n DESC, s.name COLLATE NOCASE"""):
            bucket = top.setdefault(r["label"], [])
            if len(bucket) < 3:
                bucket.append({"id": r["id"], "name": r["name"], "mentions": r["n"]})

        # TEL-9's own-constituency measure, per seat rather than per person: a
        # constituency has one holder at a time, so where the scope spans several
        # cycles the latest one in it answers — the same rule `_mp_for` follows.
        own: dict[str, sqlite3.Row] = {}
        for r in db.execute(
                f"""SELECT pss.constituency, pss.own_named, pss.own_total
                    FROM person_settlement_stats pss
                    WHERE {period_sql(period, 'pss.period_number') or '1=1'}
                    ORDER BY pss.period_number"""):
            own[r["constituency"]] = r
        holders = {m["constituency"]: m
                   for m in _mp_for(db, [r["label"] for r in rows], period)}

        features = []
        for r in rows:
            count = r["settlements"] or 0
            named = r["named"] or 0
            seat_own = own.get(r["label"])
            features.append({
                "type": "Feature", "id": r["id"],
                "properties": {
                    "cell": r["id"],
                    "label": r["label"],
                    "number": r["number"],
                    "county": r["county"],
                    # The seat town, not the office's formal name for the seat: "Pécs"
                    # locates a constituency for a reader where "Baranya vármegye, 01.
                    # számú egyéni választókerület" only restates its number.
                    "seat": r["seat"],
                    "electorate": r["electorate"],
                    "mentions": r["mentions"],
                    "settlements": count,
                    "named": named,
                    "blind": count - named,
                    # Reported with `settlements` beside it, because a share over one
                    # place is not a share — 19 of the 106 constituencies hold three
                    # settlements or fewer, and 5 hold exactly one (TEL-16).
                    "blind_share": round((count - named) / count, 4) if count else None,
                    # How many of this cell's settlements it shares with another
                    # constituency, so the overlap is visible on the cell that has it
                    # and not only in the note under the map.
                    "shared": r["shared"] or 0,
                    "top": top.get(r["label"], []),
                    # Who holds the seat, and how much of their own patch they have
                    # named. Carried per cell rather than shaded, because `own_total`
                    # runs from 1 to 174 settlements: as a colour it would invite a
                    # comparison the denominators do not support, while as a figure
                    # read one cell at a time with its own `n of m` it is a fact
                    # (TEL-9's floor, TEL-16).
                    # Who holds the seat **in the latest cycle in scope**, or nothing
                    # where this DB has no holder recorded for it in that cycle — never
                    # a member of another cycle passed off as the current one.
                    "mp": ({"person_id": holders[r["label"]]["person_id"],
                            "name": holders[r["label"]]["name"],
                            "faction": holders[r["label"]]["faction"]}
                           if r["label"] in holders else None),
                    "own_named": seat_own["own_named"] if seat_own else None,
                    "own_total": seat_own["own_total"] if seat_own else None,
                },
                "geometry": {"type": "Polygon",
                             "coordinates": [json.loads(r["boundary"])]},
            })

        # The two figures that keep the cells honest (see the docstring).
        overlap = db.execute(
            f"""SELECT COUNT(*) AS settlements, COALESCE(SUM(n), 0) AS mentions FROM (
                    SELECT sc.settlement_id,
                           (SELECT COALESCE(SUM(st.mention_count), 0)
                            FROM settlement_stats st
                            WHERE st.settlement_id = sc.settlement_id AND {scope}) AS n
                    FROM settlement_constituency sc
                    GROUP BY sc.settlement_id HAVING COUNT(*) > 1)""").fetchone()
        loose = db.execute(
            f"""SELECT s.name AS name,
                       COALESCE(SUM(st.mention_count), 0) AS mentions
                FROM settlement s
                LEFT JOIN settlement_constituency sc ON sc.settlement_id = s.id
                LEFT JOIN settlement_stats st
                       ON st.settlement_id = s.id AND {scope}
                WHERE sc.settlement_id IS NULL
                GROUP BY s.id HAVING mentions > 0
                ORDER BY mentions DESC""").fetchall()

        mentions = [f["properties"]["mentions"] for f in features]
        return {
            "type": "FeatureCollection", "features": features,
            "bins": "oevk",
            "max_mentions": max(mentions) if mentions else 0,
            # Every constituency holds twenty-odd settlements, so the quietest of them
            # still counts in the dozens: anchored at zero the ramp would spend its two
            # palest steps on a range no cell occupies, and 103 of the 106 would share
            # two colours. Reported so the client stretches the ramp over the observed
            # range instead, with the legend stating the edges it lands on.
            "min_mentions": min(mentions) if mentions else 0,
            "cells": len(features),
            "overlap": {"settlements": overlap["settlements"],
                        "mentions": overlap["mentions"]},
            "unattributed": {
                "settlements": len(loose),
                "mentions": sum(r["mentions"] for r in loose),
                "names": [r["name"] for r in loose[:3]],
            },
        }

    return {
        **cached_aggregate("settlement_oevk_segments", (period_key(period),), compute),
        "map": {
            "tile_url": settings.map_tile_url,
            "tile_attribution": settings.map_tile_attribution,
            "max_zoom": settings.map_max_zoom,
        },
    }


@router.get("/summary")
def settlement_summary(
    period: Optional[List[int]] = Query(None),
    db: sqlite3.Connection = Depends(get_db),
):
    """How much of the country the House actually names, in scope (TEL-7): the named
    / never-named split, the totals behind it, and the same split per county — the
    breakdown that turns "1 300 never mentioned" into somewhere to look.

    Every number here is stated **for the cycle scope it was computed in**, because
    "never mentioned" over one term is a different claim from over the corpus, and
    the page must not blur them (TEL-7).
    """
    _require_tables(db)

    def compute():
        scope = _stats_join(period)[0]
        rows = db.execute(
            f"""SELECT s.county AS county, COUNT(*) AS settlements,
                       SUM(CASE WHEN t.n > 0 THEN 1 ELSE 0 END) AS named,
                       COALESCE(SUM(t.n), 0) AS mentions
                FROM settlement s
                LEFT JOIN (SELECT st.settlement_id AS sid, SUM(st.mention_count) AS n
                           FROM settlement_stats st WHERE {scope}
                           GROUP BY st.settlement_id) t ON t.sid = s.id
                GROUP BY s.county ORDER BY s.county COLLATE NOCASE""").fetchall()
        counties = [{"county": r["county"], "settlements": r["settlements"],
                     "named": r["named"] or 0,
                     "blind": r["settlements"] - (r["named"] or 0),
                     "mentions": r["mentions"]} for r in rows]
        return {
            "settlements": sum(c["settlements"] for c in counties),
            "named": sum(c["named"] for c in counties),
            "blind": sum(c["blind"] for c in counties),
            "mentions": sum(c["mentions"] for c in counties),
            "counties": counties,
            "source": _source(db),
        }

    return cached_aggregate("settlement_summary", (period_key(period),), compute)


# A share computed over a handful of mentions is noise, and noise sorted descending
# is worse than noise: ranked by focus alone, the top of the list is whoever named
# exactly one place, which happened to be theirs — 100 %, from three sentences. So a
# **ratio ordering** requires a floor of mentions to rank at all. The floor applies
# only to the ratio sorts: ordering by raw mentions has no denominator to distort, so
# it shows everyone.
RATIO_MIN_MENTIONS = 5


@router.get("/representatives")
def settlement_representatives(
    sort: str = Query("focus", pattern="^(focus|coverage|mentions)$"),
    min_mentions: Optional[int] = Query(
        None, ge=0, le=500,
        description="Minimum settlement mentions to be ranked by a ratio; "
                    f"defaults to {RATIO_MIN_MENTIONS} for focus/coverage, 0 for mentions"),
    period: Optional[List[int]] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
):
    """TEL-9 — do MPs talk about their own constituency?

    Two measures per representative, kept apart because they answer different
    questions: **focus** is the share of the places they name that are their own,
    **coverage** the share of their own constituency's settlements they have ever
    named. Only MPs with a single-member constituency appear at all: a list MP has no
    denominator, so they are **absent, not zero** (TEL-9).

    Presented as a neutral fact and never a ranking of diligence (§1.2) — the
    frontend carries that caveat with the numbers, and the API says so here so a
    third-party consumer of the same rows reads them the same way.
    """
    _require_tables(db)
    scope = period_sql(period, "pss.period_number")
    where = scope or "1=1"
    floor = (RATIO_MIN_MENTIONS if sort != "mentions" else 0) \
        if min_mentions is None else min_mentions
    having = f"HAVING SUM(pss.mention_count) >= {int(floor)}" if floor else ""
    order = {
        # Focus and coverage are ratios, so they are computed in SQL over the stored
        # counts rather than stored themselves — a multi-cycle scope has to sum the
        # numerators and denominators before dividing, and a stored ratio could not.
        "focus": ("CAST(SUM(pss.own_mentions) AS REAL) / "
                  "NULLIF(SUM(pss.mention_count), 0) DESC"),
        "coverage": ("CAST(SUM(pss.own_named) AS REAL) / "
                     "NULLIF(MAX(pss.own_total), 0) DESC"),
        "mentions": "SUM(pss.mention_count) DESC",
    }[sort]
    total = db.execute(
        f"""SELECT COUNT(*) AS c FROM (
                SELECT pss.person_id FROM person_settlement_stats pss
                WHERE {where} GROUP BY pss.person_id {having})""").fetchone()["c"]
    rows = db.execute(
        f"""SELECT pss.person_id, p.label AS name, p.photo_file,
                   MAX(pss.constituency) AS constituency,
                   SUM(pss.mention_count) AS mentions,
                   SUM(pss.own_mentions) AS own_mentions,
                   MAX(pss.own_named) AS own_named,
                   MAX(pss.own_total) AS own_total,
                   f.label AS faction_label, f.color AS faction_color
            FROM person_settlement_stats pss
            JOIN person p ON p.person_id = pss.person_id
            LEFT JOIN membership ms ON ms.person_id = pss.person_id
                  AND ms.period_number = pss.period_number
            LEFT JOIN faction f ON f.id = ms.faction_id
            WHERE {where}
            GROUP BY pss.person_id
            {having}
            ORDER BY {order}, p.label COLLATE NOCASE
            LIMIT ? OFFSET ?""", (limit, offset)).fetchall()
    return {
        "total": total, "limit": limit, "offset": offset, "sort": sort,
        # Reported so the page can state the floor it is showing rather than implying
        # the list is everyone (TRUST-1).
        "min_mentions": int(floor),
        "representatives": [{
            "person_id": r["person_id"], "name": r["name"],
            "photo_file": r["photo_file"],
            "constituency": r["constituency"],
            "mentions": r["mentions"], "own_mentions": r["own_mentions"],
            "own_named": r["own_named"], "own_total": r["own_total"],
            "focus": (round(r["own_mentions"] / r["mentions"], 4)
                      if r["mentions"] else None),
            "coverage": (round(r["own_named"] / r["own_total"], 4)
                         if r["own_total"] else None),
            "faction": ({"label": r["faction_label"], "color": r["faction_color"]}
                        if r["faction_label"] else None),
        } for r in rows],
        # Restated on the wire so the caveat travels with the numbers (TEL-9/§1.2).
        "note": "focus = own-constituency share of settlement mentions; "
                "coverage = share of the constituency's settlements ever named. "
                "List MPs have no constituency and are omitted, not zeroed.",
    }


@router.get("/representative/{person_id}")
def representative_settlements(
    person_id: str,
    period: Optional[List[int]] = Query(None),
    db: sqlite3.Connection = Depends(get_db),
):
    """One representative's own-constituency measures and the places they name —
    the panel the profile shows (TEL-9/REP-3).

    ``own`` is **absent** rather than zeroed when the person holds no single-member
    constituency in scope (a list MP, a minister who is not an MP, an advocate): the
    measure does not apply to them, and reporting nought would be a claim about their
    conduct rather than about the data (TEL-9, REP-9's precedent).

    Declared before ``/{maz}/{taz}`` on purpose: both are two-segment paths, and the
    first declaration wins.
    """
    _require_tables(db)
    scope = period_sql(period, "pss.period_number")
    own = db.execute(
        f"""SELECT MAX(pss.constituency) AS constituency,
                   SUM(pss.mention_count) AS mentions,
                   SUM(pss.own_mentions) AS own_mentions,
                   MAX(pss.own_named) AS own_named,
                   MAX(pss.own_total) AS own_total
            FROM person_settlement_stats pss
            WHERE pss.person_id = ? {('AND ' + scope) if scope else ''}""",
        (person_id,)).fetchone()
    top = db.execute(
        f"""SELECT sss.settlement_id, s.name, s.county,
                   SUM(sss.mention_count) AS mentions,
                   EXISTS(SELECT 1 FROM settlement_constituency sc
                          WHERE sc.settlement_id = sss.settlement_id
                            AND sc.label = (SELECT MAX(constituency)
                                            FROM person_settlement_stats
                                            WHERE person_id = sss.person_id)) AS own
            FROM settlement_speaker_stats sss
            JOIN settlement s ON s.id = sss.settlement_id
            WHERE sss.person_id = ? AND {_stats_join(period, 'sss')[0]}
            GROUP BY sss.settlement_id
            ORDER BY mentions DESC, s.name COLLATE NOCASE LIMIT 20""",
        (person_id,)).fetchall()
    return {
        "person_id": person_id,
        "settlements": [{
            "id": r["settlement_id"], "name": r["name"], "county": r["county"],
            "mentions": r["mentions"], "own": bool(r["own"]),
        } for r in top],
        "own": ({
            "constituency": own["constituency"],
            "mentions": own["mentions"], "own_mentions": own["own_mentions"],
            "own_named": own["own_named"], "own_total": own["own_total"],
            "focus": (round(own["own_mentions"] / own["mentions"], 4)
                      if own["mentions"] else None),
            "coverage": (round(own["own_named"] / own["own_total"], 4)
                         if own["own_total"] else None),
        } if own and own["constituency"] else None),
    }


def _answer_period(db: sqlite3.Connection,
                   period: Optional[List[int]]) -> Optional[int]:
    """The cycle a "who represents this place" answer belongs to: the **latest cycle in
    the reader's scope**, or the latest cycle in the data when the scope is all cycles.

    A seat has one holder per cycle, so a multi-cycle scope has several answers and the
    page can show only one. The latest is the one a reader asking "who represents my
    town" means — a scope of *2018–2022, 2022–2026* asks about two terms, but the
    question is about now.
    """
    scope = period_list(period)
    if scope:
        return max(scope)
    row = db.execute("SELECT MAX(number) FROM electoral_period").fetchone()
    return row[0] if row and row[0] is not None else None


def _mp_for(db: sqlite3.Connection, labels: list[str],
            period: Optional[List[int]]) -> list[dict]:
    """The MP holding each of these constituencies **in one named cycle** — REP-10's
    join, made here so a settlement's page lands the reader on the person responsible
    for the place.

    The cycle is not a refinement here, it is what makes the answer exist at all. A
    single-member constituency has one holder at a time but several over the corpus:
    `person.constituency` is a point-in-time field recording the seat *a person held*,
    and 95 of the 138 seat labels in it belong to more than one person — *Fejér 4. OEVK*
    to five. Matching on the label alone therefore answers with whichever of them the
    table happened to return first, which is neither the current member nor a stable
    answer between two identical requests.

    So the cycle is resolved first (`_answer_period`: the **latest cycle in the reader's
    scope**), and every candidate must be shown to have actually sat in it — from the
    per-cycle mandate history (REP-14) where the DB has it, otherwise from the person's
    stored seat corroborated by a `membership` row for that cycle.

    A seat with **no holder recorded in that cycle is omitted**, and the page falls back
    to naming the constituency alone. The tempting alternative — answering from the
    nearest cycle that does have a holder — is worse than silence here, because the seat
    labels come from the register of the *current* map: walking back past a
    redistricting answers with a member of a differently drawn constituency that merely
    shares a name (on this corpus it reached cycle 39, three boundary sets ago). Every
    row therefore carries the `period_number` it is answering for, which the page shows,
    and an answer that is not for the cycle asked about is not given at all.
    """
    if not labels:
        return []
    target = _answer_period(db, period)
    # A seat can be spelled differently in the MP records than in the register (the
    # county rename behind `valasztas.COUNTY_ALIASES`), so the query asks for every
    # spelling and the answer is reported under the register's current one.
    canonical = {variant: label
                 for label in labels for variant in valasztas.label_variants(label)}
    keys = list(canonical)
    marks = ",".join("?" * len(keys))
    # The one cycle being answered for. A seat handed over mid-term has two rows in it,
    # so the later mandate wins and the name breaks any remaining tie — the answer has
    # to be the same on two identical requests.
    def during(column: str) -> str:
        return f"AND {column} = {int(target)}" if target is not None else ""
    rows = []
    if _has_rows(db, "person_mandate"):
        rows = db.execute(
            f"""SELECT pm.person_id, p.label AS name, pm.constituency,
                       pm.period_number, f.label AS faction_label,
                       f.color AS faction_color
                FROM person_mandate pm
                JOIN person p ON p.person_id = pm.person_id
                LEFT JOIN membership ms ON ms.person_id = pm.person_id
                      AND ms.period_number = pm.period_number
                LEFT JOIN faction f ON f.id = ms.faction_id
                WHERE pm.constituency IN ({marks})
                  {during('pm.period_number')}
                ORDER BY pm.date_start DESC, p.label COLLATE NOCASE""",
            keys).fetchall()
    # Then fill any seat the mandate history did not answer for from the person's stored
    # seat — the history is per cycle but not always complete (a cycle scraped before
    # REP-14, an MP whose election record upstream never published), and a settlement
    # whose member is missing from it must not read as unrepresented. `membership` is
    # what dates the stored seat: it says which cycles the person actually sat in, so it
    # is what turns "held this seat once" into "held it in this cycle".
    answered = {canonical[r["constituency"]] for r in rows}
    unanswered = [variant for label in labels if label not in answered
                  for variant in valasztas.label_variants(label)]
    if unanswered:
        marks = ",".join("?" * len(unanswered))
        rows = list(rows) + db.execute(
            f"""SELECT p.person_id, p.label AS name, p.constituency,
                       ms.period_number AS period_number,
                       f.label AS faction_label, f.color AS faction_color
                FROM person p
                JOIN membership ms ON ms.person_id = p.person_id
                                  AND ms.period_number IS NOT NULL
                                  {during('ms.period_number')}
                LEFT JOIN faction f ON f.id = ms.faction_id
                WHERE p.constituency IN ({marks})
                GROUP BY p.person_id, p.constituency
                ORDER BY p.label COLLATE NOCASE""", unanswered).fetchall()
    seen: set[str] = set()
    out = []
    for r in rows:
        label = canonical[r["constituency"]]
        if label in seen:
            continue
        seen.add(label)
        out.append({
            "person_id": r["person_id"], "name": r["name"],
            # Always the cycle asked about — the page states it, so "who represents this
            # place" is never an undated claim.
            "constituency": label, "period_number": r["period_number"],
            "faction": ({"label": r["faction_label"], "color": r["faction_color"]}
                        if r["faction_label"] else None),
        })
    return out


@router.get("/{maz}/{taz}")
def settlement_detail(
    maz: str = Path(pattern=r"^\d{2}$"), taz: str = Path(pattern=r"^\d{3}$"),
    period: Optional[List[int]] = Query(None),
    db: sqlite3.Connection = Depends(get_db),
):
    """One settlement's page (TEL-8): how often it was named, when, by whom, which
    constituency it is in and who holds it.

    A settlement with **no** mentions is a 200, not a 404: it exists, it is simply
    never named in scope, and that page is the blind spot — deep-linkable so it can
    be cited (TEL-8).
    """
    _require_tables(db)
    sid = f"{maz}/{taz}"
    row = db.execute(
        "SELECT id, name, name_en, county, lat, lon, electorate, ambiguity, "
        "ambiguity_reason FROM settlement WHERE id = ?", (sid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Settlement not found")

    scope = _stats_join(period)[0]
    agg = db.execute(
        f"""SELECT COALESCE(SUM(mention_count), 0) AS mentions,
                   COALESCE(SUM(speech_count), 0) AS speeches,
                   COALESCE(SUM(session_count), 0) AS sessions,
                   MIN(first_date) AS first_date, MAX(last_date) AS last_date
            FROM settlement_stats st
            WHERE st.settlement_id = ? AND {scope}""", (sid,)).fetchone()
    # Distinct speakers cannot be summed across cycles (the same MP recurs), so it is
    # derived over the scope instead — CYC-4's rule for counts that would double-count.
    speakers = db.execute(
        f"""SELECT COUNT(DISTINCT person_id) AS c FROM settlement_mention
            WHERE settlement_id = ? {period_and(period, 'period_number')}""",
        (sid,)).fetchone()["c"]
    top = db.execute(
        f"""SELECT sss.person_id, p.label AS name, p.photo_file,
                   SUM(sss.mention_count) AS mentions,
                   f.label AS faction_label, f.color AS faction_color
            FROM settlement_speaker_stats sss
            JOIN person p ON p.person_id = sss.person_id
            LEFT JOIN membership ms ON ms.person_id = sss.person_id
                  AND ms.period_number = sss.period_number
            LEFT JOIN faction f ON f.id = ms.faction_id
            WHERE sss.settlement_id = ?
              AND {_stats_join(period, 'sss')[0]}
            GROUP BY sss.person_id
            ORDER BY mentions DESC, p.label COLLATE NOCASE LIMIT 10""",
        (sid,)).fetchall()
    labels = [r["label"] for r in db.execute(
        "SELECT label FROM settlement_constituency WHERE settlement_id = ? "
        "ORDER BY number", (sid,))]

    return {
        "settlement": {
            "id": row["id"], "name": row["name"], "name_en": row["name_en"],
            "county": row["county"], "lat": row["lat"], "lon": row["lon"],
            "electorate": row["electorate"],
            # Why this name needed corroboration to count (TEL-3): the page shows it
            # so a low number on a homonym village is explained, not mysterious.
            "ambiguity": row["ambiguity"], "ambiguity_reason": row["ambiguity_reason"],
        },
        "mentions": agg["mentions"], "speeches": agg["speeches"],
        "sessions": agg["sessions"], "speakers": speakers,
        "first_date": agg["first_date"], "last_date": agg["last_date"],
        "top_speakers": [{
            "person_id": r["person_id"], "name": r["name"],
            "photo_file": r["photo_file"], "mentions": r["mentions"],
            "faction": ({"label": r["faction_label"], "color": r["faction_color"]}
                        if r["faction_label"] else None),
        } for r in top],
        "constituencies": labels,
        "representatives": _mp_for(db, labels, period),
        "source": _source(db),
    }


@router.get("/{maz}/{taz}/trend")
def settlement_trend(
    maz: str = Path(pattern=r"^\d{2}$"), taz: str = Path(pattern=r"^\d{3}$"),
    period: Optional[List[int]] = Query(None),
    db: sqlite3.Connection = Depends(get_db),
):
    """Mentions of this settlement per calendar year (TEL-8) — shaped as the search
    trend's ``{period, hits}`` buckets so the site's one histogram component renders
    it unchanged (SEA-8), with quiet years as zero rather than gaps."""
    _require_tables(db)
    sid = f"{maz}/{taz}"

    def compute():
        rows = db.execute(
            f"""SELECT substr(ss.date, 1, 4) AS y, COUNT(*) AS c
                FROM settlement_mention sm
                JOIN session ss ON ss.id = sm.session_id
                WHERE sm.settlement_id = ?
                  {period_and(period, 'sm.period_number')}
                GROUP BY y ORDER BY y""", (sid,)).fetchall()
        buckets = {r["y"]: r["c"] for r in rows if r["y"]}
        if not buckets:
            return {"buckets": [], "granularity": "year"}
        years = range(int(min(buckets)), int(max(buckets)) + 1)
        return {"granularity": "year",
                "buckets": [{"period": str(y), "hits": buckets.get(str(y), 0)}
                            for y in years]}

    return cached_aggregate("settlement_trend", (sid, period_key(period)), compute)


@router.get("/{maz}/{taz}/mentions")
def settlement_mentions(
    maz: str = Path(pattern=r"^\d{2}$"), taz: str = Path(pattern=r"^\d{3}$"),
    period: Optional[List[int]] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
):
    """The sentences that produced this settlement's count, newest first (TEL-4).

    This is what makes the number evidence rather than an assertion: each row carries
    the sentence text, its speaker and faction, the sitting date, and the speech uid
    the viewer deep-links by (VIE-5) — so a reader can watch the moment the place was
    named. The matched surface form and its offsets come along so the client can
    highlight exactly what matched, as the search results do (SEA-4).
    """
    _require_tables(db)
    sid = f"{maz}/{taz}"
    scope = period_and(period, "sm.period_number")
    total = db.execute(
        f"SELECT COUNT(*) AS c FROM settlement_mention sm "
        f"WHERE sm.settlement_id = ? {scope}", (sid,)).fetchone()["c"]
    rows = db.execute(
        f"""SELECT sm.sentence_id, sm.speech_uid, sm.session_id, sm.surface,
                   sm.char_start, sm.char_end, sm.form,
                   se.text, se.time_start,
                   ss.date, sp.person_id, sp.speaker_label,
                   p.label AS person_name, ai.title AS agenda_title,
                   f.label AS faction_label, f.color AS faction_color
            FROM settlement_mention sm
            JOIN sentence se ON se.id = sm.sentence_id
            JOIN speech sp ON sp.uid = sm.speech_uid
            JOIN session ss ON ss.id = sm.session_id
            LEFT JOIN person p ON p.person_id = sp.person_id
            LEFT JOIN agenda_item ai ON ai.id = sp.agenda_item_id
            LEFT JOIN faction f ON f.id = sp.faction_id
            WHERE sm.settlement_id = ? {scope}
            ORDER BY ss.date DESC, sm.sentence_id DESC
            LIMIT ? OFFSET ?""", (sid, limit, offset)).fetchall()
    return {
        "total": total, "limit": limit, "offset": offset,
        "mentions": [{
            "sentence_id": r["sentence_id"], "speech_uid": r["speech_uid"],
            "session_id": r["session_id"], "date": r["date"],
            "text": r["text"], "time_start": r["time_start"],
            "surface": r["surface"], "char_start": r["char_start"],
            "char_end": r["char_end"], "form": r["form"],
            "person_id": r["person_id"],
            "name": r["person_name"] or r["speaker_label"],
            "agenda_title": r["agenda_title"],
            "faction": ({"label": r["faction_label"], "color": r["faction_color"]}
                        if r["faction_label"] else None),
        } for r in rows],
    }
