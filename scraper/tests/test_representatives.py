"""Offline tests for the MP registry stage (`parlamonitor.representatives`).

Focused on what this stage *assembles* rather than passes through:

* REP-13 — the asset declarations (*vagyonnyilatkozatok*), merged into one series
  from three separate upstream queries, and the CV, a static PDF whose URL we may
  only publish once it is known to resolve; and
* REP-14 — the **terminated mandates**: the roster is point-in-time, so the MPs who
  left mid-cycle are pulled from the composition-changes registry and each record's
  mandate (its term, whether it ended early, the handover) is derived here.

Driven against a fake Felicitas client, so no network is touched.
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


def _mandate_change(person_id="d035", name="Dömötör Csaba", *,
                    start="2026-05-09", end="2026-07-15",
                    reason="képviselői megbízatásról lemondott",
                    successor=("006F", "Palóc André"), successor_start="2026-08-10"):
    """One row of the composition-changes registry: a mandate that ended mid-cycle
    and the MP who took the seat (field names verified against the live API,
    2026-08). ``successor=None`` is a seat that was never filled."""
    return {"kepviseloId": person_id, "kepviseloNeve": f"Dr. {name}",
            "kepviseloNeveSorrendezeshez": name,
            "frakcioNeve": "Fidesz", "frakcioId": 7,
            "mandatumKezdete": start, "mandatumVege": end,
            "mandatumAllapotNeve": reason, "valasztoKeruletNeve": "Országos lista",
            "kovetkezoKepviseloId": successor[0] if successor else None,
            "kovetkezoKepviseloNeve": successor[1] if successor else "",
            "kovetkezoMandatumKezdete": successor_start if successor else None,
            "letezikKovetkezoKepviselo": bool(successor)}


class FakeFelicitas:
    """Answers the roster + per-MP detail queries, and records CV probes."""

    def __init__(self, details=None, cvs=(), changes=None):
        self.details = details or {}
        self.cvs = set(cvs)          # ids that HAVE a published CV
        self.cv_probes: list[str] = []
        self.detail_calls: list[tuple[str, str]] = []
        self.changes = changes or {"mandate": [], "faction": []}
        self.http = object()

    def cycle_ranges(self):
        return {43: {"start": "2026-05-09", "end": None}}

    def representative_list(self, cycle, start, end):
        return [dict(r) for r in ROSTER]

    def composition_changes(self, cycle, start, end):
        return {k: [dict(r) for r in v] for k, v in self.changes.items()}

    def representative_detail(self, query, person_id):
        self.detail_calls.append((query, person_id))
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


# --- terminated mandates & composition changes (REP-14) ---------------------

def test_departed_mp_is_added_from_the_composition_changes():
    """The roster is point-in-time, so an MP who resigned mid-cycle is simply not
    in it. They come from the changes registry instead — as a full record, enriched
    by the same detail queries, since they live in the same person-id space."""
    fake = FakeFelicitas(changes={"mandate": [_mandate_change()], "faction": []})
    reg = reps.fetch_representatives(fake, 43, details=False, link_wikidata=False)

    ids = [r["personID"] for r in reg["data"]]
    assert ids == ["k001", "d035"]          # roster first, then the departed
    gone = reg["data"][1]
    assert gone["label"] == "Dömötör Csaba" and gone["labelFull"] == "Dr. Dömötör Csaba"
    assert gone["faction"] == {"id": 7, "label": "Fidesz", "position": None}
    assert gone["mandate"]["terminated"] is True
    assert gone["mandate"]["end"] == "2026-07-15"
    assert gone["mandate"]["endReason"] == "képviselői megbízatásról lemondott"
    assert gone["mandate"]["successor"] == {
        "personID": "006F", "label": "Palóc André", "mandateStart": "2026-08-10"}
    assert reg["meta"]["departed"] == 1 and reg["meta"]["count"] == 2


def test_sitting_mp_mandate_comes_from_their_election_history():
    """Someone the changes registry does not name served the term out: their mandate
    is dated from their own election history and is NOT marked terminated."""
    fake = FakeFelicitas(details={"kepviselo-valasztasi-adatok-query": [
        {"cikus": "2026-", "valasztasiKerulet": "Budapest 1. OEVK",
         "megvalasztasNapja": "2026-04-12",
         "mandatumKezdete": "2026-05-08T22:00:00Z", "mandatumVege": None}]})
    reg = reps.fetch_representatives(fake, 43, link_wikidata=False)
    mandate = reg["data"][0]["mandate"]
    assert mandate["terminated"] is False and mandate["endReason"] is None
    assert mandate["start"] == "2026-05-08T22:00:00Z" and mandate["end"] is None
    assert mandate["constituency"] == "Budapest 1. OEVK"
    assert reg["meta"]["departed"] == 0


def test_the_successor_knows_who_they_replaced():
    """The handover is recorded from both ends: the MP seated mid-cycle carries the
    predecessor whose seat they took, so neither profile is a dead end."""
    fake = FakeFelicitas(changes={
        "mandate": [_mandate_change(successor=("k001", "Kovács Béla"))], "faction": []})
    reg = reps.fetch_representatives(fake, 43, details=False, link_wikidata=False)
    seated = reg["data"][0]
    assert seated["personID"] == "k001"
    assert seated["mandate"]["terminated"] is False
    # …named the way the rest of the site labels people: the plain form, not "Dr. …".
    assert seated["mandate"]["predecessor"] == {
        "personID": "d035", "label": "Dömötör Csaba",
        "mandateEnd": "2026-07-15", "endReason": "képviselői megbízatásról lemondott"}
    # …and the seat's own start date, which nothing else in their record has.
    assert seated["mandate"]["start"] == "2026-08-10"


def test_a_seat_never_filled_has_no_successor():
    fake = FakeFelicitas(changes={"mandate": [_mandate_change(successor=None)],
                                  "faction": []})
    reg = reps.fetch_representatives(fake, 43, details=False, link_wikidata=False)
    assert reg["data"][1]["mandate"]["successor"] is None


def test_mandate_of_a_neighbouring_cycle_is_not_borrowed():
    """Upstream dates a mandate by the UTC instant of a *local* midnight, so a
    cycle's whole opening cohort carries the calendar date of the previous cycle's
    last day. Picking the cycle's own term must survive that."""
    rec = {"electionHistory": [
        # the cycle after this one (starts the evening before its first day)
        {"cycle": "2026-", "mandateStart": "2026-05-08T22:00:00Z", "mandateEnd": None,
         "constituency": "Budapest 1. OEVK"},
        # the cycle in question
        {"cycle": "2022-2026", "mandateStart": "2022-05-01T22:00:00Z",
         "mandateEnd": "2026-05-08T21:59:59Z", "constituency": "Budapest 2. OEVK"},
        # the one before it
        {"cycle": "2018-2022", "mandateStart": "2018-05-07T22:00:00Z",
         "mandateEnd": "2022-05-01T21:59:59Z", "constituency": "Budapest 3. OEVK"},
    ]}
    found = reps._mandate_in_cycle(rec, "2022-05-02", "2026-05-08")
    assert found["constituency"] == "Budapest 2. OEVK"
    # …and the running cycle picks its own, not the one that ended the day before.
    assert reps._mandate_in_cycle(rec, "2026-05-09", None)["mandateEnd"] is None


def test_only_new_reuses_the_details_already_on_file():
    """Backfilling the departed into a scraped cycle must not re-fetch the sitting
    MPs' details — that is ~13 queries each, and politeness is the budget (SCR-4)."""
    previous = {"data": [{"personID": "k001", "label": "Kovács Béla",
                          "email": "kovacs.bela@parlament.hu",
                          "electionHistory": [{"cycle": "2026-",
                                               "mandateStart": "2026-05-08T22:00:00Z",
                                               "mandateEnd": None,
                                               "constituency": "Budapest 1. OEVK"}],
                          "assetDeclarations": [{"title": "Vagyonnyilatkozat 2026"}]}]}
    fake = FakeFelicitas(changes={"mandate": [_mandate_change()], "faction": []})
    reg = reps.fetch_representatives(fake, 43, link_wikidata=False, previous=previous)

    assert {pid for _, pid in fake.detail_calls} == {"d035"}   # only the new one
    kept = reg["data"][0]
    assert kept["email"] == "kovacs.bela@parlament.hu"
    assert kept["assetDeclarations"] == [{"title": "Vagyonnyilatkozat 2026"}]
    # The reused election history still dates the mandate.
    assert kept["mandate"]["constituency"] == "Budapest 1. OEVK"
    assert reg["meta"]["detailsReused"] == 1


def test_changes_are_kept_on_the_registry():
    """Both halves are stored as scraped: the mandate handovers the loader dates,
    and the faction switches kept as a cross-check of the per-MP history."""
    switch = {"kepviseloId": "k001", "kepviselo": "Kovács Béla", "frakciobol": "LMP",
              "frakcioba": "független", "valtasDatum": "2025-02-16"}
    fake = FakeFelicitas(changes={"mandate": [_mandate_change()], "faction": [switch]})
    reg = reps.fetch_representatives(fake, 43, details=False, link_wikidata=False)
    assert reg["changes"]["faction"] == [switch]
    assert [r["kepviseloId"] for r in reg["changes"]["mandate"]] == ["d035"]


def test_a_failing_changes_query_leaves_the_roster_intact():
    """An old cycle upstream has no changes listing for must still scrape: the
    registry degrades to the point-in-time roster rather than failing (SCR-5)."""
    class Boom(FakeFelicitas):
        def composition_changes(self, cycle, start, end):
            raise RuntimeError("no such query")

    reg = reps.fetch_representatives(Boom(), 43, details=False, link_wikidata=False)
    assert [r["personID"] for r in reg["data"]] == ["k001"]
    assert reg["data"][0]["mandate"]["terminated"] is False
    assert reg["changes"] == {"mandate": [], "faction": []}
