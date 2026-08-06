"""Constituency geography from the National Election Office — the "which
constituency am I in?" lookup (REP-10).

`parlament.hu` tells us *which* OEVK an MP was elected in, but never *where* that
constituency is: no boundaries, and no mapping from a place to its constituency.
That mapping is published by the **Nemzeti Választási Iroda** on its election-night
result site (`vtr.valasztas.hu`), as a handful of static JSON files:

- ``config.json``          — the current data **version** directory (``ver``); every
  other file lives under it, so the version is resolved first and never hard-coded.
- ``Telepulesek.json``     — every settlement, with the constituency number(s)
  (``evk_lst``) it belongs to. For all but 23 settlements that list has exactly
  one entry, which *is* the answer.
- ``OevkAdatok.json``      — the 106 constituencies with their official names and seats.
- ``OevkPoligonok.json``   — each constituency's boundary polygon.
- ``<maz>/Telep-Topo-<maz>.json`` — each settlement's own boundary, per county.
  Only fetched for the counties that actually hold a split settlement.

A settlement that spans **several** constituencies (the 15 split Budapest districts
and 8 large cities) cannot be resolved from the place name alone, so the boundaries
are what let the reader pick their own: drawn over a street map at the settlement's
zoom level, the constituency polygons partition it, and the reader clicks the part
they live in. The boundaries are handed to the frontend as ordinary **GeoJSON**, so
the map is a plain Leaflet layer with no bespoke geometry format in between.

Fetching is **lazy, disk-cached and version-scoped**: the search index (settlements
+ constituency names, ~1 MB) and the geometry (polygons, only for a split
settlement's county) are separate stages, so no single request pulls everything.
A cached copy that is merely *stale* is preferred to failing — the electoral map
does not change between elections — and a total failure surfaces as an error the
UI can show, never as a wrong or empty answer (cf. SCR-5).

This module is read-only and self-contained: it never touches the database (the
EVK → MP join lives in the representatives router) and nothing here is required
for the rest of the site to work (OPS-4: disable with
``PARLAMONITOR_EVK_LOOKUP=0``).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from .config import settings

logger = logging.getLogger(__name__)

# County code (`maz`) 01 is the capital; the rest are counties. `OevkAdatok`
# spells the name out ("Baranya vármegye", "Budapest főváros") while
# parlament.hu's constituency labels use the bare name ("Baranya 4. OEVK"), so
# the suffix is stripped to get back to the form the MP records use.
_COUNTY_SUFFIXES = (" vármegye", " megye", " főváros")

# "Budapest 05. kerület" — the districts are the settlements most people will
# search for, and they are as often written "V. kerület" or "5. kerület" as with
# the zero-padded arabic numeral the source uses.
_DISTRICT_RE = re.compile(r"^Budapest (\d{1,2})\. kerület$")
_ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI",
          "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX",
          "XXI", "XXII", "XXIII")


class LookupUnavailable(RuntimeError):
    """The upstream constituency data could not be obtained and no cached copy
    exists. The endpoint turns this into a 503 so the UI can say so plainly."""


def fold(s: str | None) -> str:
    """Accent- and case-folded form, matching the site-wide rule (FOLD-1/§4B) so
    ``pecs`` finds *Pécs* and ``V. kerulet`` finds *Budapest 05. kerület*."""
    decomposed = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def county_name(maz_nev: str | None) -> str:
    """The bare county/capital name behind an upstream ``maz_nev``."""
    name = (maz_nev or "").strip()
    for suffix in _COUNTY_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def constituency_label(county: str, number: int) -> str:
    """The constituency in the form **parlament.hu's MP records use** — e.g.
    ``"Budapest 12. OEVK"`` — which is what the EVK → MP join matches on."""
    return f"{county} {number}. OEVK"


# ---------------------------------------------------------------------------
# Fetching + caching
# ---------------------------------------------------------------------------

def _default_fetch(url: str) -> bytes:
    """Fetch one upstream JSON file. Stdlib only: this runs on the request path,
    and the lookup must not add a dependency of its own (SCR-6 in spirit)."""
    req = urllib.request.Request(
        url, headers={"User-Agent": settings.vtr_user_agent,
                      "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=settings.vtr_timeout) as resp:
        return resp.read()


def _cache_root() -> Path:
    configured = settings.vtr_cache_dir
    if configured:
        return Path(configured)
    # Beside the database by default, which on the standard deploy is a mounted
    # volume — so the cache survives a container restart.
    return Path(settings.db_path).resolve().parent / "vtr-cache"


def _cache_file(version: str, name: str) -> Path:
    # `name` may carry a county sub-directory ("01/Telep-Topo-01.json"); flatten
    # it so the cache is one directory per data version.
    return _cache_root() / version / name.replace("/", "__")


def _read_cache(path: Path) -> tuple[dict | None, bool]:
    """``(payload, fresh)``. A payload older than the TTL is still returned, with
    ``fresh=False``, so a failed refetch can fall back to it."""
    try:
        raw = path.read_bytes()
        age = time.time() - path.stat().st_mtime
    except OSError:
        return None, False
    try:
        payload = json.loads(raw)
    except ValueError:
        logger.warning("Unreadable VTR cache entry %s; refetching", path)
        return None, False
    return payload, age < settings.vtr_cache_ttl


def _write_cache(path: Path, payload: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a concurrent reader never sees a partial file. The
        # temp name carries the writer's identity because the cache directory is
        # shared: several uvicorn workers — and, during a blue/green deploy, two
        # serving containers — can miss a cold cache at once and would otherwise
        # interleave writes into one temp path.
        tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}-{threading.get_ident()}.tmp")
        try:
            tmp.write_bytes(payload)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)   # only survives if the rename failed
    except OSError as exc:
        logger.warning("Could not cache VTR file %s (%s); will refetch", path, exc)


def _get_json(name: str, version: str, *, fetch: Callable[[str], bytes] | None = None) -> dict:
    """One upstream file, from the version-scoped disk cache when fresh, else
    fetched (and cached). A stale cache entry rescues a failed fetch."""
    path = _cache_file(version, name)
    cached, fresh = _read_cache(path)
    if cached is not None and fresh:
        return cached

    url = f"{settings.vtr_base_url.rstrip('/')}/{version}/ver/{name}"
    try:
        raw = (fetch or _default_fetch)(url)
        payload = json.loads(raw)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        if cached is not None:
            # The electoral map is static between elections, so serving a stale
            # copy is right — and far better than a broken page.
            logger.warning("VTR fetch of %s failed (%s); serving the cached copy", url, exc)
            return cached
        raise LookupUnavailable(f"could not fetch {url}: {exc}") from exc
    _write_cache(path, raw if isinstance(raw, bytes) else json.dumps(payload).encode())
    return payload


def _get_version(*, fetch: Callable[[str], bytes] | None = None) -> str:
    """The current data-version directory, from ``config.json``.

    Everything else hangs off it, so it is never hard-coded (the office publishes a
    new version whenever the data is revised). Cached like any other file, and a
    stale copy is used when the fetch fails.
    """
    path = _cache_root() / "config.json"
    cached, fresh = _read_cache(path)
    if cached is not None and fresh and cached.get("ver"):
        return str(cached["ver"])

    url = f"{settings.vtr_base_url.rstrip('/')}/config.json"
    try:
        raw = (fetch or _default_fetch)(url)
        payload = json.loads(raw)
        version = str(payload["ver"])
    except (urllib.error.URLError, OSError, ValueError, KeyError, TimeoutError) as exc:
        if cached is not None and cached.get("ver"):
            logger.warning("VTR config fetch failed (%s); keeping version %s",
                           exc, cached["ver"])
            return str(cached["ver"])
        raise LookupUnavailable(f"could not resolve the VTR data version: {exc}") from exc
    _write_cache(path, raw if isinstance(raw, bytes) else json.dumps(payload).encode())
    return version


# ---------------------------------------------------------------------------
# In-process memoization
#
# The parsed indexes are a few MB and shared by every request; re-parsing them per
# request would dominate the endpoint's cost. Entries expire on the same TTL as the
# disk cache, so a refreshed upstream version is picked up without a restart.
# ---------------------------------------------------------------------------

_mem: dict[tuple, tuple[float, object]] = {}
_mem_lock = threading.Lock()


def _memoized(key: tuple, build: Callable[[], object]) -> object:
    now = time.monotonic()
    with _mem_lock:
        hit = _mem.get(key)
        if hit is not None and hit[0] > now:
            return hit[1]
    value = build()          # built outside the lock: it may do network I/O
    with _mem_lock:
        _mem[key] = (now + settings.vtr_cache_ttl, value)
    return value


def reset_cache() -> None:
    """Drop the in-process memo (tests; and a way to force a re-read)."""
    with _mem_lock:
        _mem.clear()


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _ring(spec: str) -> list[list[float]]:
    """Parse an upstream ``"lat lon,lat lon,…"`` polygon into a **GeoJSON linear
    ring**: ``[[lon, lat], …]``, closed, wound counter-clockwise.

    Three conversions, all required by RFC 7946 and none done upstream: the
    coordinate order is reversed (GeoJSON is longitude-first, the source is
    latitude-first), the ring is closed (the source leaves the last point dangling),
    and an exterior ring is oriented counter-clockwise. Malformed points are skipped
    rather than failing the whole lookup.
    """
    out: list[list[float]] = []
    for point in (spec or "").split(","):
        parts = point.split()
        if len(parts) != 2:
            continue
        try:
            lat, lon = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        out.append([lon, lat])
    if len(out) < 3:
        return []
    if _signed_area(out) < 0:
        out.reverse()
    if out[0] != out[-1]:
        out.append(list(out[0]))
    return out


def _signed_area(ring: list[list[float]]) -> float:
    """Twice the signed shoelace area — positive when the ring is counter-clockwise."""
    total = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % len(ring)]
        total += x1 * y2 - x2 * y1
    return total


def _polygon(ring: list[list[float]]) -> dict | None:
    """A GeoJSON ``Polygon`` geometry around one exterior ring."""
    return {"type": "Polygon", "coordinates": [ring]} if ring else None


def _bbox(ring: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _contains(ring: list[list[float]], x: float, y: float) -> bool:
    """Even-odd point-in-polygon test."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


# How finely the settlement is sampled when choosing where to put a
# constituency's label on the map. 48×32 is ~1500 tests per constituency —
# microseconds, and fine enough that the label lands inside its own patch.
_LABEL_GRID = (48, 32)


def _label_point(settlement: list[list[float]],
                 evk_ring: list[list[float]]) -> list[float] | None:
    """A point to anchor a constituency's on-map label: the centre of the part of
    the settlement that lies in this constituency.

    The constituency polygon is far larger than the settlement, so its own centroid
    is usually off-screen; and a concave settlement's centroid can fall outside it.
    Sampling the overlap avoids both.
    """
    x0, y0, x1, y1 = _bbox(settlement)
    cols, rows = _LABEL_GRID
    sx = sy = 0.0
    hits = 0
    for r in range(rows):
        y = y0 + (r + 0.5) * (y1 - y0) / rows
        for c in range(cols):
            x = x0 + (c + 0.5) * (x1 - x0) / cols
            if _contains(settlement, x, y) and _contains(evk_ring, x, y):
                sx += x
                sy += y
                hits += 1
    if not hits:
        return None
    return [round(sx / hits, 5), round(sy / hits, 5)]


# ---------------------------------------------------------------------------
# The settlement / constituency index
# ---------------------------------------------------------------------------

def _search_keys(name: str, district_no: int | None) -> list[str]:
    """Folded strings a settlement can be found by. The name itself always; for a
    Budapest district also its un-padded arabic and roman spellings, since that is
    how people write them."""
    keys = [fold(name)]
    if district_no and 1 <= district_no <= len(_ROMAN):
        roman = _ROMAN[district_no - 1]
        keys += [fold(f"Budapest {district_no}. kerület"),
                 fold(f"Budapest {roman}. kerület"),
                 fold(f"{district_no}. kerület"),
                 fold(f"{roman}. kerület")]
    # De-duplicate, keeping order (the full name stays first, so it wins ranking).
    seen: set[str] = set()
    return [k for k in keys if not (k in seen or seen.add(k))]


def _build_index(version: str, *, fetch: Callable[[str], bytes] | None = None) -> dict:
    """Settlements + constituencies, without any geometry.

    Enough to search for a place and, for the ~99 % of settlements that sit in a
    single constituency, to answer outright.
    """
    towns = _get_json("Telepulesek.json", version, fetch=fetch)
    districts = _get_json("OevkAdatok.json", version, fetch=fetch)

    constituencies: dict[str, dict] = {}
    for rec in districts.get("list") or []:
        maz, evk = rec.get("maz"), rec.get("evk")
        if not maz or not evk:
            continue
        county = county_name(rec.get("maz_nev"))
        number = int(evk)
        constituencies[f"{maz}/{evk}"] = {
            "maz": maz, "evk": evk, "number": number, "county": county,
            "label": constituency_label(county, number),
            "official_name": rec.get("evk_nev"),
            "official_name_en": rec.get("evk_nev_en"),
            "seat": rec.get("szekhely"),
            "electorate": (rec.get("letszam") or {}).get("osszesen"),
        }

    settlements = []
    for rec in towns.get("list") or []:
        info = rec.get("leiro") or {}
        maz, taz = info.get("maz"), info.get("taz")
        name = (info.get("megnev") or "").strip()
        if not (maz and taz and name):
            continue
        evks = [e for e in (info.get("evk_lst") or []) if f"{maz}/{e}" in constituencies]
        district = _DISTRICT_RE.match(name)
        settlements.append({
            "maz": maz, "taz": taz, "name": name,
            "name_en": (info.get("megnev_en") or "").strip() or None,
            "county": constituencies[f"{maz}/{evks[0]}"]["county"] if evks else None,
            "evks": evks,
            "electorate": (rec.get("letszam") or {}).get("osszesen"),
            "_keys": _search_keys(name, int(district.group(1)) if district else None),
        })

    header = towns.get("PvOnHeader") or {}
    logger.info("VTR index (version %s): %d settlements, %d constituencies",
                version, len(settlements), len(constituencies))
    return {
        "version": version,
        "header": header,
        "settlements": settlements,
        "by_id": {f"{s['maz']}/{s['taz']}": s for s in settlements},
        "constituencies": constituencies,
    }


def index(*, fetch: Callable[[str], bytes] | None = None) -> dict:
    """The memoized settlement/constituency index. Raises `LookupUnavailable`
    when the data cannot be obtained at all."""
    if not settings.evk_lookup:
        raise LookupUnavailable("the constituency lookup is disabled "
                                "(PARLAMONITOR_EVK_LOOKUP=0)")
    version = _get_version(fetch=fetch)
    return _memoized(("index", settings.vtr_base_url, version),
                     lambda: _build_index(version, fetch=fetch))


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_settlements(query: str, limit: int = 25, *,
                       fetch: Callable[[str], bytes] | None = None) -> tuple[list[dict], int]:
    """Settlements matching ``query``, accent- and case-insensitively (FOLD-1).

    Ranked so that the obvious answer is first: an exact name match, then a
    prefix match, then a contains match; within a rank the larger electorate
    first, because a bare "szent" should surface towns before hamlets.
    """
    data = index(fetch=fetch)
    needle = fold((query or "").strip())
    if not needle:
        return [], 0

    scored: list[tuple[int, int, str, dict]] = []
    for s in data["settlements"]:
        best = None
        for key in s["_keys"]:
            if key == needle:
                rank = 0
            elif key.startswith(needle):
                rank = 1
            elif needle in key:
                rank = 2
            else:
                continue
            best = rank if best is None else min(best, rank)
        if best is not None:
            scored.append((best, -(s["electorate"] or 0), fold(s["name"]), s))

    scored.sort(key=lambda t: t[:3])
    return [_settlement_summary(s, data) for *_, s in scored[:limit]], len(scored)


def _settlement_summary(s: dict, data: dict) -> dict:
    """The list-row shape: enough to show and pick a settlement, no geometry."""
    return {
        "maz": s["maz"], "taz": s["taz"], "name": s["name"],
        "name_en": s["name_en"], "county": s["county"],
        "electorate": s["electorate"],
        "constituency_count": len(s["evks"]),
        # A single-constituency settlement can be labelled straight from the list.
        "constituency": (data["constituencies"][f"{s['maz']}/{s['evks'][0]}"]["label"]
                         if len(s["evks"]) == 1 else None),
    }


# ---------------------------------------------------------------------------
# One settlement, with the geometry needed to disambiguate it
# ---------------------------------------------------------------------------

def _outlines(maz: str, version: str, *,
              fetch: Callable[[str], bytes] | None = None) -> dict[str, list[list[float]]]:
    """Every settlement boundary in one county, keyed by ``taz``.

    Fetched per county and only when a split settlement there is actually opened,
    so the counties with no split settlement are never downloaded.
    """
    payload = _get_json(f"{maz}/Telep-Topo-{maz}.json", version, fetch=fetch)
    return {rec["taz"]: _ring(rec.get("poligon"))
            for rec in (payload.get("list") or []) if rec.get("taz")}


def _polygons(version: str, *,
              fetch: Callable[[str], bytes] | None = None) -> dict[str, list[list[float]]]:
    """Every constituency boundary, keyed ``"<maz>/<evk>"``."""
    payload = _get_json("OevkPoligonok.json", version, fetch=fetch)
    out: dict[str, list[list[float]]] = {}
    for rec in payload.get("list") or []:
        maz, evk = rec.get("maz"), rec.get("evk")
        if maz and evk:
            out[f"{maz}/{evk}"] = _ring(rec.get("poligon"))
    return out


def settlement(maz: str, taz: str, *,
               fetch: Callable[[str], bytes] | None = None) -> dict | None:
    """One settlement with its constituency (or constituencies).

    A settlement in a **single** constituency needs no geometry — the answer is the
    name — so none is fetched. A **split** settlement gets the boundaries needed to
    choose, as one GeoJSON ``FeatureCollection``: a feature per candidate
    constituency plus the settlement's own outline, each feature carrying the
    properties the map needs to colour, label and identify it.
    """
    data = index(fetch=fetch)
    found = data["by_id"].get(f"{maz}/{taz}")
    if found is None:
        return None

    version = data["version"]
    parts = [dict(data["constituencies"][f"{maz}/{e}"]) for e in found["evks"]]
    # Sorted by constituency number, which is also how the map and the list of
    # buttons beside it label them — the number is what identifies a region to the
    # reader, since they are all drawn alike.
    parts.sort(key=lambda c: c["number"])

    geojson = None
    if len(parts) > 1:
        geojson = _split_geojson(maz, taz, found, parts, version, fetch=fetch)

    header = data["header"]
    return {
        "settlement": {
            "maz": maz, "taz": taz, "name": found["name"],
            "name_en": found["name_en"], "county": found["county"],
            "electorate": found["electorate"],
        },
        "constituencies": parts,
        "geojson": geojson,
        "map": {
            "tile_url": settings.map_tile_url,
            "tile_attribution": settings.map_tile_attribution,
            "max_zoom": settings.map_max_zoom,
        } if geojson else None,
        "source": {
            "name": "Nemzeti Választási Iroda",
            "url": settings.vtr_base_url.rstrip("/"),
            "version": version,
            "generated": header.get("generated"),
            "election_date": header.get("val_dat"),
        },
    }


def _split_geojson(maz: str, taz: str, found: dict, parts: list[dict], version: str, *,
                   fetch: Callable[[str], bytes] | None = None) -> dict | None:
    """The map layer for a split settlement, as GeoJSON.

    Each constituency is a feature; the settlement's outline is one more, last so a
    renderer that respects source order draws it on top. ``label_point`` rides on the
    constituency feature's properties because a constituency polygon reaches far
    beyond the settlement, so its own centroid — where a map library would put a
    label by default — is usually off screen.

    Returns ``None`` (and the caller falls back to the plain list of constituencies)
    when the outline is missing, so a gap in the upstream geometry costs the map, not
    the answer.
    """
    rings = _memoized(("polygons", settings.vtr_base_url, version),
                      lambda: _polygons(version, fetch=fetch))
    outline = _memoized(("outlines", settings.vtr_base_url, version, maz),
                        lambda: _outlines(maz, version, fetch=fetch)).get(taz)
    if not outline:
        logger.warning("No settlement outline for %s/%s; serving the split "
                       "constituency list without a map", maz, taz)
        return None

    features = []
    for part in parts:
        ring = rings.get(f"{maz}/{part['evk']}")
        geometry = _polygon(ring)
        if geometry is None:
            continue
        features.append({
            "type": "Feature",
            "id": f"{maz}/{part['evk']}",
            "properties": {
                "kind": "constituency",
                "maz": maz, "evk": part["evk"], "number": part["number"],
                "label": part["label"],
                "official_name": part["official_name"],
                "label_point": _label_point(outline, ring),
            },
            "geometry": geometry,
        })
    if not features:
        return None
    features.append({
        "type": "Feature",
        "id": f"settlement/{maz}/{taz}",
        "properties": {"kind": "settlement", "name": found["name"]},
        "geometry": _polygon(outline),
    })
    return {"type": "FeatureCollection", "features": features}
