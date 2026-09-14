"""Interjections — the Közbeszólások module (§6E).

Two halves, matching the module's own split:

* the **extractor** (`app.interjections`) is pure — no DB, no network, no model — so
  it is tested with the shapes the shorthand writers actually produce, which is the
  only way to see whether the parenthetical rules and the refusal to guess at a name
  behave (INT-2/INT-3);
* the **pipeline** (loader → table → API) is tested over a synthetic sitting whose
  transcript carries real heckling, so the graph, its top-N cut and the drill-down
  are exercised end to end.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app import interjections, loader

# ---------------------------------------------------------------------------
# The extractor (INT-2)
# ---------------------------------------------------------------------------


def found(text):
    return [(i.speaker, i.text) for i in interjections.extract(text)]


@pytest.mark.parametrize("text, expected", [
    # The plain case: one heckle inside a speech.
    ("…hogy ön fél (Balla György: Úgy van!); ön fél attól…",
     [("Balla György", "Úgy van!")]),
    # One parenthetical bundling several, chained on a spaced dash. The stage
    # direction and the quoteless heckle in the same bundle are not interjections.
    ("(Szavazás. ‑ Novák Előd: Nagykoalíció! ‑ Gulyás Gergely közbeszól.)",
     [("Novák Előd", "Nagykoalíció!")]),
    # Two separate parentheticals in one speech, in reading order.
    ("Elmondom (Vadai Ágnes: Nem igaz!) hogy mi történt (Tordai Bence: De igen!).",
     [("Vadai Ágnes", "Nem igaz!"), ("Tordai Bence", "De igen!")]),
    # An honorific in front of the name, and a four-part name.
    ("(Dr. Lukács László György: Ez nem így van!)",
     [("Lukács László György", "Ez nem így van!")]),
    # A hyphenated surname must not be split by the bundle separator, which needs
    # spaces around its dash.
    ("(Ruszin-Szendi Romulusz: Tessék?)", [("Ruszin-Szendi Romulusz", "Tessék?")]),
    # An initial is a name token ("Z. Kárpát Dániel").
    ("(Z. Kárpát Dániel: Így van!)", [("Z. Kárpát Dániel", "Így van!")]),
    # The chair is one token, so it is not a name — this is the presiding officer
    # speaking, not a member heckling (486 of these in cycle 34 alone).
    ("(Elnök: Képviselő úr!)", []),
    # A bench shouting anonymously: the words are there, the heckler is not.
    ("(Közbeszólások a Fidesz padsoraiból: Nem!)", []),
    ("(Hangok a DK soraiból: Úgy van!)", []),
    # The voting display, which is shaped exactly like an attribution but whose
    # "name" carries lowercase words (398 occurrences in cycle 42).
    ("(A táblán megjelenő eredmény: 120 igen.)", []),
    # A stage direction with no attribution at all.
    ("(Taps a kormánypártok soraiból.)", []),
    # References the speaker dictated stay part of the speech, not asides.
    ("A Házszabály 9. § (2) bekezdése szerint (V. 9.) ez így van.", []),
    # A wall-clock stamp is not an attribution either.
    ("(13.20)", []),
    # An unclosed parenthetical still yields its heckle: the transcripts drop a
    # closing bracket often enough that dropping the content with it would lose
    # real interjections.
    ("Mondom tovább (Nacsa Lőrinc: Ezt már mondtad!", [("Nacsa Lőrinc", "Ezt már mondtad!")]),
    # More than four name tokens is not a name (nor is a lowercase-led phrase).
    ("(Egy nagyon hosszú furcsa hosszabb valami: Nem!)", []),
])
def test_extraction_keeps_attributed_quoted_interjections(text, expected):
    assert found(text) == expected


def test_a_heckle_spanning_the_sentence_split_is_still_one_interjection():
    """The segmenter cuts a direction with an internal full stop into several
    sentences; the loader scans the rejoined speech, so the bracket is whole."""
    joined = "Kezdem. (A képviselő feláll. ‑ Nacsa Lőrinc: Nem igaz!) Folytatom."
    assert found(joined) == [("Nacsa Lőrinc", "Nem igaz!")]


# ---------------------------------------------------------------------------
# Resolving a written name to a person (INT-3)
# ---------------------------------------------------------------------------

@pytest.fixture
def index():
    return interjections.build_name_index([
        ("k001", ("Kovács Béla", "Dr. Kovács Béla")),
        ("n002", ("Nagy Anna", "Nagy Anna")),
        ("h003", ("Hankó Balázs", "Hankó Balázs")),
        # The collision: two people, one name. Only one of them sits in cycle 43.
        ("t004", ("Tóth István", "Tóth István")),
        ("t005", ("Tóth István", "Dr. Tóth István")),
    ])


def test_a_name_resolves_accent_and_honorific_insensitively(index):
    assert interjections.resolve_name("Kovács Béla", index) == "k001"
    assert interjections.resolve_name("Dr. Kovács Béla", index) == "k001"
    assert interjections.resolve_name("Kovacs Bela", index) == "k001"


def test_extra_trailing_tokens_fall_back_to_the_longest_prefix(index):
    """A middle name the register does not carry, a stage direction that ran into
    the name, and the dative-marked member a shout was aimed at."""
    assert interjections.resolve_name("Hankó Balázs Zoltán", index) == "h003"
    assert interjections.resolve_name("Nagy Anna felnevetve", index) == "n002"
    assert interjections.resolve_name("Kovács Béla Nagy Annának", index) == "k001"


def test_an_unknown_name_resolves_to_nobody(index):
    assert interjections.resolve_name("Senki Sándor", index) is None


def test_a_collision_is_never_guessed_at(index):
    assert interjections.resolve_name("Tóth István", index) is None


def test_the_cycle_breaks_a_collision_when_it_seats_exactly_one(index):
    assert interjections.resolve_name("Tóth István", index, frozenset({"t004"})) == "t004"
    # ...and does not when it seats both.
    assert interjections.resolve_name(
        "Tóth István", index, frozenset({"t004", "t005"})) is None


# ---------------------------------------------------------------------------
# The pipeline: loader → table → API
# ---------------------------------------------------------------------------

# Three speakers so a top-N cut has something to cut, and the interruptions are
# spread unevenly on purpose: Kovács is the most involved (heckles twice, is
# heckled three times), Szabó the least.
def _heckled_session():
    video = "https://example/playlist.m3u8"

    def speech(index, person_id, label, faction, sentences, agenda="Általános vita",
               agenda_type="debate", speech_type=None):
        return {
            "originID": f"43-2-{index}", "speechIndex": index,
            "electoralPeriod": {"number": 43},
            "agendaItem": {"title": agenda, "officialTitle": agenda,
                           "type": agenda_type, "nativeType": f"HU-{agenda_type}"},
            "people": [{"type": "memberOfParliament", "label": label,
                        "context": "main-speaker", "personID": person_id,
                        "faction": {"label": faction}}],
            "media": {"videoFileURI": video, "duration": 7200,
                      "creator": "Magyar Országgyűlés", "license": "https://lic",
                      "sourcePage": "https://parlament.hu/x"},
            "textContents": [{"type": "proceedings",
                              "sourceURI": "https://parlament.hu/x",
                              "textBody": [{"speech_id": f"43-2-{index}",
                                            "sentences": sentences}]}],
            "debug": {"confidence": 0.7, "align-method": "estimated-day-offset",
                      "felszolalasTipusa": speech_type},
        }

    def sentence(text, start):
        return {"text": text, "timeStart": float(start),
                "timeEnd": float(start + 10), "paragraph": 0}

    return {
        "meta": {"session": "43002", "electoralPeriod": 43, "sitting": 2,
                 "date": "2026-05-16", "dateStart": "2026-05-16T08:00:00",
                 "dateEnd": "2026-05-16T10:00:00", "source": "felicitas-json",
                 "dayVideoURI": video, "timingMethod": "estimated-day-offset",
                 "sourceScrapedAt": "2026-06-18T00:00:00+00:00"},
        "data": [
            # Kovács holds the floor and is heckled three times — twice by Nagy
            # (one of those bundled with a stage direction) and once by Szabó.
            speech(1, "k001", "Kovács Béla", "Fidesz", [
                sentence("A költségvetés fontos kérdés. (Nagy Anna: Ez nem igaz!)", 10),
                sentence("Folytatom (Taps. ‑ Nagy Anna: Mondja már!) a gondolatot.", 30),
                sentence("És végül (Szabó Géza: Elég volt!) befejezem.", 50),
            ]),
            # Nagy holds the floor and is heckled twice by Kovács. An unattributed
            # bench shout in the same speech buys nobody an arrow.
            speech(2, "n002", "Nagy Anna", "TISZA", [
                sentence("Válaszolok önnek. (Kovács Béla: Nem így van!)", 70),
                sentence("Az adatok mást mutatnak (Kovács Béla: Tessék?) sajnos.", 90),
                sentence("Ezt mindenki tudja. (Közbeszólások a Fidesz padsoraiból: Nem!)", 110),
            ]),
            # The chair announcing a vote, heckled once — a procedural speech, so
            # this pair is stored but never counted (STAT-1).
            speech(3, "k001", "Kovács Béla", "Fidesz", [
                sentence("Az Országgyűlés határozatképes. (Nagy Anna: Szégyen!)", 130),
            ], agenda="Szavazás", agenda_type="voting",
               speech_type="ülésvezetés"),
        ],
    }


def _heckler_registry():
    """The shared registry plus a third member, so the corpus has someone the
    top-N cut can leave out."""
    return {
        "meta": {"cycle": 43, "cycleStart": "2026-05-09", "cycleEnd": None,
                 "source": "felicitas-kepviselo-api", "withDetails": True, "count": 3},
        "data": [
            {"personID": "k001", "label": "Kovács Béla", "firstname": "Béla",
             "lastname": "Kovács", "faction": {"label": "Fidesz", "id": 7}},
            {"personID": "n002", "label": "Nagy Anna", "firstname": "Anna",
             "lastname": "Nagy", "faction": {"label": "TISZA"}},
            {"personID": "s003", "label": "Szabó Géza", "firstname": "Géza",
             "lastname": "Szabó", "faction": {"label": "TISZA"}},
        ],
    }


@pytest.fixture
def heckled_db(tmp_path):
    data = tmp_path / "data"
    (data / "processed").mkdir(parents=True)
    (data / "processed" / "representatives-43.json").write_text(
        json.dumps(_heckler_registry(), ensure_ascii=False))
    (data / "processed" / "43002-session.json").write_text(
        json.dumps(_heckled_session(), ensure_ascii=False))
    out = tmp_path / "heckled.db"
    loader.build_database(data, out)
    return out


@pytest.fixture
def heckled_conn(heckled_db):
    conn = sqlite3.connect(heckled_db)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def heckled_client(heckled_db, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "db_path", str(heckled_db))
    monkeypatch.setattr(db_module.settings, "db_path", str(heckled_db))
    from app.main import app
    return TestClient(app)


def test_the_loader_stores_every_interjection_with_both_ends(heckled_conn):
    rows = heckled_conn.execute(
        "SELECT speaker_id, target_id, text, procedural FROM interjection "
        "ORDER BY speech_uid, ord").fetchall()
    assert [(r["speaker_id"], r["target_id"], r["text"]) for r in rows] == [
        ("n002", "k001", "Ez nem igaz!"),
        ("n002", "k001", "Mondja már!"),
        ("s003", "k001", "Elég volt!"),
        ("k001", "n002", "Nem így van!"),
        ("k001", "n002", "Tessék?"),
        ("n002", "k001", "Szégyen!"),
    ]
    # The one inside the chair's vote announcement is flagged, not dropped.
    assert [r["procedural"] for r in rows] == [0, 0, 0, 0, 0, 1]


def test_an_interjection_points_at_the_sentence_that_carried_it(heckled_conn):
    row = heckled_conn.execute(
        "SELECT i.sentence_id, se.text FROM interjection i "
        "JOIN sentence se ON se.id = i.sentence_id "
        "WHERE i.text = 'Mondja már!'").fetchone()
    assert "Folytatom" in row["text"]


def test_the_graph_counts_pairs_and_excludes_the_chairing_speech(heckled_client):
    res = heckled_client.get("/api/v1/interjections/graph", params={"period": 43})
    assert res.status_code == 200
    data = res.json()
    at = {n["person_id"]: i for i, n in enumerate(data["nodes"])}
    edges = {(data["nodes"][l["source"]]["person_id"],
              data["nodes"][l["target"]]["person_id"]): l["count"]
             for l in data["links"]}
    # "Szégyen!" was shouted over the chair announcing a vote — not counted.
    assert edges == {("n002", "k001"): 2, ("s003", "k001"): 1, ("k001", "n002"): 2}
    assert data["total"] == 5
    assert set(at) == {"k001", "n002", "s003"}
    # Coverage names what the picture leaves out rather than hiding it.
    assert data["coverage"] == {"extracted": 6, "attributed": 6, "procedural": 1}


def test_a_node_carries_its_totals_and_its_faction_colour(heckled_client):
    data = heckled_client.get("/api/v1/interjections/graph",
                              params={"period": 43}).json()
    kovacs = next(n for n in data["nodes"] if n["person_id"] == "k001")
    assert (kovacs["out"], kovacs["in"]) == (2, 3)
    assert kovacs["faction"]["label"] == "Fidesz"


def test_the_top_n_cut_keeps_the_most_involved_and_only_whole_arrows(heckled_client):
    data = heckled_client.get("/api/v1/interjections/graph",
                              params={"period": 43, "top": 2}).json()
    # Szabó (one interjection) drops out, and with them the arrow they were an end
    # of — an arrow is only drawn when both its ends survive the cut.
    assert [n["person_id"] for n in data["nodes"]] == ["k001", "n002"]
    assert data["shown"] == 4
    assert data["total"] == 5
    assert data["people_total"] == 3
    # The node totals stay the person's real ones; what is drawn is reported apart.
    kovacs = next(n for n in data["nodes"] if n["person_id"] == "k001")
    assert (kovacs["in"], kovacs["shown_in"]) == (3, 2)


def test_rank_made_cuts_on_interjections_sent(heckled_client):
    """`rank=made` ranks on interjections *sent* rather than on the whole
    exchange, and holds to the same "exactly `top` people" cut."""
    data = heckled_client.get("/api/v1/interjections/graph",
                              params={"period": 43, "top": 2, "rank": "made"}).json()
    assert data["rank"] == "made"
    edges = {(data["nodes"][l["source"]]["person_id"],
              data["nodes"][l["target"]]["person_id"]): l["count"]
             for l in data["links"]}
    # Kovács and Nagy made 2 each; Szabó made 1 and drops out, taking the arrow
    # they were an end of with them.
    assert [n["person_id"] for n in data["nodes"]] == ["k001", "n002"]
    assert edges == {("n002", "k001"): 2, ("k001", "n002"): 2}
    kovacs = next(n for n in data["nodes"] if n["person_id"] == "k001")
    # A node's own totals are the person's real ones whatever the cut ranked by.
    assert (kovacs["out"], kovacs["in"]) == (2, 3)


def test_rank_received_cuts_on_interjections_received(heckled_client):
    """`rank=received` is the symmetric cut, and picks a different pair: Szabó
    made an interjection but never took one, so they are last here — where on
    `made` they beat the member who took three."""
    data = heckled_client.get("/api/v1/interjections/graph",
                              params={"period": 43, "top": 2,
                                      "rank": "received"}).json()
    assert data["rank"] == "received"
    # k001 (took 3) and n002 (took 2) are the top two; Szabó (took none) is out.
    assert [n["person_id"] for n in data["nodes"]] == ["k001", "n002"]
    assert len(data["nodes"]) == 2


def test_a_rank_the_api_does_not_know_is_refused(heckled_client):
    res = heckled_client.get("/api/v1/interjections/graph",
                             params={"period": 43, "rank": "loudest"})
    assert res.status_code == 422


def test_clicking_an_arrow_lists_the_words_behind_it(heckled_client):
    res = heckled_client.get("/api/v1/interjections/list",
                             params={"period": 43, "speaker": "n002",
                                     "target": "k001"})
    data = res.json()
    assert data["total"] == 2
    assert {i["text"] for i in data["interjections"]} == {"Ez nem igaz!", "Mondja már!"}
    one = data["interjections"][0]
    # Every row opens onto the moment it was shouted (INT-7).
    assert one["speech_uid"] and one["sentence_id"] and one["date"] == "2026-05-16"
    assert one["speaker"]["label"] == "Nagy Anna"
    assert one["target"]["label"] == "Kovács Béla"


def test_clicking_a_person_lists_both_directions(heckled_client):
    """A picked node highlights every arrow touching it, so the list under it has
    to hold every arrow touching it — made and received alike."""
    data = heckled_client.get("/api/v1/interjections/list",
                              params={"period": 43, "person": "k001"}).json()
    # Two Kovács shouted at Nagy, three shouted at Kovács; the sixth was over the
    # chair's vote announcement and is counted nowhere.
    assert data["total"] == 5
    assert {i["text"] for i in data["interjections"]} == {
        "Ez nem igaz!", "Mondja már!", "Elég volt!", "Nem így van!", "Tessék?"}


def test_one_direction_is_still_askable_on_its_own(heckled_client):
    data = heckled_client.get("/api/v1/interjections/list",
                              params={"period": 43, "speaker": "n002"}).json()
    # Two over Kovács's speech; the third was over the chair's vote announcement.
    assert data["total"] == 2


def test_the_partner_lists_split_a_person_by_direction(heckled_client):
    """The two choosers on a member's own panel: who shouted at them, and whom
    they shouted at, each with the count that opening it will show."""
    data = heckled_client.get("/api/v1/interjections/partners",
                              params={"period": 43, "person": "k001"}).json()
    # Nagy heckled Kovács twice and Szabó once — busiest first.
    assert [(p["person_id"], p["label"], p["count"])
            for p in data["received_from"]] == [
        ("n002", "Nagy Anna", 2), ("s003", "Szabó Géza", 1)]
    assert [(p["person_id"], p["count"]) for p in data["made_to"]] == [("n002", 2)]
    # The chair's heckled vote announcement is Kovács's too, and stays out of
    # both lists exactly as it stays out of the graph (STAT-1).
    assert sum(p["count"] for p in data["received_from"]) == 3


def test_a_partner_count_matches_the_list_it_opens(heckled_client):
    """The count in a chooser is a promise about the list behind it: picking
    that counterpart must show exactly that many interjections."""
    partners = heckled_client.get("/api/v1/interjections/partners",
                                  params={"period": 43, "person": "k001"}).json()
    for p in partners["received_from"]:
        listed = heckled_client.get(
            "/api/v1/interjections/list",
            params={"period": 43, "person": "k001", "speaker": p["person_id"]}).json()
        assert listed["total"] == p["count"]


def test_the_list_refuses_an_unfiltered_dump(heckled_client):
    assert heckled_client.get("/api/v1/interjections/list").status_code == 400


def test_the_module_is_advertised_in_the_manifest(heckled_client):
    modules = {m["name"] for m in heckled_client.get("/api/v1/meta").json()["modules"]}
    assert "interjections" in modules


def test_a_db_without_the_table_says_so_rather_than_erroring(heckled_db,
                                                             heckled_client):
    """A DB built before the module existed keeps working; the page is told the
    table is not built rather than being handed a 500 (EXT-6)."""
    conn = sqlite3.connect(heckled_db)
    conn.execute("DROP TABLE interjection")
    conn.commit()
    conn.close()
    res = heckled_client.get("/api/v1/interjections/graph", params={"period": 43})
    assert res.status_code == 503


def test_reloading_a_sitting_does_not_duplicate_its_interjections(tmp_path):
    """`--update` re-derives only the sittings that changed (ING-4/SCR-2)."""
    data = tmp_path / "data"
    (data / "processed").mkdir(parents=True)
    (data / "processed" / "representatives-43.json").write_text(
        json.dumps(_heckler_registry(), ensure_ascii=False))
    session_file = data / "processed" / "43002-session.json"
    session_file.write_text(json.dumps(_heckled_session(), ensure_ascii=False))
    out = tmp_path / "reload.db"
    loader.build_database(data, out)

    def count():
        conn = sqlite3.connect(out)
        try:
            return conn.execute("SELECT COUNT(*) FROM interjection").fetchone()[0]
        finally:
            conn.close()

    before = count()
    record = _heckled_session()
    record["meta"]["sourceScrapedAt"] = "2026-06-19T00:00:00+00:00"
    session_file.write_text(json.dumps(record, ensure_ascii=False))
    assert loader.update_database(data, out) is True
    assert count() == before
