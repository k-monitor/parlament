"""Remuneration (REP-17) — what the House publishes, and the arithmetic behind it.

The **figure is official**. parlament.hu publishes each representative's monthly
gross tiszteletdíj (the ``kepviselo-javadalmazasa`` query the registry scraper
now pulls), and that published amount is what the profile shows. Nothing here
invents it.

What this module adds is the **explanation**. A bare "2 618 986 Ft" tells a
reader nothing about why it is that and not something else, and the answer is
entirely public: the Ogytv. fixes every fee as a multiple of one base amount, so
dividing the published figure by that base recovers the statutory rate and the
section that sets it. That division is exact arithmetic on official data, which
is the point — it is not a guess at what someone is paid, it is a reading of a
number we were given.

Why the published figure rather than a computed one: we tried computing it, and
against 45 sitting MPs it matched 36 and missed 9. It misses a mid-month change,
an absence deduction under §107, an office the registry does not record, and a
partial month when a mandate ends — a departing member's last month came to
253 450 Ft, which no rule table would ever have produced. The statute gives the
rate; only the House knows the payment.

Two things the figure still is not, and the payload says so in ``caveats``:

* **It excludes the költségtérítés** — the accommodation, office, staff and
  travel frames of §108–§115, which are reimbursements against a budget rather
  than income, and whose drawn amounts are not published.
* **It excludes any other public salary.** §106(2) pays a minister or state
  secretary who is also an MP this fee *on top of* a government salary set by a
  different law, so for them it is a component, not a total.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

# --- The base amount (Ogytv. §104(1)) --------------------------------------
#
# The law states the base as a formula — 1.8× the KSH-published national average
# monthly gross earnings of the preceding year, in force from 1 March to the end
# of the following February — but which KSH series the House applies is not
# something the statute pins down, so the amount here is the **published** one
# rather than one we evaluate. It is confirmed, not inferred: every clean
# published fee divides by it to a statutory rate exactly (2.0, 2.2, 2.4, 2.65,
# 2.91), which a wrong base could not do.
#
# To update: add a new entry each March with the new `valid_from`, close the
# previous one, and cite the source. Never edit an old entry in place — a past
# amount stays true for the period it applied to.
ALAPDIJ_SCHEDULE = [
    {
        "valid_from": "2026-07-01",
        "valid_to": None,
        "amount_huf": 1_309_493,
        "formula": "a KSH által közzétett, előző évi nemzetgazdasági havi "
                   "átlagos bruttó kereset 1,8-szorosa",
        "section": "104. § (1)",
        "source": "2026. évi XVII. törvény 3. §-ával megállapított 104. § (1); "
                  "az összeg a parlament.hu által közzétett tiszteletdíjakból "
                  "visszaszámolva egyezik",
    },
]

# --- The rate table (Ogytv. §104, §105, §120) -------------------------------
#
# Each entry is a multiple of the §104(1) base. Verbatim from the consolidated
# text in force 2026.08.26 (Nemzeti Jogszabálytár), whose §104, §105 and §120
# were established by the 2026. évi XVII. törvény.
#
# Several offices share a rate, which is why `sections` is a list: a published
# 2.4× fee is a Deputy Speaker's, a standing committee chair's, a House
# Steward's or a deputy faction leader's, and the amount alone cannot say which.
RULES = [
    {"role": "house_speaker",    "multiplier": 2.91, "sections": ["120. §"]},
    {"role": "faction_leader",   "multiplier": 2.65, "sections": ["104. § (4)"]},
    {"role": "deputy_speaker",   "multiplier": 2.4,  "sections": ["105. § (1)"]},
    {"role": "committee_chair",  "multiplier": 2.4,  "sections": ["105. § (2)"]},
    {"role": "house_steward",    "multiplier": 2.4,  "sections": ["4. § (6)"]},
    {"role": "faction_deputy",   "multiplier": 2.4,  "sections": ["104. § (5)"]},
    {"role": "notary",           "multiplier": 2.2,  "sections": ["105. § (3)"]},
    {"role": "committee_vice",   "multiplier": 2.2,  "sections": ["105. § (4)"]},
    {"role": "multi_committee",  "multiplier": 2.0,  "sections": ["104. § (2) b)"]},
    {"role": "one_committee",    "multiplier": 1.8,  "sections": ["104. § (2) a)"]},
    {"role": "member",           "multiplier": 1.0,  "sections": ["104. § (1)"]},
]
_BY_ROLE = {r["role"]: r for r in RULES}

# Every distinct rate, with the sections that carry it — what a published amount
# can be matched against.
_RATES: dict[float, list[str]] = {}
for _r in RULES:
    _RATES.setdefault(_r["multiplier"], [])
    for _s in _r["sections"]:
        if _s not in _RATES[_r["multiplier"]]:
            _RATES[_r["multiplier"]].append(_s)

# A published fee is the base times a rate, rounded to the forint, so a clean
# month divides back to within a rounding error. Anything further off is not a
# rate we should name: it is a part-month, a §107 deduction or a mid-month
# change, and saying which would be a guess.
_RATE_TOLERANCE = 0.001


def base_for(on: str) -> Optional[dict]:
    """The §104(1) base amount in force in the given month (ISO date), or None."""
    for entry in ALAPDIJ_SCHEDULE:
        if entry["valid_from"] <= on and (entry["valid_to"] is None or on <= entry["valid_to"]):
            return entry
    return None


def _committee_kind(entry: dict) -> Optional[str]:
    """Classify one committee membership for §104(2) / §105(2)-(4) purposes.

    ``"legislative"`` for the Törvényalkotási Bizottság, ``"standing"`` for a
    standing committee (§104(2)a) names the nationalities committee alongside
    them), ``None`` for a body the fee rules ignore.

    Upstream gives no committee *type*, only a name and an ``isSubcommittee``
    flag, so this is by name and deliberately conservative: subcommittees carry
    no fee of their own, and an investigative or ad-hoc committee is not a
    §14(1)a) standing committee.
    """
    if entry.get("isSubcommittee"):
        return None
    name = (entry.get("committee") or "").strip()
    if not name:
        return None
    low = name.lower()
    if "albizottság" in low:
        return None
    if "vizsgálóbizottság" in low or "vizsgáló bizottság" in low:
        return None
    if low.startswith("törvényalkotási bizottság"):
        return "legislative"
    if "bizottság" not in low:
        return None
    return "standing"


# `person_office.title` as the office registry spells it. The two *kor-* offices
# are deliberately absent: a korelnök/korjegyző presides over the constitutive
# sitting by seniority and holds no paid office afterwards.
_OFFICE_ROLES = {
    "az országgyűlés elnöke": "house_speaker",
    "az országgyűlés alelnöke": "deputy_speaker",
    "az országgyűlés törvényalkotásért felelős alelnöke": "deputy_speaker",
    "az országgyűlés elnökének feladatait ellátó alelnök": "deputy_speaker",
    "az országgyűlés háznagya": "house_steward",
    "jegyző": "notary",
}


def entitlements(committees: list, offices: list, faction_position: Optional[str]) -> list[str]:
    """Every fee rule this seat appears to trigger, as role keys, highest first.

    Used only to **name** a rate the published amount already fixed — never to
    produce an amount. Our office data and the payroll disagree often enough
    that this is corroboration, not a source.
    """
    roles = {"member"}

    standing = legislative = 0
    for c in committees or []:
        kind = _committee_kind(c)
        if kind is None:
            continue
        role = (c.get("role") or "").strip().lower()
        if kind == "legislative":
            legislative += 1
        else:
            standing += 1
        # A chair or vice-chair fee is only earned on a standing committee
        # (§105(2) and (4) both point at §14(1)a)), never on the Törvényalkotási
        # Bizottság, whose members fall under §104(2)b) instead.
        if kind == "standing":
            if role in ("elnök", "társelnök"):
                roles.add("committee_chair")
            elif role in ("alelnök", "helyettesítő alelnök"):
                roles.add("committee_vice")

    if legislative or standing >= 2:
        roles.add("multi_committee")
    elif standing == 1:
        roles.add("one_committee")

    for title in offices or []:
        role = _OFFICE_ROLES.get((title or "").strip().lower())
        if role:
            roles.add(role)

    position = (faction_position or "").strip().lower()
    if position == "frakcióvezető":
        roles.add("faction_leader")
    elif position == "frakcióvezető-helyettes":
        # §104(5)'s 2.4× is quota-limited — one deputy per each started
        # twenty-five members — and which deputies fall inside it is decided in
        # the faction and never published. As a *source* that was unusable; as a
        # way to read a published 2.4× it is exactly right.
        roles.add("faction_deputy")

    return sorted(roles, key=lambda r: -_BY_ROLE[r]["multiplier"])


def explain(amount_huf: int, month: str, committees: list, offices: list,
            faction_position: Optional[str]) -> Optional[dict]:
    """Read a published monthly fee against the statute.

    Returns the base it is a multiple of, the rate, the sections that set that
    rate and — where our own office data corroborates one of them — which office
    it is. ``exact`` is False when the amount is not a clean multiple, which is
    a real and common case (a part-month, a §107 deduction) and is reported as
    such rather than rounded to the nearest rule.
    """
    base = base_for(month)
    if base is None or not amount_huf:
        return None
    ratio = amount_huf / base["amount_huf"]

    matched = next((m for m in _RATES if abs(ratio - m) <= _RATE_TOLERANCE), None)
    out = {
        "base_huf": base["amount_huf"],
        "base_section": base["section"],
        "base_formula": base["formula"],
        "base_valid_from": base["valid_from"],
        "base_source": base["source"],
        "exact": matched is not None,
        # The rate as published data gives it: the matched statutory one, or the
        # real ratio rounded for display when the month is not a clean multiple.
        "multiplier": matched if matched is not None else round(ratio, 4),
        "sections": _RATES.get(matched, []) if matched is not None else [],
        "role": None,
    }
    if matched is not None:
        # Name the office only where what we hold agrees with what was paid. On
        # a mismatch the honest output is the rate and its candidate sections —
        # asserting an office the payroll contradicts would be the one thing
        # this panel must not do.
        for role in entitlements(committees, offices, faction_position):
            if abs(_BY_ROLE[role]["multiplier"] - matched) <= _RATE_TOLERANCE:
                out["role"] = role
                out["sections"] = _BY_ROLE[role]["sections"]
                break
    return out


def _current_period(db: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return db.execute(
        "SELECT number, date_start FROM electoral_period "
        "WHERE date_end IS NULL OR date_end = '' "
        "ORDER BY number DESC LIMIT 1").fetchone()


def _held_on(entry: dict, on: str) -> bool:
    """Is this committee seat / office held in the given month?

    An open end means "still held". Dates arrive as ISO instants, so comparing
    the date prefix is enough.
    """
    end = (entry.get("end") or entry.get("date_end") or "").strip()
    if end and end[:10] < on:
        return False
    start = (entry.get("start") or entry.get("date_start") or "").strip()
    return not start or start[:10] <= on


def for_person(db: sqlite3.Connection, person_id: str, remuneration: list,
               committees: list, offices: list) -> Optional[dict]:
    """The published remuneration for one person, with the statute read against it.

    ``None`` when nothing is published for them — which is the honest answer for
    everyone who is not currently paid, and needs no special-casing: the House
    publishes months, so a former MP simply has none.
    """
    months = [m for m in (remuneration or [])
              if isinstance(m, dict) and m.get("month") and m.get("amountHuf") is not None]
    if not months:
        return None
    months.sort(key=lambda m: m["month"], reverse=True)
    latest = months[0]
    month = str(latest["month"])[:10]
    amount = int(latest["amountHuf"])

    # The offices held *in the month that was paid*, not today: the fee is for
    # that month, so corroborating it against a seat taken up afterwards would
    # explain it with a fact that had not happened yet.
    try:
        period = _current_period(db)
    except sqlite3.OperationalError:
        period = None
    cycle = (period["date_start"] or "")[:4] if period else ""
    cycle_committees = [c for c in (committees or [])
                        if (not cycle or (c.get("cycle") or "").startswith(cycle))
                        and _held_on(c, month)]
    cycle_offices = [o.get("title") for o in (offices or []) if _held_on(o, month)]

    position = None
    if period is not None:
        try:
            row = db.execute(
                "SELECT position FROM membership WHERE person_id = ? AND period_number = ?",
                (person_id, period["number"])).fetchone()
            position = row["position"] if row else None
        except sqlite3.OperationalError:
            position = None

    caveats = ["excludes_expenses"]
    if any((o.get("category") or "") in ("minister", "state-secretary", "pm")
           and _held_on(o, month) for o in (offices or [])):
        caveats.append("government_office")

    basis = explain(amount, month, cycle_committees, cycle_offices, position)
    if basis and not basis["exact"]:
        # Not a clean multiple of the base: a part-month (a mandate that began or
        # ended mid-month), or a deduction under §107. We say that rather than
        # forcing the nearest rule onto it.
        caveats.append("partial_month")

    return {
        "source": "parlament.hu",
        "amount_huf": amount,
        "month": month,
        "basis": basis,
        # Every published month, newest first — two months today, a series as it
        # accumulates, and the only place a reader can see a fee change.
        "history": [{"month": str(m["month"])[:10], "amount_huf": int(m["amountHuf"])}
                    for m in months],
        "caveats": caveats,
    }
