"""Remuneration (REP-17).

The figure is parlament.hu's, so nothing here tests a computed amount. What it
tests is the reading of that figure against the statute: that a published fee is
matched to the right rate and section, that an office is named only where our
own data agrees with what was paid, and — the part that matters most — that a
month which is *not* a clean multiple is reported as such instead of being
rounded onto the nearest rule.
"""

from __future__ import annotations

import sqlite3

import pytest

from app import salary

MONTH = "2026-08-01"
BASE = 1_309_493        # the §104(1) amount in force from 2026-07-01


def _fee(multiplier):
    """A published fee at the given statutory rate, rounded as the payroll does."""
    return round(BASE * multiplier)


def _c(name, role="tag", *, sub=False, cycle="2026-", start="2026-05-09T00:00:00Z", end=None):
    return {"committee": name, "role": role, "isSubcommittee": sub,
            "cycle": cycle, "start": start, "end": end}


def _months(*pairs):
    return [{"month": m, "amountHuf": a} for m, a in pairs]


# --- the base ---------------------------------------------------------------

def test_base_is_dated_and_sourced():
    base = salary.base_for(MONTH)
    assert base["amount_huf"] == BASE
    assert base["valid_from"] <= MONTH
    assert base["formula"] and base["source"] and base["section"]


def test_a_month_outside_the_schedule_is_not_explained():
    """Without a base for that month there is no arithmetic to show — but the
    published amount itself is unaffected, so only `basis` goes."""
    assert salary.base_for("2000-01-01") is None
    assert salary.explain(2_618_986, "2000-01-01", [], [], None) is None


# --- reading a published fee against the statute ----------------------------

@pytest.mark.parametrize("multiplier, section", [
    (2.91, "120. §"),
    (2.65, "104. § (4)"),
    (2.4,  "105. § (1)"),
    (2.2,  "105. § (3)"),
    (2.0,  "104. § (2) b)"),
    (1.8,  "104. § (2) a)"),
    (1.0,  "104. § (1)"),
])
def test_each_rate_is_recognised(multiplier, section):
    out = salary.explain(_fee(multiplier), MONTH, [], [], None)
    assert out["exact"] is True
    assert out["multiplier"] == multiplier
    assert section in out["sections"]
    assert out["base_huf"] == BASE


def test_a_rate_shared_by_several_offices_lists_them_all():
    """2.4× is a Deputy Speaker's fee, a committee chair's, a House Steward's and
    a deputy faction leader's. With nothing to disambiguate it, the honest output
    is every section that sets that rate — not a pick."""
    out = salary.explain(_fee(2.4), MONTH, [], [], None)
    assert out["role"] is None
    assert set(out["sections"]) >= {"105. § (1)", "105. § (2)", "104. § (5)", "4. § (6)"}


def test_our_own_data_names_the_office_when_it_agrees():
    out = salary.explain(_fee(2.4), MONTH, [_c("Külügyi Bizottság", "elnök")], [], "tag")
    assert out["role"] == "committee_chair"
    assert out["sections"] == ["105. § (2)"]


def test_the_quota_limited_deputy_is_readable_even_though_it_was_unguessable():
    """§104(5)'s 2.4× could never be *predicted* — the quota is decided inside the
    faction — but once a 2.4× fee is published for a deputy faction leader, that
    is what it is."""
    out = salary.explain(_fee(2.4), MONTH, [_c("Külügyi Bizottság")], [],
                         "frakcióvezető-helyettes")
    assert out["role"] == "faction_deputy"
    assert out["sections"] == ["104. § (5)"]


def test_an_office_the_payroll_contradicts_is_not_asserted():
    """A real case: a committee vice-chair on our books paid at 2.0×. Naming the
    vice-chairmanship would contradict the payment it claims to explain, so the
    rate is reported with its section and no office."""
    out = salary.explain(_fee(2.0), MONTH,
                         [_c("Külügyi Bizottság", "alelnök"), _c("Oktatási Bizottság")],
                         [], "tag")
    assert out["exact"] is True
    assert out["multiplier"] == 2.0
    assert out["role"] == "multi_committee"     # the entitlement that *does* match
    out2 = salary.explain(_fee(2.2), MONTH, [_c("Külügyi Bizottság")], [], "tag")
    assert out2["role"] is None                 # nothing of ours pays 2.2 here
    assert out2["sections"] == ["105. § (3)", "105. § (4)"]


def test_a_part_month_is_not_rounded_onto_the_nearest_rule():
    """A departing member's last month came to 253 450 Ft. There is no rule that
    produces it, and pretending otherwise would be inventing a fact."""
    out = salary.explain(253_450, MONTH, [], [], None)
    assert out["exact"] is False
    assert out["sections"] == [] and out["role"] is None
    assert out["multiplier"] == pytest.approx(253_450 / BASE, abs=1e-4)


def test_a_deduction_is_not_rounded_either():
    """1 967 775 Ft is 1.5027× the base — a §107 absence deduction, not a rate."""
    assert salary.explain(1_967_775, MONTH, [], [], None)["exact"] is False


# --- what carries no fee ----------------------------------------------------

def test_subcommittee_and_investigative_seats_do_not_corroborate_a_rate():
    """Neither is a §14(1)a) standing committee, so neither may be offered as the
    explanation of a chair's fee."""
    seats = [_c("Turisztikai Albizottság", "elnök", sub=True),
             _c("A Kegyelmi Botrány Felelőseit Feltáró Vizsgálóbizottság", "elnök")]
    assert salary.entitlements(seats, [], "tag") == ["member"]


def test_legislative_committee_chair_is_not_a_105_chair():
    """§105(2) points at §14(1)a), which the Törvényalkotási Bizottság is not:
    its members fall under §104(2)b)."""
    roles = salary.entitlements([_c("Törvényalkotási Bizottság", "elnök")], [], "tag")
    assert "committee_chair" not in roles and "multi_committee" in roles


def test_temporary_opening_sitting_offices_carry_no_rate():
    assert salary.entitlements([], ["korjegyző"], "tag") == ["member"]
    assert salary.entitlements([], ["az Országgyűlés korelnöke"], "tag") == ["member"]


# --- the DB-backed entry point ---------------------------------------------

@pytest.fixture()
def seated_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE electoral_period (number INT, label TEXT, date_start TEXT, date_end TEXT);
        CREATE TABLE membership (person_id TEXT, period_number INT, position TEXT);
        INSERT INTO electoral_period VALUES (42, '42. ciklus', '2022-05-02', '2026-05-08');
        INSERT INTO electoral_period VALUES (43, '43. ciklus', '2026-05-09', NULL);
        INSERT INTO membership VALUES ('p1', 43, 'tag');
    """)
    db.commit()
    return db


def _office(title, end=None, category="parliamentary", start="2026-05-09T00:00:00Z"):
    return {"title": title, "category": category, "start": start, "end": end}


def test_the_published_figure_is_what_is_served(seated_db):
    """Not a derived one: the amount out is the amount in, for the newest month."""
    out = salary.for_person(seated_db, "p1",
                            _months(("2026-07-01", 2_618_986), ("2026-08-01", 2_880_885)),
                            [_c("Külügyi Bizottság")], [])
    assert out["amount_huf"] == 2_880_885
    assert out["month"] == "2026-08-01"
    assert out["source"] == "parlament.hu"
    assert [h["month"] for h in out["history"]] == ["2026-08-01", "2026-07-01"]


def test_nobody_without_a_published_month_gets_a_figure(seated_db):
    """A former MP simply has no months, so this needs no special case."""
    assert salary.for_person(seated_db, "p1", [], [], []) is None
    assert salary.for_person(seated_db, "p1", None, [], []) is None


def test_a_month_is_explained_against_the_offices_held_in_it(seated_db):
    """The fee is for that month, so a chairmanship taken up afterwards must not
    be offered as its explanation."""
    later = _c("Külügyi Bizottság", "elnök", start="2026-09-01T00:00:00Z")
    out = salary.for_person(seated_db, "p1", _months(("2026-08-01", _fee(2.4))),
                            [later], [])
    assert out["basis"]["role"] is None
    ended = _c("Külügyi Bizottság", "elnök", end="2026-06-30T21:59:59Z")
    out2 = salary.for_person(seated_db, "p1", _months(("2026-08-01", _fee(2.4))),
                             [ended], [])
    assert out2["basis"]["role"] is None


def test_archive_cycle_seats_do_not_explain_a_current_fee(seated_db):
    old = _c("Külügyi Bizottság", "elnök", cycle="2022-2026",
             start="2022-05-02T00:00:00Z", end="2026-05-08T21:59:59Z")
    out = salary.for_person(seated_db, "p1", _months(("2026-08-01", _fee(2.4))), [old], [])
    assert out["basis"]["role"] is None


def test_expenses_are_always_excluded(seated_db):
    out = salary.for_person(seated_db, "p1", _months(("2026-08-01", _fee(1.8))), [], [])
    assert "excludes_expenses" in out["caveats"]


def test_government_office_is_flagged_as_partial(seated_db):
    """§106(2): a minister draws this fee as well as a salary fixed elsewhere."""
    out = salary.for_person(seated_db, "p1", _months(("2026-08-01", _fee(1.8))), [],
                            [_office("külügyminiszter", category="minister")])
    assert "government_office" in out["caveats"]


def test_a_part_month_is_flagged(seated_db):
    out = salary.for_person(seated_db, "p1", _months(("2026-08-01", 253_450)), [], [])
    assert "partial_month" in out["caveats"]
    assert out["amount_huf"] == 253_450        # still the published figure


def test_a_db_without_the_registries_still_serves_the_figure():
    """The published amount does not depend on our tables; only the corroboration
    does, so a DB missing them loses the office name and keeps the number."""
    bare = sqlite3.connect(":memory:")
    bare.row_factory = sqlite3.Row
    out = salary.for_person(bare, "p1", _months(("2026-08-01", _fee(2.4))), [], [])
    assert out["amount_huf"] == _fee(2.4)
    assert out["basis"]["exact"] is True
