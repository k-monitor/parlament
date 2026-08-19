"""Settlement mentions — the Települések module (§6D).

Two halves, matching the module's own split:

* the **matcher** (`app.settlements`) is pure — no DB, no network, no model — so it
  is tested with plain Hungarian sentences, which is the only way to see whether the
  morphology and the ambiguity policy actually behave (TEL-2/TEL-3);
* the **pipeline** (loader → tables → API) is tested over the shared synthetic corpus
  with a fake election-office fetcher, so no test touches the network.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from app import loader, settlements, valasztas

# ---------------------------------------------------------------------------
# The matcher (TEL-2 / TEL-3)
# ---------------------------------------------------------------------------

# A miniature register, deliberately mixing the hard cases: two ordinary towns, a
# name that is an everyday word (*Alap* = "fund"), one that is a stop-word (*Baj* =
# "trouble"), one that is also an MP's surname (*Varga*), one that is also a county
# (*Veszprém*), a name whose demonym elides a vowel (*Eger* → *egri*), one ending in
# a vowel (*Kalocsa* → *Kalocsán*), the capital, and two Budapest districts.
NAMES = {
    "Kaposvár": "14/091", "Szeged": "06/001", "Pécs": "02/001", "Eger": "10/001",
    "Kalocsa": "03/012", "Nyíregyháza": "15/001", "Alap": "17/005", "Baj": "11/010",
    "Varga": "14/200", "Veszprém": "19/001", "Sima": "05/300",
    "Budapest": settlements.BUDAPEST_ID,
    "Budapest 09. kerület": "01/009", "Budapest 21. kerület": "01/021",
}
# The corpus statistics the ambiguity policy is derived from (TEL-3 gate 2).
# *Veszprém* sits in the weak tier on purpose: it is a county name as well as a
# town, so it has to be caught by the county look-ahead rather than by ambiguity.
DOC_FREQ = {"alap": 900, "sima": 680, "veszprém": 5, "eger": 1}
STOPWORDS = {"baj"}
PEOPLE = {"Varga", "Mihály"}


@pytest.fixture
def gazetteer():
    return settlements.build(NAMES, lemma_doc_freq=DOC_FREQ, stopwords=STOPWORDS,
                             person_names=PEOPLE)


def found(gazetteer, text, blocked=()):
    """The settlement ids a sentence yields, in order."""
    return [m.settlement_id for m in gazetteer.scan(text, blocked)]


@pytest.mark.parametrize("text, expected", [
    # The bare nominative, mid-sentence.
    ("Erről szólt Kaposvár döntése.", ["14/091"]),
    # The case endings a place actually takes.
    ("Kaposváron új kórház épült.", ["14/091"]),
    ("Szegedre költözött a család.", ["06/001"]),
    ("Pécsről indult a vonat.", ["02/001"]),
    ("Pécsett tartották a konferenciát.", ["02/001"]),
    ("Szegedig meg sem álltunk.", ["06/001"]),
    ("Szegedet említette a miniszter.", ["06/001"]),
    # A final vowel lengthens under the suffix and has to be undone.
    ("Kalocsán járt a bizottság.", ["03/012"]),
    # The instrumental assimilates: the suffix's own v vanishes.
    ("Egyeztettünk Szegeddel is.", ["06/001"]),
    ("Péccsel közösen pályáztak.", ["02/001"]),
])
def test_inflected_forms_resolve_to_the_settlement(gazetteer, text, expected):
    assert found(gazetteer, text) == expected


@pytest.mark.parametrize("text, expected", [
    ("A kaposvári kórházban voltam.", ["14/091"]),
    ("A szegediek tiltakoztak.", ["06/001"]),
    # Eger's demonym elides the stem vowel, which no general rule predicts.
    ("Az egri vár felújítása.", ["10/001"]),
    # A long compound's demonym drops the final a (*Nyíregyháza* → *nyíregyházi*).
    ("A nyíregyházi uszoda ügye.", ["15/001"]),
])
def test_the_demonym_counts_as_a_mention(gazetteer, text, expected):
    """"a kaposvári kórház" names Kaposvár as surely as "Kaposváron" does — and is
    how a speaker most often refers to a place at all (TEL-2)."""
    assert found(gazetteer, text) == expected


def test_a_longer_name_is_never_reduced_to_a_shorter_one(gazetteer):
    """*Pécsvárad* is its own town and must not be read as *Pécs*: only real suffixes
    are stripped, and "várad" is not one."""
    assert found(gazetteer, "Pécsváradon jártunk.") == []


# --- the four gates (TEL-3) ------------------------------------------------

def test_an_organisation_span_vetoes_the_match(gazetteer):
    """Gate 1. The NER layer is used as a veto, not a detector: an institution named
    after a town is not a mention of the town, and no per-name rule is needed to know
    it — the entity layer already found the span."""
    text = "Erről a Szeged Városi Bíróság döntött."
    assert found(gazetteer, text) == ["06/001"]              # without the veto
    assert found(gazetteer, text, [(8, 29)]) == []            # with it


def test_a_name_shared_with_an_mp_is_ambiguous_by_derivation(gazetteer):
    """Gate 2 backs gate 1 up where the NER layer missed the person: *Varga* is a
    sitting MP's surname as well as a Somogy village, and the person register says so
    without anyone writing the rule down."""
    assert "Varga" in gazetteer.need_cue
    assert found(gazetteer, "Ezt Varga Mihály jelentette be.") == []
    assert found(gazetteer, "Varga község határában.") == ["14/200"]


def test_an_everyday_word_needs_the_sentence_to_vouch_for_it(gazetteer):
    """Gate 3, strict tier. *Alap* is a fund far more often than it is a village, and
    a case ending does not help — "az Alapból" is money."""
    assert "Alap" in gazetteer.need_cue
    assert found(gazetteer, "A forrás a Nemzeti Foglalkoztatási Alapból jött.") == []
    assert found(gazetteer, "Alap község polgármestere kérte.") == ["17/005"]


def test_a_stop_word_name_is_treated_as_ambiguous(gazetteer):
    """Derived from the stop-word list, not hand-written: "Baj lesz ebből" is trouble."""
    assert "Baj" in gazetteer.need_cue
    assert found(gazetteer, "Ebből még Baj lesz.") == []
    assert found(gazetteer, "Baj településen történt.") == ["11/010"]


def test_a_county_reading_is_rejected(gazetteer):
    """Gate 5. "Veszprém megyére" is the county; the look-ahead reaches past an
    intervening list, because upstream writes "Veszprém, Vas és Zala megyére"."""
    assert found(gazetteer, "A rendelet Veszprém megyére vonatkozik.") == []
    assert found(gazetteer, "Ez Veszprém, Vas és Zala megyét érinti.") == []
    assert found(gazetteer, "Ez Veszprémben történt.") == ["19/001"]


def test_the_superessive_alone_does_not_vouch_for_a_weak_name():
    """The superessive is also how Hungarian makes an adverb, so *Sima* ("smooth")
    is not located by "simán" — while an unambiguously locative ending is enough."""
    gazetteer = settlements.build(
        {"Sima": "05/300", "Bicske": "07/002"},
        lemma_doc_freq={"sima": 5}, stopwords=(), person_names=())
    assert "Sima" in gazetteer.need_suffix
    assert found(gazetteer, "Ez simán megoldható.") == []
    assert found(gazetteer, "Ez Simában történt.") == ["05/300"]


def test_a_sentence_initial_bare_name_needs_a_cue(gazetteer):
    """Capitalization at the start of a sentence says nothing about a proper noun, so
    a bare nominative there is not evidence on its own."""
    assert found(gazetteer, "Baj van a rendszerrel.") == []
    # ...while the same name mid-sentence with a place word is fine.
    assert found(gazetteer, "Ez Baj község határában van.") == ["11/010"]


def test_the_reviewed_table_holds_a_lake_to_the_strict_standard():
    """Gate 4: no lemma frequency betrays *Balaton*, because the lake is a proper
    noun too — which is exactly what the hand-reviewed table is for."""
    gazetteer = settlements.build({"Balaton": "10/030"})
    assert "Balaton" in gazetteer.need_cue
    assert found(gazetteer, "A Balatonban fürödtünk.") == []
    assert found(gazetteer, "Balaton községben lakik.") == ["10/030"]


# --- Budapest and its districts (TEL-5) ------------------------------------

def test_the_capital_is_its_own_entity(gazetteer):
    assert found(gazetteer, "Ez Budapesten történt.") == [settlements.BUDAPEST_ID]


@pytest.mark.parametrize("text, expected", [
    ("Ez a IX. kerületben van.", "01/009"),
    ("A XXI. kerület ügye.", "01/021"),
    ("Budapest 9. kerületében épült.", "01/009"),
    ("IX. kerületi lakosok tiltakoztak.", "01/009"),
])
def test_a_district_is_recognised_by_the_forms_people_use(gazetteer, text, expected):
    """The register spells them "Budapest 09. kerület", which nobody says aloud — so
    without this every district of the capital would be a permanent blind spot."""
    assert found(gazetteer, text) == [expected]


def test_a_district_phrase_is_not_also_counted_as_the_capital(gazetteer):
    """"Budapest 9. kerülete" contains the word Budapest; counted twice it would be
    both a district mention and a capital mention of the same six words."""
    assert found(gazetteer, "Ez Budapest 9. kerületében van.") == ["01/009"]


def test_a_bare_arabic_district_number_is_not_a_district(gazetteer):
    """A bare "9. kerület" is as often a *választókerület*, so only the roman form —
    or an explicit "Budapest" — counts (TEL-3's precision-first rule)."""
    assert found(gazetteer, "Ez a 9. kerületben van.") == []


def test_a_district_alias_needs_a_place_ending(gazetteer):
    """Half the district names are also football clubs: "Ferencvárosban" is a place,
    "a Ferencváros" is a team."""
    assert found(gazetteer, "Ez Ferencvárosban nyílt meg.") == ["01/009"]
    assert found(gazetteer, "Idén a Ferencváros lett a bajnok.") == []


# ---------------------------------------------------------------------------
# The pipeline: loader → tables → API
# ---------------------------------------------------------------------------

VERSION = "09091200"
_HEADER = {"generated": "2026-04-11T21:00:00+02:00",
           "val_dat": "2026-04-12T00:00:00+02:00"}

# Two constituencies, matching the seats the shared MP registry hands out
# ("Budapest 1. OEVK" for Kovács, "Pest 4. OEVK" for Nagy), so the TEL-9 join has
# something real to bite on. Kovács's seat holds the district our synthetic
# transcript talks about; Nagy's holds a town it does not.
def _oevk_adatok():
    return {"PvOnHeader": _HEADER, "list": [
        {"maz": "01", "maz_nev": "Budapest főváros", "evk": "01",
         "evk_nev": "Budapest főváros, 01. számú egyéni választókerület",
         "szekhely": "Budapest 05. kerület", "letszam": {"osszesen": 70000}},
        {"maz": "14", "maz_nev": "Pest vármegye", "evk": "04",
         "evk_nev": "Pest vármegye, 04. számú egyéni választókerület",
         "szekhely": "Szentendre", "letszam": {"osszesen": 68000}},
    ]}


def _telepulesek():
    return {"PvOnHeader": _HEADER, "list": [
        {"leiro": {"maz": "01", "taz": "005", "megnev": "Budapest 05. kerület",
                   "megnev_en": "Budapest 5th district", "evk_lst": ["01"]},
         "letszam": {"osszesen": 17222}},
        {"leiro": {"maz": "14", "taz": "001", "megnev": "Szentendre",
                   "megnev_en": "Szentendre", "evk_lst": ["04"]},
         "letszam": {"osszesen": 20000}},
        {"leiro": {"maz": "14", "taz": "002", "megnev": "Kaposvár",
                   "megnev_en": "Kaposvár", "evk_lst": ["04"]},
         "letszam": {"osszesen": 50000}},
    ]}


# `centrum` is what the mention map is drawn from — the register publishes a centre
# point per settlement, so no polygon has to be parsed for it (TEL-5).
_CENTRES = {"01": {"005": "47.4979 19.0402"},
            "14": {"001": "47.6667 19.0754", "002": "46.3594 17.7968"}}


def _telep_topo(maz):
    return {"PvOnHeader": _HEADER, "list": [
        {"maz": maz, "taz": taz, "centrum": centre, "poligon": ""}
        for taz, centre in _CENTRES.get(maz, {}).items()
    ]}


def _square_spec(lat, lon, size=0.1):
    """A square ring around a point in the upstream ``"lat lon,…"`` format, with a
    **collinear midpoint on each side** — so a generalisation pass has something it
    ought to drop, and a test can see that it did without the shape changing (TEL-16)."""
    corners = [(lat - size, lon - size), (lat - size, lon + size),
               (lat + size, lon + size), (lat + size, lon - size)]
    points = []
    for i, corner in enumerate(corners):
        nxt = corners[(i + 1) % 4]
        points.append(corner)
        points.append(((corner[0] + nxt[0]) / 2, (corner[1] + nxt[1]) / 2))
    return ",".join(f"{a:.6f} {b:.6f}" for a, b in points)


# The constituency boundaries the map's constituency binning is drawn on (TEL-16).
# Deliberately not the same geography as the settlements they hold — nothing in the
# feature does point-in-polygon, the settlement → constituency mapping is the register's
# own `evk_lst`, so a boundary only has to exist and be well formed.
_OEVK_POLYGONS = {"01/01": (47.4979, 19.0402), "14/04": (47.6667, 19.0754)}


def _oevk_poligonok():
    return {"PvOnHeader": _HEADER, "list": [
        {"maz": key[:2], "evk": key[3:],
         "centrum": f"{lat:.6f} {lon:.6f}", "poligon": _square_spec(lat, lon)}
        for key, (lat, lon) in _OEVK_POLYGONS.items()
    ]}


def _fake_fetch(url: str) -> bytes:
    if url.endswith("/config.json"):
        body = {"ver": VERSION}
    elif url.endswith("/Telepulesek.json"):
        body = _telepulesek()
    elif url.endswith("/OevkAdatok.json"):
        body = _oevk_adatok()
    elif url.endswith("/OevkPoligonok.json"):
        body = _oevk_poligonok()
    elif "Telep-Topo-" in url:
        body = _telep_topo(url.rsplit("Telep-Topo-", 1)[1][:2])
    else:
        raise OSError(f"unexpected VTR URL in a test: {url}")
    return json.dumps(body, ensure_ascii=False).encode()


@pytest.fixture
def vtr(tmp_path, monkeypatch):
    """Point every `settings` object at a temp cache and the fake fetcher.

    Each module binds `settings` at import and another test in the suite reloads
    `app.config`, so patching one object would silently miss the others — the same
    hazard `test_constituency_lookup` documents.
    """
    from app import config as config_module
    from app import valasztas as valasztas_module
    from app.modules.settlements import router as settlements_router
    # `Settings` is an unhashable dataclass, so the distinct objects are collected
    # by identity rather than in a set.
    candidates = [config_module.settings, valasztas_module.settings,
                  loader.settings, settlements_router.settings]
    objects = list({id(obj): obj for obj in candidates}.values())
    for obj in objects:
        monkeypatch.setattr(obj, "vtr_cache_dir", str(tmp_path / "vtr-cache"))
        monkeypatch.setattr(obj, "evk_lookup", True)
    monkeypatch.setattr(valasztas, "_default_fetch", _fake_fetch)
    valasztas.reset_cache()
    yield
    valasztas.reset_cache()


# The shared synthetic sitting talks about the budget, not about places, so the
# mention pass needs text with settlements in it. Rather than reshape the fixture
# corpus (every other test reads it), the sentences are rewritten in place — which is
# also a fair test of the pass, since it reads `sentence`/`speech` and nothing else.
#
# Both sentences belong to the fixture's FIRST speech, i.e. to Kovács Béla (k001),
# who holds "Budapest 1. OEVK" — which in the miniature tree above contains the 5th
# district and nothing else. So his own constituency is named once (the district), and
# the other places named (Kaposvár twice, Szentendre once) are somebody else's, while
# "Budapest" is the capital as a whole and counts towards neither (TEL-9).
def _seed_place_text(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        UPDATE sentence SET text =
            'A kaposvári kórház fejlesztése Kaposváron fontos kérdés.'
            WHERE ord = 0;
        UPDATE sentence SET text =
            'Ez Budapesten és az V. kerületben is így van, Szentendrén nem!'
            WHERE ord = 1;
    """)
    conn.commit()
    conn.close()


@pytest.fixture
def built(db_path, vtr):
    """The §6D tables, built over the shared corpus with place-bearing sentences."""
    _seed_place_text(db_path)
    conn = loader.connect(db_path)
    loader.rebuild_settlements(conn)
    loader.rebuild_settlement_mentions(conn)
    loader.rebuild_settlement_stats(conn)
    conn.close()
    return db_path


def test_the_register_is_stored_with_its_geography(built):
    conn = sqlite3.connect(built)
    conn.row_factory = sqlite3.Row
    rows = {r["name"]: r for r in conn.execute("SELECT * FROM settlement")}
    # Three settlements from the register, plus the capital as its own entity.
    assert set(rows) == {"Budapest 05. kerület", "Szentendre", "Kaposvár", "Budapest"}
    # The stored point is the label point, not the register's territorial centre —
    # see `test_a_settlement_is_stored_where_the_basemap_labels_it` below.
    point = settlements.label_point("15/091", "Kaposvár")
    assert (rows["Kaposvár"]["lat"], rows["Kaposvár"]["lon"]) == point
    assert rows["Kaposvár"]["county"] == "Pest"        # per this miniature tree
    # The folded name is what the search box matches on (§4B FOLD-1).
    assert rows["Kaposvár"]["name_fold"] == "kaposvar"
    # ...and the constituency join is stored in the form parlament.hu's MP records
    # use, which is what REP-10's join needs (EXT-2).
    labels = {r["settlement_id"]: r["label"] for r in
              conn.execute("SELECT * FROM settlement_constituency")}
    assert labels["14/002"] == "Pest 4. OEVK"
    conn.close()


def test_a_settlement_is_stored_where_the_basemap_labels_it(built):
    """TEL-5: the office's `centrum` centres a settlement's **territory**, which over
    the 24 largest towns lands a mean 3 km from the name the basemap prints — so every
    dot missed its own label. The stored point is the checked-in gazetteer's label
    point (the OSM place node the basemap draws that name at), and the register's
    point survives only as the fallback."""
    conn = sqlite3.connect(built)
    point = conn.execute(
        "SELECT lat, lon FROM settlement WHERE name = 'Kaposvár'").fetchone()
    conn.close()
    assert point == settlements.label_point("15/091", "Kaposvár")
    # Not the `centrum` the fixture's register published for it...
    assert point != (46.3594, 17.7968)
    # ...but the same town: a label point moves the dot across a town, never off it.
    assert abs(point[0] - 46.3594) < 0.05 and abs(point[1] - 17.7968) < 0.05


def test_a_label_point_is_never_taken_from_a_renumbered_id():
    """The ids are the election office's own, and an election can hand one to a
    different settlement — so an id match counts only where the name agrees, and the
    name (unique across the register, and what survives renumbering) carries the
    match over on its own. A name the table has never heard of keeps whatever the
    register published, which is why this returns None rather than a guess."""
    kaposvar = settlements.label_point("15/091", "Kaposvár")
    assert kaposvar is not None
    # The fixture's miniature register gives 14/002 to Kaposvár, the real one gives
    # it to a Pest settlement: the name is what resolves that, in both directions.
    assert settlements.label_point("14/002", "Kaposvár") == kaposvar
    assert settlements.label_point("15/091", "Szentendre") != kaposvar
    assert settlements.label_point("99/999", "Nincsilyentelepülés") is None


def test_mentions_are_stored_per_sentence_with_their_speech(built):
    """A mention is a citation (TEL-4): it carries the sentence, the speech, the
    sitting and the speaker, so any count can be opened."""
    conn = sqlite3.connect(built)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT sm.*, s.name FROM settlement_mention sm
           JOIN settlement s ON s.id = sm.settlement_id
           ORDER BY sm.sentence_id, sm.char_start""").fetchall()
    names = [r["name"] for r in rows]
    # Both forms of Kaposvár in the first sentence, then the capital, the district and
    # Szentendre in the second — the district recognised from the form a speaker
    # actually uses, not from the register's own "Budapest 05. kerület".
    assert names.count("Kaposvár") == 2
    assert "Budapest" in names and "Budapest 05. kerület" in names
    assert "Szentendre" in names
    assert every_row_is_wired(rows)
    # The demonym is marked as such, so the methodology can report the mix.
    assert {r["form"] for r in rows if r["name"] == "Kaposvár"} == {"name", "demonym"}
    conn.close()


def every_row_is_wired(rows):
    return all(r["speech_uid"] and r["session_id"] and r["period_number"]
               and r["person_id"] for r in rows)


def test_a_never_mentioned_settlement_has_no_stats_row_and_reads_as_a_blind_spot(built):
    """Absence is the finding, so it is absence of a row — and the read path
    left-joins so it comes back as a zero, never as a missing settlement (TEL-7)."""
    conn = sqlite3.connect(built)
    # Every settlement in this miniature register happens to be named, so one is
    # silenced to make the blind-spot shape observable.
    conn.execute("DELETE FROM settlement_mention WHERE settlement_id = '14/001'")
    conn.commit()
    loader.rebuild_settlement_stats(conn)
    named = {r[0] for r in conn.execute(
        "SELECT settlement_id FROM settlement_stats WHERE period_number IS NULL")}
    assert "14/002" in named            # Kaposvár, mentioned
    assert "14/001" not in named        # Szentendre, now never named: no row at all
    # The settlement itself is untouched — it is the mentions that are absent, which
    # is what makes the row's absence readable as silence rather than as missing data.
    assert conn.execute("SELECT COUNT(*) FROM settlement WHERE id = '14/001'"
                        ).fetchone()[0] == 1
    conn.close()


def test_the_api_lists_and_filters_the_blind_spots(client, built, vtr):
    all_places = client.get("/api/v1/settlements", params={"period": 43}).json()
    assert all_places["built"] is True
    assert all_places["total"] == 4
    blind = client.get("/api/v1/settlements",
                       params={"period": 43, "mentioned": "no"}).json()
    names = [s["name"] for s in blind["settlements"]]
    assert names == []          # the miniature register is fully covered...
    assert blind["total"] == 0
    # ...and the complement is exactly the rest.
    named = client.get("/api/v1/settlements",
                       params={"period": 43, "mentioned": "yes"}).json()
    assert named["total"] + blind["total"] == all_places["total"]


def test_the_search_box_folds_accents(client, built, vtr):
    """§4B FOLD-1 applies here like every other text box on the site."""
    res = client.get("/api/v1/settlements", params={"q": "kaposvar"}).json()
    assert [s["name"] for s in res["settlements"]] == ["Kaposvár"]


def test_the_map_includes_the_never_mentioned_places(client, built, vtr):
    """The map's whole point is that silence is visible, so a zero-mention settlement
    is a point on it, not an omission (TEL-6/TEL-7)."""
    conn = sqlite3.connect(built)
    conn.execute("DELETE FROM settlement_mention WHERE settlement_id = '14/001'")
    conn.commit()
    loader.rebuild_settlement_stats(conn)
    conn.close()
    res = client.get("/api/v1/settlements/map", params={"period": 43}).json()
    by_name = {row[1]: row for row in res["points"]}
    assert by_name["Szentendre"][5] == 0        # drawn, at zero
    assert by_name["Kaposvár"][5] == 2
    assert res["named"] >= 1 and res["blind"] == 1
    # The basemap config travels with the payload but is not frozen into its cache.
    assert res["map"]["tile_url"]


def test_the_summary_reports_the_coverage_split(client, built, vtr):
    res = client.get("/api/v1/settlements/summary", params={"period": 43}).json()
    assert res["named"] + res["blind"] == res["settlements"] == 4
    assert res["mentions"] > 0
    assert res["source"]["geography"].startswith("Nemzeti")


def test_a_settlement_page_carries_its_citations_and_its_mp(client, built, vtr):
    detail = client.get("/api/v1/settlements/14/002", params={"period": 43}).json()
    assert detail["settlement"]["name"] == "Kaposvár"
    assert detail["mentions"] == 2
    assert detail["constituencies"] == ["Pest 4. OEVK"]
    # REP-10's join, which is why a reader who came for a count stays on the page.
    assert [r["name"] for r in detail["representatives"]] == ["Nagy Anna"]
    cites = client.get("/api/v1/settlements/14/002/mentions",
                       params={"period": 43}).json()
    assert cites["total"] == 2
    first = cites["mentions"][0]
    # Enough to open the moment it was said (VIE-5) and to highlight what matched.
    assert first["speech_uid"] and first["date"]
    assert first["text"][first["char_start"]:first["char_end"]] == first["surface"]


def test_a_blind_spot_has_a_page_rather_than_a_404(client, built, vtr):
    """It exists, it is simply never named — and that page *is* the finding, so it
    has to be linkable and citable (TEL-8)."""
    conn = sqlite3.connect(built)
    conn.execute("DELETE FROM settlement_mention WHERE settlement_id = '14/001'")
    conn.commit()
    loader.rebuild_settlement_stats(conn)
    conn.close()
    res = client.get("/api/v1/settlements/14/001", params={"period": 43})
    assert res.status_code == 200
    assert res.json()["mentions"] == 0


def test_an_unknown_settlement_is_a_404(client, built, vtr):
    assert client.get("/api/v1/settlements/99/999").status_code == 404


def test_the_own_constituency_measures_are_two_separate_numbers(client, built, vtr):
    """TEL-9. Kovács holds Budapest 1. OEVK, which in this tree holds exactly one
    settlement — the 5th district — and he names it once, alongside three mentions of
    places that are not his. So coverage is 1/1 while focus is 1 in 4: the two
    measures answer different questions and must not collapse into one number."""
    res = client.get("/api/v1/settlements/representative/k001",
                     params={"period": 43}).json()
    own = res["own"]
    assert own["constituency"] == "Budapest 1. OEVK"
    assert own["mentions"] == 4 and own["own_mentions"] == 1
    assert own["focus"] == 0.25
    assert own["own_named"] == 1 and own["own_total"] == 1
    assert own["coverage"] == 1.0
    # The list of places they name marks which are their own.
    by_name = {s["name"]: s["own"] for s in res["settlements"]}
    assert by_name["Budapest 05. kerület"] is True
    assert by_name["Kaposvár"] is False


def test_the_capital_is_excluded_from_the_focus_ratio(built):
    """Budapest spans sixteen constituencies, so it is evidence neither way about
    whether an MP talks about their own patch — and counted as "somewhere else" it
    punished exactly the Budapest members it should not."""
    conn = sqlite3.connect(built)
    conn.row_factory = sqlite3.Row
    # The capital's own mention is kept in the corpus...
    assert conn.execute(
        "SELECT COUNT(*) FROM settlement_mention WHERE person_id = 'k001' "
        "AND settlement_id = ?", (settlements.BUDAPEST_ID,)).fetchone()[0] == 1
    all_mentions = conn.execute(
        "SELECT COUNT(*) FROM settlement_mention WHERE person_id = 'k001'"
    ).fetchone()[0]
    row = conn.execute(
        "SELECT mention_count FROM person_settlement_stats WHERE person_id = 'k001'"
    ).fetchone()
    # ...but it is not in the ratio's denominator: 5 mentions, 4 of them measurable.
    assert all_mentions == 5
    assert row["mention_count"] == 4
    conn.close()


def test_a_list_mp_gets_no_row_rather_than_a_zero(built):
    """Absence of a denominator is not a score of nought (TEL-9), so the measure is
    omitted for anyone without a single-member seat."""
    conn = sqlite3.connect(built)
    assert conn.execute("SELECT COUNT(*) FROM person_settlement_stats "
                        "WHERE person_id = 'k001'").fetchone()[0] == 1
    conn.execute("UPDATE person SET constituency = 'Országos lista' "
                 "WHERE person_id = 'k001'")
    conn.execute("UPDATE person_mandate SET constituency = 'Országos lista'")
    conn.commit()
    loader._rebuild_person_settlement_stats(conn)
    assert conn.execute("SELECT COUNT(*) FROM person_settlement_stats "
                        "WHERE person_id = 'k001'").fetchone()[0] == 0
    conn.close()


def test_procedural_speeches_are_not_scanned(db_path, vtr):
    """STAT-1 applies here as everywhere — and it is also what keeps the printed
    record's own colophon ("Nyomda: … Bt., Vác") out of the counts."""
    conn = loader.connect(db_path)
    conn.execute("UPDATE speech SET procedural = 1")
    conn.execute("UPDATE sentence SET text = 'Ez Kaposváron történt.'")
    conn.commit()
    loader.rebuild_settlements(conn)
    assert loader.rebuild_settlement_mentions(conn) == 0
    conn.close()


def test_an_unreachable_register_keeps_what_is_already_stored(built, monkeypatch):
    """The electoral map is static between elections, so a stale register is a fine
    register — and an outage must cost the refresh, not the data (REP-10's rule)."""
    def dead(url):
        raise OSError("no network")
    monkeypatch.setattr(valasztas, "_default_fetch", dead)
    valasztas.reset_cache()
    conn = loader.connect(built)
    monkeypatch.setattr(loader.settings, "vtr_cache_dir", "/nonexistent-cache")
    assert loader.rebuild_settlements(conn) == 4      # the four already stored
    assert conn.execute("SELECT COUNT(*) FROM settlement").fetchone()[0] == 4
    conn.close()


def test_a_db_without_the_tables_answers_not_built_rather_than_erroring(client, db_path):
    """A DB loaded before this module existed simply has no settlements (EXT-6)."""
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        DROP TABLE IF EXISTS settlement_mention;
        DROP TABLE IF EXISTS settlement_constituency;
        DROP TABLE IF EXISTS settlement_stats;
        DROP TABLE IF EXISTS settlement_speaker_stats;
        DROP TABLE IF EXISTS person_settlement_stats;
        DROP TABLE IF EXISTS settlement;
    """)
    conn.commit()
    conn.close()
    listing = client.get("/api/v1/settlements")
    assert listing.status_code == 200
    assert listing.json() == {"settlements": [], "total": 0, "limit": 50,
                              "offset": 0, "built": False}
    assert client.get("/api/v1/settlements/map").status_code == 503


def test_a_ratio_ranking_has_a_mentions_floor(client, built, vtr):
    """A share computed over three mentions is noise, and noise sorted descending is
    worse than noise — so ranking by a ratio requires a floor, while ranking by the
    raw count (which has no denominator to distort) shows everyone."""
    ranked = client.get("/api/v1/settlements/representatives",
                        params={"period": 43, "sort": "focus"}).json()
    # Kovács has 4 measurable mentions, below the default floor of 5.
    assert ranked["min_mentions"] == 5
    assert ranked["total"] == 0 and ranked["representatives"] == []
    # ...and the floor is stated on the wire so the page can say what it is showing.
    counted = client.get("/api/v1/settlements/representatives",
                         params={"period": 43, "sort": "mentions"}).json()
    assert counted["min_mentions"] == 0
    assert [r["person_id"] for r in counted["representatives"]] == ["k001"]
    # An explicit floor overrides the default in either direction.
    opened = client.get("/api/v1/settlements/representatives",
                        params={"period": 43, "sort": "focus",
                                "min_mentions": 0}).json()
    assert [r["person_id"] for r in opened["representatives"]] == ["k001"]


# ---------------------------------------------------------------------------
# The segmented (H3) map — TEL-15
# ---------------------------------------------------------------------------

h3_only = pytest.mark.skipif(not settlements.h3_available(),
                             reason="the h3 package is not installed")


@h3_only
def test_the_matcher_module_bins_a_point_and_returns_geojson():
    """The two h3 helpers the rest of the feature is built on: a coordinate goes in,
    a cell comes out, and the cell's boundary comes back as GeoJSON — longitude first
    and the ring closed, so the frontend sees one geometry format from this whole
    feature (TEL-15)."""
    cell = settlements.h3_cell(47.4979, 19.0402, 5)
    assert cell and settlements.h3_cell(47.4979, 19.0402, 5) == cell   # deterministic
    # A different resolution is a different (nested) cell, never the same id.
    assert settlements.h3_cell(47.4979, 19.0402, 4) != cell
    polygon = settlements.h3_polygon(cell)
    ring = polygon["coordinates"][0]
    assert polygon["type"] == "Polygon"
    assert ring[0] == ring[-1]                       # closed (RFC 7946)
    assert len(ring) == 7                            # a hexagon, plus the repeat
    lon, lat = ring[0]
    assert 16 < lon < 23 and 45 < lat < 49           # longitude first, over Hungary
    # And the areas that let the UI label a resolution with what it means.
    assert settlements.h3_cell_area(4) > settlements.h3_cell_area(6)


def test_h3_helpers_are_quiet_when_the_library_is_missing(monkeypatch):
    """Its absence is not an error anywhere: the cell table is simply never built and
    the endpoint reports the view unavailable (EXT-6)."""
    monkeypatch.setattr(settlements, "h3", None)
    assert settlements.h3_available() is False
    assert settlements.h3_cell(47.5, 19.0, 5) is None
    assert settlements.h3_polygon("851e037bfffffff") is None
    assert settlements.h3_cell_area(5) is None


@h3_only
def test_cells_are_precomputed_once_per_offered_resolution(built):
    """A cell is a function of the coordinates alone, so it is derived with the
    register and never on a request (TEL-11/TEL-15)."""
    conn = sqlite3.connect(built)
    rows = conn.execute(
        "SELECT resolution, COUNT(*), COUNT(DISTINCT cell) FROM settlement_h3 "
        "GROUP BY resolution ORDER BY resolution").fetchall()
    assert [r[0] for r in rows] == list(settlements.H3_RESOLUTIONS)
    # Every settlement with coordinates is binned, at every resolution.
    placed = conn.execute(
        "SELECT COUNT(*) FROM settlement WHERE lat IS NOT NULL").fetchone()[0]
    assert all(r[1] == placed for r in rows)
    # A finer resolution never bins *more* coarsely than a coarser one.
    counts = [r[2] for r in rows]
    assert counts == sorted(counts)
    conn.close()


@h3_only
def test_a_settlement_without_coordinates_is_simply_not_binned(built):
    """A county whose geometry was unreachable leaves its settlements countable and
    searchable; they only drop off the map (SCR-5)."""
    conn = loader.connect(built)
    conn.execute("UPDATE settlement SET lat = NULL, lon = NULL WHERE id = '14/002'")
    conn.commit()
    loader.rebuild_settlement_cells(conn)
    assert conn.execute("SELECT COUNT(*) FROM settlement_h3 "
                        "WHERE settlement_id = '14/002'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM settlement_h3").fetchone()[0] > 0
    conn.close()


@h3_only
def test_the_segments_endpoint_aggregates_the_settlements_in_each_cell(client, built, vtr):
    res = client.get("/api/v1/settlements/map/segments",
                     params={"period": 43}).json()
    assert res["type"] == "FeatureCollection"
    assert res["resolution"] == settlements.H3_DEFAULT_RESOLUTION
    assert res["cells"] == len(res["features"]) >= 1
    # Every settlement in the register lands in exactly one cell, so the cells'
    # settlement counts must add up to the register — no place lost, none doubled.
    total = sum(f["properties"]["settlements"] for f in res["features"])
    assert total == 4
    # ...and so must the mentions, against the point map's own total.
    points = client.get("/api/v1/settlements/map", params={"period": 43}).json()
    assert (sum(f["properties"]["mentions"] for f in res["features"])
            == sum(p[5] for p in points["points"]))
    for f in res["features"]:
        p = f["properties"]
        # The denominator is always disclosed, and the share agrees with the counts
        # it is derived from (TEL-15).
        assert p["named"] + p["blind"] == p["settlements"]
        assert p["blind_share"] == pytest.approx(p["blind"] / p["settlements"])
        # A cell names what it is made of: a hexagon with no names in it is a shape.
        assert p["top"] and len(p["top"]) <= 3
        assert f["geometry"]["coordinates"][0][0] == f["geometry"]["coordinates"][0][-1]


@h3_only
def test_a_coarser_resolution_bins_the_same_data_into_fewer_cells(client, built, vtr):
    """H3 cells nest, so a coarser reading is the same data re-binned — the totals
    must survive it exactly (TEL-15)."""
    fine = client.get("/api/v1/settlements/map/segments",
                      params={"period": 43, "resolution": max(settlements.H3_RESOLUTIONS)}).json()
    coarse = client.get("/api/v1/settlements/map/segments",
                        params={"period": 43, "resolution": min(settlements.H3_RESOLUTIONS)}).json()
    assert coarse["cells"] <= fine["cells"]
    assert (sum(f["properties"]["settlements"] for f in coarse["features"])
            == sum(f["properties"]["settlements"] for f in fine["features"]))
    assert (sum(f["properties"]["mentions"] for f in coarse["features"])
            == sum(f["properties"]["mentions"] for f in fine["features"]))


@h3_only
def test_an_unoffered_resolution_is_refused_rather_than_served(client, built, vtr):
    """The offered set is deployment config, so a hand-written resolution outside it
    is a 400 naming the set — not a silently different map."""
    res = client.get("/api/v1/settlements/map/segments",
                     params={"period": 43, "resolution": 9})
    assert res.status_code == 400
    assert "PARLAMONITOR_H3_RESOLUTIONS" in res.json()["detail"]


@h3_only
def test_the_point_map_advertises_which_binnings_are_available(client, built, vtr):
    """So the page offers only the options the server can build, without a second round
    trip (EXT-6)."""
    res = client.get("/api/v1/settlements/map", params={"period": 43}).json()
    assert res["segments"]["available"] is True
    # Both binnings, constituencies first — the order the UI offers them in.
    assert res["segments"]["bins"] == ["oevk", "h3"]
    assert res["segments"]["default"] == settlements.H3_DEFAULT_RESOLUTION
    offered = res["segments"]["resolutions"]
    assert [r["resolution"] for r in offered] == list(settlements.H3_RESOLUTIONS)
    # Each option carries its cell size, so the UI can label it with what it means.
    assert all(r["area_km2"] > 0 for r in offered)


def test_without_h3_the_hexagon_binning_is_unavailable_not_empty(client, built, vtr,
                                                                 monkeypatch):
    """An honest 503 and an unadvertised option, never a blank map — and neither the
    point map nor the *other* binning is touched (EXT-6, SCR-5)."""
    from app.modules.settlements import router as settlements_router
    monkeypatch.setattr(settlements_router.settlements, "h3", None)
    assert client.get("/api/v1/settlements/map/segments",
                      params={"bins": "h3"}).status_code == 503
    point_map = client.get("/api/v1/settlements/map", params={"period": 43}).json()
    assert point_map["segments"]["bins"] == ["oevk"]  # the one that needs no library
    assert point_map["segments"]["available"] is True
    assert point_map["points"]                       # the map itself still works
    assert client.get("/api/v1/settlements/map/segments",
                      params={"bins": "oevk"}).status_code == 200


# ---------------------------------------------------------------------------
# The constituency binning — TEL-16
# ---------------------------------------------------------------------------

def test_a_boundary_is_generalised_but_stays_a_closed_ring():
    """The office draws constituency boundaries for a street-level map; the segmented
    view shows all 106 at once. Generalising is what makes that a payload rather than
    two megabytes — and a ring that comes out of it has to still be a ring."""
    ring = valasztas._ring(_square_spec(47.5, 19.0))
    assert ring[0] == ring[-1] and len(ring) == 9        # closed, midpoints included
    out = valasztas.simplify(ring, 0.002)
    assert out[0] == out[-1]                             # still closed (RFC 7946)
    assert 4 <= len(out) < len(ring)                     # collinear midpoints dropped
    # The corners themselves survive: generalising must not move the shape.
    corners = {(round(x, 4), round(y, 4)) for x, y in out}
    assert (round(19.1, 4), round(47.6, 4)) in corners


def test_a_ring_that_would_collapse_is_left_alone():
    """Half a boundary is worse than a heavy one, so a shape with nothing to spare is
    returned verbatim rather than as a degenerate polygon."""
    triangle = [[19.0, 47.5], [19.1, 47.5], [19.05, 47.6], [19.0, 47.5]]
    assert valasztas.simplify(triangle, 10) == triangle
    assert valasztas.simplify(triangle, 0) == triangle    # tolerance 0 = verbatim


def test_a_renamed_county_is_joined_under_both_spellings():
    """Csongrád became Csongrád-Csanád in 2020 and parlament.hu never relabelled the
    seats, so matching on the string alone left all four constituencies around Szeged
    with no member. The current name is what is stored and shown; the old one is only
    ever a key to join on."""
    assert valasztas.label_variants("Csongrád-Csanád 2. OEVK") == [
        "Csongrád-Csanád 2. OEVK", "Csongrád 2. OEVK"]
    # Anything not renamed is its own only spelling — no speculative variants.
    assert valasztas.label_variants("Pest 4. OEVK") == ["Pest 4. OEVK"]


def test_the_alias_reaches_the_seat_to_member_join(client, built, vtr, monkeypatch):
    """Not merely that the variants exist, but that the join uses them: a settlement
    whose register label differs from its member's record still lands the reader on the
    member, reported under the register's current spelling."""
    monkeypatch.setitem(valasztas.COUNTY_ALIASES, "Pest-Nógrád", ("Pest",))
    conn = sqlite3.connect(built)
    conn.execute("UPDATE settlement_constituency SET label = 'Pest-Nógrád 4. OEVK' "
                 "WHERE label = 'Pest 4. OEVK'")
    conn.commit()
    conn.close()
    # `person.constituency` still says "Pest 4. OEVK" — the old spelling.
    detail = client.get("/api/v1/settlements/14/002", params={"period": 43}).json()
    assert detail["constituencies"] == ["Pest-Nógrád 4. OEVK"]
    assert [r["name"] for r in detail["representatives"]] == ["Nagy Anna"]
    # Reported under the register's name, not the MP record's older one.
    assert detail["representatives"][0]["constituency"] == "Pest-Nógrád 4. OEVK"


def test_the_constituency_register_is_stored_with_its_boundary(built):
    """Derived with the register, never on a request: a boundary is a function of the
    register version alone, and Douglas–Peucker over 99 000 vertices has no business on
    a request path (TEL-11)."""
    conn = sqlite3.connect(built)
    conn.row_factory = sqlite3.Row
    rows = {r["label"]: r for r in conn.execute("SELECT * FROM constituency")}
    assert set(rows) == {"Budapest 1. OEVK", "Pest 4. OEVK"}
    seat = rows["Pest 4. OEVK"]
    assert seat["seat"] == "Szentendre" and seat["electorate"] == 68000
    assert seat["county"] == "Pest" and seat["number"] == 4
    # The office's own centre point, and the boundary as a stored GeoJSON ring.
    assert seat["lat"] == pytest.approx(47.6667)
    ring = json.loads(seat["boundary"])
    assert ring[0] == ring[-1]
    lon, lat = ring[0]
    assert 16 < lon < 23 and 45 < lat < 49            # longitude first, over Hungary
    conn.close()


def test_the_constituency_segments_aggregate_the_settlements_in_each_seat(client, built,
                                                                         vtr):
    res = client.get("/api/v1/settlements/map/segments",
                     params={"bins": "oevk", "period": 43}).json()
    assert res["type"] == "FeatureCollection" and res["bins"] == "oevk"
    assert res["cells"] == len(res["features"]) == 2
    cells = {f["properties"]["label"]: f["properties"] for f in res["features"]}
    # Kovács's seat holds the 5th district (named once); Nagy's holds Szentendre and
    # Kaposvár (named once and twice).
    assert cells["Budapest 1. OEVK"]["settlements"] == 1
    assert cells["Budapest 1. OEVK"]["mentions"] == 1
    assert cells["Pest 4. OEVK"]["settlements"] == 2
    assert cells["Pest 4. OEVK"]["mentions"] == 3
    for props in cells.values():
        # The denominator is always disclosed, and agrees with the counts it is from.
        assert props["named"] + props["blind"] == props["settlements"]
        assert props["blind_share"] == pytest.approx(props["blind"] / props["settlements"])
        # A cell names what it is made of, its seat town and who holds it — a
        # constituency, unlike a hexagon, is a thing a reader can act on.
        assert props["top"] and len(props["top"]) <= 3
        assert props["seat"] and props["electorate"]
        assert props["mp"]["name"] and props["mp"]["person_id"]
    # TEL-9's measure, per seat, with its own denominator beside it — never as a shade.
    own = cells["Budapest 1. OEVK"]
    assert own["own_named"] == 1 and own["own_total"] == 1
    # The ramp's floor as well as its ceiling: every constituency holds settlements, so
    # the quietest is nowhere near zero and a ramp anchored there wastes half of itself.
    assert res["min_mentions"] == 1 and res["max_mentions"] == 3


def test_a_split_settlement_is_counted_in_each_of_its_constituencies(client, built, vtr):
    """A mention names the *place*, never the part of it that falls in one seat — as
    Debrecen really does sit in three. So it counts in each, the cells therefore do not
    sum to the national total, and the payload says so rather than leaving a reader to
    add them up (TEL-16)."""
    conn = sqlite3.connect(built)
    conn.execute("INSERT INTO settlement_constituency(settlement_id, label, number) "
                 "VALUES ('14/002', 'Budapest 1. OEVK', 1)")
    conn.commit()
    conn.close()
    res = client.get("/api/v1/settlements/map/segments",
                     params={"bins": "oevk", "period": 43}).json()
    cells = {f["properties"]["label"]: f["properties"] for f in res["features"]}
    # Kaposvár's two mentions now appear in both seats...
    assert cells["Budapest 1. OEVK"]["mentions"] == 3      # was 1
    assert cells["Pest 4. OEVK"]["mentions"] == 3
    # ...each of which says which of its settlements it shares.
    assert cells["Budapest 1. OEVK"]["shared"] == 1
    assert cells["Pest 4. OEVK"]["shared"] == 1
    # ...and the overlap is reported, so the sum is never mistaken for the total.
    assert res["overlap"] == {"settlements": 1, "mentions": 2}
    point_map = client.get("/api/v1/settlements/map", params={"period": 43}).json()
    assert (sum(f["properties"]["mentions"] for f in res["features"])
            > sum(p[5] for p in point_map["points"]))


def test_the_capital_as_a_whole_belongs_to_no_constituency(client, built, vtr):
    """Budapest spans sixteen seats, so its mentions — the largest single figure in the
    corpus — are attributed to none of them. Reported as `unattributed`, because a quiet
    capital would otherwise be the map's most visible and most wrong claim (TEL-16)."""
    res = client.get("/api/v1/settlements/map/segments",
                     params={"bins": "oevk", "period": 43}).json()
    loose = res["unattributed"]
    assert loose["settlements"] == 1 and "Budapest" in loose["names"]
    assert loose["mentions"] == 1        # "Budapesten", in the seeded sentence
    # It is in no cell, but its districts still are.
    assert not any(s["name"] == "Budapest"
                   for f in res["features"] for s in f["properties"]["top"])
    cells = {f["properties"]["label"]: f["properties"] for f in res["features"]}
    assert ([s["name"] for s in cells["Budapest 1. OEVK"]["top"]]
            == ["Budapest 05. kerület"])


def test_without_boundaries_the_constituency_binning_is_unavailable_not_empty(
        client, built, vtr):
    """The names and the geometry come from different upstream files, so an unreachable
    polygon file costs the map and not the rest: the binning is simply not offered,
    rather than drawn as a country with holes in it (EXT-6, SCR-5)."""
    conn = sqlite3.connect(built)
    conn.execute("UPDATE constituency SET boundary = NULL")
    conn.commit()
    conn.close()
    res = client.get("/api/v1/settlements/map/segments", params={"bins": "oevk"})
    assert res.status_code == 503
    point_map = client.get("/api/v1/settlements/map", params={"period": 43}).json()
    assert "oevk" not in point_map["segments"]["bins"]
    assert point_map["points"]                       # the map itself still works


def test_an_unreachable_boundary_source_keeps_the_constituencies_it_has(built,
                                                                       monkeypatch):
    """Degrades a layer at a time, like the register beside it: a failed refresh leaves
    the rows already stored rather than emptying them (SCR-5)."""
    conn = loader.connect(built)
    before = conn.execute("SELECT COUNT(*) FROM constituency").fetchone()[0]
    monkeypatch.setattr(valasztas, "index",
                        lambda **kw: (_ for _ in ()).throw(
                            valasztas.LookupUnavailable("offline")))
    assert loader.rebuild_constituencies(conn) == before
    assert conn.execute("SELECT COUNT(*) FROM constituency").fetchone()[0] == before
    conn.close()


# ---------------------------------------------------------------------------
# "Ki képviseli?" — the seat holder is resolved for ONE cycle (REP-10)
# ---------------------------------------------------------------------------

def _add_predecessor(db_path, seat="Pest 4. OEVK", period=42):
    """A second person with the same stored seat, sitting one cycle earlier.

    This is the ordinary case in the real registry, not an edge one: `person.constituency`
    records the seat a person *held*, so 95 of its 138 seat labels belong to more than
    one person — *Fejér 4. OEVK* to five.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT OR IGNORE INTO electoral_period(number, label) VALUES (?,?)",
                 (period, f"{period}. ciklus"))
    conn.execute("INSERT INTO person(person_id, label, constituency) VALUES (?,?,?)",
                 ("x001", "Előd Elek", seat))
    conn.execute("INSERT INTO membership(person_id, period_number) VALUES (?,?)",
                 ("x001", period))
    conn.commit()
    conn.close()


def _rep_of(client, params):
    """The member the settlement page names for Kaposvár, with the cycle it answers
    for."""
    detail = client.get("/api/v1/settlements/14/002", params=params).json()
    return [(r["name"], r["period_number"]) for r in detail["representatives"]]


def test_the_member_shown_is_the_holder_in_the_latest_selected_cycle(client, built, vtr):
    """A seat has one holder per cycle but several over the corpus, so the label alone
    is not an answer — matched on the label, the page reported whichever row the table
    happened to return first, which was neither current nor stable between two identical
    requests. The cycle is resolved first, and the answer is the holder in it."""
    _add_predecessor(built)
    assert _rep_of(client, {"period": 43}) == [("Nagy Anna", 43)]
    assert _rep_of(client, {"period": 42}) == [("Előd Elek", 42)]
    # A multi-cycle scope has several answers and the page can show one: the latest,
    # because a reader asking who represents their town means now.
    assert _rep_of(client, {"period": [42, 43]}) == [("Nagy Anna", 43)]
    # "All cycles" is the same question — the latest cycle in the data answers it.
    assert _rep_of(client, {}) == [("Nagy Anna", 43)]


def test_a_seat_with_no_holder_in_that_cycle_is_omitted_not_back_filled(client, built,
                                                                       vtr):
    """The seat labels come from the register of the **current** map, so answering from
    the nearest cycle that happens to have a holder reaches past a redistricting and
    names the member of a differently drawn constituency of the same name (on the real
    corpus it reached three boundary sets back). Silence is the honest answer, and the
    page names the constituency it could not answer for."""
    _add_predecessor(built)
    conn = sqlite3.connect(built)
    conn.execute("DELETE FROM membership WHERE person_id = 'n002'")
    conn.commit()
    conn.close()
    assert _rep_of(client, {"period": 43}) == []
    # The seat itself is still reported, so the page can say what it is.
    detail = client.get("/api/v1/settlements/14/002", params={"period": 43}).json()
    assert detail["constituencies"] == ["Pest 4. OEVK"]
    # ...and the predecessor is still the answer for the cycle they actually sat in.
    assert _rep_of(client, {"period": 42}) == [("Előd Elek", 42)]


def test_the_mandate_history_outranks_the_stored_seat(client, built, vtr):
    """`person_mandate` is per cycle and authoritative (REP-14); `person.constituency`
    is a point-in-time field corroborated by `membership`, used only where the history
    has no answer."""
    _add_predecessor(built)
    conn = sqlite3.connect(built)
    conn.execute("INSERT INTO person_mandate(person_id, period_number, constituency) "
                 "VALUES ('x001', 43, 'Pest 4. OEVK')")
    conn.commit()
    conn.close()
    assert _rep_of(client, {"period": 43}) == [("Előd Elek", 43)]


def test_the_constituency_map_names_the_same_member_as_the_page(client, built, vtr):
    """One resolver, so a cell's tooltip and the settlement's page cannot disagree about
    who holds the seat — or about which cycle they are answering for (TEL-16)."""
    _add_predecessor(built)
    for period, expected in ((43, "Nagy Anna"), (42, "Előd Elek")):
        res = client.get("/api/v1/settlements/map/segments",
                         params={"bins": "oevk", "period": period}).json()
        cells = {f["properties"]["label"]: f["properties"] for f in res["features"]}
        assert cells["Pest 4. OEVK"]["mp"]["name"] == expected
        assert (_rep_of(client, {"period": period})[0][0]
                == cells["Pest 4. OEVK"]["mp"]["name"])
