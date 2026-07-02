"""Shared fixtures: build a small SQLite DB from synthetic session records and
expose both a raw connection and a TestClient bound to it.

The fixtures use the real loader and real schema, so the tests exercise the
production code paths (OPS-3) rather than a stand-in.
"""

from __future__ import annotations

import os
import sqlite3

# Default the word cloud to the dependency-free regex backend so the shared
# fixtures are deterministic and don't load the (optional, ~127 MB) HuSpaCy model.
# The dedicated HuSpaCy tests opt back in explicitly. Must precede the app imports
# below, which instantiate config.settings from the environment.
os.environ.setdefault("PARLAMONITOR_WORDCLOUD_BACKEND", "regex")

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
                                         "timeStart": 10.0, "timeEnd": 20.0,
                                         "paragraph": 0},
                                        {"text": "Az ÁGAZATI fejlesztés ügye sürgős!",
                                         "timeStart": 20.0, "timeEnd": 40.0,
                                         "paragraph": 1},
                                    ]}]}],
                "debug": {"confidence": 0.7, "align-method": "estimated-day-offset",
                          "speechUUID": "uuid-sp-1"},
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


def _bills_registry():
    """Three irományok: two törvényjavaslatok (mainType T) — one with a known-MP
    sponsor (links to k001), one government bill (no MP link) — and one non-bill
    document (an interpelláció, mainType I) for the "Egyéb irományok" page, so the
    main_type / main_type_not / type filters are exercised (BILL-9)."""
    return {
        "meta": {"cycle": 43, "mainTypes": "all", "source": "felicitas-iromany-api",
                 "scrapedAt": "2026-06-18T00:00:00+00:00", "count": 3},
        "data": [
            {"billId": "bill-uuid-1", "billNumber": "T/100", "billNumberSort": 100,
             "title": "A költségvetésről szóló törvényjavaslat", "type": "törvényjavaslat",
             "mainType": "T", "status": "tárgysorozatban",
             "submittedDate": "2026-05-10T09:00:00Z",
             "textUrl": "https://www.parlament.hu/irom43/00100/00100.pdf",
             "textCaption": "szöveges PDF", "noText": False,
             "stages": [
                 {"key": "TARGYSOROZATBAN", "label": "Tárgysorozatban", "done": True},
                 {"key": "ALTALANOS_VITA_ALATT", "label": "Általános vita alatt", "done": True},
                 {"key": "ZAROSZAVAZAS", "label": "Zárószavazás", "done": False},
             ],
             "sponsors": [{"personID": "k001", "factionId": 7, "committeeId": None,
                           "label": "Kovács Béla (Fidesz)"}],
             "detail": {
                 "header": {"subtype": "törvényjavaslat nemzetközi szerződésről",
                            "character": "új", "negotiationMode": "kivételes tárgyalásban",
                            "statusType": "folyamatban", "currentEvent": "általános vita alatt",
                            "promulgationNumber": None, "mkNumber": None,
                            "promulgationDate": None, "remark": "teszt megjegyzés",
                            "lastModifier": "100/4"},
                 "events": [
                     {"date": "2026-05-26T13:04:20Z", "name": "kivételességi javaslat elfogadva",
                      "personID": "k001", "committeeId": None, "relatedLabel": "Kovács Béla",
                      "speechNumber": "3/43", "speechId": "uuid-sp-1",
                      "voteId": "v-1", "remark": ""},
                     {"date": "2026-05-26T16:45:00Z", "name": "részletes vita megkezdve",
                      "personID": None, "committeeId": None, "relatedLabel": None,
                      "speechNumber": None, "speechId": None,
                      "voteId": None, "remark": None}],
                 "committeeEvents": [
                     {"date": "2026-05-26T17:01:00Z", "name": "a bizottság előadója",
                      "committee": "Törvényalkotási Bizottság", "committeeId": "c-1",
                      "personID": "k001", "personLabel": "Kovács Béla",
                      "amendment": "100/2", "overreachingAmendment": None, "report": "100/3"}],
                 "votes": [
                     {"voteId": "v-1", "date": "2026-05-26T13:04:20Z",
                      "subject": "kivételességi javaslat elfogadva",
                      "yes": 139, "no": 48, "abstain": 0, "result": "Elfogadva"}],
                 "deadlines": [
                     {"name": "módosító javaslat benyújtása", "deadline": "2026-05-26T16:05:00Z",
                      "reference": "HHSZ 62. § (3)", "remark": None}],
                 "committees": [
                     {"committee": "Törvényalkotási Bizottság", "committeeId": "c-1",
                      "role": "Kijelölt bizottság", "reference": "62. § (5)", "parts": None}],
                 "documents": [
                     {"kind": "justification", "title": "önálló indítvány és indokolása",
                      "url": "https://www.parlament.hu/irom43/00100/00100.pdf",
                      "date": "2026-05-10T09:00:00Z", "published": None},
                     {"kind": "background", "title": "háttéranyag",
                      "url": "https://www.parlament.hu/documents/d/guest/x",
                      "date": None, "published": None}],
                 "motionSummary": [
                     {"type": "Kivételességi javaslat", "valid": 1, "withdrawn": 0, "total": 1}],
                 "motions": [
                     {"iromanyId": "mot-1", "billNumber": "T/100/3", "billNumberSort": 100003,
                      "mainType": "egyéb", "type": "Módosító javaslat",
                      "submittedDate": "2026-05-20T10:00:00Z",
                      "textUrl": "https://www.parlament.hu/irom43/00100/00100-0003.pdf",
                      "textCaption": "szöveges PDF", "noText": False, "hasVote": True,
                      "note": None,
                      "sponsors": [{"personID": "k001", "factionId": 7,
                                    "committeeId": None, "label": "Kovács Béla (Fidesz)"}]},
                     {"iromanyId": "mot-2", "billNumber": "T/100/1", "billNumberSort": 100001,
                      "mainType": "egyéb", "type": "Bizottság kijelölése tárgysorozatba vételre",
                      "submittedDate": "2026-05-15T10:00:00Z",
                      "textUrl": None, "textCaption": None, "noText": True, "hasVote": False,
                      "note": None,
                      "sponsors": [{"personID": None, "factionId": None,
                                    "committeeId": "c-9", "label": "az Országgyűlés elnöke"}]}],
             }},
            {"billId": "bill-uuid-2", "billNumber": "T/101", "billNumberSort": 101,
             "title": "A kormány javaslata", "type": "törvényjavaslat",
             "mainType": "T", "status": "kihirdetve",
             "submittedDate": "2026-05-12T09:00:00Z",
             "textUrl": None, "textCaption": None, "noText": True,
             "sponsors": [{"personID": None, "factionId": None, "committeeId": None,
                           "label": "kormány (pénzügyminiszter)"}],
             # A promulgated bill carries its Magyar Közlöny links (kihirdetve).
             "detail": {
                 "header": {"mkNumber": 44, "promulgationDate": "2026-05-09",
                            "kozlonyUrl": "https://magyarkozlony.hu/?year=2026&month=&serial=44",
                            "kozlonyDocUrl": "https://magyarkozlony.hu/dokumentumok/abc123/megtekintes"}}},
            {"billId": "doc-uuid-3", "billNumber": "I/5", "billNumberSort": 5,
             "title": "Interpelláció a közlekedésről", "type": "interpelláció",
             "mainType": "I", "status": "benyújtva",
             "submittedDate": "2026-06-01T09:00:00Z",
             "textUrl": None, "textCaption": None, "noText": True,
             "sponsors": [{"personID": "k001", "factionId": 7, "committeeId": None,
                           "label": "Kovács Béla"}]},
        ],
    }


def _votes_registry():
    """Two votes. The first decides bill T/100 (id bill-uuid-1) and has the same
    vote id (v-1) as that bill's vote tally — exercising the bill <-> vote link —
    with a per-MP roll call (k001 Igen, n002 Nem) and a per-faction breakdown.
    The second has no roll call (a list/voice vote) and decides a non-held
    iromány (H/9), so its subject stays label-only (no bill link)."""
    return {
        "meta": {"cycle": 43, "dateFrom": "2026-05-09", "dateTo": "2026-06-19",
                 "source": "felicitas-szavazas-api",
                 "scrapedAt": "2026-06-19T00:00:00+00:00", "count": 2},
        "data": [
            {"voteId": "v-1", "datetime": "2026-05-26T13:04:20Z",
             "votingMode": "Gépi szavazás", "subject": "kivételességi javaslat elfogadva",
             "result": "Elfogadva", "yes": 1, "no": 1, "abstain": 0, "cycle": 43,
             "hasPerMp": True,
             "subjects": [
                 {"billId": "bill-uuid-1", "billNumber": "T/100",
                  "title": "A költségvetésről szóló törvényjavaslat"}],
             "detail": {
                 "header": {"votingModeDisplay": "Gépi szavazás",
                            "subjectDisplay": "kivételességi javaslat elfogadva",
                            "totalVotes": 2, "remark": None},
                 "records": [
                     {"personID": "k001", "name": "Kovács Béla",
                      "factionName": "Fidesz", "voteValue": "Igen"},
                     {"personID": "n002", "name": "Nagy Anna",
                      "factionName": "TISZA", "voteValue": "Nem"},
                     {"personID": "x999", "name": "Külső Géza",
                      "factionName": "független", "voteValue": "Tartózkodás"}],
                 "factionStats": [
                     {"factionName": "Fidesz", "factionId": 7, "againstFaction": "0 fő",
                      "total": 1, "yes": 1, "no": 0, "abstain": 0, "absent": 0,
                      "notVoting": 0},
                     {"factionName": "TISZA", "factionId": None, "againstFaction": "0 fő",
                      "total": 1, "yes": 0, "no": 1, "abstain": 0, "absent": 0,
                      "notVoting": 0}],
             }},
            {"voteId": "v-2", "datetime": "2026-05-27T10:00:00Z",
             "votingMode": "Listás", "subject": "határozati javaslat",
             "result": "Elutasítva", "yes": 0, "no": 2, "abstain": 0, "cycle": 43,
             "hasPerMp": False,
             "subjects": [
                 {"billId": "h-uuid-9", "billNumber": "H/9",
                  "title": "Egy határozati javaslat"}],
             "detail": {"header": {}, "records": [], "factionStats": []}},
        ],
    }


@pytest.fixture
def db_path(tmp_path):
    data = tmp_path / "data"
    (data / "processed").mkdir(parents=True)
    import json
    (data / "processed" / "representatives-43.json").write_text(
        json.dumps(_registry(), ensure_ascii=False))
    (data / "processed" / "bills-43.json").write_text(
        json.dumps(_bills_registry(), ensure_ascii=False))
    (data / "processed" / "votes-43.json").write_text(
        json.dumps(_votes_registry(), ensure_ascii=False))
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
