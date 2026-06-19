"""Votes module: loader (JSON -> vote/vote_record/…) + API contract (OPS-3).

Exercises the cross-module links that are the point of this module (EXT-2): the
per-MP roll call -> person, the vote subject -> bill, and the bill vote tally
<-> the full vote (shared szavazasId).
"""

from __future__ import annotations

from app import loader


# --- loader ---------------------------------------------------------------

def test_votes_and_children_loaded(conn):
    assert conn.execute("SELECT COUNT(*) FROM vote").fetchone()[0] == 2
    # 3 roll-call records on v-1, none on v-2
    assert conn.execute("SELECT COUNT(*) FROM vote_record").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM vote_faction_stat").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM vote_subject").fetchone()[0] == 2


def test_has_per_mp_derived_from_records(conn):
    """has_per_mp is derived from whether records exist, not the unreliable
    upstream hasKepviselo flag."""
    assert conn.execute("SELECT has_per_mp FROM vote WHERE id='v-1'").fetchone()[0] == 1
    assert conn.execute("SELECT has_per_mp FROM vote WHERE id='v-2'").fetchone()[0] == 0


def test_record_links_known_mp_only(conn):
    """EXT-2: a roll-call record for a known MP links to person; an unknown
    kepviseloId keeps its name but no link (no stub person invented)."""
    k = conn.execute("SELECT person_id FROM vote_record WHERE name='Kovács Béla'").fetchone()
    assert k["person_id"] == "k001"
    x = conn.execute("SELECT person_id, name FROM vote_record WHERE name='Külső Géza'").fetchone()
    assert x["person_id"] is None
    assert conn.execute("SELECT COUNT(*) FROM person WHERE is_mp=1").fetchone()[0] == 2


def test_value_code_normalized(conn):
    rows = dict(conn.execute(
        "SELECT value, value_code FROM vote_record").fetchall())
    assert rows["Igen"] == "yes" and rows["Nem"] == "no"
    assert rows["Tartózkodás"] == "abstain"


def test_subject_links_held_bill_only(conn):
    """A vote subject that is a held bill resolves to it via iromany_id at query
    time (no hard FK, EXT-1); an iromány type we don't hold (H/9) yields no bill
    but keeps its number/title (SCR-5)."""
    held = conn.execute(
        """SELECT b.id AS bill_id FROM vote_subject vs
           LEFT JOIN bill b ON b.id = vs.iromany_id
           WHERE vs.bill_number='T/100'""").fetchone()
    assert held["bill_id"] == "bill-uuid-1"
    other = conn.execute(
        """SELECT b.id AS bill_id, vs.title FROM vote_subject vs
           LEFT JOIN bill b ON b.id = vs.iromany_id
           WHERE vs.bill_number='H/9'""").fetchone()
    assert other["bill_id"] is None and other["title"]


def test_faction_stat_resolved_by_name(conn):
    row = conn.execute(
        """SELECT f.label FROM vote_faction_stat vs JOIN faction f ON f.id=vs.faction_id
           WHERE vs.vote_id='v-1' AND vs.faction_name='Fidesz'""").fetchone()
    assert row["label"] == "Fidesz"


def test_reingest_votes_is_idempotent(conn, db_path):
    """ING-4: re-loading a cycle's votes replaces, never duplicates."""
    from tests.conftest import _votes_registry
    c = loader.connect(db_path)
    loader.load_votes(c, _votes_registry())
    loader.load_votes(c, _votes_registry())
    assert c.execute("SELECT COUNT(*) FROM vote").fetchone()[0] == 2
    assert c.execute("SELECT COUNT(*) FROM vote_record").fetchone()[0] == 3
    c.close()


# --- API ------------------------------------------------------------------

def test_list_votes(client):
    d = client.get("/api/v1/votes").json()
    assert d["total"] == 2
    # Most-recent first (v-2 is later).
    assert d["votes"][0]["id"] == "v-2"
    # subjects come through with the bill link where held
    v1 = next(v for v in d["votes"] if v["id"] == "v-1")
    assert v1["subjects"][0]["bill_id"] == "bill-uuid-1"


def test_list_votes_filters(client):
    assert client.get("/api/v1/votes", params={"result": "Elfogadva"}).json()["total"] == 1
    assert client.get("/api/v1/votes", params={"q": "T/100"}).json()["total"] == 1
    assert client.get("/api/v1/votes", params={"bill": "bill-uuid-1"}).json()["total"] == 1


def test_vote_facets(client):
    d = client.get("/api/v1/votes/facets").json()
    assert "Elfogadva" in d["results"] and "Elutasítva" in d["results"]


def test_get_vote_rollcall(client):
    d = client.get("/api/v1/votes/v-1").json()
    assert d["subject"] == "kivételességi javaslat elfogadva"
    assert d["total_votes"] == 2
    # tally by normalized code
    assert d["tally"] == {"yes": 1, "no": 1, "abstain": 1}
    # roll call: MP resolved to a profile link; faction colour attached
    k = next(r for r in d["records"] if r["name"] == "Kovács Béla")
    assert k["person_id"] == "k001" and k["value_code"] == "yes"
    assert k["faction_color"]
    # subject -> held bill
    assert d["subjects"][0]["bill_id"] == "bill-uuid-1"
    # faction breakdown present
    assert any(fs["faction_name"] == "Fidesz" for fs in d["faction_stats"])


def test_get_vote_404(client):
    assert client.get("/api/v1/votes/nope").status_code == 404


def test_bill_links_to_vote(client):
    """The bill's vote tally resolves a vote_ref to the full roll call when the
    Votes module has ingested that szavazasId (bill <-> vote, EXT-2)."""
    d = client.get("/api/v1/bills/bill-uuid-1").json()
    v = d["votes"][0]
    assert v["vote_id"] == "v-1"
    assert v["vote_ref"] == "v-1"


def test_representative_votes(client):
    """The reciprocal link: an MP's profile lists how they voted (EXT-2)."""
    d = client.get("/api/v1/representatives/k001/votes").json()
    assert d["available"] is True
    assert d["total"] == 1
    v = d["votes"][0]
    assert v["id"] == "v-1" and v["value_code"] == "yes"
    assert v["subjects"][0]["bill_number"] == "T/100"
