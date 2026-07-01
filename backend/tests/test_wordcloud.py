"""Word-cloud TF·IDF ranking (WCLOUD): the cloud favours words distinctive to a
sitting day over the parliamentary vocabulary that recurs every day."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import loader
from app.wordfreq import tfidf_scores
from tests.conftest import _registry, _session_record


def test_tfidf_favours_distinctive_word():
    """A word in 1 of 10 days outranks a word in all 10 days at equal frequency,
    and even when the ubiquitous word is several times more frequent."""
    df = {"klima": 1, "magyar": 10}
    s = tfidf_scores({"klima": 4, "magyar": 4}, df, n_docs=10)
    assert s["klima"] > s["magyar"]
    s2 = tfidf_scores({"klima": 4, "magyar": 20}, df, n_docs=10)
    assert s2["klima"] > s2["magyar"]
    # a word on every day is driven toward (but not below) zero
    assert tfidf_scores({"magyar": 50}, {"magyar": 10}, 10)["magyar"] >= 0


def _wc_record(session, sitting, date, sentences):
    rec = _session_record(session=session, sitting=sitting, date=date)
    rec["data"][0]["textContents"][0]["textBody"][0]["sentences"] = [
        {"text": t, "timeStart": 0.0, "timeEnd": 1.0} for t in sentences]
    rec["data"] = rec["data"][:1]   # keep only the one speech with text
    return rec


@pytest.fixture
def wc_client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "processed").mkdir(parents=True)
    (data / "processed" / "representatives-43.json").write_text(
        json.dumps(_registry(), ensure_ascii=False))
    # 3 sitting days: "klímavédelem" is unique to day 1; "magyarország" recurs
    # on every day at the *same* per-day frequency as klímavédelem on day 1.
    (data / "processed" / "43001-session.json").write_text(json.dumps(_wc_record(
        "43001", 1, "2026-05-09",
        ["Klímavédelem klímavédelem klímavédelem klímavédelem.",
         "Magyarország magyarország magyarország magyarország."]),
        ensure_ascii=False))
    (data / "processed" / "43002-session.json").write_text(json.dumps(_wc_record(
        "43002", 2, "2026-05-10",
        ["Magyarország magyarország magyarország magyarország."]),
        ensure_ascii=False))
    (data / "processed" / "43003-session.json").write_text(json.dumps(_wc_record(
        "43003", 3, "2026-05-11",
        ["Magyarország magyarország magyarország magyarország."]),
        ensure_ascii=False))
    out = tmp_path / "wc.db"
    loader.build_database(data, out)

    from app.config import settings
    from app import db as db_module
    monkeypatch.setattr(settings, "db_path", str(out))
    monkeypatch.setattr(db_module.settings, "db_path", str(out))
    from app.main import app
    return TestClient(app)


def test_doc_freq_table_counts_days(wc_client):
    import sqlite3
    from app.config import settings
    c = sqlite3.connect(settings.db_path); c.row_factory = sqlite3.Row
    df = {r["word"]: r["doc_count"]
          for r in c.execute("SELECT word, doc_count FROM word_doc_freq "
                             "WHERE period_number = 43")}
    assert df["klímavédelem"] == 1 and df["magyarország"] == 3
    assert c.execute("SELECT n_docs FROM word_doc_total WHERE period_number=43"
                     ).fetchone()["n_docs"] == 3
    c.close()


def test_distinctive_word_tops_the_cloud(wc_client):
    d = wc_client.get("/api/v1/proceedings/sessions/43001/wordcloud").json()
    words = {w["text"]: w for w in d["words"]}
    assert "klímavédelem" in words and "magyarország" in words
    # equal raw counts on this day…
    assert words["klímavédelem"]["count"] == words["magyarország"]["count"]
    # …but the day-distinctive word outweighs the every-day one and ranks first.
    assert words["klímavédelem"]["weight"] > words["magyarország"]["weight"]
    assert d["words"][0]["text"] == "klímavédelem"


def test_new_words_are_first_ever_occurrences(wc_client):
    """NEW-1: a word is 'new' only on the earliest sitting day (by date) that ever
    said it. Day 1 (2026-05-09) is the corpus's first day, so both its words
    debut there; 'magyarország' recurs on days 2 and 3 but is not new again."""
    d1 = wc_client.get("/api/v1/proceedings/sessions/43001/new-words").json()
    day1 = {w["text"]: w for w in d1["words"]}
    assert "klímavédelem" in day1 and "magyarország" in day1
    assert day1["klímavédelem"]["count"] == 4

    # Day 2/3 only repeat 'magyarország' (already said on day 1) → nothing new.
    d2 = wc_client.get("/api/v1/proceedings/sessions/43002/new-words").json()
    assert [w["text"] for w in d2["words"]] == []
    d3 = wc_client.get("/api/v1/proceedings/sessions/43003/new-words").json()
    assert "magyarország" not in {w["text"] for w in d3["words"]}


def test_word_first_seen_table_picks_earliest_day(wc_client):
    import sqlite3
    from app.config import settings
    c = sqlite3.connect(settings.db_path); c.row_factory = sqlite3.Row
    rows = {r["word"]: r["session_id"]
            for r in c.execute("SELECT word, session_id FROM word_first_seen")}
    c.close()
    # 'magyarország' is said on all three days but first-seen is the earliest one.
    assert rows["magyarország"] == "43001"
    assert rows["klímavédelem"] == "43001"
