"""Bills module: loader (JSON -> bill/bill_sponsor) + API contract (OPS-3)."""

from __future__ import annotations

from app import loader


# --- loader ---------------------------------------------------------------

def test_bills_and_sponsors_loaded(conn):
    # All iromány types share the bill table (BILL-9): 2 törvényjavaslat + 1 interpelláció.
    assert conn.execute("SELECT COUNT(*) FROM bill").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM bill_sponsor").fetchone()[0] == 3
    assert conn.execute(
        "SELECT main_type FROM bill WHERE id='doc-uuid-3'").fetchone()[0] == "I"


def test_sponsor_links_known_mp_only(conn):
    """EXT-2: a sponsor that is a known MP links to person; a government
    submitter keeps its label with no link (no stub person invented)."""
    linked = conn.execute(
        "SELECT person_id FROM bill_sponsor WHERE bill_id='bill-uuid-1'").fetchone()
    assert linked["person_id"] == "k001"
    govt = conn.execute(
        "SELECT person_id, label FROM bill_sponsor WHERE bill_id='bill-uuid-2'").fetchone()
    assert govt["person_id"] is None
    assert "kormány" in govt["label"]
    # No stub person was created for the government submitter.
    assert conn.execute("SELECT COUNT(*) FROM person WHERE is_mp=1").fetchone()[0] == 2


def test_sponsor_faction_resolved_by_ext_id(conn):
    """factionId (Felicitas frakcioId) resolves to the shared faction row."""
    row = conn.execute(
        """SELECT f.label FROM bill_sponsor bs JOIN faction f ON f.id=bs.faction_id
           WHERE bs.bill_id='bill-uuid-1'""").fetchone()
    assert row["label"] == "Fidesz"


def test_bill_source_url_falls_back_when_no_text(conn):
    """LEGAL-1: a bill with text links to the PDF; one without falls back to
    the generic portal page rather than a dead link."""
    with_text = conn.execute("SELECT source_url FROM bill WHERE id='bill-uuid-1'").fetchone()
    assert with_text["source_url"].endswith("00100.pdf")
    no_text = conn.execute("SELECT source_url FROM bill WHERE id='bill-uuid-2'").fetchone()
    assert "iromanyok-lekerdezese" in no_text["source_url"]


def test_reingest_bills_is_idempotent(conn, db_path):
    """ING-4: re-loading a cycle's bills replaces, never duplicates."""
    from tests.conftest import _bills_registry
    c = loader.connect(db_path)
    loader.load_bills(c, _bills_registry())
    loader.load_bills(c, _bills_registry())
    assert c.execute("SELECT COUNT(*) FROM bill").fetchone()[0] == 3
    assert c.execute("SELECT COUNT(*) FROM bill_sponsor").fetchone()[0] == 3
    c.close()


# --- API ------------------------------------------------------------------

def test_list_bills(client):
    d = client.get("/api/v1/bills").json()
    assert d["total"] == 3
    # Default sort is by number, descending.
    assert d["bills"][0]["bill_number"] == "T/101"


def test_list_bills_scoped_to_bills(client):
    """The bills page passes main_type=T, so it sees only törvényjavaslatok."""
    d = client.get("/api/v1/bills", params={"main_type": "T"}).json()
    assert d["total"] == 2
    assert {b["bill_number"] for b in d["bills"]} == {"T/100", "T/101"}


def test_list_other_documents(client):
    """The "Egyéb irományok" page passes main_type_not=T, so it excludes bills
    and surfaces the other iromány types (BILL-9)."""
    d = client.get("/api/v1/bills", params={"main_type_not": "T"}).json()
    assert d["total"] == 1
    assert d["bills"][0]["bill_number"] == "I/5"
    assert d["bills"][0]["type"] == "interpelláció"


def test_list_bills_main_type_in_and_not_in(client):
    """The profile splits an MP's irományok into buckets by main type: questions
    (K/A/I), bills (T/H), and everything else (main_type_not_in the first two)."""
    # bills bucket: both törvényjavaslatok
    bills = client.get("/api/v1/bills", params={"main_type_in": "T,H"}).json()
    assert bills["total"] == 2
    assert {b["bill_number"] for b in bills["bills"]} == {"T/100", "T/101"}
    # questions bucket: the interpelláció
    questions = client.get("/api/v1/bills", params={"main_type_in": "K,A,I"}).json()
    assert questions["total"] == 1 and questions["bills"][0]["bill_number"] == "I/5"
    # "other" bucket excludes both of the above — nothing left in this fixture
    other = client.get("/api/v1/bills", params={"main_type_not_in": "K,A,I,T,H"}).json()
    assert other["total"] == 0


def test_list_documents_filter_by_type(client):
    d = client.get("/api/v1/bills", params={"type": "interpelláció"}).json()
    assert d["total"] == 1 and d["bills"][0]["bill_number"] == "I/5"
    # a type that exists only among bills is excluded by main_type_not=T
    assert client.get("/api/v1/bills",
                      params={"main_type_not": "T", "type": "törvényjavaslat"}).json()["total"] == 0


def test_list_bills_filter_by_sponsor(client):
    # The profile "bills submitted" link points at the bills page (main_type=T);
    # k001 also sponsors a non-bill document, which that scoped view excludes.
    d = client.get("/api/v1/bills", params={"sponsor": "k001", "main_type": "T"}).json()
    assert d["total"] == 1
    bill = d["bills"][0]
    assert bill["bill_number"] == "T/100"
    sp = bill["sponsors"][0]
    assert sp["person_id"] == "k001"
    assert sp["name"] == "Kovács Béla"           # resolved from the person row
    assert sp["faction"]["label"] == "Fidesz"


def test_list_bills_search_and_status(client):
    assert client.get("/api/v1/bills", params={"q": "költségvetés"}).json()["total"] == 1
    assert client.get("/api/v1/bills", params={"status": "kihirdetve"}).json()["total"] == 1


def test_bill_facets(client):
    d = client.get("/api/v1/bills/facets").json()
    assert "tárgysorozatban" in d["statuses"]
    assert any(t["main_type"] == "T" for t in d["types"])


def test_bill_facets_scoped_by_main_type(client):
    """Facets honor the include/exclude used by each browse page: the bills page
    (main_type=T) sees only bill types; the other-irományok page (main_type_not=T)
    sees only the rest (BILL-9)."""
    bills = client.get("/api/v1/bills/facets", params={"main_type": "T"}).json()
    assert {t["type"] for t in bills["types"]} == {"törvényjavaslat"}
    other = client.get("/api/v1/bills/facets", params={"main_type_not": "T"}).json()
    assert {t["type"] for t in other["types"]} == {"interpelláció"}


def test_bill_facets_scoped_by_sponsor(client):
    """A profile lists only the document types an MP actually submitted: k001
    sponsors a törvényjavaslat and an interpelláció, so both main_types appear."""
    d = client.get("/api/v1/bills/facets", params={"sponsor": "k001"}).json()
    assert {t["main_type"] for t in d["types"]} == {"T", "I"}


def test_get_bill_detail(client):
    d = client.get("/api/v1/bills/bill-uuid-1").json()
    assert d["bill_number"] == "T/100"
    assert d["text_url"].endswith("00100.pdf")
    assert d["sponsors"][0]["person_id"] == "k001"


def test_get_bill_404(client):
    assert client.get("/api/v1/bills/does-not-exist").status_code == 404


def test_bill_timeline_stages(client):
    """The stage diagram is returned in order, with the furthest-reached stage
    flagged `current` (so the UI can split past from future events)."""
    d = client.get("/api/v1/bills/bill-uuid-1").json()
    labels = [s["label"] for s in d["stages"]]
    assert labels == ["Tárgysorozatban", "Általános vita alatt", "Zárószavazás"]
    assert [s["done"] for s in d["stages"]] == [True, True, False]
    # current = the last done stage; future stages are not current.
    assert [s["current"] for s in d["stages"]] == [False, True, False]


def test_bill_without_diagram_has_empty_stages(client):
    """A bill with no stage diagram (kellDiagram false upstream) returns []."""
    d = client.get("/api/v1/bills/bill-uuid-2").json()
    assert d["stages"] == []


def test_bill_detail_sections(client):
    """The detail sheet's sections (events, votes, committees, deadlines,
    documents, motion summary) and extra header fields round-trip through the
    loader into the API."""
    d = client.get("/api/v1/bills/bill-uuid-1").json()
    # extra header fields
    assert d["subtype"] == "törvényjavaslat nemzetközi szerződésről"
    assert d["negotiation_mode"] == "kivételes tárgyalásban"
    assert d["current_event"] == "általános vita alatt"
    assert d["last_modifier"] == "100/4"
    # event history, in order, with the MP resolved (EXT-2) and a speech ref
    assert [e["name"] for e in d["events"]] == [
        "kivételességi javaslat elfogadva", "részletes vita megkezdve"]
    assert d["events"][0]["person_id"] == "k001"
    assert d["events"][0]["person_name"]  # resolved through the shared person
    assert d["events"][0]["speech_number"] == "3/43"
    # the speech ref resolves to an in-site viewer uid via speech.speech_uuid;
    # an event with no matching speech stays unlinked
    assert d["events"][0]["speech_uid"] == "43001-1"
    assert d["events"][1]["speech_uid"] is None
    # votes / committees / deadlines / documents / motion summary
    v = d["votes"][0]
    assert (v["yes"], v["no"], v["abstain"], v["result"]) == (139, 48, 0, "Elfogadva")
    assert d["committee_events"][0]["committee"] == "Törvényalkotási Bizottság"
    assert d["committees"][0]["role"] == "Kijelölt bizottság"
    assert d["deadlines"][0]["reference"] == "HHSZ 62. § (3)"
    assert {x["kind"] for x in d["documents"]} == {"justification", "background"}
    assert d["motion_summary"][0]["total"] == 1


def test_bill_non_self_standing_motions(client):
    """The individual non-self-standing motions round-trip with their PDF and an
    MP-linked submitter (EXT-2); a committee/Speaker submitter stays label-only."""
    d = client.get("/api/v1/bills/bill-uuid-1").json()
    motions = d["motions"]
    assert [m["bill_number"] for m in motions] == ["T/100/3", "T/100/1"]
    # first motion: has a downloadable PDF and an MP-linked submitter
    m0 = motions[0]
    assert m0["type"] == "Módosító javaslat"
    assert m0["text_url"].endswith("00100-0003.pdf")
    assert m0["no_text"] is False and m0["has_vote"] is True
    assert m0["sponsors"][0]["person_id"] == "k001"
    assert m0["sponsors"][0]["name"]            # resolved through shared person
    assert m0["sponsors"][0]["faction"]["label"]
    # second motion: no text, committee/Speaker submitter kept label-only (no link)
    m1 = motions[1]
    assert m1["text_url"] is None and m1["no_text"] is True
    assert m1["sponsors"][0]["person_id"] is None
    assert m1["sponsors"][0]["label"] == "az Országgyűlés elnöke"


def test_bill_detail_absent_is_empty(client):
    """A bill scraped without detail still returns the sections as empty lists
    (no crash, EXT-6/SCR-5 degraded shape)."""
    d = client.get("/api/v1/bills/bill-uuid-2").json()
    assert d["events"] == [] and d["votes"] == [] and d["documents"] == []
    assert d["motions"] == []
    assert d["subtype"] is None


def test_promulgated_bill_has_kozlony_links(client):
    """A promulgated (kihirdetve) bill exposes its Magyar Közlöny links: the
    direct gazette PDF (preferred) and the issue-listing page (fallback)."""
    d = client.get("/api/v1/bills/bill-uuid-2").json()
    assert d["mk_number"] == 44
    assert d["kozlony_url"] == "https://magyarkozlony.hu/?year=2026&month=&serial=44"
    assert d["kozlony_doc_url"] == "https://magyarkozlony.hu/dokumentumok/abc123/megtekintes"


# --- debate speeches (BILL-10) -------------------------------------------

def _seed_debate(db_path):
    """Add a run of plenary speeches to the test sitting and bracket them with a
    pair of debate events on bill-uuid-1, so the debate panel has data. Each
    test gets its own freshly-built DB file, so this is isolated."""
    import sqlite3
    c = sqlite3.connect(db_path)
    # Four speeches (index 10..13) in the existing sitting (43001, 2026-05-09),
    # each with its own Felicitas UUID so the bracket events can resolve to them.
    for i in range(10, 14):
        c.execute(
            """INSERT INTO speech (uid, origin_id, speech_uuid, session_id,
                   period_number, speech_index, person_id, speaker_label,
                   time_start, time_end, duration, has_text)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"43001-{i}", f"43-1-{i}", f"uuid-deb-{i}", "43001", 43, i,
             "k001" if i % 2 else "n002", "Kovács Béla" if i % 2 else "Nagy Anna",
             100.0 + i, 110.0 + i, 10.0, 1 if i != 13 else 0))
    # Opening event -> first speech, closing -> last; the speeches in between are
    # the debate. ord places them after the bill's existing events.
    c.execute("""INSERT INTO bill_event (bill_id, ord, event_date, name, speech_id)
                 VALUES ('bill-uuid-1', 50, '2026-05-09T09:00:00Z',
                         'általános vita megkezdve', 'uuid-deb-10')""")
    c.execute("""INSERT INTO bill_event (bill_id, ord, event_date, name, speech_id)
                 VALUES ('bill-uuid-1', 51, '2026-05-09T10:00:00Z',
                         'általános vita lezárva', 'uuid-deb-13')""")
    c.commit(); c.close()


def test_bill_debate_speeches(client, db_path):
    """A debate bracket (általános vita megkezdve … lezárva) yields a panel of the
    plenary speeches between its two anchor speeches, in order, each linkable to
    the viewer (BILL-10 / EXT-2)."""
    _seed_debate(db_path)
    d = client.get("/api/v1/bills/bill-uuid-1").json()
    assert len(d["debates"]) == 1
    deb = d["debates"][0]
    assert deb["label"] == "általános vita"
    assert deb["start_speech_uid"] == "43001-10"
    assert deb["end_speech_uid"] == "43001-13"
    # all four speeches in the bracket, in proceedings order (inclusive of anchors)
    assert [s["uid"] for s in deb["speeches"]] == [
        "43001-10", "43001-11", "43001-12", "43001-13"]
    # speakers resolve through the shared person entity (EXT-2)
    assert deb["speeches"][0]["speaker"]["person_id"] == "n002"
    assert deb["speeches"][1]["speaker"]["person_id"] == "k001"
    # the no-transcript speech is flagged for the UI (VIE-8)
    assert deb["speeches"][-1]["has_text"] is False


def test_bill_without_debate_has_empty_panel(client):
    """A bill whose events carry no resolvable debate bracket returns no debates
    (graceful, SCR-5) — bill-uuid-1's seeded detail has only 'részletes vita'."""
    d = client.get("/api/v1/bills/bill-uuid-1").json()
    assert d["debates"] == []


# --- video answer (question-type irományok) ------------------------------

def _seed_answer(db_path):
    """Add an oral-answer speech to the test sitting and an answer event on
    bill-uuid-1 pointing at it, so the embedded video answer has data."""
    import sqlite3
    c = sqlite3.connect(db_path)
    c.execute(
        """INSERT INTO speech (uid, origin_id, speech_uuid, session_id,
               period_number, speech_index, person_id, speaker_label,
               speaker_status, video_start, video_end, duration, has_text)
           VALUES ('43001-90','43-1-90','uuid-answer','43001',43,90,'k001',
                   'Kovács Béla','memberOfGovernment',100.0,160.0,60.0,1)""")
    c.execute("""INSERT INTO bill_event (bill_id, ord, event_date, name,
                     related_label, speech_id)
                 VALUES ('bill-uuid-1', 60, '2026-05-09T11:00:00Z',
                         'kérdés megválaszolva',
                         'Belügyminisztérium államtitkára', 'uuid-answer')""")
    c.commit(); c.close()


def test_bill_video_answer(client, db_path):
    """A question-type iromány's oral answer event resolves to the answer speech
    and exposes an embeddable video clip (VIE-9), with the responder labelled."""
    _seed_answer(db_path)
    va = client.get("/api/v1/bills/bill-uuid-1").json()["video_answer"]
    assert va is not None
    assert va["event_name"] == "kérdés megválaszolva"
    assert va["speech_uid"] == "43001-90"
    assert va["responder_label"] == "Belügyminisztérium államtitkára"
    assert va["speaker"]["label"]            # resolved through the shared person
    assert va["video_uri"]                   # something to embed (clip or day stream)


def test_bill_without_oral_answer_has_no_video(client):
    """A bill with no oral-answer event (bill-uuid-1's seeded events are only
    legislative) exposes no video answer (None, graceful)."""
    assert client.get("/api/v1/bills/bill-uuid-1").json()["video_answer"] is None
