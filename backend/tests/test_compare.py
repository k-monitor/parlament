"""Comparing representatives side by side (REP-15) — the /compare endpoint.

The comparison page is a summary of pages the site already serves, so almost
everything worth testing here is about it *not drifting* from them:

* every figure equals what `/{id}/statistics` reports for the same person and the
  same scope — the two pages must never disagree (and since the refactor they
  share one vote-participation helper, this is the test that keeps them sharing it);
* a value that does not apply to a person comes back **null, not zero** — a
  minister has no roll calls to miss, and a zero there would be read as a perfect
  attendance record (TRUST-1);
* the URL is a shareable address, so it must survive being wrong: an unknown id is
  reported and skipped rather than 404ing the whole comparison, ids past the limit
  are reported rather than silently dropped, and `/representatives/compare` is
  never taken for a person called "compare".
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app import loader


def _compare(client, ids, **params):
    q = [("id", i) for i in ids] + list(params.items())
    r = client.get("/api/v1/representatives/compare", params=q)
    assert r.status_code == 200, r.text
    return r.json()


def test_columns_come_back_in_the_order_asked_for(client):
    """A comparison's left-to-right order is the reader's, not the database's."""
    assert [p["person_id"] for p in _compare(client, ["n002", "k001"])["people"]] \
        == ["n002", "k001"]
    assert [p["person_id"] for p in _compare(client, ["k001", "n002"])["people"]] \
        == ["k001", "n002"]


def test_duplicate_ids_collapse_to_one_column(client):
    """A person compared with themselves is not a comparison — and the tray can
    offer the same id twice (a profile plus the same person ticked in the list)."""
    data = _compare(client, ["k001", "k001", "n002"])
    assert [p["person_id"] for p in data["people"]] == ["k001", "n002"]


def test_figures_match_the_profile_statistics_endpoint(client):
    """The invariant the whole feature rests on: same person, same scope, same
    numbers as their own page — speeches, speaking time and the vote split alike."""
    data = _compare(client, ["k001", "n002"], period=43)
    for p in data["people"]:
        stats = client.get(f"/api/v1/representatives/{p['person_id']}/statistics",
                           params={"period": 43}).json()["totals"]
        assert p["speech_count"] == stats["speech_count"]
        assert p["speaking_seconds"] == stats["speaking_seconds"]
        assert p["sentence_count"] == stats["sentence_count"]
        assert p["own_motions"] == stats["bills_submitted"]
        assert p["votes_total"] == stats["votes_total"]
        assert p["votes_missed"] == stats["votes_missed"]
        assert p["votes_missed_pct"] == stats["votes_missed_pct"]
        assert p["vote_breakdown"] == stats["vote_breakdown"]


def test_average_speech_length_is_the_two_totals_divided(client):
    """The one derived figure. Null — not zero — for someone who never spoke, since
    "no speeches" has no average length."""
    data = _compare(client, ["k001"], period=43)
    p = data["people"][0]
    assert p["speech_count"] > 0
    assert p["avg_speech_seconds"] == pytest.approx(
        p["speaking_seconds"] / p["speech_count"])

    empty = _compare(client, ["k001"], period=1)["people"][0]
    assert empty["speech_count"] == 0
    assert empty["avg_speech_seconds"] is None


def test_speaking_days_counts_sitting_days_not_speeches(client):
    """Two speeches on one sitting day are two speeches but one day."""
    p = _compare(client, ["k001"], period=43)["people"][0]
    assert p["speaking_days"] == 1
    assert p["speech_count"] >= 1


def test_documents_are_bucketed_like_the_profile_sections(client):
    """The three counts split the same way the profile splits its three submitted-
    irományok sections (K/A/I, T/H, the rest), so a column matches those badges."""
    p = _compare(client, ["k001"], period=43)["people"][0]
    docs = p["documents"]
    assert docs["total"] == docs["questions"] + docs["bills"] + docs["other"]
    for main_types, key in (("K,A,I", "questions"), ("T,H", "bills")):
        listed = client.get("/api/v1/bills", params={
            "sponsor": "k001", "period": 43, "main_type_in": main_types,
            "limit": 1}).json()["total"]
        assert docs[key] == listed


def test_a_person_without_a_mandate_gets_nulls_not_zeroes(conn, client):
    """A non-MP holds no mandate, so there are no roll calls for them to miss:
    their whole participation is *not applicable*, and reporting it as zeroes would
    read as a spotless attendance record (TRUST-1). `zzz9` is the fixture's office
    holder who never sat in the House.

    The counterpart matters as much: an actual MP must still get the numbers, or
    this rule would be indistinguishable from the feature being broken."""
    assert conn.execute("SELECT is_mp FROM person WHERE person_id='zzz9'"
                        ).fetchone()["is_mp"] == 0
    non_mp, mp = _compare(client, ["zzz9", "k001"], period=43)["people"]
    assert non_mp["is_mp"] is False
    assert non_mp["votes_total"] is None
    assert non_mp["votes_missed"] is None
    assert non_mp["votes_missed_pct"] is None
    assert non_mp["vote_breakdown"] is None
    # What the corpus *does* know about them is still there.
    assert non_mp["label"]
    assert mp["is_mp"] is True and mp["vote_breakdown"] is not None


def test_a_constituency_is_only_reported_for_someone_holding_a_seat(conn, client):
    """A seat is an MP's notion. Should a non-MP row carry a leftover constituency
    (they held one in an earlier cycle, say), the comparison must not print it as if
    they represented that district now — the cell is "not applicable", not a
    district (REP-9/REP-12)."""
    assert _compare(client, ["k001"])["people"][0]["constituency"] == "Budapest 1. OEVK"

    conn.execute("UPDATE person SET constituency='Pest 4. OEVK' WHERE person_id='zzz9'")
    conn.commit()
    assert _compare(client, ["zzz9"])["people"][0]["constituency"] is None


def test_an_unknown_id_is_reported_and_skipped_not_fatal(client):
    """A comparison link shared before a re-import must still open for the people
    it can still name."""
    data = _compare(client, ["k001", "nobody-here", "n002"])
    assert data["missing"] == ["nobody-here"]
    assert [p["person_id"] for p in data["people"]] == ["k001", "n002"]


def test_ids_past_the_limit_are_reported_not_silently_truncated(client):
    """Five ids in a hand-edited URL: four columns, and the page is told the rest
    were left out rather than quietly showing a shorter comparison."""
    ids = ["k001", "n002", "x999", "a1", "b2"]
    data = _compare(client, ids)
    assert data["limit"] == 4
    assert data["dropped"] == ["b2"]
    assert len(data["people"]) + len(data["missing"]) == 4


def test_no_ids_is_an_empty_comparison_not_an_error(client):
    """The page opens as its own picker, so the endpoint answers it as such."""
    data = _compare(client, [])
    assert data["people"] == [] and data["missing"] == []


def test_scope_is_the_selected_cycle(client):
    """Everything countable is cycle-scoped (§4A): a cycle the person was not
    active in reads zero, and the scope description names the cycle."""
    in_cycle = _compare(client, ["k001"], period=43)
    assert in_cycle["scope"]["periods"] == [43]
    assert "43" in in_cycle["scope"]["description"]
    assert in_cycle["people"][0]["speech_count"] > 0

    other = _compare(client, ["k001"], period=1)
    assert other["people"][0]["speech_count"] == 0
    assert other["people"][0]["speaking_seconds"] == 0


def test_career_figures_are_not_cycle_scoped(client):
    """Committee seats and published declarations are biography, like the profile's
    own lists: the same whichever cycle is selected (the UI says so per row)."""
    a = _compare(client, ["k001"], period=43)["people"][0]
    b = _compare(client, ["k001"], period=1)["people"][0]
    assert a["declaration_count"] == b["declaration_count"]
    assert a["committee_count"] == b["committee_count"]
    # The fixture's k001 has two declarations, one of which was never published —
    # only the published one is counted (REP-13).
    assert a["declaration_count"] == 1


def test_compare_is_not_taken_for_a_person_id(client):
    """The static segment is declared before `/{person_id}`, so the path resolves
    to the comparison rather than to a search for somebody called "compare"."""
    r = client.get("/api/v1/representatives/compare")
    assert r.status_code == 200
    assert "people" in r.json()


def test_modules_off_leave_their_rows_out_rather_than_faking_them(client, monkeypatch):
    """EXT-6: with Bills and Votes disabled the dependent figures are absent and
    flagged as such — never a zero the reader would take for a real count."""
    # Patched on the Settings object *this router* holds, not on
    # `app.config.settings`: modules bind `settings` at import, and another test in
    # the suite `importlib.reload`s app.config, rebinding that name to a fresh
    # object — the hazard test_site_cycles.py and test_og.py both document.
    from app.modules.representatives import router as reps_router
    monkeypatch.setattr(reps_router.settings, "enabled_modules",
                        ["proceedings", "representatives"])

    data = _compare(client, ["k001"], period=43)
    assert data["bills_available"] is False and data["votes_available"] is False
    p = data["people"][0]
    assert p["documents"] is None and p["own_motions"] is None
    assert p["votes_total"] is None and p["vote_breakdown"] is None
    # The speech figures belong to no optional module, so they are unaffected.
    assert p["speech_count"] > 0


def test_the_roll_call_universe_is_counted_once_for_every_column(client, monkeypatch):
    """Four columns must not mean four scans of the whole `vote` table: the
    person-independent universe is counted once and passed in (the reason
    `_vote_participation` takes `total_rollcall` at all)."""
    from app.modules.representatives import router as reps_router

    calls: list = []
    real = reps_router._vote_participation

    def spy(db, person_id, election_history_json, periods, total_rollcall=None):
        calls.append(total_rollcall)
        return real(db, person_id, election_history_json, periods, total_rollcall)

    monkeypatch.setattr(reps_router, "_vote_participation", spy)
    _compare(client, ["k001", "n002"], period=43)
    assert len(calls) == 2
    # Both columns got the same precounted universe rather than None (= count it
    # yourself), and it is the real count.
    assert calls[0] == calls[1] and calls[0] is not None


def test_votes_outside_a_mandate_are_not_counted_as_absences(tmp_path, data_dir,
                                                            monkeypatch):
    """The regression the shared helper protects: a replacement seated mid-cycle
    must not be shown as having missed everything before they arrived. Their
    `not_mp` bucket holds those votes and stays out of the denominator — the same
    rule the profile applies, now applied by the same code."""
    # Give n002 a mandate that starts after the fixture's only roll-call vote.
    processed = data_dir / "processed" / "representatives-43.json"
    registry = json.loads(processed.read_text())
    for rec in registry["data"]:
        if rec["personID"] == "n002":
            rec["electionHistory"] = [
                {"cycle": "2026-", "constituency": "Pest 4. OEVK",
                 "electionDate": "2026-04-12",
                 "mandateStart": "2026-06-01T00:00:00Z", "mandateEnd": None}]
    processed.write_text(json.dumps(registry, ensure_ascii=False))

    out = tmp_path / "late.db"
    loader.build_database(data_dir, out)
    from app.config import settings
    monkeypatch.setattr(settings, "db_path", str(out))
    monkeypatch.setattr(db_module.settings, "db_path", str(out))
    from app.main import app
    client = TestClient(app)

    p = _compare(client, ["n002"], period=43)["people"][0]
    b = p["vote_breakdown"]
    assert b["not_mp"] >= 1                      # the vote predating their seat
    assert b["total"] == b["voted"] + b["novote"] + b["absent"] + b["not_present"]
    # …and it is excluded from the base, so it cannot be read as an absence.
    assert b["not_mp"] not in (b["absent"], b["not_present"]) or b["not_mp"] == 0
    assert p["votes_missed"] == b["novote"] + b["absent"] + b["not_present"]
