"""The order paper for the sitting that is coming (NR-3 / NR-5).

Everything else the loader ingests is a record of what the House has done; this
is the one source that says what it is *about to* do, and that difference is
what the tests are mostly about. The plan lives in its own tables so it can
never be mistaken for the record, it is replaced wholesale on every load rather
than accumulating order papers nobody will read again, and the API hands the
reader the document and the moment it was issued alongside every claim.

The fixture registry is in `conftest._aktualis_registry`.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app import loader


# ---------------------------------------------------------------------------
# Ingestion (NR-3)
# ---------------------------------------------------------------------------


def test_the_page_and_its_order_paper_land_in_their_own_tables(conn):
    docs = conn.execute(
        "SELECT slug, kind, doc_date, item_count FROM agenda_doc ORDER BY ord"
    ).fetchall()
    assert [(d["slug"], d["kind"]) for d in docs] == [
        ("nr_20260514_elfogadott", "agenda"),
        ("ut_20260514_elfogadott", "sitting_plan"),
    ]
    # Only the napirend carries a parsed agenda; the others are links.
    assert docs[0]["item_count"] == 4
    assert docs[1]["item_count"] == 0

    days = conn.execute(
        "SELECT ord, date, weekday, starts_at, decisions_from FROM agenda_doc_day "
        "ORDER BY ord").fetchall()
    assert [d["date"] for d in days] == ["2026-05-14", "2026-05-15"]
    # A day can have several "legkorábban" decision times; all of them are kept.
    assert days[1]["decisions_from"] == "09:40,11:30"


def test_the_plan_does_not_touch_the_record(conn):
    """`session` / `agenda_item` hold what was actually said, and an order paper
    must never add to them: items get dropped and reordered between the napirend
    and the sitting (NR-4)."""
    sessions = conn.execute("SELECT COUNT(*) FROM session").fetchone()[0]
    agenda_items = conn.execute("SELECT COUNT(*) FROM agenda_item").fetchone()[0]
    assert sessions == 1                 # the one synthetic sitting, unchanged
    assert agenda_items >= 1
    # The announced sitting days are NOT sessions.
    assert conn.execute(
        "SELECT COUNT(*) FROM session WHERE date IN ('2026-05-14','2026-05-15')"
    ).fetchone()[0] == 0


def test_an_item_resolves_to_a_bill_only_when_we_hold_that_iromany(conn):
    """The order paper prints the iromány number and nothing else, so this is
    the only way an agenda item can reach the bill page."""
    rows = {r["bill_code"]: r["bill_id"] for r in conn.execute(
        "SELECT bill_code, bill_id FROM agenda_doc_item WHERE bill_code IS NOT NULL")}
    assert rows["T/100"] == "bill-uuid-1"
    # An iromány the registry does not hold links to nothing rather than to a guess.
    assert rows["T/999"] is None
    # A motion number is not the bill's number.
    assert rows["T/100/1"] is None


def test_an_unnumbered_procedural_item_is_kept_with_a_null_ordinal(conn):
    row = conn.execute(
        "SELECT ordinal, ref, section, flags FROM agenda_doc_item "
        "WHERE bill_code = 'T/100/1'").fetchone()
    assert row["ordinal"] is None and row["ref"] is None
    assert row["section"].startswith("Döntés kivételes eljárásban")
    assert row["flags"] == "exceptional"


def test_one_ref_can_appear_twice_in_a_day(conn):
    """A bill debated and voted on the same day is two ordinals and one ref —
    that is the listing's own structure, not a duplicate."""
    rows = conn.execute(
        "SELECT ordinal, stage FROM agenda_doc_item WHERE ref = '1' "
        "ORDER BY ordinal").fetchall()
    assert [r["ordinal"] for r in rows] == [1, 2]
    assert rows[0]["stage"] != rows[1]["stage"]


def test_the_house_committee_meeting_is_stored_alongside(conn):
    raw = conn.execute(
        "SELECT value FROM agenda_meta WHERE key = 'house_committee'").fetchone()[0]
    assert json.loads(raw)["date"] == "2026-05-18"


def test_reloading_replaces_the_whole_set(conn, data_dir):
    """The page states only the House's current position, so an order paper the
    House has withdrawn must disappear with it rather than linger as a sitting
    that will never happen (NR-3)."""
    registry = json.loads(
        (data_dir / "processed" / "aktualis.json").read_text())
    agenda = registry["data"]["agenda"]
    agenda["slug"] = "nr_20260601_elfogadott"
    agenda["days"] = [{"month": 6, "dayOfMonth": 1, "weekday": "HÉTFŐ",
                       "date": "2026-06-01", "startsAt": "13:00",
                       "decisionsFrom": [], "items": [
                           {"ordinal": 1, "ref": "1", "billCode": None,
                            "title": "Interpellációk", "section": None,
                            "submitter": None, "stage": None, "timeWindow": None,
                            "notes": None, "flags": None}]}]
    agenda["itemCount"] = 1
    registry["data"]["documents"][0]["slug"] = "nr_20260601_elfogadott"

    loader.load_aktualis(conn, registry)

    assert [r["slug"] for r in conn.execute(
        "SELECT slug FROM agenda_doc ORDER BY ord")][0] == "nr_20260601_elfogadott"
    assert conn.execute("SELECT COUNT(*) FROM agenda_doc_item").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM agenda_doc_day").fetchone()[0] == 1


def test_a_corpus_with_no_aktualis_file_loads_fine(tmp_path, data_dir):
    """Absent on a corpus scraped before the stage existed, which is not an
    error — the site then simply shows no upcoming sitting."""
    (data_dir / "processed" / "aktualis.json").unlink()
    out = tmp_path / "no-aktualis.db"
    loader.build_database(data_dir, out)
    conn = sqlite3.connect(out)
    conn.row_factory = sqlite3.Row
    assert conn.execute("SELECT COUNT(*) FROM agenda_doc").fetchone()[0] == 0
    conn.close()


def test_a_new_order_paper_alone_triggers_an_update(data_dir, db_path):
    """A napirend lands between sittings, when nothing else has changed — so the
    incremental path has to notice it on its own (SCR-2)."""
    registry = json.loads((data_dir / "processed" / "aktualis.json").read_text())
    registry["data"]["agenda"]["days"][0]["items"][0]["title"] = "Új napirendi pont"
    (data_dir / "processed" / "aktualis.json").write_text(
        json.dumps(registry, ensure_ascii=False))

    assert loader.update_database(data_dir, db_path) is True

    c = sqlite3.connect(db_path)
    try:
        assert c.execute(
            "SELECT title FROM agenda_doc_item WHERE bill_code = 'T/100/1'"
        ).fetchone()[0] == "Új napirendi pont"
    finally:
        c.close()


def test_an_item_gains_its_bill_link_when_the_iromany_lands(conn):
    """The order paper is published before the iromány is registered often
    enough that the link has to be re-derived, not resolved once."""
    conn.execute("UPDATE agenda_doc_item SET bill_code = 'T/4242' "
                 "WHERE bill_code = 'T/999'")
    conn.commit()
    loader._relink_agenda_bills(conn)
    assert conn.execute(
        "SELECT bill_id FROM agenda_doc_item WHERE bill_code = 'T/4242'"
    ).fetchone()[0] is None

    conn.execute(
        "INSERT INTO bill(id, bill_number, period_number, title) VALUES (?,?,?,?)",
        ("bill-late", "T/4242", 43, "Egy később beiktatott iromány"))
    loader._relink_agenda_bills(conn)
    conn.commit()
    assert conn.execute(
        "SELECT bill_id FROM agenda_doc_item WHERE bill_code = 'T/4242'"
    ).fetchone()[0] == "bill-late"


# ---------------------------------------------------------------------------
# The API (NR-5)
# ---------------------------------------------------------------------------


@pytest.fixture
def upcoming(client):
    r = client.get("/api/v1/proceedings/upcoming")
    assert r.status_code == 200
    return r.json()


def test_the_endpoint_returns_the_days_with_their_items(upcoming):
    agenda = upcoming["agenda"]
    assert agenda["slug"] == "nr_20260514_elfogadott"
    assert agenda["firstDate"] == "2026-05-14"
    assert agenda["lastDate"] == "2026-05-15"
    assert [len(d["items"]) for d in agenda["days"]] == [3, 1]
    monday = agenda["days"][0]
    assert monday["startsAt"] == "13:00"
    assert monday["decisionsFrom"] == ["14:45"]
    assert monday["endsNote"].endswith("megtárgyalása")


def test_every_claim_carries_the_document_and_its_moment(upcoming):
    """An order paper is a plan; the reader has to be able to see which document
    said so and as of when (TRUST-1)."""
    agenda = upcoming["agenda"]
    assert agenda["url"].endswith("nr_20260514_elfogadott")
    assert agenda["statusLabel"] == "Elfogadott"
    assert agenda["statusAt"] == "2026-05-14T15:45"
    assert upcoming["source"]["page"].endswith("/aktualis")
    assert upcoming["source"]["scrapedAt"]


def test_an_item_carries_its_bill_link_and_its_flags(upcoming):
    item = upcoming["agenda"]["days"][0]["items"][1]
    assert item["billCode"] == "T/100"
    assert item["billId"] == "bill-uuid-1"
    # Joined from the bill registry, so the card can show more than a number.
    assert item["billTitle"] == "A költségvetésről szóló törvényjavaslat"
    assert item["flags"] == ["cardinal", "quorum", "two_thirds"]
    assert item["notes"] == ["Határozatképesség szükséges!"]
    assert item["detail"]["committee"].endswith("Pénzügyi Bizottság")


def test_flags_and_notes_are_lists_even_when_empty(upcoming):
    """The serializer never hands the UI a null it would have to guard."""
    item = upcoming["agenda"]["days"][1]["items"][0]
    assert item["flags"] == [] and item["notes"] == []
    assert item["detail"] is None
    assert item["billId"] is None and item["billTitle"] is None


def test_the_other_documents_and_the_house_committee_come_along(upcoming):
    assert [d["kind"] for d in upcoming["documents"]] == ["sitting_plan"]
    assert upcoming["documents"][0]["label"] == "Ülésterv"
    assert upcoming["houseCommittee"]["date"] == "2026-05-18"
    assert upcoming["houseCommittee"]["time"] == "13:00"


def test_meta_advertises_the_feature(client):
    assert client.get("/api/v1/meta").json()["features"]["upcoming_agenda"] is True


def test_a_db_without_the_tables_answers_empty_rather_than_failing(
        tmp_path, data_dir, monkeypatch):
    """A DB built before this stage existed keeps working: the endpoint says
    there is nothing announced rather than returning a 500."""
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
    c = TestClient(app)

    body = c.get("/api/v1/proceedings/upcoming").json()
    assert body == {"agenda": None, "documents": [], "houseCommittee": None,
                    "source": None}
    assert c.get("/api/v1/meta").json()["features"]["upcoming_agenda"] is False
