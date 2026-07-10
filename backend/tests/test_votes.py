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


def test_list_votes_filter_by_voting_mode(client):
    """The voting-mode ("szavazás típusa") filter narrows to an exact mode."""
    d = client.get("/api/v1/votes", params={"voting_mode": "Listás"}).json()
    assert d["total"] == 1 and d["votes"][0]["id"] == "v-2"
    assert client.get(
        "/api/v1/votes", params={"voting_mode": "Gépi szavazás"}).json()["total"] == 1


def test_list_votes_filter_by_date_range(client):
    """Date bounds compare on the date part, so an upper bound is inclusive of the
    whole day even though vote_datetime carries a time (v-1 is at T13:04:20Z)."""
    # v-1 (05-26) only.
    d = client.get("/api/v1/votes", params={"date_to": "2026-05-26"}).json()
    assert d["total"] == 1 and d["votes"][0]["id"] == "v-1"
    # v-2 (05-27) only.
    assert client.get(
        "/api/v1/votes", params={"date_from": "2026-05-27"}).json()["total"] == 1
    # A window covering both.
    assert client.get("/api/v1/votes", params={
        "date_from": "2026-05-26", "date_to": "2026-05-27"}).json()["total"] == 2


def test_list_votes_sort_order(client):
    """Default is newest-first; date_asc flips it. Unknown values fall back to
    the default (never spliced from raw input)."""
    d = client.get("/api/v1/votes").json()
    assert d["sort"] == "date_desc" and d["votes"][0]["id"] == "v-2"
    asc = client.get("/api/v1/votes", params={"sort": "date_asc"}).json()
    assert asc["sort"] == "date_asc" and asc["votes"][0]["id"] == "v-1"
    bogus = client.get("/api/v1/votes", params={"sort": "; DROP TABLE vote"}).json()
    assert bogus["sort"] == "date_desc" and bogus["votes"][0]["id"] == "v-2"


def test_vote_facets(client):
    d = client.get("/api/v1/votes/facets").json()
    assert "Elfogadva" in d["results"] and "Elutasítva" in d["results"]
    # Distinct voting modes for the "szavazás típusa" control.
    assert set(d["voting_modes"]) == {"Gépi szavazás", "Listás"}


def test_get_vote_rollcall(client):
    d = client.get("/api/v1/votes/v-1").json()
    assert d["subject"] == "kivételességi javaslat elfogadva"
    assert d["total_votes"] == 2
    # tally by normalized code
    assert d["tally"] == {"yes": 1, "no": 1, "abstain": 1}
    # The parlament.hu deep link is always present in the payload; it's null here
    # only because the fixture's vote id isn't a resolvable UUID (see the
    # parlament_links unit test for the real-id case).
    assert "source_url" in d and d["source_url"] is None
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


def test_representative_vote_breakdown(client, db_path):
    """The statistics endpoint returns the roll-call participation split for the
    profile pie. Starting from the fixture (k001 has one Igen for v-1, a
    roll-call vote), we add one vote per remaining category plus one roll-call
    vote k001 has no record in — which is counted as "nem volt jelen". Abstention
    counts as having voted, so v-1 (Igen) + v-3 (Tartózkodás) → voted == 2."""
    import sqlite3
    c = sqlite3.connect(db_path)
    extra = [
        ("v-3", "Tartózkodás", "abstain"),
        ("v-4", "Nem szavazott", "novote"),
        ("v-5", "Előre bejelentett hiányzó", "absent"),
    ]
    for vid, value, code in extra:
        c.execute("INSERT INTO vote (id, period_number, vote_datetime, result, has_per_mp) "
                  "VALUES (?, 43, '2026-05-28T10:00:00Z', 'Elfogadva', 1)", (vid,))
        c.execute("INSERT INTO vote_record (vote_id, person_id, name, value, value_code) "
                  "VALUES (?, 'k001', 'Kovács Béla', ?, ?)", (vid, value, code))
    # A roll-call vote k001 has NO record in → "nem volt jelen".
    c.execute("INSERT INTO vote (id, period_number, vote_datetime, result, has_per_mp) "
              "VALUES ('v-6', 43, '2026-05-29T10:00:00Z', 'Elfogadva', 1)")
    # A procedural quorum check k001 was present for (Igen) — must be ignored,
    # not counted as a cast vote nor in the total (parlament.hu excludes these).
    c.execute("INSERT INTO vote (id, period_number, vote_datetime, result, has_per_mp) "
              "VALUES ('v-q', 43, '2026-05-09T08:45:00Z', 'Határozatképes', 1)")
    c.execute("INSERT INTO vote_record (vote_id, person_id, name, value, value_code) "
              "VALUES ('v-q', 'k001', 'Kovács Béla', 'Igen', 'yes')")
    c.commit(); c.close()

    b = client.get("/api/v1/representatives/k001/statistics").json()["totals"]["vote_breakdown"]
    assert b["voted"] == 2        # v-1 Igen + v-3 Tartózkodás (v-q quorum excluded)
    assert "abstain" not in b     # folded into "voted"
    assert b["novote"] == 1       # v-4
    assert b["absent"] == 1       # v-5, igazoltan távol
    assert b["not_present"] == 1  # v-6, no record → nem volt jelen
    # total = every substantive roll-call vote in scope (v-1,3,4,5,6); v-2 has no
    # roll call and v-q is a quorum check — both excluded.
    assert b["total"] == 5


def test_representative_vote_lists_exclude_quorum_checks(client, db_path):
    """The profile's grouped vote list (vote-days) and per-day list also drop
    procedural quorum checks, so their counts match the participation stats."""
    import sqlite3
    c = sqlite3.connect(db_path)
    c.execute("INSERT INTO vote (id, period_number, vote_datetime, result, has_per_mp) "
              "VALUES ('v-q', 43, '2026-05-26T08:45:00Z', 'Határozatképtelen', 1)")
    c.execute("INSERT INTO vote_record (vote_id, person_id, name, value, value_code) "
              "VALUES ('v-q', 'k001', 'Kovács Béla', 'Igen', 'yes')")
    c.commit(); c.close()

    # k001 has one substantive vote (v-1, Igen) in the fixture; the quorum check
    # must not appear in the day list nor bump its count.
    days = client.get("/api/v1/representatives/k001/vote-days").json()
    assert days["total"] == 1
    votes = client.get("/api/v1/representatives/k001/votes").json()
    assert votes["total"] == 1
    assert all(v["result"] not in ("Határozatképes", "Határozatképtelen")
               for v in votes["votes"])


def test_votes_list_person_scope_matches_breakdown(client, db_path):
    """The Votes list scoped to one MP + a participation segment (the link each
    profile-pie slice points to) returns exactly that segment's votes, so the
    count matches the pie. Reuses the breakdown fixture: v-1 Igen, v-3 abstain,
    v-4 novote, v-5 absent, v-6 no record (nem volt jelen), v-q quorum check."""
    import sqlite3
    c = sqlite3.connect(db_path)
    for vid, value, code in (("v-3", "Tartózkodás", "abstain"),
                             ("v-4", "Nem szavazott", "novote"),
                             ("v-5", "Előre bejelentett hiányzó", "absent")):
        c.execute("INSERT INTO vote (id, period_number, vote_datetime, result, has_per_mp) "
                  "VALUES (?, 43, '2026-05-28T10:00:00Z', 'Elfogadva', 1)", (vid,))
        c.execute("INSERT INTO vote_record (vote_id, person_id, name, value, value_code) "
                  "VALUES (?, 'k001', 'Kovács Béla', ?, ?)", (vid, value, code))
    c.execute("INSERT INTO vote (id, period_number, vote_datetime, result, has_per_mp) "
              "VALUES ('v-6', 43, '2026-05-29T10:00:00Z', 'Elfogadva', 1)")
    c.execute("INSERT INTO vote (id, period_number, vote_datetime, result, has_per_mp) "
              "VALUES ('v-q', 43, '2026-05-09T08:45:00Z', 'Határozatképes', 1)")
    c.execute("INSERT INTO vote_record (vote_id, person_id, name, value, value_code) "
              "VALUES ('v-q', 'k001', 'Kovács Béla', 'Igen', 'yes')")
    c.commit(); c.close()

    def scope(value):
        return client.get("/api/v1/votes",
                          params={"person": "k001", "value": value}).json()

    # One page per pie segment; counts equal the breakdown's.
    assert scope("voted")["total"] == 2          # v-1 + v-3
    assert scope("novote")["total"] == 1         # v-4
    assert scope("absent")["total"] == 1         # v-5
    assert scope("not_present")["total"] == 1    # v-6, no record

    voted = scope("voted")
    # The list carries the MP's own cast value per vote, and their name.
    assert voted["person"]["id"] == "k001" and voted["person"]["label"]
    assert {v["person_value_code"] for v in voted["votes"]} <= {"yes", "abstain"}
    # "nem volt jelen" votes have no record → null cast value, and it's v-6.
    np = scope("not_present")
    assert np["votes"][0]["id"] == "v-6"
    assert np["votes"][0]["person_value_code"] is None
    # The quorum check is never listed, in any segment.
    all_ids = {v["id"] for seg in ("voted", "novote", "absent", "not_present")
               for v in scope(seg)["votes"]}
    assert "v-q" not in all_ids
    # No value → every substantive vote the MP has a record in (voted+novote+absent).
    assert scope("")["total"] == 4
