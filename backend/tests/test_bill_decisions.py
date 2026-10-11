"""What the House decided in a date range — the week digest's decision list (HOME-4).

The whole difficulty is telling an iromány's *own* vote from the procedural votes
on the way to it, which only the vote's upstream wording does, and then reading
the outcome off the vote's result rather than that wording. Every subject below
is one the House actually used in cycle 43.
"""

from __future__ import annotations

import pytest

RANGE = {"date_from": "2026-09-21", "date_to": "2026-09-27"}


def _bill(c, bill_id, number, main_type, type_, title, sort):
    c.execute("INSERT INTO bill (id, bill_number, number_sort, period_number, title, "
              "type, main_type) VALUES (?,?,?,43,?,?,?)",
              (bill_id, number, sort, title, type_, main_type))


def _vote(c, bill_id, ord_, when, subject, result, yes=135, no=50, abstain=0,
          vote_id=None):
    c.execute("INSERT INTO bill_vote (bill_id, ord, vote_id, vote_date, subject, yes, "
              "no, abstain, result) VALUES (?,?,?,?,?,?,?,?,?)",
              (bill_id, ord_, vote_id, when, subject, yes, no, abstain, result))


@pytest.fixture
def week(conn):
    """One sitting week's votes, decisive and procedural mixed together."""
    # T/100: an amendment vote, then its final vote split into a qualified and a
    # simple-majority part — one decision.
    _vote(conn, "bill-uuid-1", 10, "2026-09-22T08:02:00Z",
          "összegző módosító javaslat elfogadva", "Elfogadva")
    _vote(conn, "bill-uuid-1", 11, "2026-09-22T08:06:00Z",
          "önálló indítvány minősített többséget igénylő része elfogadva", "Elfogadva",
          135, 46, 6, vote_id="v-missing")
    _vote(conn, "bill-uuid-1", 12, "2026-09-22T08:07:00Z",
          "önálló indítvány egyszerű többséget igénylő része elfogadva", "Elfogadva",
          136, 45, 6)
    # T/101: the House refused to take it onto its agenda — not "voted down".
    _vote(conn, "bill-uuid-2", 1, "2026-09-21T14:00:00Z",
          "Országgyűlés a tárgysorozatba vételt elutasította", "Elutasítva", 50, 130, 0)
    # S/710: an election, one vote per nominee, and not every nominee got in.
    _bill(conn, "s-710", "S/710", "S",
          "az Országgyűlés személyi döntését kezdeményező indítvány",
          "A Médiatanács tagjainak megválasztásáról", 710)
    for i, result in enumerate(["Elfogadva", "Elutasítva", "Elfogadva"]):
        _vote(conn, "s-710", i, f"2026-09-22T08:3{i}:00Z",
              "önálló indítvány elfogadva" if result == "Elfogadva"
              else "önálló indítvány elutasítva", result)
    # H/707: immunity waived, on a vote the Votes module holds (v-1).
    _bill(conn, "h-707", "H/707", "H", "mentelmi",
          "Kocsis Máté országgyűlési képviselő mentelmi ügyében", 707)
    _vote(conn, "h-707", 0, "2026-09-22T08:19:00Z", "mentelmi jog felfüggesztve",
          "Elfogadva", 143, 44, 0, vote_id="v-1")
    # ...and a vote on it the week after, outside the range.
    _vote(conn, "h-707", 1, "2026-09-28T08:00:00Z", "mentelmi jog fenntartva",
          "Elutasítva")
    # I/5: an interpellation answer the MP who asked rejected; the House accepted it.
    _vote(conn, "doc-uuid-3", 0, "2026-09-21T14:30:00Z",
          "Országgyűlés az interpellációs választ elfogadta", "Elfogadva", 127, 48, 0)
    conn.commit()


def _decisions(client, **params):
    res = client.get("/api/v1/bills/decisions", params={**RANGE, **params})
    assert res.status_code == 200, res.text
    return res.json()["decisions"]


def test_one_entry_per_decided_iromany_grouped_by_what_was_decided(client, week):
    got = [(d["bill_number"], d["category"], d["stage"], d["outcome"])
           for d in _decisions(client)]
    assert got == [
        ("T/101", "law", "agenda", "rejected"),       # Monday, before T/100's Tuesday
        ("T/100", "law", "final", "adopted"),
        ("S/710", "personnel", "final", "mixed"),
        ("H/707", "immunity", "final", "adopted"),
        ("I/5", "interpellation", "final", "adopted"),
    ]


def test_a_law_passed_in_two_parts_is_one_decision_with_the_qualified_tally(client, week):
    law = next(d for d in _decisions(client) if d["bill_number"] == "T/100")
    # The amendment vote before it is procedural and not counted.
    assert (law["votes"], law["adopted"], law["rejected"]) == (2, 2, 0)
    assert law["date"] == "2026-09-22"
    assert law["tally"] == {"yes": 135, "no": 46, "abstain": 6, "vote_ref": None}


def test_an_election_that_went_both_ways_has_counts_but_no_single_tally(client, week):
    election = next(d for d in _decisions(client) if d["bill_number"] == "S/710")
    assert (election["votes"], election["adopted"], election["rejected"]) == (3, 2, 1)
    # No one vote speaks for three nominees.
    assert election["tally"] is None


def test_a_tally_links_the_vote_the_votes_module_holds(client, week):
    immunity = next(d for d in _decisions(client) if d["bill_number"] == "H/707")
    assert immunity["tally"]["vote_ref"] == "v-1"
    assert immunity["votes"] == 1               # the next week's vote is out of range


def test_the_outcome_is_read_off_the_result_not_the_wording(client, conn, week):
    _bill(conn, "h-800", "H/800", "H", "határozati javaslat", "Egy határozat", 800)
    # Worded as adopted, recorded as rejected — the record wins.
    _vote(conn, "h-800", 0, "2026-09-23T09:00:00Z", "önálló indítvány elfogadva",
          "Elutasítva", 80, 110, 0)
    # A failed quorum decides nothing, whatever the wording.
    _bill(conn, "h-801", "H/801", "H", "határozati javaslat", "Egy másik határozat", 801)
    _vote(conn, "h-801", 0, "2026-09-23T09:05:00Z", "önálló indítvány elfogadva",
          "Határozatképtelen", 60, 0, 0)
    conn.commit()
    resolutions = [d for d in _decisions(client) if d["category"] == "resolution"]
    assert [(d["bill_number"], d["outcome"]) for d in resolutions] == [("H/800", "rejected")]


def test_a_week_without_decisive_votes_is_an_empty_list(client, week):
    assert _decisions(client, date_from="2026-09-14", date_to="2026-09-20") == []


@pytest.mark.parametrize("params", [
    {"date_from": "2026-09-27", "date_to": "2026-09-21"},    # reversed
    {"date_from": "2026-01-01", "date_to": "2026-09-27"},    # not a digest any more
    {"date_from": "2026-02-30", "date_to": "2026-03-02"},    # no such day
    {"date_from": "2026-9-21", "date_to": "2026-09-27"},     # malformed
])
def test_the_range_must_be_a_real_ordered_span_of_at_most_a_month(client, params):
    assert client.get("/api/v1/bills/decisions", params=params).status_code == 422
