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


# Counties whose name parlament.hu still writes in an older form than the election
# office does. **Csongrád** became *Csongrád-Csanád* in June 2020, but the seats there
# were never relabelled in the MP records — so the register's "Csongrád-Csanád 2. OEVK"
# and parlament.hu's "Csongrád 2. OEVK" are one seat, and matching on the string alone
# leaves all four constituencies around Szeged with no member at all (REP-10, TEL-9).
# The current name stays the one that is *stored and shown*; the old one is only ever a
# key to join on.
COUNTY_ALIASES: dict[str, tuple[str, ...]] = {
    "Csongrád-Csanád": ("Csongrád",),
}


def label_variants(label: str) -> list[str]:
    """Every spelling a constituency label may carry in the MP records, the current
    one first. Anything not covered by `COUNTY_ALIASES` is its own only spelling."""
    out = [label]
    for current, older in COUNTY_ALIASES.items():
        if label.startswith(current + " "):
            out += [label.replace(current, alt, 1) for alt in older]
    return out


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


def simplify(ring: list[list[float]], tolerance: float) -> list[list[float]]:
    """Douglas–Peucker generalisation of a closed linear ring.

    The upstream constituency boundaries are drawn for a street-level map — 99 000
    vertices over the 106 of them, some two megabytes of GeoJSON. The segmented map
    (§6D TEL-16) draws all 106 at once at the scale of the whole country, where that
    detail is invisible and only the payload is real.

    Recursion is anchored on the ring as an open sequence, so the closing point is
    stripped first and restored after. A ring that would collapse below a triangle is
    returned **unsimplified** rather than as a degenerate shape: a boundary is either
    drawn or it is not, and half of one is worse than the original's weight.

    Each ring is generalised independently, so two neighbours' shared boundary can
    diverge by up to the tolerance on either side. That is why the segments are drawn
    with a seam in the surface colour: at the scale this view is for, the gap is a
    fraction of a pixel and hides inside the seam. Preserving the shared edge exactly
    would mean carrying the topology, which is a great deal of machinery for a
    difference nobody can see.
    """
    if tolerance <= 0 or len(ring) < 5:
        return ring
    closed = ring[0] == ring[-1]
    pts = ring[:-1] if closed else list(ring)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        x1, y1 = pts[i]
        x2, y2 = pts[j]
        dx, dy = x2 - x1, y2 - y1
        span = (dx * dx + dy * dy) ** 0.5
        worst, at = -1.0, -1
        for k in range(i + 1, j):
            x, y = pts[k]
            # A zero-length base is the normal case at the top of a ring (its two
            # anchors are neighbours), so distance falls back to the anchor itself —
            # which keeps the vertex furthest from it and lets the recursion proceed.
            dist = (((x - x1) ** 2 + (y - y1) ** 2) ** 0.5 if span == 0
                    else abs(dx * (y1 - y) - dy * (x1 - x)) / span)
            if dist > worst:
                worst, at = dist, k
        if worst > tolerance:
            keep[at] = True
            stack.append((i, at))
            stack.append((at, j))
    out = [p for p, k in zip(pts, keep) if k]
    if len(out) < 3:
        return ring
    if closed:
        out.append(list(out[0]))
    return out


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


def outlines(maz: str, *, version: str | None = None,
             fetch: Callable[[str], bytes] | None = None) -> dict[str, list[list[float]]]:
    """Every settlement boundary in one county, keyed by ``taz``, as GeoJSON rings.

    The public form of the lookup's per-county fetch, for the offline tools that
    need the geography wholesale rather than one split settlement's worth of it —
    `build_settlement_points.py` assigns OSM place nodes to settlements with it.
    """
    return _outlines(maz, version or _get_version(fetch=fetch), fetch=fetch)


def _oevk_geometry(version: str, *,
                   fetch: Callable[[str], bytes] | None = None) -> dict[str, dict]:
    """Every constituency's boundary **and** published centre point, keyed
    ``"<maz>/<evk>"``. One parse of the one file that holds both, so the split-settlement
    map (REP-10) and the segmented map (TEL-16) do not each pay for it."""
    payload = _get_json("OevkPoligonok.json", version, fetch=fetch)
    out: dict[str, dict] = {}
    for rec in payload.get("list") or []:
        maz, evk = rec.get("maz"), rec.get("evk")
        if not (maz and evk):
            continue
        centre = (rec.get("centrum") or "").split()
        point = None
        if len(centre) == 2:
            try:
                point = [float(centre[1]), float(centre[0])]     # [lon, lat]
            except ValueError:
                point = None
        out[f"{maz}/{evk}"] = {"ring": _ring(rec.get("poligon")), "centre": point}
    return out


def _polygons(version: str, *,
              fetch: Callable[[str], bytes] | None = None) -> dict[str, list[list[float]]]:
    """Every constituency boundary, keyed ``"<maz>/<evk>"``."""
    return {key: geo["ring"]
            for key, geo in _oevk_geometry(version, fetch=fetch).items()}


def _centres_for_county(maz: str, version: str, *,
                        fetch: Callable[[str], bytes] | None = None) -> dict[str, list[float]]:
    """Every settlement's published **centre point** in one county, keyed by ``taz``.

    The same per-county topology file the split-settlement map reads (``_outlines``),
    but taking only its ``centrum`` field — so the settlement-mention map (§6D TEL-5)
    needs no polygon parsing and no geometry of its own. Returned ``[lon, lat]``,
    GeoJSON's order, like everything else this module hands out.
    """
    payload = _get_json(f"{maz}/Telep-Topo-{maz}.json", version, fetch=fetch)
    out: dict[str, list[float]] = {}
    for rec in payload.get("list") or []:
        taz, centre = rec.get("taz"), (rec.get("centrum") or "").split()
        if not taz or len(centre) != 2:
            continue
        try:
            lat, lon = float(centre[0]), float(centre[1])
        except ValueError:
            continue
        out[taz] = [lon, lat]
    return out


def register(*, fetch: Callable[[str], bytes] | None = None) -> dict:
    """The whole settlement register with coordinates — the gazetteer the Települések
    module is built from (§6D TEL-5).

    One row per settlement: its register spelling, county, centre point, electorate
    and the constituency (or constituencies) it belongs to. This is the **only** place
    the settlement module gets geography from, so it inherits REP-10's source, cache,
    version resolution and degradation wholesale — including that a merely *stale*
    copy is preferred to failing.

    Unlike the lookup's per-settlement path this needs **every** county's centre
    points, so it fetches all 20 topology files once (≈4 MB, then cached). That is
    why it is called by the **loader**, never on a request: the mention counts it
    feeds are precomputed (TEL-11). A county whose file cannot be fetched simply
    yields settlements without coordinates — they stay countable and searchable and
    only drop off the map (SCR-5).
    """
    data = index(fetch=fetch)
    version = data["version"]
    counties = sorted({s["maz"] for s in data["settlements"]})
    points: dict[str, list[float]] = {}
    for maz in counties:
        try:
            for taz, point in _memoized(
                    ("centres", settings.vtr_base_url, version, maz),
                    lambda maz=maz: _centres_for_county(maz, version, fetch=fetch)).items():
                points[f"{maz}/{taz}"] = point
        except (LookupUnavailable, urllib.error.URLError, OSError, ValueError) as exc:
            logger.warning("No centre points for county %s (%s); its settlements "
                           "will have no coordinates", maz, exc)

    rows = []
    for s in data["settlements"]:
        sid = f"{s['maz']}/{s['taz']}"
        point = points.get(sid)
        rows.append({
            "id": sid, "name": s["name"], "name_en": s["name_en"],
            "county": s["county"], "electorate": s["electorate"],
            "lon": point[0] if point else None,
            "lat": point[1] if point else None,
            "constituencies": [
                {"label": data["constituencies"][f"{s['maz']}/{e}"]["label"],
                 "number": data["constituencies"][f"{s['maz']}/{e}"]["number"],
                 "evk": e}
                for e in s["evks"]],
        })
    header = data["header"]
    return {
        "version": version,
        "settlements": rows,
        "source": {
            "name": "Nemzeti Választási Iroda",
            "url": settings.vtr_base_url.rstrip("/"),
            "version": version,
            "generated": header.get("generated"),
            "election_date": header.get("val_dat"),
        },
    }


def constituencies(*, fetch: Callable[[str], bytes] | None = None,
                   tolerance: float | None = None) -> dict:
    """All 106 single-member constituencies with a **generalised boundary** — the
    territory the settlement map's second binning is drawn on (§6D TEL-16).

    One row per constituency: the label parlament.hu's MP records use (so it joins to
    `person_mandate` exactly as REP-10 does), the office's own name and seat for it,
    its electorate, its centre point and its boundary as a GeoJSON ring, generalised
    to `tolerance` degrees.

    The boundary is **optional**, and a constituency without one is still returned:
    the names, the seats and the electorates come from a different upstream file than
    the geometry, so an unreachable polygon file costs the map and not the rest (the
    endpoint then offers no constituency binning at all rather than a partial country).
    Called by the **loader**, never on a request — like `register()`, whose caching,
    version resolution and degradation it inherits wholesale.
    """
    data = index(fetch=fetch)
    version = data["version"]
    tol = settings.oevk_tolerance if tolerance is None else tolerance
    geometry: dict[str, dict] = {}
    try:
        geometry = _memoized(("oevk-geometry", settings.vtr_base_url, version),
                             lambda: _oevk_geometry(version, fetch=fetch))
    except (LookupUnavailable, urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("No constituency boundaries (%s); the settlement map's "
                       "constituency binning will be unavailable", exc)

    rows = []
    vertices = kept = 0
    for key, info in sorted(data["constituencies"].items()):
        geo = geometry.get(key) or {}
        ring = geo.get("ring") or []
        vertices += len(ring)
        boundary = simplify(ring, tol) if ring else []
        kept += len(boundary)
        centre = geo.get("centre")
        rows.append({
            "id": key, "label": info["label"], "number": info["number"],
            "county": info["county"], "official_name": info["official_name"],
            "official_name_en": info["official_name_en"], "seat": info["seat"],
            "electorate": info["electorate"],
            "lon": centre[0] if centre else None,
            "lat": centre[1] if centre else None,
            "boundary": boundary or None,
        })
    if vertices:
        logger.info("Constituency boundaries: %d vertices generalised to %d at "
                    "tolerance %g°", vertices, kept, tol)
    header = data["header"]
    return {
        "version": version,
        "constituencies": rows,
        "source": {
            "name": "Nemzeti Választási Iroda",
            "url": settings.vtr_base_url.rstrip("/"),
            "version": version,
            "generated": header.get("generated"),
            "election_date": header.get("val_dat"),
        },
    }


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
