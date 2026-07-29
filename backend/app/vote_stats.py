"""Per-vote attendance and cross-voting, derived from the per-faction breakdown.

Both figures are summed from `vote_faction_stat` — never from a per-MP scan — and
both are consumed by more than one module: the Votes list ranks and shows them on
every vote card, and the Bills/Documents page shows them on the vote tallies of a
single iromány. They live here so the two agree by construction (a bill's vote
card and the same vote's card in the list must not disagree).
"""

from __future__ import annotations

import sqlite3

# The upstream per-faction breakdown carries a summary "Összesen (…)" pseudo-row
# (the whole-house totals) alongside the real factions — exclude it wherever the
# factions are aggregated, or every tally would be counted twice.
FACTION_TOTALS_LIKE = "Összesen%"

# A presence check puts no question to the House, so "voting against your faction"
# there marks arriving late, not dissent — and its raw figures are large enough to
# swamp every real division. Such votes carry no cross-voting number at all
# (rather than a misleading one), which also sorts them out of the ranking.
PRESENCE_CHECK_MODE = "Jelenlét megállapítás"

# Cross-voting per vote: how many MPs broke their own faction's line. The number
# is the upstream per-faction "frakcióval szemben" figure ("3 fő") summed over the
# real factions — the Assembly's own count, never a reconstruction. Deriving it
# from the tallies instead (minority against the faction's majority position)
# reproduces it for only ~82% of the non-zero faction rows, because the line a
# faction is measured against is its *declared* position, not its arithmetic
# majority — so nothing here ever names an individual MP as having crossed.
# CAST parses the leading integer of "3 fő"; a row whose figure is missing reads
# as 0, and no real faction row is missing one (verified across the corpus).
DEFECTORS_SUM = "SUM(CAST(COALESCE(fs.against_faction, '') AS INTEGER))"
CAST_SUM = "SUM(COALESCE(fs.yes, 0) + COALESCE(fs.no, 0) + COALESCE(fs.abstain, 0))"
CROSSVOTING_SCOPE = (f"fs.faction_name NOT LIKE '{FACTION_TOTALS_LIKE}' "
                     f"AND COALESCE(cv_v.voting_mode, '') <> '{PRESENCE_CHECK_MODE}'")

# What a vote with no per-faction breakdown (or an out-of-scope presence check)
# carries instead — the same keys, so a response's shape never varies. The empty
# faction list is a tuple (it serializes to `[]` all the same): these defaults are
# shared by every caller, so nothing may append to them in place.
NO_ATTENDANCE = {"present": None, "seats": None, "attendance": None}
NO_CROSSVOTING = {"defectors": None, "defector_share": None,
                  "defector_factions": ()}


def attendance_for(db: sqlite3.Connection, vote_ids: list[str]) -> dict[str, dict]:
    """Attendance (votes cast / seats) for the given votes, keyed by vote id.

    Fetched for one page (or one bill's vote list) at a time, so the caller's own
    query stays a plain scan unless its *ordering* needs the aggregate. Both sides
    are summed from the per-faction breakdown — summing the real factions
    reproduces the upstream "Összesen" row exactly (verified across every vote
    that has one), while also covering the few votes whose breakdown lacks that
    pseudo-row. The numerator matches the profile pie's headline metric: "present
    but did not vote" and "excused absent" both count as non-attendance."""
    if not vote_ids:
        return {}
    ph = ",".join("?" * len(vote_ids))
    rows = db.execute(
        f"""SELECT vote_id, SUM(total) AS seats,
                   SUM(COALESCE(yes, 0) + COALESCE(no, 0) + COALESCE(abstain, 0)) AS present
            FROM vote_faction_stat
            WHERE vote_id IN ({ph}) AND faction_name NOT LIKE ?
            GROUP BY vote_id""", [*vote_ids, FACTION_TOTALS_LIKE]).fetchall()
    return {r["vote_id"]: {
        "present": r["present"], "seats": r["seats"],
        "attendance": (round(r["present"] / r["seats"], 4) if r["seats"] else None),
    } for r in rows}


def crossvoting_for(db: sqlite3.Connection, vote_ids: list[str]) -> dict[str, dict]:
    """Cross-voting (MPs who broke their faction's line) for the given votes.

    Fetched per page like the attendance above. Each entry carries the house-wide
    count, its share of the votes cast, and the factions that actually broke — so
    a card can name *which* groups split without a per-MP scan (and without
    claiming which members did, see DEFECTORS_SUM)."""
    if not vote_ids:
        return {}
    ph = ",".join("?" * len(vote_ids))
    rows = db.execute(
        f"""SELECT fs.vote_id, fs.faction_id, fs.faction_name, f.color AS faction_color,
                   CAST(COALESCE(fs.against_faction, '') AS INTEGER) AS defectors,
                   COALESCE(fs.yes, 0) + COALESCE(fs.no, 0) + COALESCE(fs.abstain, 0) AS cast_votes
            FROM vote_faction_stat fs
            JOIN vote cv_v ON cv_v.id = fs.vote_id
            LEFT JOIN faction f ON f.id = fs.faction_id
            WHERE fs.vote_id IN ({ph}) AND {CROSSVOTING_SCOPE}
            ORDER BY fs.ord""", vote_ids).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        e = out.setdefault(r["vote_id"], {"defectors": 0, "cast": 0, "factions": []})
        e["defectors"] += r["defectors"]
        e["cast"] += r["cast_votes"]
        if r["defectors"]:
            e["factions"].append({
                "faction_id": r["faction_id"], "name": r["faction_name"],
                "color": r["faction_color"], "defectors": r["defectors"],
            })
    return {vid: {
        "defectors": e["defectors"],
        "defector_share": (round(e["defectors"] / e["cast"], 4) if e["cast"] else None),
        # Largest breach first, so a card truncating the list keeps the headline.
        "defector_factions": sorted(e["factions"], key=lambda x: -x["defectors"]),
    } for vid, e in out.items()}
