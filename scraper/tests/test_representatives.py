"""Offline tests for the MP registry stage (`parlamonitor.representatives`).

Focused on REP-13 — the asset declarations (*vagyonnyilatkozatok*) and the CV —
because both are assembled here rather than returned ready-made: the declarations
come from three separate upstream queries that have to be merged into one series,
and the CV is a static PDF whose URL we may only publish once it is known to
resolve. Driven against a fake Felicitas client, so no network is touched.
"""

from __future__ import annotations

from parlamonitor.representatives import scrape as reps

SERVER = "http://www.parlament.hu"


def _decl(path, text, asset_date, deadline="2026-01-31", submitted="Igen",
          filed="2026-01-20T10:00:00Z"):
    """One row as the vagyonnyilatkozat queries return it (field names verified
    against the live API, 2026-08)."""
    return {"vagyonnyilatkozatNyilvanosSzerver": SERVER,
            "vagyonnyilatkozatFileNev": path,
            "vagyonnyilatkozatFileNevSzoveg": text,
            "vagyoniAllapot": asset_date, "beadasiHatarido": deadline,
            "beadva": submitted, "vagyonmegjegyzes": None,
            "benyujtasDatuma": filed}


ROSTER = [{"kepviseloId": "k001", "kepviseloNev": "Kovács Béla",
           "kepviseloNevRendezeshez": "Kovács Béla",
           "frakcioId": 7, "frakcioNev": "Fidesz"}]


class FakeFelicitas:
    """Answers the roster + per-MP detail queries, and records CV probes."""

    def __init__(self, details=None, cvs=()):
        self.details = details or {}
        self.cvs = set(cvs)          # ids that HAVE a published CV
        self.cv_probes: list[str] = []
        self.http = object()

    def cycle_ranges(self):
        return {43: {"start": "2026-05-09", "end": None}}

    def representative_list(self, cycle, start, end):
        return [dict(r) for r in ROSTER]

    def representative_detail(self, query, person_id):
        return [dict(r) for r in self.details.get(query, [])]

    def cv_url(self, person_id):
        self.cv_probes.append(person_id)
        return (f"https://www.parlament.hu/kepv/eletrajz/hu/{person_id}.pdf"
                if person_id in self.cvs else None)


def test_declarations_merge_the_three_queries_newest_first():
    """Upstream splits the declarations across three queries by disclosure regime;
    the profile wants one series, newest first by the date the declared assets were
    held (REP-13) — not by when the filing happened to be recorded."""
    rec = {}
    reps.apply_details(rec, {
        "kepviselo-vagyon-nyilatkozata-query": [
            _decl("/2011/k001_j0111231k.pdf", "Vagyonnyilatkozat 2011", "2011-12-31"),
            _decl("/2022/0611k001_j0220502k.pdf", "Vagyonnyilatkozat 2022", "2022-05-02")],
        "kepviselo-vagyon-nyilatkozata2022query": [
            _decl("/2022/0622k001_j0220805k.pdf",
                  "Eredeti vagyonnyilatkozat - 2022. augusztus 05.", "2022-08-05")],
        "kepviselo-vagyon-nyilatkozata2023query": [
            _decl("/2024/0748k001_j0241231k.pdf", "Vagyonnyilatkozat 2024", "2024-12-31")],
    })
    assert [d["assetDate"] for d in rec["assetDeclarations"]] == [
        "2024-12-31", "2022-08-05", "2022-05-02", "2011-12-31"]
    # The URL is the adatlap's own recipe (server + /vagynyil + path), served over
    # https even though upstream still names the server over plain http.
    assert rec["assetDeclarations"][0]["url"] == (
        "https://www.parlament.hu/vagynyil/2024/0748k001_j0241231k.pdf")


def test_declaration_reported_by_two_queries_is_kept_once():
    row = _decl("/2024/0748k001_j0241231k.pdf", "Vagyonnyilatkozat 2024", "2024-12-31")
    rec = {}
    reps.apply_details(rec, {"kepviselo-vagyon-nyilatkozata-query": [dict(row)],
                             "kepviselo-vagyon-nyilatkozata2023query": [dict(row)]})
    assert len(rec["assetDeclarations"]) == 1


def test_unpublished_declaration_is_kept_without_a_link():
    """A declaration that was due but never published stays in the series as a
    dated, unlinked entry (REP-13) — that absence is the fact worth seeing."""
    rec = {}
    reps.apply_details(rec, {"kepviselo-vagyon-nyilatkozata2023query": [
        _decl(None, "Vagyonnyilatkozat 2025", "2025-12-31",
              submitted="Nem", filed=None)]})
    [d] = rec["assetDeclarations"]
    assert d["url"] is None
    assert (d["submitted"], d["deadline"]) == ("Nem", "2026-01-31")


def test_no_declarations_is_an_empty_list_not_a_missing_key():
    """The loader writes whatever is here verbatim, so "none on record" must be an
    empty list — not an absent key, which would read as "not scraped"."""
    rec = {}
    reps.apply_details(rec, {})
    assert rec["assetDeclarations"] == []


def test_cv_is_only_published_once_it_is_known_to_resolve():
    """The CV URL is derivable from the person id, but publication is the MP's own
    choice — so it is only carried when the probe found the file (REP-13)."""
    fake = FakeFelicitas(cvs={"k001"})
    rec = {"personID": "k001", "active": True}
    reps.apply_cv(fake, rec)
    assert rec["cvUrl"].endswith("/kepv/eletrajz/hu/k001.pdf")

    missing = {"personID": "k001", "active": True}
    reps.apply_cv(FakeFelicitas(), missing)
    assert "cvUrl" not in missing


def test_cv_is_not_probed_for_someone_who_no_longer_sits():
    """The House takes the CV down when the mandate ends, so a former MP's probe
    would be a request spent to learn nothing (SCR-4)."""
    fake = FakeFelicitas(cvs={"k001"})
    rec = {"personID": "k001", "active": False}
    reps.apply_cv(fake, rec)
    assert fake.cv_probes == [] and "cvUrl" not in rec


def test_cv_probe_failure_leaves_the_record_without_one():
    class Boom(FakeFelicitas):
        def cv_url(self, person_id):
            raise RuntimeError("network")

    rec = {"personID": "k001", "active": True}
    reps.apply_cv(Boom(), rec)
    assert "cvUrl" not in rec


def test_registry_carries_declarations_and_cv():
    """End to end through `fetch_representatives`: both land on the record the
    loader reads, and the CV is probed once per MP."""
    fake = FakeFelicitas(
        details={"kepviselo-adatok-query": [{"kepviseloAktivE": True}],
                 "kepviselo-vagyon-nyilatkozata2023query": [
                     _decl("/2024/0748k001_j0241231k.pdf",
                           "Vagyonnyilatkozat 2024", "2024-12-31")]},
        cvs={"k001"})
    reg = reps.fetch_representatives(fake, 43, link_wikidata=False)
    rec = reg["data"][0]
    assert len(rec["assetDeclarations"]) == 1
    assert rec["cvUrl"].endswith("k001.pdf")
    assert fake.cv_probes == ["k001"]


def test_registry_carries_the_wikidata_birth_date_and_sign(monkeypatch):
    """The roster has no birth date of its own, so it comes from the same P4966
    join as the links, sign already derived (a display-time recompute would have
    to re-answer the same question for every reader)."""
    monkeypatch.setattr(reps.wikidata, "fetch_mp_links", lambda http: {
        "k001": {"wikidataId": "Q42",
                 "wikipediaUrl": "https://hu.wikipedia.org/wiki/K",
                 "dateOfBirth": "1968-08-30", "zodiacSign": "virgo"}})
    reg = reps.fetch_representatives(FakeFelicitas(), 43, details=False)
    rec = reg["data"][0]
    assert rec["dateOfBirth"] == "1968-08-30" and rec["zodiacSign"] == "virgo"
    assert reg["meta"]["birthDatesLinked"] == 1


def test_an_mp_wikidata_knows_no_birth_date_for_gets_no_date_key(monkeypatch):
    """Absent, not null-and-signed: an undated MP must not acquire a sign."""
    monkeypatch.setattr(reps.wikidata, "fetch_mp_links", lambda http: {
        "k001": {"wikidataId": "Q42", "wikipediaUrl": None,
                 "dateOfBirth": None, "zodiacSign": None}})
    reg = reps.fetch_representatives(FakeFelicitas(), 43, details=False)
    assert "dateOfBirth" not in reg["data"][0]
    assert "zodiacSign" not in reg["data"][0]
    assert reg["meta"]["birthDatesLinked"] == 0
