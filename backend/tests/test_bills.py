"""Bills module: loader (JSON -> bill/bill_sponsor) + API contract (OPS-3)."""

from __future__ import annotations

from app import loader


# --- loader ---------------------------------------------------------------

def test_bills_and_sponsors_loaded(conn):
    assert conn.execute("SELECT COUNT(*) FROM bill").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM bill_sponsor").fetchone()[0] == 2


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
    assert c.execute("SELECT COUNT(*) FROM bill").fetchone()[0] == 2
    assert c.execute("SELECT COUNT(*) FROM bill_sponsor").fetchone()[0] == 2
    c.close()


# --- API ------------------------------------------------------------------

def test_list_bills(client):
    d = client.get("/api/v1/bills").json()
    assert d["total"] == 2
    # Default sort is by number, descending.
    assert d["bills"][0]["bill_number"] == "T/101"


def test_list_bills_filter_by_sponsor(client):
    d = client.get("/api/v1/bills", params={"sponsor": "k001"}).json()
    assert d["total"] == 1
    bill = d["bills"][0]
    assert bill["bill_number"] == "T/100"
    sp = bill["sponsors"][0]
    assert sp["person_id"] == "k001"
    assert sp["name"] == "Kovács Béla"           # resolved from the person row
    assert sp["faction"]["label"] == "Fidesz"


def test_list_bills_search_and_status(client):
    assert client.get("/api/v1/bills", params={"q": "költségvetés"}).json()["total"] == 1
    assert client.get("/api/v1/bills", params={"status": "elfogadva"}).json()["total"] == 1


def test_bill_facets(client):
    d = client.get("/api/v1/bills/facets").json()
    assert "tárgysorozatban" in d["statuses"]
    assert any(t["main_type"] == "T" for t in d["types"])


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
