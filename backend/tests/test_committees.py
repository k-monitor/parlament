"""Committees module (§6F): the loader's tables and the API over them.

Covers the two things the domain gets wrong most easily — that a committee's
membership comes from two sources that must be merged rather than concatenated,
and that an iromány reference resolves to a held bill only when we hold it.
"""

from __future__ import annotations


def test_bodies_loaded_with_parent_link(conn):
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM committee")}
    assert set(rows) == {"biz-1", "biz-1a"}
    assert rows["biz-1"]["parent_id"] is None
    assert rows["biz-1"]["kind"] == "állandó"
    assert rows["biz-1"]["standing_code"] == "KTB"
    # A subcommittee is just a committee with a parent — same table, no kind.
    assert rows["biz-1a"]["parent_id"] == "biz-1"
    assert rows["biz-1a"]["kind"] is None
    # Upstream's own aggregates ride along on the body.
    assert (rows["biz-1"]["meetings"], rows["biz-1"]["total_minutes"]) == (4, 300)


def test_member_person_link_resolves_only_for_known_people(conn):
    seats = {r["name"]: r for r in conn.execute(
        "SELECT * FROM committee_member WHERE committee_id = 'biz-1'")}
    assert seats["Kovács Béla"]["person_id"] == "k001"
    assert seats["Kovács Béla"]["faction_id"] is not None       # shared faction
    # Not in the roster: the seat keeps its label and links nowhere (SCR-5).
    assert seats["Külső Elek"]["person_id"] is None


def test_committee_list_hides_subcommittees_by_default(client):
    r = client.get("/api/v1/committees", params={"period": 43})
    assert r.status_code == 200
    body = r.json()
    assert body["tablesReady"] is True
    assert [c["name"] for c in body["items"]] == ["Költségvetési Bizottság"]
    assert body["items"][0]["memberCount"] == 2
    assert body["items"][0]["chairs"] == [
        {"personId": "k001", "name": "Kovács Béla", "faction": "Fidesz"}]
    assert body["kinds"] == [{"kind": "állandó", "count": 1}]

    both = client.get("/api/v1/committees",
                      params={"period": 43, "include_subcommittees": True}).json()
    names = {c["name"]: c for c in both["items"]}
    assert set(names) == {"Költségvetési Bizottság", "Ellenőrző Albizottság"}
    assert names["Ellenőrző Albizottság"]["parentId"] == "biz-1"
    assert names["Ellenőrző Albizottság"]["isSubcommittee"] is True


def test_committee_list_filters(client):
    assert client.get("/api/v1/committees",
                      params={"q": "költség"}).json()["total"] == 1
    assert client.get("/api/v1/committees",
                      params={"q": "nincs ilyen"}).json()["total"] == 0
    assert client.get("/api/v1/committees",
                      params={"kind": "állandó"}).json()["total"] == 1
    assert client.get("/api/v1/committees",
                      params={"kind": "vizsgáló"}).json()["total"] == 0
    assert client.get("/api/v1/committees", params={"kind": "nope"}).status_code == 400
    assert client.get("/api/v1/committees", params={"sort": "nope"}).status_code == 400


def test_committee_sheet_separates_current_and_former_members(client):
    r = client.get("/api/v1/committees/biz-1")
    assert r.status_code == 200
    c = r.json()
    assert c["name"] == "Költségvetési Bizottság"
    # Roster in seniority order, chair first.
    assert [(m["role"], m["name"]) for m in c["members"]] == [
        ("chair", "Kovács Béla"), ("member", "Külső Elek")]
    # Nagy Anna's seat ended and she is on no current roster, so she is a former
    # member — the case a snapshot alone cannot report.
    assert [(f["name"], f["reason"]) for f in c["formerMembers"]] == [
        ("Nagy Anna", "lemondott")]
    assert [s["name"] for s in c["subcommittees"]] == ["Ellenőrző Albizottság"]
    assert c["counts"] == {"meetings": 2, "minutes": 1, "discussed": 2, "tabled": 1}


def test_an_officers_two_terms_are_one_seat(client):
    """Upstream records a chair twice — a `membership` span carrying no title
    (so it reads as plain "member") and an `office` span. Keyed by role those
    come back as two seats on one committee, one of them a past "member" the
    person never stopped being."""
    items = client.get("/api/v1/committees/representative/k001").json()["items"]
    assert [i["committeeId"] for i in items] == ["biz-1", "biz-1a"]
    seat = items[0]
    # The office term describes the seat; the membership term dates it.
    assert (seat["role"], seat["roleLabel"]) == ("chair", "elnök")
    assert seat["dateStart"] == "2026-05-09T12:00:00Z"


def test_a_resigned_chairmanship_does_not_relabel_the_whole_seat(conn, client):
    """An MP who gave up the chair but stayed a member left as a member.
    Labelling the seat "elnök" for its whole span asserts an office they
    resigned."""
    conn.execute("UPDATE committee SET date_end = '2026-06-19' WHERE id = 'biz-1'")
    # The chairmanship ends early; the membership runs on to the end of term.
    conn.execute("""UPDATE committee_term SET date_end = '2026-05-30T12:00:00Z',
                        reason = 'lemondott'
                     WHERE person_id = 'k001' AND kind = 'office'""")
    conn.execute("""INSERT INTO committee_term(committee_id, person_id, name,
                        kind, role, role_label, date_start, date_end)
                    VALUES ('biz-1','k001','Kovács Béla','membership','member',
                            NULL,'2026-05-09T12:00:00Z','2026-06-19T12:00:00Z')""")
    conn.commit()
    seat = next(i for i in
                client.get("/api/v1/committees/representative/k001").json()["items"]
                if i["committeeId"] == "biz-1")
    assert seat["current"] is False
    assert seat["role"] == "member"
    # The span is still the whole seat, not just the chairmanship.
    assert (seat["dateStart"], seat["dateEnd"]) == (
        "2026-05-09T12:00:00Z", "2026-06-19T12:00:00Z")


def test_a_chairmanship_held_to_the_end_still_labels_the_seat(conn, client):
    """The converse: both terms end together, so the office is what the seat
    was — the fix must not demote every past chair to "member"."""
    conn.execute("UPDATE committee SET date_end = '2026-06-19' WHERE id = 'biz-1'")
    conn.execute("""UPDATE committee_term SET date_end = '2026-06-19T12:00:00Z'
                     WHERE person_id = 'k001' AND kind = 'office'""")
    conn.execute("""INSERT INTO committee_term(committee_id, person_id, name,
                        kind, role, role_label, date_start, date_end)
                    VALUES ('biz-1','k001','Kovács Béla','membership','member',
                            NULL,'2026-05-09T12:00:00Z','2026-06-19T12:00:00Z')""")
    conn.commit()
    seat = next(i for i in
                client.get("/api/v1/committees/representative/k001").json()["items"]
                if i["committeeId"] == "biz-1")
    assert (seat["role"], seat["roleLabel"]) == ("chair", "elnök")


def test_former_members_include_an_office_only_departure(conn, client):
    """The registry records a departing chair as an office term often enough;
    filtering to `membership` dropped them from the committee page while their
    own profile went on reporting the seat."""
    conn.execute("""INSERT INTO committee_term(committee_id, person_id, name,
                        kind, role, role_label, faction_name, date_start,
                        date_end, reason)
                    VALUES ('biz-1','x321','Volt Elnök','office','chair',
                            'elnök','Fidesz','2026-05-09T12:00:00Z',
                            '2026-05-20T12:00:00Z','lemondott')""")
    conn.commit()
    former = client.get("/api/v1/committees/biz-1").json()["formerMembers"]
    by_name = {f["name"]: f for f in former}
    assert "Volt Elnök" in by_name
    assert by_name["Volt Elnök"]["roleLabel"] == "elnök"
    # Still one row per person, newest departure first.
    assert [f["name"] for f in former] == ["Nagy Anna", "Volt Elnök"]


def test_former_members_report_a_person_once(conn, client):
    """A departure recorded as both a membership and an office term is one
    person leaving, not two."""
    conn.execute("""INSERT INTO committee_term(committee_id, person_id, name,
                        kind, role, role_label, date_start, date_end, reason)
                    VALUES ('biz-1','n002','Nagy Anna','office','deputy-chair',
                            'alelnök','2026-05-09T12:00:00Z',
                            '2026-06-01T12:00:00Z','lemondott')""")
    conn.commit()
    former = client.get("/api/v1/committees/biz-1").json()["formerMembers"]
    assert [f["name"] for f in former] == ["Nagy Anna"]


def test_upcoming_meetings(client):
    items = client.get("/api/v1/committees/upcoming").json()["items"]
    assert [i["name"] for i in items] == [
        "Költségvetési Bizottság", "a Médiatanács tagjait jelölő eseti bizottság"]
    assert items[0]["committeeId"] == "biz-1"
    assert items[0]["time"] == "10:30"
    # A body the registry does not hold is listed but links nowhere (SCR-5)…
    assert items[1]["committeeId"] is None
    # …and a called-off sitting is kept and flagged, not dropped.
    assert items[1]["cancelled"] is True


def test_upcoming_orders_by_the_clock_not_by_the_string(conn, client):
    """The time is stored as published — "9:00", not "09:00" — so a plain
    string sort puts the 9am sitting after the half-twelve one."""
    day = client.get("/api/v1/committees/upcoming").json()["items"][0]["date"]
    conn.execute("UPDATE committee_upcoming SET starts_on = ?, starts_at = '9:00' "
                 "WHERE id = 'next-2'", (day,))
    conn.commit()
    items = client.get("/api/v1/committees/upcoming").json()["items"]
    assert [i["time"] for i in items] == ["9:00", "10:30"]


def test_upcoming_drops_meetings_already_past(conn, client):
    conn.execute("UPDATE committee_upcoming SET starts_on = '2020-01-01' "
                 "WHERE id = 'next-1'")
    conn.commit()
    items = client.get("/api/v1/committees/upcoming").json()["items"]
    assert [i["id"] for i in items] == ["next-2"]


def test_upcoming_is_not_taken_for_a_committee_id(client):
    """The static segment is declared before `/{committee_id}`."""
    assert client.get("/api/v1/committees/upcoming").status_code == 200


def test_meeting_paging_is_stable_across_a_tie(conn, client):
    """Two meetings on the same day with the same number would otherwise page
    unstably — the same row twice, or one never shown."""
    conn.execute("UPDATE committee_meeting SET held_at = '2026-06-10T09:00:00Z', "
                 "number = 2")
    conn.commit()
    first = client.get("/api/v1/committees/biz-1/meetings",
                       params={"limit": 1, "offset": 0}).json()["items"]
    second = client.get("/api/v1/committees/biz-1/meetings",
                        params={"limit": 1, "offset": 1}).json()["items"]
    assert first[0]["id"] != second[0]["id"]


def test_unknown_committee_is_404(client):
    assert client.get("/api/v1/committees/nope").status_code == 404
    assert client.get("/api/v1/committees/nope/meetings").status_code == 404


def test_meetings_keep_rows_without_minutes(client):
    items = client.get("/api/v1/committees/biz-1/meetings").json()["items"]
    # Newest first, and the meeting that published nothing is still listed.
    assert [m["numberInYear"] for m in items] == ["2/2026", "1/2026"]
    assert items[0]["minutesUrl"].endswith("1.pdf")
    assert items[1]["minutesUrl"] is None
    assert items[1]["quorum"] == "Határozatképtelen"


def test_documents_report_whether_the_bill_is_held(client):
    disc = client.get("/api/v1/committees/biz-1/documents",
                      params={"role": "discussed"}).json()
    assert disc["total"] == 2
    held = {d["billNumber"]: d["held"] for d in disc["items"]}
    assert held == {"T/100": True, "H/9": False}
    by_num = {d["billNumber"]: d for d in disc["items"]}
    assert by_num["T/100"]["sponsors"] == [
        {"personID": "k001", "name": "Kovács Béla"}]

    tabled = client.get("/api/v1/committees/biz-1/documents",
                        params={"role": "tabled"}).json()
    assert [d["billNumber"] for d in tabled["items"]] == ["100/3"]
    assert tabled["items"][0]["docType"] == "Összegző jelentés"
    assert client.get("/api/v1/committees/biz-1/documents",
                      params={"role": "nope"}).status_code == 422


def test_representative_seats_merge_the_two_membership_sources(client):
    items = client.get("/api/v1/committees/representative/k001").json()["items"]
    # Two current seats (the committee's chair and the subcommittee's member),
    # each reported once even though the chair also has a term row.
    assert [(i["name"], i["role"], i["current"]) for i in items] == [
        ("Költségvetési Bizottság", "chair", True),
        ("Ellenőrző Albizottság", "member", True)]
    # …and that term's start date is folded onto the seat the roster gave.
    assert items[0]["dateStart"] == "2026-05-09T12:00:00Z"
    assert items[1]["parentName"] == "Költségvetési Bizottság"


def test_representative_seats_include_seats_already_left(client):
    items = client.get("/api/v1/committees/representative/n002").json()["items"]
    assert [(i["name"], i["current"], i["reason"]) for i in items] == [
        ("Költségvetési Bizottság", False, "lemondott")]
    assert items[0]["dateEnd"] == "2026-06-01T12:00:00Z"


def test_a_closed_committee_is_not_a_current_seat(conn, client):
    """The roster is a snapshot: for a cycle that is over it records the seats
    as they stood on its last day, so reporting them as still held would make
    every archive seat read as current."""
    conn.execute("UPDATE committee SET date_end = '2026-06-19' WHERE id = 'biz-1'")
    conn.commit()
    items = client.get("/api/v1/committees/representative/k001").json()["items"]
    by_name = {i["name"]: i for i in items}
    closed = by_name["Költségvetési Bizottság"]
    assert closed["current"] is False
    # …and the seat is dated to the day the committee itself ended, rather than
    # left open next to a body that no longer exists.
    assert closed["dateEnd"] == "2026-06-19"
    # The subcommittee is still running, so its seat is untouched.
    assert by_name["Ellenőrző Albizottság"]["current"] is True


def test_end_of_term_is_not_reported_as_a_reason(conn, client):
    """Two thirds of the registry's "reasons" are the Assembly's own term
    ending, which the end date already says; printed, they bury the ones that
    distinguish a departure."""
    conn.execute("UPDATE committee_term SET reason = 'az Országgyűlés "
                 "megbízatása megszűnt' WHERE person_id = 'n002'")
    conn.commit()
    seat = client.get("/api/v1/committees/representative/n002").json()["items"][0]
    assert seat["reason"] is None
    # The date it ended on is untouched — only the empty restatement is dropped.
    assert seat["dateEnd"] == "2026-06-01T12:00:00Z"
    former = client.get("/api/v1/committees/biz-1").json()["formerMembers"]
    assert [f["reason"] for f in former] == [None]


def test_seats_are_cycle_scoped(client):
    assert client.get("/api/v1/committees/representative/k001",
                      params={"period": 42}).json()["items"] == []
    assert client.get("/api/v1/committees",
                      params={"period": 42}).json()["total"] == 0


def test_reingesting_a_cycle_replaces_it(conn, data_dir, db_path):
    """ING-4: loading the same cycle again must not double its rows."""
    import json
    from app import loader
    registry = json.loads((data_dir / "processed" / "committees-43.json").read_text())
    before = conn.execute("SELECT COUNT(*) FROM committee_member").fetchone()[0]
    loader.load_committees(conn, registry)
    after = conn.execute("SELECT COUNT(*) FROM committee_member").fetchone()[0]
    assert before == after == 3
    assert conn.execute("SELECT COUNT(*) FROM committee").fetchone()[0] == 2


def test_a_registry_inconsistent_with_itself_is_reported(data_dir, conn, caplog):
    """A detail row naming a body the registry's own listing does not hold is
    the `albizottsagId`/`bizottsagId` trap showing up. It cannot be loaded (the
    FK), so it must at least be shouted about — and the counts logged must be
    what landed, not what the file claimed."""
    import json
    import logging
    from app import loader
    registry = json.loads((data_dir / "processed" / "committees-43.json").read_text())
    registry["meetings"].append(
        {"meetingId": "ules-x", "committeeId": "biz-nonexistent",
         "number": 9, "datetime": "2026-06-12T09:00:00Z"})
    with caplog.at_level(logging.WARNING, logger="parlamonitor.loader"):
        loader.load_committees(conn, registry)
    assert any("does not hold" in r.getMessage() for r in caplog.records)
    # The orphan is genuinely absent rather than loaded against a missing body.
    assert conn.execute("SELECT COUNT(*) FROM committee_meeting "
                        "WHERE id = 'ules-x'").fetchone()[0] == 0


def test_seats_report_which_cycles_the_registry_covers(client):
    """The scraper runs per cycle, so the module can hold 40–43 while the
    corpus goes back to 34. A profile that swapped its career-wide biography
    list for this one would silently lose the earlier seats, so the answer says
    whether it covers what was asked."""
    whole = client.get("/api/v1/committees/representative/k001").json()
    assert whole["coverage"] == [43]
    assert whole["covers_scope"] is True
    # A scope reaching into a cycle the registry was never run for still
    # answers for the cycle it does hold — but says the answer is partial, so
    # the profile can fall back to its career-wide biography list instead of
    # presenting a convincing subset.
    gap = client.get("/api/v1/committees/representative/k001",
                     params={"period": [42, 43]}).json()
    assert [i["period"] for i in gap["items"]] == [43, 43]
    assert gap["covers_scope"] is False
    assert client.get("/api/v1/committees/representative/k001",
                      params={"period": 43}).json()["covers_scope"] is True


def test_module_is_registered():
    from app.config import ALL_MODULES
    from app.modules.registry import load_modules
    assert "committees" in ALL_MODULES
    assert "committees" in {m.name for m in load_modules()}


# --- crawler-facing pages (§8.6) ------------------------------------------
# The share cards themselves live in test_og.py, where the fixture that serves
# them (an app with a frontend shell to inject into) does.

def test_committees_are_in_the_sitemap(client):
    core = client.get("/sitemap-core-1.xml").text
    assert "/representatives/committees" in core
    # Main committees get a page each; subcommittees are reachable from their
    # parent and are not advertised as entry points.
    sec = client.get("/sitemap-committees-1.xml").text
    assert "/representatives/committees/biz-1<" in sec
    assert "biz-1a" not in sec
