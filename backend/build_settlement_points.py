"""Rebuild the settlement **label gazetteer** — ``app/settlement_points.csv`` (§6D TEL-5).

**Why this file exists.** The election office publishes one point per settlement
(``centrum`` in ``Telep-Topo-<maz>.json``), and the settlement map was drawn from it.
That point is a centre of the *administrative territory*, not of the place: measured
against the 24 largest towns it sits a **mean 3.1 km** (max 7.6 km) from where the
basemap prints the town's name — Veszprém 6.5 km out, Szeged 5.6 km, Salgótarján
7.6 km. On a map whose whole job is "find my town", a dot that lands in the fields
next to the label reads as a bug, because it is one.

What a reader compares the dot against is the **OpenStreetMap place node**: the
basemap's label is drawn *at* that node, so taking the node as the settlement's point
makes the two agree by construction rather than by luck. This script matches the two
registers and writes the result as a checked-in table — the §6C/MIN-3 pattern again: a
reviewed table beats a runtime guess, and it keeps the loader free of a second network
source (a rebuild must work offline, SCR-6).

**The match needs no shared key and no name matching**, which is what makes it
trustworthy: every settlement's own boundary is already published (the polygons the
split-settlement lookup draws), so each place node is assigned to the settlement whose
polygon *contains* it. Hungary's 3 177 settlements take 3 180 place nodes with none
left over: 3 176 hold their own, and Kompolt — whose node sits a few hundred metres
inside Kál's boundary — is the single one that needs the name fallback. Names are
otherwise only a tie-break (a node inside Kecskemét's territory named Hetényegyháza is
not Kecskemét) and a check: outside Budapest every matched pair agrees on the name,
and the capital's districts differ only in numbering ("Budapest 05. kerület" against
OSM's "V. kerület").

**Licence.** The coordinates are OpenStreetMap's, ODbL. The site already carries
OpenStreetMap's attribution on every map it draws (``PARLAMONITOR_MAP_TILE_ATTRIBUTION``),
which is where these points are shown.

Run from backend/::

    .venv/bin/python build_settlement_points.py            # fetches Overpass (~2.5 MB)
    .venv/bin/python build_settlement_points.py --osm places.json   # reuse a dump

The VTR side comes from the ordinary disk cache, so only Overpass is hit — and only
when the dump is not already on disk. Re-run it after an election renumbers the
register (the ids are the office's own), or to pick up OSM corrections.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import urllib.request
from pathlib import Path

from app import settlements, valasztas

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("build_settlement_points")

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# The four place kinds that can *be* a settlement of the register: `city`/`town`/
# `village` for the 3 154 municipalities and `borough` for Budapest's 23 districts.
# `hamlet` and `suburb` are deliberately excluded — they are parts of a settlement,
# never one of their own, and including them only adds rival nodes inside the
# polygons that already have their answer.
PLACE_RANK = {"city": 4, "town": 3, "village": 2, "borough": 1}

OVERPASS_QUERY = """
[out:json][timeout:300];
area["ISO3166-1"="HU"][admin_level=2]->.hu;
node(area.hu)["place"~"^(city|town|village|borough)$"]["name"];
out body;
"""

# A node matched to a settlement by name alone (its polygon holds no node at all)
# has to be near that settlement to be believed: the register and OSM can disagree
# about a boundary by a field's width, never by a county.
NAME_FALLBACK_KM = 10.0


# ---------------------------------------------------------------------------
# Geometry (build-time only — the site itself never does point-in-polygon)
# ---------------------------------------------------------------------------

def bbox(ring: list[list[float]]) -> tuple[float, float, float, float]:
    """``(min_lon, min_lat, max_lon, max_lat)`` — the cheap test that spares the
    3 177 × 3 180 ray casts."""
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return min(lons), min(lats), max(lons), max(lats)


def contains(ring: list[list[float]], lon: float, lat: float) -> bool:
    """Even-odd ray casting over a GeoJSON ring (``[lon, lat]`` pairs)."""
    inside = False
    j = len(ring) - 1
    for i, (xi, yi) in enumerate(ring):
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def km_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Kilometres between two ``(lat, lon)`` points — equirectangular, which over a
    single settlement is exact enough to sanity-check a match."""
    return math.hypot((a[0] - b[0]) * 111.32,
                      (a[1] - b[1]) * 111.32 * math.cos(math.radians(a[0])))


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def osm_places(dump: Path | None) -> list[dict]:
    """Hungary's place nodes, from a saved dump when there is one, else Overpass
    (and then saved, so a re-run costs nothing)."""
    if dump and dump.exists():
        logger.info("Reading OSM place nodes from %s", dump)
        payload = json.loads(dump.read_bytes())
    else:
        logger.info("Querying Overpass for Hungary's place nodes…")
        request = urllib.request.Request(
            OVERPASS_URL, data=OVERPASS_QUERY.encode(),
            headers={"User-Agent": "parlamonitor build_settlement_points",
                     "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(request, timeout=600) as response:
            raw = response.read()
        payload = json.loads(raw)
        if dump:
            dump.write_bytes(raw)
            logger.info("Saved the Overpass dump to %s", dump)
    nodes = [e for e in payload.get("elements", [])
             if e.get("type") == "node" and e.get("tags", {}).get("place") in PLACE_RANK]
    logger.info("%d place nodes", len(nodes))
    return nodes


def register_geometry() -> tuple[list[dict], dict[str, list[list[float]]]]:
    """``(settlements, rings)`` — the office's register rows (id, name and its own
    territorial centre) and every settlement's boundary, keyed by the same id."""
    data = valasztas.register()
    rings: dict[str, list[list[float]]] = {}
    for maz in sorted({s["id"][:2] for s in data["settlements"]}):
        for taz, ring in valasztas.outlines(maz, version=data["version"]).items():
            if ring:
                rings[f"{maz}/{taz}"] = ring
    logger.info("%d settlements in the register, %d with a boundary",
                len(data["settlements"]), len(rings))
    return data["settlements"], rings


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def assign(nodes: list[dict], rings: dict[str, list[list[float]]]
           ) -> tuple[dict[str, list[dict]], list[dict]]:
    """Every place node dropped into the settlement whose boundary holds it.
    Returns ``(by_settlement, orphans)``."""
    boxes = {sid: bbox(ring) for sid, ring in rings.items()}
    held: dict[str, list[dict]] = {}
    orphans: list[dict] = []
    for node in nodes:
        lat, lon = node["lat"], node["lon"]
        for sid, (x0, y0, x1, y1) in boxes.items():
            if x0 <= lon <= x1 and y0 <= lat <= y1 and contains(rings[sid], lon, lat):
                held.setdefault(sid, []).append(node)
                break
        else:
            orphans.append(node)
    return held, orphans


def pick(name: str, candidates: list[dict]) -> dict:
    """The node that *is* this settlement, out of the ones inside its territory.

    A settlement's own name wins outright — a village that shares its host's
    territory (Hetényegyháza in Kecskemét's) must never take its point, and the
    capital's own node sits inside the 1st district. Failing a name, the ranking
    picks the settlement-scale node over the part-of-a-settlement one.
    """
    folded = settlements.fold(name)
    named = [c for c in candidates if settlements.fold(c["tags"].get("name")) == folded]
    if named:
        candidates = named
    elif name.endswith("kerület"):
        # "Budapest 05. kerület" is OSM's "V. kerület": the numbering differs, the
        # kind does not.
        boroughs = [c for c in candidates if c["tags"]["place"] == "borough"]
        candidates = boroughs or candidates
    return max(candidates, key=lambda c: PLACE_RANK[c["tags"]["place"]])


def build_rows(register: list[dict], rings: dict[str, list[list[float]]],
               nodes: list[dict]) -> tuple[list[dict], list[dict]]:
    """One row per settlement that could be placed, plus the ones that could not."""
    held, orphans = assign(nodes, rings)
    logger.info("%d settlements hold a place node, %d nodes fell outside every "
                "boundary", len(held), len(orphans))

    rows, unplaced = [], []
    for entry in register:
        candidates = held.get(entry["id"])
        if candidates:
            rows.append(_row(entry, pick(entry["name"], candidates)))
        else:
            unplaced.append(entry)

    # A settlement whose territory holds no node of its own is not necessarily
    # nodeless: Kompolt's sits a few hundred metres inside Kál's boundary, so the
    # leftovers — nodes no settlement claimed, and nodes a settlement passed over —
    # are searched by name before giving up on it.
    taken = {r["osm_node"] for r in rows}
    spare = [node for node in orphans + [n for ns in held.values() for n in ns]
             if node["id"] not in taken]
    missing = []
    for entry in unplaced:
        node = _by_name(entry["name"], spare, entry)
        if node:
            rows.append(_row(entry, node))
        else:
            missing.append(entry)

    # The capital as its own entity (TEL-5) is not in the office's register, so it is
    # not in the loop above: its point is OSM's Budapest node, the one every basemap
    # labels the city at.
    capital = next((n for n in nodes if n["tags"]["place"] == "city"
                    and n["tags"].get("name") == settlements.BUDAPEST_NAME), None)
    if capital:
        rows.append(_row({"id": settlements.BUDAPEST_ID,
                          "name": settlements.BUDAPEST_NAME}, capital))
    rows.sort(key=lambda r: r["id"])
    return rows, missing


def _row(entry: dict, node: dict) -> dict:
    return {"id": entry["id"], "name": entry["name"],
            "lat": round(node["lat"], 5), "lon": round(node["lon"], 5),
            "osm_node": node["id"], "place": node["tags"]["place"]}


def _by_name(name: str, spare: list[dict], entry: dict) -> dict | None:
    """Last resort for a settlement whose polygon holds no node: the node of that
    name, if it is near enough that the two registers are plainly talking about the
    same place. (Kompolt's node sits a few hundred metres inside Kál's boundary.)"""
    folded = settlements.fold(name)
    for node in spare:
        if settlements.fold(node["tags"].get("name")) != folded:
            continue
        centre = _centre(entry)
        if centre and km_between((node["lat"], node["lon"]), centre) > NAME_FALLBACK_KM:
            continue
        logger.info("%s: no node inside its boundary; matched %s by name",
                    name, node["id"])
        return node
    return None


def _centre(entry: dict) -> tuple[float, float] | None:
    """The register's own point for a settlement, when it published one."""
    if entry.get("lat") is None or entry.get("lon") is None:
        return None
    return entry["lat"], entry["lon"]


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

HEADER = """\
# Settlement label points — where the map draws a settlement's dot (§6D TEL-5).
#
# Generated by build_settlement_points.py: OpenStreetMap place nodes (ODbL) assigned
# to the election office's settlement boundaries by point-in-polygon. The basemap
# prints each settlement's name at this exact point, so the dot and the label agree.
# `id` is the office's own "<maz>/<taz>"; `name` is its spelling, and the loader
# falls back to it when an election renumbers the ids. Do not hand-edit: re-run the
# script.
"""


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(HEADER)
        writer = csv.DictWriter(
            handle, fieldnames=["id", "name", "lat", "lon", "osm_node", "place"])
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Wrote %d rows to %s", len(rows), path)


def report(rows: list[dict], register: list[dict]) -> None:
    """How far the new points moved the map — the measure the file exists for."""
    centres = {s["id"]: _centre(s) for s in register if _centre(s)}
    moved = sorted(km_between((r["lat"], r["lon"]), centres[r["id"]])
                   for r in rows if r["id"] in centres)
    if not moved:
        return
    logger.info("Displacement from the register's territorial centre: median %.2f km, "
                "p90 %.2f km, max %.2f km", moved[len(moved) // 2],
                moved[int(0.9 * len(moved))], moved[-1])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--out", type=Path,
                        default=Path(__file__).parent / "app" / "settlement_points.csv")
    parser.add_argument("--osm", type=Path, default=Path("osm-places-hu.json"),
                        help="Overpass dump to read (and to save a fresh query to)")
    args = parser.parse_args()

    register, rings = register_geometry()
    rows, missing = build_rows(register, rings, osm_places(args.osm))
    for entry in missing:
        logger.warning("No place node for %s (%s) — it keeps the register's "
                       "territorial centre", entry["name"], entry["id"])
    report(rows, register)
    write_csv(args.out, rows)


if __name__ == "__main__":
    main()
