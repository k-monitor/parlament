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
    # Two terms of one person, deliberately not in chronological order.
    {"id": "1", "kepvId": "r023", "nev": "Dr. Rétvári Bence",
     "nevElonevNelkul": "Rétvári Bence",
     "tisztseg": "közigazgatási és igazságügyi minisztériumi államtitkár",
     "tol": "2010-06-01T22:00:00Z", "ig": "2014-06-05T21:59:59Z"},
    {"id": "2", "kepvId": "r023", "nev": "Dr. Rétvári Bence",
     "nevElonevNelkul": "Rétvári Bence",
     "tisztseg": "Belügyminisztérium államtitkára",
     "tol": "2022-05-24T22:00:00Z", "ig": "2026-05-12T21:59:59Z"},
    # A non-MP minister, still in office (no `ig`) — the case no roster covers.
    {"id": "3", "kepvId": "v076", "nev": "Vitézy Dávid László",
     "nevElonevNelkul": "Vitézy Dávid László",
     "tisztseg": "közlekedési és beruházási miniszter",
     "tol": "2026-05-12T22:00:00Z", "ig": None},
    # Unusable rows: no person id to join by / no office to name.
    {"id": "4", "kepvId": "", "nev": "Nincs Azonosító",
     "tisztseg": "miniszter", "tol": "2020-01-01T23:00:00Z", "ig": None},
    {"id": "5", "kepvId": "x001", "nev": "Nincs Tisztség",
     "tisztseg": "  ", "tol": "2020-01-01T23:00:00Z", "ig": None},
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


def test_office_still_held_keeps_an_open_end():
    """The whole point of this stage for a non-MP minister: the term is dated from
    the appointment, and its end stays null while they are in office (nothing
    downstream may invent a departure date)."""
    reg = officeholders.fetch_office_holders(FakeFelicitas(), as_of="2026-07-29")
    minister = next(r for r in reg["data"] if r["personID"] == "v076")
    assert minister["offices"] == [{
        "title": "közlekedési és beruházási miniszter",
        "start": "2026-05-12T22:00:00Z", "end": None}]


def test_empty_listing_yields_no_records():
    """An empty upstream answer must stay empty (the CLI then writes nothing, so a
    bad run can't teach the loader to forget the terms it already holds)."""
    reg = officeholders.fetch_office_holders(FakeFelicitas(rows=[]),
                                            as_of="2026-07-29")
    assert reg["data"] == [] and reg["meta"]["terms"] == 0
