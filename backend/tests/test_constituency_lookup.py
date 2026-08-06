"""Constituency lookup — "which constituency am I in, and who represents it?" (REP-10).

The upstream source is the National Election Office's static JSON tree, so every
test here injects a **fake fetcher** over a miniature version of it: two counties,
three constituencies, one settlement that sits in a single constituency and one that
is split between two. No test touches the network.
"""

from __future__ import annotations

import json

import pytest

import app.main as main_module
from app import config as config_module
from app import valasztas
from app.modules.representatives import router as reps_router

# --- a miniature election-office data tree ---------------------------------

VERSION = "09091200"

# Simple rectangles, expressed the way the source does: "lat lon" pairs, comma
# separated, latitude first and the ring left open. Budapest constituencies 1 and 2
# divide the longitude range at 19.05; district 05's outline straddles that line, so
# the district is split and both are candidates.
_RECTS = {
    "01/01": "47.40 19.00,47.60 19.00,47.60 19.05,47.40 19.05",
    "01/02": "47.40 19.05,47.60 19.05,47.60 19.10,47.40 19.10",
    "14/04": "47.60 19.00,47.80 19.00,47.80 19.30,47.60 19.30",
}
_OUTLINES = {
    "01": {"005": "47.48 19.02,47.52 19.02,47.52 19.08,47.48 19.08"},
    "14": {"001": "47.65 19.05,47.70 19.05,47.70 19.15,47.65 19.15"},
}

_HEADER = {"generated": "2026-04-11T21:00:00+02:00",
           "val_dat": "2026-04-12T00:00:00+02:00"}


def _oevk_adatok():
    return {"PvOnHeader": _HEADER, "list": [
        {"maz": "01", "maz_nev": "Budapest főváros", "evk": "01",
         "evk_nev": "Budapest főváros, 01. számú egyéni választókerület",
         "evk_nev_en": "Budapest capital, 01. no. constituency",
         "szekhely": "Budapest 05. kerület", "letszam": {"osszesen": 70000}},
        {"maz": "01", "maz_nev": "Budapest főváros", "evk": "02",
         "evk_nev": "Budapest főváros, 02. számú egyéni választókerület",
         "evk_nev_en": "Budapest capital, 02. no. constituency",
         "szekhely": "Budapest 08. kerület", "letszam": {"osszesen": 71000}},
        {"maz": "14", "maz_nev": "Pest vármegye", "evk": "04",
         "evk_nev": "Pest vármegye, 04. számú egyéni választókerület",
         "evk_nev_en": "Pest county, 04. no. constituency",
         "szekhely": "Szentendre", "letszam": {"osszesen": 68000}},
    ]}


def _telepulesek():
    return {"PvOnHeader": _HEADER, "list": [
        {"leiro": {"maz": "01", "taz": "005", "megnev": "Budapest 05. kerület",
                   "megnev_en": "Budapest 5th district", "evk_lst": ["01", "02"]},
         "letszam": {"osszesen": 17222}},
        {"leiro": {"maz": "14", "taz": "001", "megnev": "Szentendre",
                   "megnev_en": "Szentendre", "evk_lst": ["04"]},
         "letszam": {"osszesen": 20000}},
    ]}


def _poligonok():
    return {"PvOnHeader": _HEADER, "list": [
        {"maz": key.split("/")[0], "evk": key.split("/")[1], "poligon": ring}
        for key, ring in _RECTS.items()
    ]}


def _telep_topo(maz):
    return {"PvOnHeader": _HEADER, "list": [
        {"maz": maz, "taz": taz, "poligon": ring}
        for taz, ring in _OUTLINES.get(maz, {}).items()
    ]}


def _fake_fetch(url: str) -> bytes:
    """Serve the miniature tree by URL suffix, and record the call."""
    _fake_fetch.calls.append(url)
    if url.endswith("/config.json"):
        body = {"ver": VERSION}
    elif url.endswith("/Telepulesek.json"):
        body = _telepulesek()
    elif url.endswith("/OevkAdatok.json"):
        body = _oevk_adatok()
    elif url.endswith("/OevkPoligonok.json"):
        body = _poligonok()
    elif "Telep-Topo-" in url:
        body = _telep_topo(url.rsplit("Telep-Topo-", 1)[1][:2])
    else:
        raise OSError(f"unexpected VTR URL in a test: {url}")
    return json.dumps(body, ensure_ascii=False).encode()


def _live_settings():
    """Every distinct ``settings`` object the code under test reads.

    Each module binds ``settings`` at import, and another test in the suite
    ``importlib.reload``s ``app.config`` — which rebinds ``app.config.settings`` to a
    NEW object while leaving already-imported modules holding the old one (the same
    hazard ``test_og.py`` documents). Patching one of them would silently miss the
    others, so a setting is patched on all of them.
    """
    seen: set[int] = set()
    out = []
    for module in (config_module, main_module, valasztas, reps_router):
        obj = getattr(module, "settings", None)
        if obj is not None and id(obj) not in seen:
            seen.add(id(obj))
            out.append(obj)
    return out


def _set_setting(monkeypatch, name, value):
    for obj in _live_settings():
        monkeypatch.setattr(obj, name, value)


@pytest.fixture
def vtr(tmp_path, monkeypatch):
    """Point the lookup at a temp cache directory and the fake fetcher, and clear the
    in-process memo either side so tests can't leak state into each other."""
    _fake_fetch.calls = []
    _set_setting(monkeypatch, "vtr_cache_dir", str(tmp_path / "vtr-cache"))
    _set_setting(monkeypatch, "evk_lookup", True)
    monkeypatch.setattr(valasztas, "_default_fetch", _fake_fetch)
    valasztas.reset_cache()
    yield _fake_fetch
    valasztas.reset_cache()


# --- searching for a settlement --------------------------------------------

def test_search_is_accent_insensitive(client, vtr):
    """FOLD-1: an unaccented query finds the accented name and vice versa."""
    d = client.get("/api/v1/representatives/constituencies/settlements",
                   params={"q": "szentendre"}).json()
    assert [s["name"] for s in d["settlements"]] == ["Szentendre"]
    # A single-constituency settlement is already the answer, so the row says which.
    assert d["settlements"][0]["constituency"] == "Pest 4. OEVK"
    assert d["settlements"][0]["constituency_count"] == 1


def test_search_finds_a_budapest_district_by_roman_numeral(client, vtr):
    """People write "V. kerület", not the source's zero-padded "Budapest 05."."""
    for term in ("V. kerulet", "5. kerület", "Budapest 5"):
        d = client.get("/api/v1/representatives/constituencies/settlements",
                       params={"q": term}).json()
        assert [s["name"] for s in d["settlements"]] == ["Budapest 05. kerület"], term
    # A split settlement cannot be answered from the list — it has no single label.
    assert d["settlements"][0]["constituency"] is None
    assert d["settlements"][0]["constituency_count"] == 2


def test_search_needs_a_query(client, vtr):
    assert client.get("/api/v1/representatives/constituencies/settlements",
                      params={"q": ""}).status_code == 422


# --- a settlement in one constituency --------------------------------------

def test_single_constituency_settlement_answers_outright(client, vtr):
    d = client.get("/api/v1/representatives/constituencies/settlements/14/001").json()
    assert d["settlement"]["name"] == "Szentendre"
    assert d["settlement"]["county"] == "Pest"
    assert len(d["constituencies"]) == 1
    part = d["constituencies"][0]
    assert part["label"] == "Pest 4. OEVK"
    assert part["official_name"] == "Pest vármegye, 04. számú egyéni választókerület"
    # Nagy Anna has no electionHistory, so this is the fallback path: the single
    # stored `constituency`, allowed only because she sits in the period.
    assert [mp["label"] for mp in part["representatives"]] == ["Nagy Anna"]
    assert part["representatives"][0]["faction"]["label"] == "TISZA"
    # One constituency needs no map, so no geometry is fetched or sent.
    assert d["geojson"] is None and d["map"] is None
    assert not any("Poligonok" in u or "Telep-Topo" in u for u in vtr.calls)


def test_the_mp_carries_a_contactable_address(client, vtr):
    """Finding out who represents you is usually a prelude to writing to them, so
    the answer carries the MP's published address — and honestly reports its
    absence rather than omitting the field."""
    split = client.get("/api/v1/representatives/constituencies/settlements/01/005").json()
    kovacs = next(c for c in split["constituencies"]
                  if c["label"] == "Budapest 1. OEVK")["representatives"][0]
    assert kovacs["email"] == "kovacs.bela@parlament.hu"

    single = client.get("/api/v1/representatives/constituencies/settlements/14/001").json()
    nagy = single["constituencies"][0]["representatives"][0]
    assert nagy["label"] == "Nagy Anna" and nagy["email"] is None


def test_answer_names_the_cycle_the_boundaries_elect(client, vtr):
    """The map is redrawn between elections, so the answer is pinned to the cycle
    the election *following* the data's election date produced — never the caller's
    cycle scope."""
    d = client.get("/api/v1/representatives/constituencies/settlements/14/001").json()
    assert d["period"]["number"] == 43           # starts 2026-05-09, election 2026-04-12
    assert d["period"]["date_start"] == "2026-05-09"


def test_lookup_ignores_the_global_cycle_scope(client, vtr):
    """A `period` param (which every other period-aware endpoint honours) must not
    change this answer — the boundaries belong to one cycle regardless."""
    base = client.get("/api/v1/representatives/constituencies/settlements/14/001").json()
    scoped = client.get("/api/v1/representatives/constituencies/settlements/14/001",
                        params={"period": 42}).json()
    assert scoped["period"] == base["period"]
    assert scoped["constituencies"] == base["constituencies"]


def test_a_past_mandate_in_another_constituency_is_not_the_answer(client, vtr):
    """Kovács held Budapest 2. in 2022-2026 and Budapest 1. from 2026. Asking about
    the split district must attribute him to 1., not to 2."""
    d = client.get("/api/v1/representatives/constituencies/settlements/01/005").json()
    by_label = {c["label"]: c for c in d["constituencies"]}
    assert [mp["label"] for mp in by_label["Budapest 1. OEVK"]["representatives"]] \
        == ["Kovács Béla"]
    assert by_label["Budapest 2. OEVK"]["representatives"] == []


# --- a settlement split between constituencies -----------------------------

def test_split_settlement_returns_pickable_geojson(client, vtr):
    d = client.get("/api/v1/representatives/constituencies/settlements/01/005").json()
    # Ordered by constituency number, which is what labels each region on the map
    # and in the list beside it — the regions are drawn alike, so the number is the
    # only thing that identifies one.
    assert [c["label"] for c in d["constituencies"]] == ["Budapest 1. OEVK",
                                                         "Budapest 2. OEVK"]
    assert [c["number"] for c in d["constituencies"]] == [1, 2]

    gj = d["geojson"]
    assert gj["type"] == "FeatureCollection"
    kinds = [f["properties"]["kind"] for f in gj["features"]]
    assert kinds == ["constituency", "constituency", "settlement"]  # outline last/on top

    # The basemap (and the attribution it requires) travels with the geometry, so the
    # map needs no build-time configuration of its own.
    assert "{z}" in d["map"]["tile_url"] and d["map"]["tile_attribution"]


def test_split_geojson_is_valid_geojson(client, vtr):
    """RFC 7946: longitude first, rings closed, exterior ring counter-clockwise —
    none of which the upstream format does."""
    d = client.get("/api/v1/representatives/constituencies/settlements/01/005").json()
    for feature in d["geojson"]["features"]:
        assert feature["geometry"]["type"] == "Polygon"
        ring = feature["geometry"]["coordinates"][0]
        assert ring[0] == ring[-1], "ring must be closed"
        lons = [p[0] for p in ring]
        lats = [p[1] for p in ring]
        assert all(18 < x < 23 for x in lons), "longitude must come first"
        assert all(45 < y < 49 for y in lats)
        assert valasztas._signed_area(ring) > 0, "exterior ring must be CCW"


def test_label_point_falls_inside_the_settlement(client, vtr):
    """The label anchor exists so a constituency's number can be drawn where the
    reader is looking: inside the part of the *settlement* it covers. A constituency
    polygon's own centroid would usually be off screen."""
    d = client.get("/api/v1/representatives/constituencies/settlements/01/005").json()
    outline = next(f for f in d["geojson"]["features"]
                   if f["properties"]["kind"] == "settlement")
    ring = outline["geometry"]["coordinates"][0]
    for feature in d["geojson"]["features"]:
        if feature["properties"]["kind"] != "constituency":
            continue
        lon, lat = feature["properties"]["label_point"]
        assert valasztas._contains(ring, lon, lat)
        # …and inside its own constituency, not merely inside the settlement.
        assert valasztas._contains(feature["geometry"]["coordinates"][0], lon, lat)


def test_split_settlement_fetches_only_its_own_county_outlines(client, vtr):
    """Outlines are per county and only the opened settlement's county is needed, so
    the other nineteen counties' (multi-MB) files are never downloaded."""
    client.get("/api/v1/representatives/constituencies/settlements/01/005")
    topo = [u for u in vtr.calls if "Telep-Topo-" in u]
    assert len(topo) == 1 and "Telep-Topo-01.json" in topo[0]


# --- failure and configuration paths ---------------------------------------

def test_unknown_settlement_is_404(client, vtr):
    assert client.get(
        "/api/v1/representatives/constituencies/settlements/14/999").status_code == 404


def test_malformed_codes_are_rejected(client, vtr):
    for path in ("14/9", "9/001", "aa/001"):
        assert client.get(
            f"/api/v1/representatives/constituencies/settlements/{path}"
        ).status_code == 422, path


def test_a_stale_cache_survives_an_upstream_outage(client, vtr, monkeypatch):
    """The electoral map does not change between elections, so a cached copy that is
    merely past its TTL is the right answer when the source is unreachable — far
    better than failing the page."""
    first = client.get("/api/v1/representatives/constituencies/settlements/14/001")
    assert first.status_code == 200

    # Expire everything, drop the memo, and break the network.
    _set_setting(monkeypatch, "vtr_cache_ttl", -1)
    valasztas.reset_cache()

    def dead(url):
        raise OSError("upstream down")
    monkeypatch.setattr(valasztas, "_default_fetch", dead)

    again = client.get("/api/v1/representatives/constituencies/settlements/14/001")
    assert again.status_code == 200
    assert again.json()["constituencies"] == first.json()["constituencies"]


def test_no_cache_and_no_upstream_is_503(client, vtr, monkeypatch):
    """Nothing cached and nothing reachable: an honest "unavailable", never an empty
    or invented answer (cf. SCR-5)."""
    def dead(url):
        raise OSError("upstream down")
    monkeypatch.setattr(valasztas, "_default_fetch", dead)
    r = client.get("/api/v1/representatives/constituencies/settlements/14/001")
    assert r.status_code == 503


def test_disabled_lookup_404s_and_is_advertised_in_meta(client, vtr, monkeypatch):
    _set_setting(monkeypatch, "evk_lookup", False)
    assert client.get("/api/v1/meta").json()["features"]["constituency_lookup"] is False
    assert client.get("/api/v1/representatives/constituencies/settlements",
                      params={"q": "szentendre"}).status_code == 404
    assert client.get(
        "/api/v1/representatives/constituencies/settlements/14/001").status_code == 404


def test_enabled_lookup_is_advertised_in_meta(client, vtr):
    assert client.get("/api/v1/meta").json()["features"]["constituency_lookup"] is True


# --- the county-name join --------------------------------------------------

@pytest.mark.parametrize("election_office, parliament, same", [
    # The one real disagreement: the county was renamed in 2020 and parlament.hu's
    # constituency labels still use the old name.
    ("csongrad-csanad", "csongrad", True),
    ("budapest", "budapest", True),
    ("pest", "budapest", False),
    ("bacs-kiskun", "bekes", False),
    ("gyor-moson-sopron", "gyor-moson-sopron", True),
])
def test_county_names_match_across_a_rename(election_office, parliament, same):
    """One name being a prefix of the other covers the rename without a hard-coded
    alias table, and no two of the twenty county names are prefixes of each other —
    so it cannot conflate distinct counties."""
    assert reps_router._county_compatible(election_office, parliament) is same
