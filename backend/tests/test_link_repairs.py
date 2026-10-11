"""Links the loader derives — sitting-day ids, portraits, minutes, gazette — and
the one-off `repair_links` pass that brings an existing DB in line with them."""
from __future__ import annotations

import contextlib
import json
import sqlite3

from app import loader

DAY_ID = "2730192"          # a pre-2026 day: parlament.hu numbers it plainly


@contextlib.contextmanager
def _db(db_path):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _write_raw_day(data_dir, session="43001", day_id=DAY_ID):
    raw = data_dir / "original" / "plenary" / f"raw-{session}-day.json"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(json.dumps({"cycle": 43, "session": session, "day_uuid": day_id,
                               "speeches": [{"text_html": "x" * 10000}]}))


def test_build_takes_the_day_id_from_the_raw_bundle(data_dir, tmp_path, monkeypatch):
    """Processed records written before `meta.dayId` existed carry the day id
    only in the raw bundle; the build reads it from there."""
    _write_raw_day(data_dir)
    db_path = tmp_path / "raw.db"
    loader.build_database(data_dir, db_path)
    with _db(db_path) as c:
        assert c.execute("SELECT day_id FROM session WHERE id = 43001"
                         ).fetchone()[0] == DAY_ID

    from fastapi.testclient import TestClient
    from app import db as db_module
    from app.config import settings
    monkeypatch.setattr(settings, "db_path", str(db_path))
    monkeypatch.setattr(db_module.settings, "db_path", str(db_path))
    from app.main import app
    from app.parlament_links import sitting_day_page_url
    s = TestClient(app).get("/api/v1/proceedings/sessions/43001").json()["session"]
    assert s["source_page"] == sitting_day_page_url(DAY_ID)


def test_meta_day_id_is_stored_as_is(conn):
    rec = json.loads(json.dumps({"meta": {"session": "43050", "electoralPeriod": 43,
                                          "sitting": 50, "date": "2026-10-01",
                                          "dayId": "f3471584-83b9-4bf5-b61f-bcb72ab23425"},
                                 "data": []}))
    loader.load_session(conn, rec)
    assert conn.execute("SELECT day_id FROM session WHERE id = 43050").fetchone()[0] \
        == "f3471584-83b9-4bf5-b61f-bcb72ab23425"


def test_a_day_without_an_id_keeps_its_stored_page(client):
    # The fixture day has no raw bundle and no meta.dayId.
    s = client.get("/api/v1/proceedings/sessions/43001").json()["session"]
    assert "#page=" not in (s["source_page"] or "")


def test_portraits_are_never_hot_linked(conn):
    loader.load_representatives(conn, {"meta": {"cycle": 43}, "data": [
        {"personID": "e267", "label": "Elek István",
         "photoURI": "https://www.parlament.hu/web/guest/felicitas/api/query/"
                     "resource/kepviseloexportok/kepviselo-exported-queries-"
                     "provider/kepviselo-kepek/e267"},
        {"personID": "006F", "label": "Palóc André", "photoFile": "006F.jpg"},
    ]})
    rows = dict(conn.execute("SELECT person_id, photo_uri FROM person "
                             "WHERE person_id IN ('e267', '006F')").fetchall())
    assert rows == {"e267": None, "006F": "/media/photos/006F.jpg"}


def test_empty_pre_1998_gazette_listing_is_not_linked(conn):
    def bill(bid, date, doc=None):
        return {"billId": bid, "billNumber": f"T/{bid}", "title": "X",
                "detail": {"header": {
                    "promulgationDate": date, "mkNumber": "127",
                    "kozlonyUrl": f"https://magyarkozlony.hu/?year={date[:4]}"
                                  "&month=&serial=127",
                    "kozlonyDocUrl": doc}}}
    loader.load_bills(conn, {"meta": {"cycle": 34}, "data": [
        bill("1001", "1992-12-22"),                       # not on the site: dropped
        bill("1002", "2005-03-01"),                       # a failed fetch: kept
        bill("1003", "1990-05-23", doc="https://magyarkozlony.hu/dokumentumok/ab/megtekintes"),
    ]})
    rows = {r[0]: r[1] for r in conn.execute(
        "SELECT id, kozlony_url FROM bill WHERE id IN ('1001','1002','1003')")}
    assert rows["1001"] is None
    assert rows["1002"].endswith("year=2005&month=&serial=127")
    assert rows["1003"] is not None          # had a PDF, so the listing exists too


def test_update_repairs_an_existing_db_once_even_when_no_file_changed(data_dir, db_path):
    """The archive cycles' files never change again, so the repair has to reach
    their rows without a reload — once per repair version, then never again."""
    _write_raw_day(data_dir)
    hotlink = ("https://www.parlament.hu/web/guest/felicitas/api/query/resource/"
               "kepviseloexportok/kepviselo-exported-queries-provider/kepviselo-kepek/k001")
    with _db(db_path) as c:      # what a DB built by the old loader holds
        c.execute("UPDATE session SET day_id = NULL")
        c.execute("UPDATE person SET photo_uri = ? WHERE person_id = 'k001'", (hotlink,))
        mid = c.execute("SELECT id FROM committee_meeting WHERE minutes_url IS NOT NULL"
                        ).fetchone()[0]
        c.execute("UPDATE committee_meeting SET minutes_url = ? WHERE id = ?",
                  ("https://www.parlament.hubiz40/bizjkv40/TAB/1802191.pdf", mid))
        bid = c.execute("SELECT id FROM bill LIMIT 1").fetchone()[0]
        c.execute("""UPDATE bill SET promulgation_date = '1992-12-22',
                         kozlony_url = 'https://magyarkozlony.hu/?year=1992&month=&serial=127',
                         kozlony_doc_url = NULL WHERE id = ?""", (bid,))
        c.execute("DELETE FROM build_meta WHERE key = 'link_repairs'")

    assert loader.update_database(data_dir, db_path) is True
    with _db(db_path) as c:
        assert c.execute("SELECT day_id FROM session WHERE id = 43001").fetchone()[0] \
            == DAY_ID
        assert c.execute("SELECT photo_uri FROM person WHERE person_id = 'k001'"
                         ).fetchone()[0] is None
        assert c.execute("SELECT minutes_url FROM committee_meeting WHERE id = ?",
                         (mid,)).fetchone()[0] == \
            "https://www.parlament.hu/biz40/bizjkv40/TAB/1802191.pdf"
        assert c.execute("SELECT kozlony_url FROM bill WHERE id = ?",
                         (bid,)).fetchone()[0] is None
    # Stamped: the next idle tick is a no-op again (no 6 GB snapshot per poll).
    assert loader.update_database(data_dir, db_path) is False
