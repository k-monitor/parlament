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
# The project default model is the transformer (hu_core_news_trf), which is run
# on Modal in production and isn't installed in dev/CI environments. The HuSpaCy
# tests only need *a* model with the same key/lemma semantics, so pin the light
# md model here — they skip cleanly if even that is absent.
os.environ.setdefault("PARLAMONITOR_HUSPACY_MODEL", "hu_core_news_md")
# Likewise disable entity extraction/resolution by default: it needs the HuSpaCy
# model (offline determinism) and the Wikidata endpoint (no network in tests), and
# K-Monitor linking would fetch the tag lists during a build. The dedicated entity
# tests opt back in explicitly (with injected fetchers).
os.environ.setdefault("PARLAMONITOR_ENTITY_LINKS", "0")
os.environ.setdefault("PARLAMONITOR_KMONITOR_LINKS", "0")
# CAP topic classification is off by default too: the pass would otherwise pull a
# 2.2 GB XLM-R checkpoint off the Hub on the first fixture build. test_parlacap.py
# turns it back on with an injected fake classifier, so no test loads a model.
os.environ.setdefault("PARLAMONITOR_PARLACAP", "0")
# Disable search analytics by default so the suite writes no analytics file and
# spawns no flush thread. The `/search` endpoint's record() call becomes a no-op;
# test_analytics.py exercises the aggregator directly with its own temp DB.
os.environ.setdefault("PARLAMONITOR_SEARCH_ANALYTICS", "0")
# And likewise the election-office geography (REP-10's source), which the settlement
# module's loader pass fetches over HTTP: with it enabled, *every* fixture that builds
# a DB would pull the national settlement register off valasztas.hu. The two suites
# that need it — test_constituency_lookup and test_settlements — turn it back on in
# their own fixtures, over an injected fake fetcher, so no test touches the network.
os.environ.setdefault("PARLAMONITOR_EVK_LOOKUP", "0")

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
        # Both MPs hold a single-member seat, spelled the way parlament.hu spells one
        # ("<county> <n>. OEVK") — that string is what the constituency lookup joins
        # on (REP-10). Kovács carries an `electionHistory`, which is the per-cycle
        # source; Nagy deliberately does not, so the lookup's fallback to the single
        # stored `constituency` (gated on sitting in the period) stays exercised.
        "data": [
            {"personID": "k001", "label": "Kovács Béla", "firstname": "Béla",
             "lastname": "Kovács", "faction": {"label": "Fidesz", "id": 7, "position": "tag"},
             "wikidataId": "Q42", "wikipediaUrl": "https://hu.wikipedia.org/wiki/Kov%C3%A1cs_B%C3%A9la",
             # Birth date from Wikidata (P569) with the signs the scraper derived
             # from it; Nagy Anna has neither, as most non-linked people won't.
             "dateOfBirth": "1968-08-30", "zodiacSign": "virgo",
             "chineseZodiacSign": "monkey",
             "constituency": "Budapest 1. OEVK", "highestEducation": "egyetem",
             # Published parliamentary address; the constituency lookup offers it as
             # a mailto/copy action (REP-10). Nagy Anna deliberately has none, so the
             # "no public address" path stays covered too.
             "email": "kovacs.bela@parlament.hu",
             "factionHistory": [{"cycle": "2026-", "label": "Fidesz", "start": "2026", "end": None}],
             "electionHistory": [
                 {"cycle": "2026-", "constituency": "Budapest 1. OEVK",
                  "electionDate": "2026-04-12",
                  "mandateStart": "2026-05-08T22:00:00Z", "mandateEnd": None},
                 # An earlier cycle in a DIFFERENT constituency, so a lookup for the
                 # current one can't leak a past mandate into the answer.
                 {"cycle": "2022-2026", "constituency": "Budapest 2. OEVK",
                  "electionDate": "2022-04-03",
                  "mandateStart": "2022-05-01T22:00:00Z",
                  "mandateEnd": "2026-05-08T21:59:59Z"},
             ],
             "education": [{"degree": "jogász", "institution": "ELTE"}],
             # Asset declarations + the published CV (REP-13). Newest first, as
             # the scraper writes them. The middle one was due but never
             # published — it has no URL and stays in the list all the same.
             "cvUrl": "https://www.parlament.hu/kepv/eletrajz/hu/k001.pdf",
             "assetDeclarations": [
                 {"title": "Vagyonnyilatkozat 2026", "assetDate": "2026-05-09",
                  "url": "https://www.parlament.hu/vagynyil/2026/k001_j0260509k.pdf",
                  "deadline": "2026-06-08", "submitted": "Igen",
                  "submittedAt": "2026-06-01T10:00:00Z", "note": None},
                 {"title": "Vagyonnyilatkozat 2025", "assetDate": "2025-12-31",
                  "url": None, "deadline": "2026-01-31", "submitted": "Nem",
                  "submittedAt": None, "note": None},
             ],
             "statistics": {"billsSubmitted": [{"cycle": 43, "ownBills": 3}]}},
            {"personID": "n002", "label": "Nagy Anna", "firstname": "Anna",
             "lastname": "Nagy", "faction": {"label": "TISZA"},
             "constituency": "Pest 4. OEVK"},
        ],
    }


def _officeholders_registry():
    """The office-holder registry (tisztségviselők): every office term with its real
    dates and the portal's own office category. Covers an MP already in the roster
    (whose own record repeats one of the terms — the loader keeps the two sources
    apart) and somebody who never spoke in the House, who is loaded as an
    office-history-only person so the all-time listing (REP-11) is complete."""
    return {
        "meta": {"asOf": "2026-07-29", "source": "felicitas-tisztsegviselok-api",
                 "count": 2, "terms": 3, "rows": 3, "skippedRows": 0,
                 "categories": {"parliamentary": 1, "state-secretary": 1, "senior": 1}},
        "data": [
            {"personID": "k001", "label": "Kovács Béla", "labelFull": "Kovács Béla",
             "firstname": "Béla", "lastname": "Kovács",
             "offices": [
                 {"title": "az Országgyűlés jegyzője", "category": "parliamentary",
                  "start": "2026-05-09T22:00:00Z", "end": None},
                 {"title": "Belügyminisztérium államtitkára",
                  "category": "state-secretary",
                  "start": "2018-05-21T22:00:00Z", "end": "2022-05-24T12:00:00Z"},
             ]},
            {"personID": "zzz9", "label": "Sosem Beszélt", "labelFull": "Dr. Sosem Beszélt",
             "firstname": "Beszélt", "lastname": "Sosem",
             "offices": [{"title": "köztársasági elnök", "category": "senior",
                          "start": "2012-05-09T22:00:00Z",
                          "end": "2017-05-09T21:59:59Z"}]},
        ],
    }


def _bills_registry():
    """Three irományok: two törvényjavaslatok (mainType T) — one with a known-MP
    sponsor (links to k001), one government bill (no MP link) — and one non-bill
    document (an interpelláció, mainType I) for the question and all-iromány pages, so the
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
                      "total": 3, "yes": 1, "no": 0, "abstain": 0, "absent": 2,
                      "notVoting": 0},
                     {"factionName": "TISZA", "factionId": None, "againstFaction": "0 fő",
                      "total": 2, "yes": 0, "no": 1, "abstain": 0, "absent": 1,
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


def _aktualis_registry():
    """The Aktuális page as the scraper writes it (NR-1): the documents it links
    and the napirend parsed out of the PDF behind one of them.

    Deliberately exercises the shapes that are easy to get wrong — an item with
    no ordinal (the procedural decisions the House takes before adopting the
    agenda), the same `ref` appearing twice in a day (a bill debated and voted
    on the same day), a bill code that resolves against the fixture registry
    (T/100) and one that does not."""
    return {
        "meta": {"source": "parlament-hu-aktualis",
                 "pageUrl": "https://www.parlament.hu/web/guest/aktualis",
                 "scrapedAt": "2026-05-12T10:00:00+00:00", "documentCount": 2,
                 "agendaSlug": "nr_20260514_elfogadott", "agendaReused": False,
                 "agendaError": None, "itemCount": 4, "pdftotext": True},
        "data": {
            "documents": [
                {"slug": "nr_20260514_elfogadott", "kind": "agenda",
                 "label": "Napirend", "date": "2026-05-14",
                 "group": "Plenáris üléshez kapcsolódó információk",
                 "url": "https://www.parlament.hu/documents/d/guest/nr_20260514_elfogadott"},
                {"slug": "ut_20260514_elfogadott", "kind": "sitting_plan",
                 "label": "Ülésterv", "date": "2026-05-14",
                 "group": "Plenáris üléshez kapcsolódó információk",
                 "url": "https://www.parlament.hu/documents/d/guest/ut_20260514_elfogadott"},
            ],
            "houseCommittee": {"place": "Országház, Pázmándy Dénes terem",
                               "raw": "2026. május 18. (hétfő) 13:00 óra",
                               "date": "2026-05-18", "time": "13:00",
                               "weekday": "hétfő"},
            "agenda": {
                "slug": "nr_20260514_elfogadott",
                "url": "https://www.parlament.hu/documents/d/guest/nr_20260514_elfogadott",
                "label": "Napirend",
                "title": "AZ ORSZÁGGYŰLÉS MÁJUS 14-15-I ÜLÉSÉNEK NAPIRENDJE",
                "term": {"year": 2026, "season": "spring"},
                "extraordinary": False,
                "statusLabel": "Elfogadott", "statusAt": "2026-05-14T15:45",
                "firstDate": "2026-05-14", "lastDate": "2026-05-15",
                "itemCount": 4, "fetchedAt": "2026-05-12T10:00:00+00:00",
                "days": [
                    {"month": 5, "dayOfMonth": 14, "weekday": "CSÜTÖRTÖK",
                     "date": "2026-05-14", "startsAt": "13:00",
                     "decisionsFrom": ["14:45"],
                     "endsNote": "kb. 22:00 óra, illetve a napirendi pontok megtárgyalása",
                     "breakNote": "szükség szerint",
                     "items": [
                         {"ordinal": None, "ref": None, "billCode": "T/100/1",
                          "section": "Döntés kivételes eljárásban történő tárgyalásról",
                          "title": "A költségvetésről — kivételességi javaslat",
                          "submitter": "Kormány - pénzügyminiszter", "stage": None,
                          "timeWindow": None, "notes": None,
                          "flags": ["exceptional"]},
                         {"ordinal": 1, "ref": "1", "billCode": "T/100",
                          "section": "Az általános viták a lezárásig",
                          "title": "A költségvetésről szóló törvényjavaslat",
                          "submitter": "Kormány - pénzügyminiszter",
                          "stage": "Általános vita a lezárásig",
                          "timeWindow": "Kb. 15:30- kb. 16:30 óráig",
                          "notes": ["Határozatképesség szükséges!"],
                          "flags": ["cardinal", "quorum", "two_thirds"],
                          "detail": {"submittedOn": "2026.05.10.",
                                     "committee": "2026.05.11. Pénzügyi Bizottság",
                                     "amendmentCount": "2"}},
                         {"ordinal": 2, "ref": "1", "billCode": "T/100",
                          "section": "Döntések, határozathozatalok",
                          "title": "A költségvetésről szóló törvényjavaslat",
                          "submitter": "Kormány - pénzügyminiszter",
                          "stage": "Döntés az összegző módosító javaslatról",
                          "timeWindow": None, "notes": None, "flags": None},
                     ]},
                    {"month": 5, "dayOfMonth": 15, "weekday": "PÉNTEK",
                     "date": "2026-05-15", "startsAt": "09:00",
                     "decisionsFrom": ["09:40", "11:30"],
                     "endsNote": None, "breakNote": None,
                     "items": [
                         {"ordinal": 1, "ref": "2", "billCode": "T/999",
                          "section": None, "title": "Egy iromány, amit nem tartunk",
                          "submitter": None, "stage": "Általános vita a lezárásig",
                          "timeWindow": None, "notes": None, "flags": None},
                     ]},
                ],
            },
        },
    }


@pytest.fixture
def data_dir(tmp_path):
    """A synthetic scraper data directory (processed/*.json) the loader builds
    from. Exposed on its own so the incremental-update tests can mutate it."""
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
    (data / "processed" / "officeholders.json").write_text(
        json.dumps(_officeholders_registry(), ensure_ascii=False))
    (data / "processed" / "aktualis.json").write_text(
        json.dumps(_aktualis_registry(), ensure_ascii=False))
    return data


@pytest.fixture
def db_path(tmp_path, data_dir):
    out = tmp_path / "test.db"
    loader.build_database(data_dir, out)
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
