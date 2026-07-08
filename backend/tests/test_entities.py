"""Entity linking (NEL, §10): NER span extraction, Wikidata candidate resolution,
and K-Monitor tag matching (persons + institutions).

All resolver logic runs fully offline via injected fetchers; the HuSpaCy
extraction tests are skipped when the (optional) model isn't installed.
"""
import pytest

from app import kmonitor, loader, nlp, wikidata


# --- Fixtures: fake Wikidata + K-Monitor sources ---------------------------

def _fake_wd_fetch(names, kind):
    """Stand-in for the Wikidata query: (names, kind) -> candidate items."""
    per = {
        "Kovács Béla": [  # matches MP k001 (wikidata_id Q42 in the reps fixture)
            {"qid": "Q42", "sitelinks": 30, "wikipedia_url": "https://hu.wikipedia.org/wiki/Kovacs",
             "label": "Kovács Béla", "description": "politikus"}],
        "Orbán Viktor": [
            {"qid": "Q57641", "sitelinks": 116, "wikipedia_url": "https://hu.wikipedia.org/wiki/Orban",
             "label": "Orbán Viktor", "description": "miniszterelnök"}],
        "Nagy István": [  # ambiguous: two humans share the name (no K-Monitor tag)
            {"qid": "Q100", "sitelinks": 5, "wikipedia_url": "https://hu.wikipedia.org/wiki/Nagy_festo",
             "label": "Nagy István", "description": "festő"},
            {"qid": "Q200", "sitelinks": 20, "wikipedia_url": "https://hu.wikipedia.org/wiki/Nagy_min",
             "label": "Nagy István", "description": "miniszter"}],
    }
    org = {
        "Alkotmánybíróság": [  # institution absent from K-Monitor → Wikipedia fallback
            {"qid": "Q300", "sitelinks": 10, "wikipedia_url": "https://hu.wikipedia.org/wiki/Ab",
             "label": "Alkotmánybíróság", "description": "testület"}],
    }
    src = per if kind == "PER" else org
    return {n: src[n] for n in names if n in src}


_PERSONS_HTML = """
<div class="keywords-list__item"><a href="adatbazis/cimkek/orban-viktor">Orbán Viktor</a> (1996)</div>
<div class="keywords-list__item"><a href="adatbazis/cimkek/meszaros-lorinc">Mészáros Lőrinc</a> (3034)</div>
<div class="keywords-list__item"><a href="adatbazis/cimkek/kovacs-bela">Kovács Béla</a> (12)</div>
"""
_INSTITUTIONS_HTML = """
<div class="keywords-list__item"><a href="adatbazis/cimkek/magyar-nemzeti-bank-mnb">Magyar Nemzeti Bank (MNB)</a> (1000)</div>
<div class="keywords-list__item"><a href="adatbazis/cimkek/fidesz">Fidesz</a> (4346)</div>
"""


def _index():
    return kmonitor._build_index(_PERSONS_HTML, _INSTITUTIONS_HTML)


def _seed_entity(conn, sentence_id, key, surface, kind="PER"):
    conn.execute(
        "INSERT INTO entity(sentence_id, entity_key, surface, char_start, char_end, kind) "
        "VALUES (?,?,?,?,?,?)", (sentence_id, key, surface, 0, len(surface), kind))


# --- K-Monitor tag index parsing -------------------------------------------

def test_kmonitor_index_parses_persons_and_institution_surfaces():
    idx = _index()
    # A person tag is found under its normalized name.
    assert any(t["slug"] == "orban-viktor" for t in idx["orbán viktor"])
    # An institution with a parenthetical acronym is reachable by BOTH the full
    # base name and the acronym.
    assert any(t["slug"] == "magyar-nemzeti-bank-mnb" for t in idx["magyar nemzeti bank"])
    assert any(t["slug"] == "magyar-nemzeti-bank-mnb" for t in idx["mnb"])
    # Kinds are tagged so PER/ORG matching stays separate.
    assert idx["fidesz"][0]["kind"] == "org"
    assert idx["kovács béla"][0]["kind"] == "person"


def test_kmonitor_parenthetical_qualifier_is_not_a_key():
    """A proper-noun qualifier in parentheses ("Planet TV (Szlovénia)") must NOT
    become its own match key — otherwise the country name "Szlovénia" wrongly
    resolves to that unrelated media tag (the false match reported from the viewer).
    Acronyms and multi-word expansions in parentheses stay indexed."""
    html_ = (
        '<div class="keywords-list__item"><a href="adatbazis/cimkek/planet-tv-szlovenia">'
        'Planet TV (Szlovénia)</a> (3)</div>\n'
        '<div class="keywords-list__item"><a href="adatbazis/cimkek/orszagos-birosagi-hivatal-obh">'
        'Országos Bírósági Hivatal (OBH)</a> (50)</div>\n'
        '<div class="keywords-list__item"><a href="adatbazis/cimkek/bkv">'
        'BKV (Budapesti Közlekedési Vállalat)</a> (77)</div>'
    )
    idx = kmonitor._build_index("", html_)
    # The qualifier is gone; the real name and its leading run remain.
    assert "szlovénia" not in idx
    assert "planet tv" in idx
    # An acronym qualifier still resolves (both the acronym and the full name).
    assert any(t["slug"] == "orszagos-birosagi-hivatal-obh" for t in idx["obh"])
    assert any(t["slug"] == "orszagos-birosagi-hivatal-obh"
               for t in idx["országos bírósági hivatal"])
    # A multi-word expansion in parentheses still resolves.
    assert any(t["slug"] == "bkv" for t in idx["bkv"])
    assert any(t["slug"] == "bkv" for t in idx["budapesti közlekedési vállalat"])


def test_kmonitor_load_index_uses_injected_fetch(monkeypatch):
    monkeypatch.setattr(kmonitor.settings, "kmonitor_links", True)

    def _fetch(url):
        return _PERSONS_HTML if "szemelyek" in url else _INSTITUTIONS_HTML

    idx = kmonitor.load_index(None, fetch=_fetch)
    assert "orbán viktor" in idx and "mnb" in idx


def test_kmonitor_load_index_disabled_is_empty(monkeypatch):
    monkeypatch.setattr(kmonitor.settings, "kmonitor_links", False)
    assert kmonitor.load_index(None, fetch=lambda url: _PERSONS_HTML) == {}


# --- Resolution: K-Monitor primary, Wikipedia fallback, ambiguity, MP link --

def test_resolve_links_kmonitor_primary_wikipedia_fallback(conn, monkeypatch):
    monkeypatch.setattr(wikidata.settings, "entity_links", True)
    monkeypatch.setattr(kmonitor.settings, "kmonitor_links", True)
    sid = conn.execute("SELECT id FROM sentence WHERE speech_id='43001-1' LIMIT 1").fetchone()[0]
    _seed_entity(conn, sid, "Kovács Béla", "Kovács Béla")            # MP + K-Monitor person
    _seed_entity(conn, sid, "Orbán Viktor", "Orbán Viktornak")       # K-Monitor person, not MP
    _seed_entity(conn, sid, "Nagy István", "Nagy István")            # no K-Monitor, ambiguous WP
    _seed_entity(conn, sid, "Magyar Nemzeti Bank", "Magyar Nemzeti Bankban", "ORG")  # K-Monitor org
    _seed_entity(conn, sid, "MNB", "MNB", "ORG")                     # K-Monitor org via acronym
    _seed_entity(conn, sid, "Alkotmánybíróság", "Alkotmánybíróság", "ORG")  # org WP fallback
    _seed_entity(conn, sid, "Ismeretlen Valaki", "Ismeretlen Valaki")       # nothing
    conn.commit()

    kinds = loader._entity_kinds(conn)
    wd = wikidata.resolve_candidates(conn, None, kinds, fetch=_fake_wd_fetch)
    linked = kmonitor.resolve_links(conn, kinds, wd, _index())
    assert linked == 6  # everything but "Ismeretlen Valaki"

    import json
    rows = {r["entity_key"]: {"kind": r["kind"], "ambiguous": r["ambiguous"],
                              "links": json.loads(r["links_json"])}
            for r in conn.execute("SELECT * FROM entity_link")}

    def types(key):
        return [l["type"] for l in rows[key]["links"]]

    # MP + K-Monitor person: internal profile first, then the K-Monitor tag; no
    # Wikipedia (K-Monitor found).
    assert types("Kovács Béla") == ["profile", "kmonitor"]
    assert rows["Kovács Béla"]["links"][0]["person_id"] == "k001"
    assert "kovacs-bela" in rows["Kovács Béla"]["links"][1]["url"]
    assert rows["Kovács Béla"]["ambiguous"] == 0

    # Non-MP with a K-Monitor tag → just the K-Monitor link (primary), no Wikipedia.
    assert types("Orbán Viktor") == ["kmonitor"]

    # No K-Monitor tag → Wikipedia fallback, ambiguous, ordered by sitelinks.
    assert types("Nagy István") == ["wikipedia", "wikipedia"]
    assert rows["Nagy István"]["ambiguous"] == 1
    assert rows["Nagy István"]["links"][0]["wikidata_id"] == "Q200"  # 20 > 5 sitelinks

    # Institution matched in K-Monitor by full name AND by acronym.
    assert types("Magyar Nemzeti Bank") == ["kmonitor"]
    assert types("MNB") == ["kmonitor"]
    assert rows["Magyar Nemzeti Bank"]["kind"] == "ORG"

    # Institution absent from K-Monitor → Wikipedia fallback (a non-human item).
    assert types("Alkotmánybíróság") == ["wikipedia"]

    # No destination at all → no row (stays unlinked in the transcript).
    assert "Ismeretlen Valaki" not in rows


def test_resolve_candidates_disabled_is_noop(conn, monkeypatch):
    monkeypatch.setattr(wikidata.settings, "entity_links", False)
    assert wikidata.resolve_candidates(conn, None, {"Orbán Viktor": "PER"},
                                       fetch=_fake_wd_fetch) == {}


def test_resolve_representatives_sets_kmonitor_url(conn, monkeypatch):
    monkeypatch.setattr(kmonitor.settings, "kmonitor_links", True)
    matched = kmonitor.resolve_representatives(conn, _index())
    assert matched == 1  # only Kovács Béla is in the K-Monitor person list
    k = conn.execute("SELECT kmonitor_url FROM person WHERE person_id='k001'").fetchone()
    assert k["kmonitor_url"] and "kovacs-bela" in k["kmonitor_url"]
    # An MP with no K-Monitor tag keeps a NULL link.
    n = conn.execute("SELECT kmonitor_url FROM person WHERE person_id='n002'").fetchone()
    assert n["kmonitor_url"] is None


# --- Endpoint ---------------------------------------------------------------

def test_speech_text_endpoint_returns_entity_links(conn, client, monkeypatch):
    """The transcript endpoint annotates a speech with its resolved destinations."""
    monkeypatch.setattr(wikidata.settings, "entity_links", True)
    monkeypatch.setattr(kmonitor.settings, "kmonitor_links", True)
    sid = conn.execute("SELECT id FROM sentence WHERE speech_id='43001-1' LIMIT 1").fetchone()[0]
    _seed_entity(conn, sid, "Orbán Viktor", "Orbán Viktor")
    conn.commit()
    kinds = loader._entity_kinds(conn)
    wd = wikidata.resolve_candidates(conn, None, kinds, fetch=_fake_wd_fetch)
    kmonitor.resolve_links(conn, kinds, wd, _index())

    d = client.get("/api/v1/proceedings/speeches/43001-1/text").json()
    ents = {e["surface"]: e for e in d["entities"]}
    assert "Orbán Viktor" in ents
    e = ents["Orbán Viktor"]
    assert e["kind"] == "PER"
    assert e["ambiguous"] is False
    assert e["links"][0]["type"] == "kmonitor"  # K-Monitor is the primary target
    assert "orban-viktor" in e["links"][0]["url"]
    # A speech with no recognized names simply carries an empty list.
    assert client.get("/api/v1/proceedings/speeches/43001-2/text").json()["entities"] == []


# --- HuSpaCy PER + ORG span extraction (skipped without the model) ----------

@pytest.mark.skipif(not nlp.available(), reason="HuSpaCy model not installed")
def test_entity_spans_finds_people_and_institutions():
    text = "Orbán Viktor a Fideszről és az Alkotmánybíróságról beszélt Brüsszelben."
    spans = list(nlp.entity_spans([text]))[0]
    by_kind: dict[str, set] = {}
    for surface, start, end, key, kind in spans:
        by_kind.setdefault(kind, set()).add(key)
        assert text[start:end] == surface   # offsets line up with the surface
    assert "Orbán Viktor" in by_kind.get("PER", set())
    # Institutions (ORG) are now captured too — at least one of the two orgs.
    assert by_kind.get("ORG")


@pytest.mark.skipif(not nlp.available(), reason="HuSpaCy model not installed")
def test_person_spans_wrapper_is_person_only():
    text = "Orbán Viktor a Fideszről beszélt."
    spans = list(nlp.person_spans([text]))[0]
    keys = {s[3] for s in spans}
    assert "Orbán Viktor" in keys and "Fidesz" not in keys
    # Wrapper drops the kind, yielding 4-tuples.
    assert all(len(s) == 4 for s in spans)


@pytest.mark.skipif(not nlp.available(), reason="HuSpaCy model not installed")
def test_entity_spans_normalizes_inflection():
    spans = list(nlp.entity_spans(["Orbán Viktornak üzent."]))[0]
    assert spans and spans[0][3] == "Orbán Viktor"
    assert spans[0][0] == "Orbán Viktornak" and spans[0][4] == "PER"
