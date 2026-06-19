"""Shared fixtures: build a small SQLite DB from synthetic session records and
expose both a raw connection and a TestClient bound to it.

The fixtures use the real loader and real schema, so the tests exercise the
production code paths (OPS-3) rather than a stand-in.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app import loader


def _session_record(session="43001", period=43, sitting=1, date="2026-05-09"):
    """A two-speech sitting: one estimated-timing speech with text, one
    video-only speech with no transcript (VIE-8 / SCR-5 degraded case)."""
    video = "https://example/playlist.m3u8"
    return {
        "meta": {"session": session, "electoralPeriod": period, "sitting": sitting,
                 "date": date, "dateStart": f"{date}T08:00:00",
                 "dateEnd": f"{date}T10:00:00", "source": "felicitas-json",
                 "dayVideoURI": video, "timingMethod": "estimated-day-offset",
                 "dayVideoPlayseq": "https://sgis.parlament.hu/archive/playseq.php?date1=x",
                 "sourceScrapedAt": "2026-06-18T00:00:00+00:00"},
        "data": [
            {
                "originID": f"{period}-{sitting}-1", "speechIndex": 1,
                "electoralPeriod": {"number": period},
                "agendaItem": {"title": "Napirend előtt", "officialTitle": "Napirend előtt",
                               "type": "procedural", "nativeType": "HU-procedural"},
                "people": [{"type": "memberOfParliament", "label": "Kovács Béla",
                            "context": "main-speaker", "personID": "k001",
                            "firstname": "Béla", "lastname": "Kovács",
                            "faction": {"label": "Fidesz", "id": 7}}],
                "media": {"videoFileURI": video, "duration": 7200,
                          "creator": "Magyar Országgyűlés", "license": "https://lic",
                          "sourcePage": "https://parlament.hu/x",
                          "videoStart": 10.0, "videoEnd": 40.0},
                "textContents": [{"type": "proceedings", "sourceURI": "https://parlament.hu/x",
                                  "textBody": [{"speech_id": f"{period}-{sitting}-1",
                                    "sentences": [
                                        {"text": "A költségvetés fontos kérdés.",
                                         "timeStart": 10.0, "timeEnd": 20.0},
                                        {"text": "Az ÁGAZATI fejlesztés ügye sürgős!",
                                         "timeStart": 20.0, "timeEnd": 40.0},
                                    ]}]}],
                "debug": {"confidence": 0.7, "align-method": "estimated-day-offset"},
            },
            {
                "originID": f"{period}-{sitting}-2", "speechIndex": 2,
                "electoralPeriod": {"number": period},
                "agendaItem": {"title": "Szavazás", "officialTitle": "Szavazás",
                               "type": "voting", "nativeType": "HU-voting"},
                "people": [{"type": "memberOfParliament", "label": "Nagy Anna",
                            "context": "main-speaker", "personID": "n002",
                            "firstname": "Anna", "lastname": "Nagy",
                            "faction": {"label": "TISZA"}}],
                "media": {"videoFileURI": video, "duration": 7200,
                          "creator": "Magyar Országgyűlés", "license": "https://lic",
                          "sourcePage": "https://parlament.hu/y",
                          "videoStart": 50.0, "videoEnd": 60.0},
                "textContents": [],   # no transcript -> degraded, still ingested
                "debug": {"confidence": 0.5, "align-method": None},
            },
        ],
    }


def _registry():
    return {
        "meta": {"cycle": 43, "cycleStart": "2026-05-09", "cycleEnd": None,
                 "source": "felicitas-kepviselo-api", "withDetails": True, "count": 2},
        "data": [
            {"personID": "k001", "label": "Kovács Béla", "firstname": "Béla",
             "lastname": "Kovács", "faction": {"label": "Fidesz", "id": 7, "position": "tag"},
             "constituency": "Budapest 1.", "highestEducation": "egyetem",
             "factionHistory": [{"cycle": "2026-", "label": "Fidesz", "start": "2026", "end": None}],
             "education": [{"degree": "jogász", "institution": "ELTE"}],
             "statistics": {"billsSubmitted": [{"cycle": 43, "ownBills": 3}]}},
            {"personID": "n002", "label": "Nagy Anna", "firstname": "Anna",
             "lastname": "Nagy", "faction": {"label": "TISZA"},
             "constituency": "Pest 4."},
        ],
    }


@pytest.fixture
def db_path(tmp_path):
    data = tmp_path / "data"
    (data / "processed").mkdir(parents=True)
    import json
    (data / "processed" / "representatives-43.json").write_text(
        json.dumps(_registry(), ensure_ascii=False))
    (data / "processed" / "43001-session.json").write_text(
        json.dumps(_session_record(), ensure_ascii=False))
    out = tmp_path / "test.db"
    loader.build_database(data, out)
    return out


@pytest.fixture
def conn(db_path):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


@pytest.fixture
def client(db_path, monkeypatch):
    # Point the API's settings + db module at the freshly built test DB.
    from app.config import settings
    monkeypatch.setattr(settings, "db_path", str(db_path))
    monkeypatch.setattr(db_module.settings, "db_path", str(db_path))
    from app.main import app
    return TestClient(app)
