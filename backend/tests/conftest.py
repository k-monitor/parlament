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
             # The published monthly remuneration (REP-17), newest first, as the
             # registry scraper writes it. August is a clean 2× the §104(1) base;
             # July is a part-month, which no statutory rate produces — the two
             # cases the profile has to tell apart. Nagy Anna has none at all.
             "remuneration": [
                 {"month": "2026-08-01", "amountHuf": 2618986},
                 {"month": "2026-07-01", "amountHuf": 1103450},
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



def _committees_registry():
    """Two bodies of one cycle: a main committee with a subcommittee under it.

    The roster holds a chair who is a known MP (k001, so the person link
    resolves) and a member who is not in the roster (x777, so the seat keeps its
    label and no link — SCR-5). The term list holds one *former* member who is
    on no current roster, which is the case the snapshot alone cannot show, plus
    a term for the sitting chair so the merge-into-one-seat path is exercised.
    One document is a held bill (bill-uuid-1) and one is not, and one meeting
    has published minutes while the other has none.
    """
    return {
        "meta": {"cycle": 43, "dateFrom": "2026-05-09", "dateTo": "2026-06-19",
                 "membersAsOf": "2026-06-19", "source": "felicitas-bizottsag-api",
                 "scrapedAt": "2026-06-19T00:00:00+00:00", "count": 2,
                 "withDetail": True,
                 "counts": {"bodies": 2, "members": 3, "terms": 2,
                            "meetings": 2, "documents": 2, "submissions": 1}},
        "data": [
            {"committeeId": "biz-1", "name": "Költségvetési Bizottság",
             "parentId": None, "parentName": None, "isSubcommittee": False,
             "standingCode": "KTB", "code": "002A", "cycle": 43,
             "dateStart": "2026-05-09", "dateEnd": None, "ord": 1,
             "type": "állandó", "email": "ktb[kukac]parlament.hu", "active": True,
             "siteUrl": "https://www.parlament.hu/web/guest/43-002A",
             "meetingStats": {"committeeId": "biz-1", "meetings": 4,
                              "totalMinutes": 300, "quorate": 3, "inquorate": 1,
                              "lostQuorum": 0, "agendaRejected": 0}},
            {"committeeId": "biz-1a", "name": "Ellenőrző Albizottság",
             "parentId": "biz-1", "parentName": "Költségvetési Bizottság",
             "isSubcommittee": True, "standingCode": None, "code": None,
             "cycle": 43, "dateStart": "2026-06-01", "dateEnd": None, "ord": 1,
             "type": None, "email": None, "active": True, "siteUrl": None,
             "meetingStats": None},
        ],
        "members": [
            {"committeeId": "biz-1", "personID": "k001", "name": "Kovács Béla",
             "role": "chair", "roleLabel": "elnök", "factionId": 7,
             "factionName": "Fidesz", "governing": True, "ord": 0,
             "asOf": "2026-06-19"},
            {"committeeId": "biz-1", "personID": "x777", "name": "Külső Elek",
             "role": "member", "roleLabel": "tag", "factionId": None,
             "factionName": "független", "governing": False, "ord": 1,
             "asOf": "2026-06-19"},
            {"committeeId": "biz-1a", "personID": "k001", "name": "Kovács Béla",
             "role": "member", "roleLabel": "tag", "factionId": 7,
             "factionName": "Fidesz", "governing": True, "ord": 0,
             "asOf": "2026-06-19"},
        ],
        "terms": [
            {"committeeId": "biz-1", "personID": "k001", "name": "Kovács Béla",
             "factionName": "Fidesz", "kind": "office", "role": "chair",
             "roleLabel": "elnök", "dateStart": "2026-05-09T12:00:00Z",
             "dateEnd": None, "reason": None, "replacing": None},
            {"committeeId": "biz-1", "personID": "n002", "name": "Nagy Anna",
             "factionName": "TISZA", "kind": "membership", "role": "member",
             "roleLabel": None, "dateStart": "2026-05-09T12:00:00Z",
             "dateEnd": "2026-06-01T12:00:00Z", "reason": "lemondott",
             "replacing": None},
        ],
        "meetings": [
            {"meetingId": "ules-1", "committeeId": "biz-1",
             "committeeName": "Költségvetési Bizottság", "number": 2,
             "numberInYear": "2/2026", "datetime": "2026-06-10T09:00:00Z",
             "kind": "nyilvános", "quorum": "Határozatképes", "durationS": 3600,
             "minutesUrl": "https://www.parlament.hu/biz43/x/1.pdf"},
            {"meetingId": "ules-2", "committeeId": "biz-1",
             "committeeName": "Költségvetési Bizottság", "number": 1,
             "numberInYear": "1/2026", "datetime": "2026-05-20T09:00:00Z",
             "kind": "zárt", "quorum": "Határozatképtelen", "durationS": 600,
             "minutesUrl": None},
        ],
        "documents": [
            {"committeeId": "biz-1", "billId": "bill-uuid-1",
             "billNumber": "T/100",
             "title": "A költségvetésről szóló törvényjavaslat",
             "status": "részletes vita lezárva",
             "referredAt": "2026-05-20T10:00:00Z",
             "sponsors": [{"personID": "k001", "name": "Kovács Béla"}]},
            {"committeeId": "biz-1", "billId": "iromany-unheld",
             "billNumber": "H/9", "title": "Egy nem tárolt határozati javaslat",
             "status": None, "referredAt": "2026-05-21T10:00:00Z",
             "sponsors": [{"personID": None, "name": "kormány"}]},
        ],
        "submissions": [
            {"committeeId": "biz-1", "kind": "motion", "billId": "irom-9",
             "billNumber": "100/3", "title": "Összegző módosító javaslat",
             "docType": "Összegző jelentés",
             "textUrl": "https://www.parlament.hu/irom43/00100/00100-0003.pdf"},
        ],
        # The schedule ahead: one meeting of a body we hold and one of a body
        # we do not (announced before it is constituted), so the unlinked case
        # is covered. Dates are relative to the run, since the endpoint drops
        # anything already past.
        "upcoming": [
            {"meetingId": "next-1", "committeeId": "biz-1",
             "committeeName": "Költségvetési Bizottság",
             "date": _soon(7), "time": "10:30",
             "venue": "Széll Kálmán terem", "cancelled": False},
            {"meetingId": "next-2", "committeeId": "biz-unknown",
             "committeeName": "a Médiatanács tagjait jelölő eseti bizottság",
             "date": _soon(9), "time": "9:00", "venue": None,
             "cancelled": True},
        ],
    }


def _committee_minutes_registry():
    """The parsed jegyzőkönyv of one of the two meetings above (BIZ-15).

    `ules-1` published minutes and this is them; `ules-2` published none and so
    has no row here at all. The document exercises the three things that are
    easy to get wrong downstream: a speaker who is a known MP (so the person
    link resolves), one who is not (so the speech keeps its label — SCR-5), and
    a speech marked `continued`, which is the chair carrying on past an agenda
    heading without their name being printed again. The agenda names a held
    bill (T/100) so the late-bound link is exercised too.
    """
    return {
        "meta": {"cycle": 43, "source": "parlament-hu-bizottsagi-jegyzokonyv",
                 "scrapedAt": "2026-06-19T00:00:00+00:00", "pdftotext": True,
                 "count": 1,
                 "counts": {"meetings": 1, "fetched": 1, "reused": 0,
                            "errors": 0, "pending": 0, "speeches": 3,
                            "chars": 60, "textBytesStored": 0,
                            "pdfBytesStored": 0}},
        "data": [{
            "meetingId": "ules-1", "committeeId": "biz-1",
            "committeeName": "Költségvetési Bizottság",
            "datetime": "2026-06-10T09:00:00Z",
            "minutesUrl": "https://www.parlament.hu/biz43/x/1.pdf",
            "cover": {
                "registryNumber": "KTB/2-1/2026.", "meetingLabel": "KTB-2/2026",
                "termLabel": "KTB-2/2026-2030",
                "committeeLabel": "Költségvetési Bizottságának",
                "date": "2026-06-10", "weekday": "szerda", "startsAt": "11:00",
                "venue": "az Országház Nagy Imre termében (főemelet 61.)",
                "heldLabel": "üléséről", "closed": False,
                "openedAt": "11:00", "closedAt": "12:05"},
            "contents": [
                {"title": "Az ülés megnyitása", "page": 3, "level": 0},
                {"title": "A költségvetésről szóló T/100. számú törvényjavaslat",
                 "page": 3, "level": 0}],
            "agenda": [
                {"ordinal": 1,
                 "title": "A költségvetésről szóló törvényjavaslat (T/100. szám)",
                 "notes": ["Kormány - pénzügyminiszter", "Részletes vita"],
                 "billNumber": "T/100"},
                {"ordinal": 2, "title": "Egyebek", "notes": [],
                 "billNumber": None}],
            "participants": {
                "chairs": [{"name": "Kovács Béla", "faction": "Fidesz",
                            "role": "a bizottság elnöke"}],
                "present": [
                    {"name": "Kovács Béla", "faction": "Fidesz",
                     "role": "a bizottság elnöke"},
                    {"name": "Külső Elek", "faction": "független", "role": None}],
                "proxies": [{"absent": {"name": "Nagy Anna", "faction": "TISZA",
                                        "role": None},
                             "heldBy": {"name": "Kovács Béla",
                                        "faction": "Fidesz", "role": None}}],
                "staff": [{"name": "Titkár Tamás", "title": "tanácsadó",
                           "org": None}],
                "guests": [{"name": "Vendég Viktor", "title": "államtitkár",
                            "org": "Pénzügyminisztérium"}]},
            "sections": [
                {"title": "Az ülés megnyitása", "level": 0, "page": 3,
                 "preamble": "(Az ülés kezdetének időpontja: 11 óra)",
                 "speechCount": 1},
                {"title": "A költségvetésről szóló T/100. számú törvényjavaslat",
                 "level": 0, "page": 3, "preamble": None, "speechCount": 2}],
            "speeches": [
                {"ord": 0, "section": 0, "chair": True, "continued": False,
                 "name": "Kovács Béla", "faction": "Fidesz",
                 "role": "a bizottság elnöke", "org": None,
                 "text": "Köszöntöm a bizottság tagjait."},
                {"ord": 1, "section": 1, "chair": True, "continued": True,
                 "name": "Kovács Béla", "faction": "Fidesz",
                 "role": "a bizottság elnöke", "org": None,
                 "text": "Soron következik az 1. napirendi pont."},
                {"ord": 2, "section": 1, "chair": False, "continued": False,
                 "name": "Vendég Viktor", "faction": None,
                 "role": "államtitkár", "org": "Pénzügyminisztérium",
                 "text": "Köszönöm a szót, elnök úr."}],
            "stats": {"agendaItems": 2, "sections": 2, "speeches": 3,
                      "speakers": 2, "chars": 60, "present": 2, "guests": 1,
                      "proxies": 1},
        }],
    }


def _committee_videos_registry():
    """Three recordings (BIZ-16), one per matching outcome.

    The first names the body we hold and falls on the day of `ules-1`, so it
    matches a committee *and* a meeting. The second names the same body on a day
    it did not sit, so it matches the body only — which is the normal state on
    the day of a sitting, before the meeting listing catches up. The third is a
    plenary broadcast and matches nothing, which is how the channel's other
    two-thirds are expected to land.
    """
    return {
        "meta": {"source": "youtube-orszaggyules-elo",
                 "channelId": "UCz4RJ6wkXoc3iG3cTxl5sOQ",
                 "channelUrl": "https://www.youtube.com/channel/UCz4RJ6wkXoc3iG3cTxl5sOQ",
                 "scrapedAt": "2026-06-19T00:00:00+00:00",
                 "method": ["rss", "yt-dlp"], "backfilled": True, "errors": [],
                 "earliestVideo": "2026-06-10", "latestVideo": "2026-06-17",
                 "count": 3,
                 "counts": {"videos": 3, "feed": 3, "seen": 3,
                            "committee": 2, "plenary": 1}},
        "data": [
            {"videoId": "vid-1",
             "url": "https://www.youtube.com/watch?v=vid-1",
             "title": "2026. június 10. - A Költségvetési Bizottság ülése",
             "date": "2026-06-10", "kind": "committee",
             "committeeLabel": "A Költségvetési Bizottság", "continued": False,
             "publishedAt": "2026-06-10T09:05:00+00:00",
             "thumbnail": "https://i.ytimg.com/vi/vid-1/hqdefault.jpg",
             "durationS": 3900, "views": 1200, "description": None},
            {"videoId": "vid-2",
             "url": "https://www.youtube.com/watch?v=vid-2",
             "title": "2026. június 17. - A Költségvetési Bizottság ülése",
             "date": "2026-06-17", "kind": "committee",
             "committeeLabel": "A Költségvetési Bizottság", "continued": False,
             "publishedAt": "2026-06-17T09:05:00+00:00",
             "thumbnail": None, "durationS": 1800, "views": 40,
             "description": None},
            {"videoId": "vid-3",
             "url": "https://www.youtube.com/watch?v=vid-3",
             "title": "2026. június 15. - Az Országgyűlés ülésének élő közvetítése",
             "date": "2026-06-15", "kind": "plenary", "committeeLabel": None,
             "continued": False,
             "publishedAt": "2026-06-15T08:00:00+00:00",
             "thumbnail": None, "durationS": None, "views": None,
             "description": None},
        ],
    }


def _soon(days: int) -> str:
    from datetime import date, timedelta
    return (date.today() + timedelta(days=days)).isoformat()


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
    (data / "processed" / "committees-43.json").write_text(
        json.dumps(_committees_registry(), ensure_ascii=False))
    (data / "processed" / "committee-minutes-43.json").write_text(
        json.dumps(_committee_minutes_registry(), ensure_ascii=False))
    (data / "processed" / "committee-videos.json").write_text(
        json.dumps(_committee_videos_registry(), ensure_ascii=False))
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
