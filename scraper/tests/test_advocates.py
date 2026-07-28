"""Offline tests for the nationality-advocate stage (`parlamonitor.advocates`).

Drives ``fetch_advocates`` / ``advocate_cycles`` against a fake Felicitas client
(no network) to pin down what the registry file must contain: the person id the
transcripts already carry, the nationality, the reused per-MP detail enrichment,
and the roster counts standing in for the detail queries on a ``--no-details``
run. Also covers the empty-cycle case, since that is how the backfill decides
which cycles have advocates at all.
"""

from __future__ import annotations

from parlamonitor.advocates import scrape as advocates


# One roster row as ``szoszolo-lista-query`` returns it (field names verified
# against the live API, 2026-07).
ROSTER = {
    "004O": {
        "id": "row-1", "szoszoloId": "004O",
        "nevRendezeshez": "Aba-Horváth István",
        "szoszoloMegjelenoNeve": "Aba-Horváth István",
        "nemzetiseg": "roma", "frakcioIdCsikhoz": None,
        "onalloInditvanyokSzama": 0, "felszolalasokSzama": 0, "ciklusParam": 43,
    },
    "a717": {
        "id": "row-2", "szoszoloId": "a717",
        "nevRendezeshez": "Alexov Lyubomir",
        "szoszoloMegjelenoNeve": "Alexov Lyubomir",
        "nemzetiseg": "szerb", "frakcioIdCsikhoz": None,
        "onalloInditvanyokSzama": 2, "felszolalasokSzama": 14, "ciklusParam": 43,
    },
}


class FakeFelicitas:
    """Answers only what the advocate stage asks for; counts the calls."""

    def __init__(self, rosters=None, details=None):
        # cycle -> roster rows
        self.rosters = rosters if rosters is not None else {43: list(ROSTER.values())}
        self.details = details or {}
        self.list_calls: list[int] = []
        self.detail_calls: list[tuple[str, str]] = []
        self.photos: list[str] = []
        self.http = object()

    def cycle_ranges(self):
        return {40: {"start": "2014-05-06", "end": "2018-05-07"},
                43: {"start": "2026-05-09", "end": None}}

    def advocate_list(self, cycle):
        self.list_calls.append(cycle)
        return [dict(r) for r in self.rosters.get(cycle, [])]

    def representative_detail(self, query, person_id):
        self.detail_calls.append((query, person_id))
        return [dict(r) for r in self.details.get((query, person_id), [])]

    def photo(self, person_id):
        self.photos.append(person_id)
        return b"jpegbytes"


def test_roster_only_record_shape():
    """A ``--no-details`` run already yields everything the loader needs."""
    reg = advocates.fetch_advocates(FakeFelicitas(), 43, details=False,
                                   link_wikidata=False)
    assert reg["meta"]["cycle"] == 43
    assert reg["meta"]["count"] == 2
    assert reg["meta"]["source"] == "felicitas-szoszolo-api"
    # Cycle bounds come from the shared rebind ranges, so the loader can stamp the
    # electoral period from an advocates file alone.
    assert reg["meta"]["cycleStart"] == "2026-05-09"

    rec = reg["data"][0]
    # The id is the one the proceedings scraper already stamped on the speeches.
    assert rec["personID"] == "004O"
    assert rec["label"] == "Aba-Horváth István"
    assert (rec["firstname"], rec["lastname"]) == ("István", "Aba-Horváth")
    assert rec["nationality"] == "roma"
    assert rec["mandate"] == advocates.MANDATE
    assert rec["photoURI"].endswith("/004O")


def test_roster_counts_fill_in_for_the_cycle():
    """The list query's own per-cycle counters are kept, in the MP registry's
    shape, so the profile has numbers without the per-person detail queries."""
    reg = advocates.fetch_advocates(FakeFelicitas(), 43, details=False,
                                    link_wikidata=False)
    stats = reg["data"][1]["statistics"]
    assert stats["speeches"] == [{"cycle": 43, "count": 14}]
    assert stats["billsSubmitted"] == [{"cycle": 43, "ownBills": 2}]


def test_details_are_the_shared_per_person_queries():
    """An advocate is in the MP id space, so the MP detail queries answer for them
    and their (multi-cycle) rows win over the single-cycle roster counts."""
    details = {
        ("kepviselo-adatok-query", "a717"): [
            {"ulohely": "3/6/13", "honlap": "http://example.hu",
             "legmagasabbIskolaiVegzettseg": "egyetem", "kepviseloAktivE": False}],
        ("kepviselo-bizottsagi-tagsagai-query", "a717"): [
            {"ciklus": "2026-", "bizottsagNeve": "Magyarországi Nemzetiségek Bizottsága",
             "bizottsagiTisztseg": "tag"}],
        ("kepviselo-felszolalasok-szama-query", "a717"): [
            {"ciklusId": 43, "felszolalasokSzama": 14},
            {"ciklusId": 40, "felszolalasokSzama": 22}],
    }
    fake = FakeFelicitas(rosters={43: [ROSTER["a717"]]}, details=details)
    reg = advocates.fetch_advocates(fake, 43, details=True, link_wikidata=False)
    rec = reg["data"][0]
    assert rec["seat"] == "3/6/13"
    assert rec["website"] == "http://example.hu"
    assert rec["committeeMemberships"][0]["committee"] == \
        "Magyarországi Nemzetiségek Bizottsága"
    # Every cycle on record, not just the one scraped.
    assert [s["cycle"] for s in rec["statistics"]["speeches"]] == [43, 40]
    # An advocate has no faction and no constituency; both come back empty rather
    # than absent, so the loader's COALESCE merge can't invent one.
    assert rec["factionHistory"] == [] and rec["electionHistory"] == []


def test_empty_cycle_is_not_an_error_and_costs_one_request():
    fake = FakeFelicitas(rosters={})
    reg = advocates.fetch_advocates(fake, 39, details=True, link_wikidata=True)
    assert reg["data"] == [] and reg["meta"]["count"] == 0
    assert fake.list_calls == [39]
    assert fake.detail_calls == []          # nothing to enrich


def test_advocate_cycles_probes_from_the_first_cycle_with_the_office():
    """The backfill discovers its own cycle list — no hard-coded years."""
    fake = FakeFelicitas(rosters={43: list(ROSTER.values())})
    assert advocates.advocate_cycles(fake) == [43]
    # Only cycles at/after the office was created are probed (40 and 43 here).
    assert fake.list_calls == [40, 43]


def test_photos_are_saved_next_to_the_mp_portraits(tmp_path):
    fake = FakeFelicitas(rosters={43: [ROSTER["004O"]]})
    reg = advocates.fetch_advocates(fake, 43, details=False, link_wikidata=False,
                                    photos_dir=tmp_path)
    assert (tmp_path / "004O.jpg").read_bytes() == b"jpegbytes"
    # The record points at the local file, which is what makes the loader serve
    # the portrait from /media/photos instead of hot-linking parlament.hu.
    assert reg["data"][0]["photoFile"] == "004O.jpg"


def test_save_writes_a_per_cycle_file(tmp_path):
    from parlamonitor.config import Paths
    paths = Paths(tmp_path)
    paths.ensure()
    reg = advocates.fetch_advocates(FakeFelicitas(), 43, details=False,
                                    link_wikidata=False)
    advocates.save_advocates(paths, 43, reg)
    out = paths.processed / "advocates-43.json"
    assert out.exists()
    # Separate from the MP registry: adding advocates to an existing scrape must
    # not touch (or require re-running) the representatives stage.
    assert not (paths.processed / "representatives-43.json").exists()
