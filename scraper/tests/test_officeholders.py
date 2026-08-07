"""Offline tests for the office-holder stage (`parlamonitor.officeholders`).

Drives ``fetch_office_holders`` against a fake Felicitas client (no network) to
pin down the registry file's contract: one record per person keyed by the id the
transcripts already carry, their office terms newest first with the **upstream**
start/end dates, and an open end left open for an office still held. The rows are
shaped exactly as ``tisztsegviselo`` returns them (field names verified against
the live API, 2026-07).
"""

from __future__ import annotations

from parlamonitor.officeholders import scrape as officeholders


ROWS = [
    # Two terms of one person, deliberately not in chronological order. `category`
    # is what the client tags each row with, from the listing it came back under.
    {"id": "1", "kepvId": "r023", "nev": "Dr. Rétvári Bence",
     "nevElonevNelkul": "Rétvári Bence",
     "tisztseg": "közigazgatási és igazságügyi minisztériumi államtitkár",
     "tol": "2010-06-01T22:00:00Z", "ig": "2014-06-05T21:59:59Z",
     "category": "state-secretary"},
    {"id": "2", "kepvId": "r023", "nev": "Dr. Rétvári Bence",
     "nevElonevNelkul": "Rétvári Bence",
     "tisztseg": "Belügyminisztérium államtitkára",
     "tol": "2022-05-24T22:00:00Z", "ig": "2026-05-12T21:59:59Z",
     "category": "state-secretary"},
    # A non-MP minister, still in office (no `ig`) — the case no roster covers.
    {"id": "3", "kepvId": "v076", "nev": "Vitézy Dávid László",
     "nevElonevNelkul": "Vitézy Dávid László",
     "tisztseg": "közlekedési és beruházási miniszter",
     "tol": "2026-05-12T22:00:00Z", "ig": None, "category": "minister"},
    # Unusable rows: no person id to join by / no office to name.
    {"id": "4", "kepvId": "", "nev": "Nincs Azonosító",
     "tisztseg": "miniszter", "tol": "2020-01-01T23:00:00Z", "ig": None,
     "category": "minister"},
    {"id": "5", "kepvId": "x001", "nev": "Nincs Tisztség",
     "tisztseg": "  ", "tol": "2020-01-01T23:00:00Z", "ig": None,
     "category": "other"},
]


class FakeFelicitas:
    """Answers only the office-holder listing; records how it was asked."""

    def __init__(self, rows=None):
        self.rows = ROWS if rows is None else rows
        self.calls: list[dict] = []

    def office_holders(self, *, as_of, earliest=None):
        self.calls.append({"as_of": as_of, "earliest": earliest})
        return [dict(r) for r in self.rows]


def test_registry_groups_terms_by_person_newest_first():
    fake = FakeFelicitas()
    reg = officeholders.fetch_office_holders(fake, as_of="2026-07-29")

    assert reg["meta"]["source"] == "felicitas-tisztsegviselok-api"
    assert reg["meta"]["asOf"] == "2026-07-29"
    assert fake.calls == [{"as_of": "2026-07-29", "earliest": None}]
    # Two usable people; the id-less and title-less rows are reported, not hidden.
    assert reg["meta"]["count"] == 2
    assert reg["meta"]["terms"] == 3
    assert reg["meta"]["skippedRows"] == 2

    by_id = {r["personID"]: r for r in reg["data"]}
    retvari = by_id["r023"]
    # The name without the honorific is the label (matching the MP roster's), and
    # terms come newest first so the current office reads off the top.
    assert retvari["label"] == "Rétvári Bence"
    assert retvari["labelFull"] == "Dr. Rétvári Bence"
    assert [o["title"] for o in retvari["offices"]] == [
        "Belügyminisztérium államtitkára",
        "közigazgatási és igazságügyi minisztériumi államtitkár"]
    assert retvari["offices"][0]["start"] == "2022-05-24T22:00:00Z"
    assert retvari["offices"][0]["end"] == "2026-05-12T21:59:59Z"
    # The name is split for name-ordered listings: for an office holder who is in
    # no MP roster this file is the only place that happens.
    assert (retvari["firstname"], retvari["lastname"]) == ("Bence", "Rétvári")


def test_terms_keep_their_category_and_meta_counts_them():
    """Each term carries the portal's own office category (the rows themselves have
    only a free-text title), and the file reports the per-category totals so a
    category going empty upstream is visible between runs."""
    reg = officeholders.fetch_office_holders(FakeFelicitas(), as_of="2026-07-29")
    by_id = {r["personID"]: r for r in reg["data"]}
    assert {o["category"] for o in by_id["r023"]["offices"]} == {"state-secretary"}
    assert by_id["v076"]["offices"][0]["category"] == "minister"
    assert reg["meta"]["categories"] == {"state-secretary": 2, "minister": 1}


def test_untagged_rows_are_counted_as_uncategorised():
    """A row with no category (an older cached listing) still loads — it is just
    filed as uncategorised rather than dropped or guessed at from its title."""
    reg = officeholders.fetch_office_holders(
        FakeFelicitas(rows=[{"id": "1", "kepvId": "k001", "nev": "Kiss Anna",
                             "nevElonevNelkul": "Kiss Anna", "tisztseg": "miniszter",
                             "tol": "2020-01-01T23:00:00Z", "ig": None}]),
        as_of="2026-07-29")
    assert reg["data"][0]["offices"][0]["category"] is None
    assert reg["meta"]["categories"] == {"uncategorised": 1}


def test_office_still_held_keeps_an_open_end():
    """The whole point of this stage for a non-MP minister: the term is dated from
    the appointment, and its end stays null while they are in office (nothing
    downstream may invent a departure date)."""
    reg = officeholders.fetch_office_holders(FakeFelicitas(), as_of="2026-07-29")
    minister = next(r for r in reg["data"] if r["personID"] == "v076")
    assert minister["offices"] == [{
        "title": "közlekedési és beruházási miniszter",
        "start": "2026-05-12T22:00:00Z", "end": None,
        "category": "minister"}]


def test_empty_listing_yields_no_records():
    """An empty upstream answer must stay empty (the CLI then writes nothing, so a
    bad run can't teach the loader to forget the terms it already holds)."""
    reg = officeholders.fetch_office_holders(FakeFelicitas(rows=[]),
                                            as_of="2026-07-29")
    assert reg["data"] == [] and reg["meta"]["terms"] == 0


def test_client_asks_each_category_separately_and_files_a_row_once():
    """The category is not in the data — it is *which listing* answered — so the
    client asks one category at a time and tags the rows. A term that comes back
    under several categories (a prime minister is also a minister) is kept once,
    under the first/most specific one."""
    from parlamonitor.felicitas import OFFICE_CATEGORIES, FelicitasClient

    pm = {"id": "1", "kepvId": "o001", "tisztseg": "miniszterelnök"}
    secretary = {"id": "2", "kepvId": "s001", "tisztseg": "Miniszterelnökség államtitkára"}
    answers = {"pMiniszterelnok": [pm],
               "pMiniszter": [pm],              # the same term, broader listing
               "pAllamtitkar": [secretary]}

    asked = []
    fc = FelicitasClient.__new__(FelicitasClient)

    def fake_select_all(provider, query, body, size=None):
        on = [p for p in OFFICE_CATEGORIES.values() if body.get(p)]
        asked.append(on)
        return [dict(r) for r in answers.get(on[0], [])]

    fc.select_all = fake_select_all
    rows = fc.office_holders(as_of="2026-07-29")

    # One listing per category, each with exactly its own flag on.
    assert asked == [[p] for p in OFFICE_CATEGORIES.values()]
    assert [(r["id"], r["category"]) for r in rows] == [("1", "pm"), ("2", "state-secretary")]
