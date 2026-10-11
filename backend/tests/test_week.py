"""The sitting week at a glance — the home page's "A múlt héten" panel.

Two things matter here beyond the arithmetic. Which week is "last week" is a
statement about *today*, and a recess must not leave the panel empty, so the
week resolution is tested against a pinned calendar. And parlament.hu publishes a
held sitting in instalments (SIT-2), so a day with nothing in it yet has to be
listed as such rather than silently dropped from the week — the panel's whole
job on a Monday morning.

The shared fixture holds one half-published sitting on Saturday 2026-05-09
(`conftest._session_record`): two speeches, one of them without a transcript.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.modules.proceedings import router as proceedings_router


@pytest.fixture
def today(monkeypatch):
    """Pin the Hungarian calendar the endpoint reads "today" off."""
    def pin(d: date):
        monkeypatch.setattr(proceedings_router, "_hu_today", lambda: d)
    return pin


def _week(client, **params):
    res = client.get("/api/v1/proceedings/week", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def test_last_week_is_the_monday_to_sunday_week_before_this_one(client, today):
    today(date(2026, 5, 13))                   # the Wednesday after the sitting
    body = _week(client)
    assert body["week"] == {"start": "2026-05-04", "end": "2026-05-10", "last_week": True}
    [day] = body["days"]
    assert day["id"] == "43001" and day["date"] == "2026-05-09"
    assert day["status"] == "published"
    # One of two speeches has no transcript yet, and the day is young enough for
    # it still to come: the same `pending` the sittings list shows (SIT-2).
    assert (day["speeches"], day["speeches_with_text"]) == (2, 1)
    assert day["processing"] == "pending"
    assert body["totals"] == {"days": 1, "speeches": 2, "seconds": 40.0, "agenda_items": 2}


def test_a_week_the_house_did_not_sit_falls_back_to_the_latest_sitting_week(client, today):
    today(date(2026, 5, 27))                   # last week (05-18..24) had no sitting
    body = _week(client)
    assert body["week"]["start"] == "2026-05-04"
    # ...and says it is not last week, so the panel can title itself honestly.
    assert body["week"]["last_week"] is False
    assert [d["id"] for d in body["days"]] == ["43001"]


def test_the_week_still_being_held_is_never_reported_as_last_week(client, today):
    # The sitting is this week's; there is nothing before it. The current week is
    # the order paper's to speak for, so there is no "last week" at all.
    today(date(2026, 5, 9))
    body = _week(client)
    assert body["week"] is None
    assert body["days"] == [] and body["totals"]["days"] == 0


def test_an_explicit_date_reports_the_week_containing_it(client, today):
    today(date(2026, 7, 1))
    body = _week(client, date="2026-05-06")
    assert (body["week"]["start"], body["week"]["end"]) == ("2026-05-04", "2026-05-10")
    assert body["week"]["last_week"] is False
    # Weeks old by now: the missing transcript is not coming any more, and the day
    # is no longer promised as "being processed".
    assert body["days"][0]["processing"] == "incomplete"


def test_days_with_nothing_published_yet_are_listed_not_dropped(conn, client, today):
    conn.executemany(
        "INSERT INTO session (id, period_number, sitting, date, status) VALUES (?,?,?,?,?)",
        [("43002", 43, 2, "2026-05-07", "scheduled"),
         ("43003", 43, 3, "2026-05-08", "awaiting_media")])
    conn.commit()
    today(date(2026, 5, 11))
    body = _week(client)
    assert [(d["id"], d["status"], d["speeches"]) for d in body["days"]] == [
        ("43002", "scheduled", 0), ("43003", "awaiting_media", 0), ("43001", "published", 2)]
    # The day's own status carries the answer; no completeness claim is made.
    assert [d["processing"] for d in body["days"][:2]] == [None, None]
    assert body["totals"]["days"] == 3 and body["totals"]["speeches"] == 2


def test_speakers_rank_by_speaking_time_and_leave_the_chair_out(conn, client, today):
    today(date(2026, 5, 13))
    conn.execute("UPDATE speech SET procedural = 1 WHERE person_id = 'n002'")
    conn.commit()
    body = _week(client)
    # Chairing speech is shown on the day but never counted as speaking (STAT-1).
    assert [s["person_id"] for s in body["top_speakers"]] == ["k001"]
    top = body["top_speakers"][0]
    assert (top["label"], top["speeches"], top["seconds"]) == ("Kovács Béla", 1, 30.0)
    assert top["faction"]["label"] == "Fidesz"
    # The day's own length still includes it.
    assert body["totals"]["seconds"] == 40.0


def test_the_week_words_come_from_the_transcripts(client, today):
    today(date(2026, 5, 13))
    words = [w["text"] for w in _week(client)["words"]]
    assert "költségvetés" in words and "fejlesztés" in words


def test_the_speakers_carry_the_office_they_spoke_in(conn, client, today):
    today(date(2026, 5, 13))
    conn.execute("UPDATE speech SET speaker_office = 'pénzügyminiszter' "
                 "WHERE person_id = 'k001'")
    conn.commit()
    offices = {s["person_id"]: s["office"] for s in _week(client)["top_speakers"]}
    # A minister tops a question day by office; an MP speaking as an MP has none.
    assert offices == {"k001": "pénzügyminiszter", "n002": None}


def test_the_words_say_whether_they_were_scored_or_only_counted(conn, client, today):
    today(date(2026, 5, 13))
    # With no document frequencies to score against, the week's words are only
    # its most frequent ones, and the response says so rather than passing them
    # off as distinctive.
    conn.execute("DELETE FROM word_doc_total")
    conn.commit()
    body = _week(client)
    assert body["words_measure"] == "frequency"
    assert body["words"]                        # still served; the panel decides


def test_the_words_are_tfidf_scored_where_the_cycle_has_frequencies(client, today):
    today(date(2026, 5, 13))
    assert _week(client)["words_measure"] == "tfidf"


def test_this_weeks_days_before_today_are_listed_but_not_summed(conn, client, today):
    # Monday's sitting has been held, the order paper (which shows only the days
    # still ahead) has dropped it, and last week is still the week reported.
    conn.executemany(
        "INSERT INTO session (id, period_number, sitting, date, status) VALUES (?,?,?,?,?)",
        [("43002", 43, 2, "2026-05-11", "scheduled"),
         ("43003", 43, 3, "2026-05-13", "scheduled")])     # today: the order paper's
    conn.commit()
    today(date(2026, 5, 13))
    body = _week(client)
    assert body["week"]["start"] == "2026-05-04"
    assert [(d["id"], d["status"], d["speeches"]) for d in body["this_week"]] == [
        ("43002", "scheduled", 0)]
    assert [d["id"] for d in body["days"]] == ["43001"]
    assert body["totals"]["days"] == 1


def test_on_a_monday_this_week_has_nothing_behind_it_yet(conn, client, today):
    conn.execute("INSERT INTO session (id, period_number, sitting, date, status) "
                 "VALUES ('43002', 43, 2, '2026-05-11', 'scheduled')")
    conn.commit()
    today(date(2026, 5, 11))
    assert _week(client)["this_week"] == []


def test_this_week_is_listed_even_with_no_earlier_week_to_report(conn, client, today):
    # The sitting on Saturday 05-09 is this week's, so there is no last week; a
    # Sunday reader still sees that the House sat.
    today(date(2026, 5, 10))
    body = _week(client)
    assert body["week"] is None
    assert [d["id"] for d in body["this_week"]] == ["43001"]


def test_an_explicit_date_has_no_this_week(client, today):
    today(date(2026, 5, 13))
    assert _week(client, date="2026-05-06")["this_week"] == []


@pytest.mark.parametrize("bad", ["2026-5-6", "tegnap", "2026-02-30"])
def test_a_malformed_date_is_rejected(client, bad):
    assert client.get("/api/v1/proceedings/week", params={"date": bad}).status_code == 422
