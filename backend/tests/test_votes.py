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


def test_representative_vote_days(client):
    """The grouped-by-day view: one row per sitting, with a per-day count whose
    sum equals the flat vote total (EXT-2)."""
    flat = client.get("/api/v1/representatives/k001/votes").json()
    days = client.get("/api/v1/representatives/k001/vote-days").json()
    assert days["available"] is True
    assert days["total"] == flat["total"]
    assert sum(x["count"] for x in days["days"]) == days["total"]
    assert days["days"][0]["date"] == "2026-05-26"


def test_representative_votes_filtered_by_date(client):
    """The lazy-loaded day spoiler fetches only that day's votes."""
    days = client.get("/api/v1/representatives/k001/vote-days").json()["days"]
    date = days[0]["date"]
    d = client.get("/api/v1/representatives/k001/votes",
                   params={"date": date}).json()
    assert d["total"] == days[0]["count"]
    assert all(v["vote_datetime"].startswith(date) for v in d["votes"])
    # a day the MP didn't vote on yields nothing
    assert client.get("/api/v1/representatives/k001/votes",
                      params={"date": "1999-01-01"}).json()["total"] == 0


def test_representative_absence_stats(client, db_path):
    """REP-3 attendance: the statistics endpoint reports how many roll-call
    votes the MP was absent from, nominally and as a percentage of the votes
    they could have cast. k001 has one Igen record; we add two absences across
    two further votes so the share is 2/3 ≈ 66.7%."""
    import sqlite3
    c = sqlite3.connect(db_path)
    for vid in ("v-3", "v-4"):
        c.execute("INSERT INTO vote (id, period_number, vote_datetime, result) "
                  "VALUES (?, 43, '2026-05-28T10:00:00Z', 'Elfogadva')", (vid,))
        c.execute("INSERT INTO vote_record (vote_id, person_id, name, value, value_code) "
                  "VALUES (?, 'k001', 'Kovács Béla', 'Előre bejelentett hiányzó', 'absent')",
                  (vid,))
    c.commit(); c.close()

    t = client.get("/api/v1/representatives/k001/statistics").json()["totals"]
    assert t["votes_available"] is True
    assert t["votes_total"] == 3
    assert t["votes_absent"] == 2
    assert t["votes_absent_pct"] == 66.7
