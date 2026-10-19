"""The irományok the coming sitting will vote on or debate (HOME-5).

The order paper (NR-5) is a list of *items* per day; the home page's highlight
turns it round to the bills. What the tests pin down is that turn: every item
naming an iromány we hold becomes a step of that iromány, a procedural decision
taken on a motion counts to the motion's bill, and what each step will do is read
off the stage line the napirend prints.

The fixture order paper is `conftest._aktualis_registry`: T/100 is announced with
its exceptional-procedure motion (T/100/1), a general debate and a decision on the
summary amendment; T/999 is a number we do not hold.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app import loader
from app.modules.bills.router import _agenda_step_kind


@pytest.fixture
def upcoming(client):
    r = client.get("/api/v1/bills/upcoming")
    assert r.status_code == 200
    return r.json()


def test_one_entry_per_iromany_we_hold(upcoming):
    """T/100 appears on three items and once here; T/999 is not ours, so it is
    left out rather than shown as a bare number."""
    assert [b["bill_number"] for b in upcoming["bills"]] == ["T/100"]
    bill = upcoming["bills"][0]
    assert bill["bill_id"] == "bill-uuid-1"
    assert bill["category"] == "law"


def test_each_item_becomes_a_step_of_its_bill(upcoming):
    steps = upcoming["bills"][0]["steps"]
    assert [(s["date"], s["kind"], s["label"]) for s in steps] == [
        # Taken on the motion T/100/1, which the reader knows as T/100.
        ("2026-05-14", "procedure", "Döntés kivételes eljárásban történő tárgyalásról"),
        ("2026-05-14", "debate", "Általános vita a lezárásig"),
        ("2026-05-14", "vote", "Döntés az összegző módosító javaslatról"),
    ]
    assert steps[0]["code"] == "T/100/1"
    # A decision on the summary amendment is a vote, but not the final one.
    assert steps[2]["final"] is False


def test_the_bill_brings_where_it_stands(upcoming):
    bill = upcoming["bills"][0]
    assert bill["status"] == "tárgysorozatban"
    current = [s["key"] for s in bill["stages"] if s["current"]]
    assert current == ["ALTALANOS_VITA_ALATT"]
    assert bill["sponsors"][0]["person_id"] == "k001"


def test_only_the_flags_about_the_bill_come_along(upcoming):
    """"Határozatképesség szükséges" describes the item's slot on the day, not
    the bill, so it stays on the order paper's own card."""
    assert upcoming["bills"][0]["flags"] == ["exceptional", "two_thirds", "cardinal"]


def test_the_response_names_the_order_paper_it_read(upcoming):
    """Still a plan: the reader has to be able to see which document said so and
    as of when (TRUST-1)."""
    agenda = upcoming["agenda"]
    assert agenda["url"].endswith("nr_20260514_elfogadott")
    assert agenda["status_at"] == "2026-05-14T15:45"
    assert (agenda["first_date"], agenda["last_date"]) == ("2026-05-14", "2026-05-15")


def test_a_number_left_at_the_head_of_the_title_still_finds_its_bill(conn, client):
    """The parser now and then leaves the number in the title instead of the
    gutter ("T/668/10 A köztársasági elnök által …"); the item is still that
    bill's vote."""
    conn.execute(
        "UPDATE agenda_doc_item SET bill_code = NULL, bill_id = NULL, "
        "title = 'T/100/10 A köztársasági elnök által visszaküldött törvény' "
        "WHERE stage = 'Döntés az összegző módosító javaslatról'")
    conn.commit()
    steps = client.get("/api/v1/bills/upcoming").json()["bills"][0]["steps"]
    assert steps[-1]["kind"] == "vote" and steps[-1]["code"] is None


@pytest.mark.parametrize("stage, section, expected", [
    ("Általános vita a lezárásig", "Az általános viták a lezárásig", ("debate", False)),
    ("Összevont vita", "Az összevont vita", ("debate", False)),
    ("Bizottsági jelentések és az összegző módosító javaslat vitája", None,
     ("debate", False)),
    ("Döntés az összegző módosító javaslatról", "Döntések, határozathozatalok",
     ("vote", False)),
    ("Döntés az összegző módosító javaslatról és a zárószavazás",
     "Döntések, határozathozatalok", ("vote", True)),
    ("A zárószavazás", "Döntések, határozathozatalok", ("vote", True)),
    ("Tárgyalás és döntés", "Döntések, határozathozatalok", ("vote", False)),
    # An election prints no stage; the heading says it is a decision.
    (None, "Döntések, határozathozatalok", ("vote", False)),
    (None, "Döntés sürgős tárgyalásról", ("procedure", False)),
    (None, "Döntés a napirend kiegészítéséről", ("procedure", False)),
    (None, "A határozathozatalokat követően", ("other", False)),
    (None, None, ("other", False)),
])
def test_what_a_step_will_do_is_read_off_the_printed_stage(stage, section, expected):
    assert _agenda_step_kind(stage, section) == expected


def test_a_db_without_the_order_paper_answers_empty(tmp_path, data_dir, monkeypatch):
    (data_dir / "processed" / "aktualis.json").unlink()
    out = tmp_path / "old.db"
    loader.build_database(data_dir, out)
    conn = sqlite3.connect(out)
    conn.executescript("DROP TABLE agenda_doc_item; DROP TABLE agenda_doc_day; "
                       "DROP TABLE agenda_doc; DROP TABLE agenda_meta;")
    conn.commit()
    conn.close()

    from app.config import settings
    monkeypatch.setattr(settings, "db_path", str(out))
    monkeypatch.setattr(db_module.settings, "db_path", str(out))
    from app.main import app

    body = TestClient(app).get("/api/v1/bills/upcoming").json()
    assert body == {"agenda": None, "bills": []}
