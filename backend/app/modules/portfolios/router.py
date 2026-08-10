"""Portfolios module API (§6C / EXT-1) — /api/v1/portfolios.

The government side of the corpus: which tárca answered which questions, which
one the government submitted an iromány through, and which speeches its minister
and state secretaries gave. It owns **no source of its own** — every row it reads
is one another module already wrote (`bill`, `bill_event`, `bill_sponsor`,
`speech`, `person_office`), joined through the `portfolio*` tables the loader
derives from them (EXT-2). Disabling the module removes these routes and the
portfolio filter on the bills list; nothing else notices (EXT-6).

The iromány lists themselves are **not** served here: `/api/v1/bills` already
lists irományok with sponsors, responders, filters and paging, and it gained a
`portfolio` filter (MIN-7), so a profile's two document panels are that endpoint
scoped to this tárca. One list implementation, one row shape, no duplication.
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ...analytics import search_analytics
from ...db import (get_db, like_contains, period_bounds, period_key, period_list,
                   period_sql)
from ...query_cache import cached_aggregate

router = APIRouter(prefix="/portfolios", tags=["portfolios"])

# The cycles whose speeches carry `speaker_office` at all. A tárca's speech panel
# is empty for every other cycle not because its ministers were silent but
# because the field was added to the scraper later and those cycles have not been
# re-scraped (MIN-10) — so the API reports the coverage and the UI says so,
# rather than letting an empty list read as silence.
_SPEECH_COVERAGE_SQL = """
    SELECT DISTINCT s.period_number AS p FROM speech s
    WHERE s.speaker_office IS NOT NULL AND s.speaker_office <> ''
"""


def _tables_present(db: sqlite3.Connection) -> bool:
    """Whether the loader has built the §6C tables. A DB loaded before this
    module existed simply has no portfolios — the endpoints answer empty (and the
    frontend hides the tab) instead of erroring (EXT-6, SCR-5)."""
    return bool(db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='portfolio'"
    ).fetchone())


def _require_tables(db: sqlite3.Connection) -> None:
    if not _tables_present(db):
        raise HTTPException(
            status_code=503,
            detail="Portfolio tables not built yet; run the loader (or "
                   "migrate_portfolios.py) to derive them.")


def _speech_coverage(db: sqlite3.Connection) -> list[int]:
    return sorted(r["p"] for r in db.execute(_SPEECH_COVERAGE_SQL)
                  if r["p"] is not None)


def _counts(db: sqlite3.Connection, period: Optional[List[int]]) -> dict[str, dict]:
    """Per-tárca counts within the cycle scope: irományok answered, irományok
    submitted, plenary speeches. One query per link kind for the whole listing —
    the tables are small (a hundred portfolios over ~57 000 links), so this is a
    grouped scan, not a per-row lookup."""
    out: dict[str, dict] = {}

    def bump(slug: str, key: str, n: int) -> None:
        out.setdefault(slug, {"answered": 0, "submitted": 0, "speeches": 0})[key] = n

    # Scoped by the **link's** own cycle, not the parent iromány's: parlament.hu
    # re-lists an iromány still in progress under the new cycle, so a bill the
    # government submitted in 2024 comes back as a cycle-43 row. Counted by the
    # parent, the Építési és Közlekedési Minisztérium — abolished in 2026 — turned
    # up in the 2026 listing on the strength of one 2024 document.
    per_link = period_sql(period, "pb.period_number")
    for role in ("answered", "submitted"):
        sql = ("SELECT pb.portfolio_slug AS slug, COUNT(*) AS c "
               "FROM portfolio_bill pb WHERE pb.role = ?")
        if per_link:
            sql += f" AND {per_link}"
        for r in db.execute(sql + " GROUP BY pb.portfolio_slug", (role,)):
            bump(r["slug"], role, r["c"])

    per_speech = period_sql(period, "s.period_number")
    sql = ("SELECT ps.portfolio_slug AS slug, COUNT(*) AS c "
           "FROM portfolio_speech ps JOIN speech s ON s.uid = ps.speech_uid "
           "WHERE s.procedural = 0")
    if per_speech:
        sql += f" AND {per_speech}"
    for r in db.execute(sql + " GROUP BY ps.portfolio_slug"):
        bump(r["slug"], "speeches", r["c"])
    return out


def _holders(db: sqlite3.Connection, period: Optional[List[int]],
             slugs: Optional[list[str]] = None) -> dict[str, list]:
    """Who held each tárca's offices within the cycle scope, newest first.

    **Not the plain overlap the office-holder listing uses (REP-11).** An
    outgoing government stays in office until the new one is sworn in — a week or
    two into the new cycle — so every one of its ministers and state secretaries
    overlaps the cycle that replaced them, and the interior ministry's panel for
    2026 opened with the 2022 government's whole bench above the people actually
    running it. A term therefore belongs to a cycle when it **began in it**.

    The exception is a term that is **still open**, which is kept whenever it
    started before the scope ended: the offices that answer to the House but are
    deliberately not synchronised with it — the MNB's governor and deputies, the
    ombudsman, the Állami Számvevőszék, the prosecutor general — run six to nine
    years across cycle boundaries, and a start-date-only rule would empty their
    panels of the very people currently holding them.

    An office still held keeps an open end — the last day of the data is never
    presented as a departure (REP-2)."""
    bounds = period_bounds(db, period_list(period))
    if bounds is None:                    # cycles this DB cannot date: say nothing
        return {}
    start, end = bounds
    where, params = ["1=1"], []
    if start:
        where.append("(po.date_start >= ? OR po.date_end IS NULL)")
        params.append(start)
    if end:
        where.append("(po.date_start IS NULL OR po.date_start <= ?)")
        params.append(end)
    if slugs:
        where.append("po.portfolio_slug IN (" + ",".join("?" * len(slugs)) + ")")
        params.extend(slugs)
    rows = db.execute(
        f"""SELECT po.portfolio_slug AS slug, po.person_id, po.title, po.category,
                   po.date_start, po.date_end, p.label AS name
            FROM portfolio_office po
            JOIN person p ON p.person_id = po.person_id
            WHERE {' AND '.join(where)}
            ORDER BY po.portfolio_slug,
                     CASE po.category WHEN 'pm' THEN 0 WHEN 'minister' THEN 1
                                      WHEN 'state-secretary' THEN 2 ELSE 3 END,
                     po.date_start DESC""", params).fetchall()
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r["slug"], []).append({
            "person_id": r["person_id"], "name": r["name"], "title": r["title"],
            "category": r["category"],
            "date_start": r["date_start"], "date_end": r["date_end"],
        })
    return out


@router.get("")
def list_portfolios(
    q: Optional[str] = None,
    kind: Optional[str] = None,
    period: Optional[List[int]] = Query(
        None, description="Electoral period number(s); repeat to scope to several cycles"),
    db: sqlite3.Connection = Depends(get_db),
):
    """The tárca listing (MIN-5), scoped to the selected cycles.

    Not paginated: there are under a hundred portfolios in the whole corpus, and
    the page groups them by kind — ministries first, the independent bodies that
    answer to the House last — so a reader sees the whole government at once. A
    tárca with no activity at all in scope is left out: it says nothing about the
    cycle being viewed. `q` matches the name accent-insensitively (§4B FOLD-1).
    """
    if not _tables_present(db):
        return {"portfolios": [], "kinds": [], "speech_coverage": [], "built": False}

    counts = _counts(db, period)
    where, params = ["1=1"], {}
    if q:
        where.append("fold(name) LIKE fold(:q) ESCAPE '\\'")
        params["q"] = like_contains(q.strip())
    if kind:
        where.append("kind = :kind")
        params["kind"] = kind
    rows = db.execute(
        f"SELECT slug, name, kind, ord FROM portfolio WHERE {' AND '.join(where)} "
        "ORDER BY ord", params).fetchall()

    listed = [r for r in rows if any(counts.get(r["slug"], {}).values())]
    holders = _holders(db, period, [r["slug"] for r in listed])
    out = []
    for r in listed:
        c = counts.get(r["slug"], {})
        # The card names the tárca's most senior current office-holder — a
        # minister over a state secretary — since that is who a reader recognises.
        who = holders.get(r["slug"]) or []
        out.append({
            "slug": r["slug"], "name": r["name"], "kind": r["kind"],
            "answered": c.get("answered", 0), "submitted": c.get("submitted", 0),
            "speeches": c.get("speeches", 0),
            "holders": who[:3], "holder_count": len(who),
        })
    # Privacy-respecting analytics (PRIV-2): the typed keyword + the filters it
    # was combined with. A no-keyword browse of the list records nothing. The
    # listing is not paginated, so there is no page depth to record.
    search_analytics.record(source="portfolios", query=q, period=period,
                            kind=kind, results=len(out))

    return {
        "portfolios": out,
        "kinds": [k for k in ("ministry", "pm", "no-portfolio", "other", "body")
                  if any(p["kind"] == k for p in out)],
        "speech_coverage": _speech_coverage(db),
        "built": True,
    }


# A written question (`írásbeli kérdés`): main_type K whose iromány type says the
# answer was given in writing rather than from the floor — the same split the
# Kérdések Sankey draws as its own `W` category (§6A). Folded on both sides so the
# accented `í` matches however upstream cased it (FOLD-3).
_WRITTEN_QUESTION_SQL = "b.main_type = 'K' AND fold(b.type) LIKE fold('%írásbeli%')"


def _response_days(db: sqlite3.Connection, slug: str,
                   period: Optional[List[int]]) -> Optional[dict]:
    """How long the tárca took to answer a **written question**, in days: the
    median and the count it is computed over (MIN-8).

    Only written questions count. Their span is the one thing the ministry itself
    controls: the clock starts when the question is submitted and stops when the
    tárca sends its reply. An interpelláció or an azonnali kérdés is answered from
    the floor, so its answer date is set by when the House next sat — measured the
    same way it would report the sitting calendar, not the ministry, and a
    plenary-heavy remit would look slower than a written-question one for no
    reason a reader could act on. Mixing the two into one median measures neither.

    Only questions whose **outcome is on record** count — which in this version is
    all of them, since a question enters this table by being answered (MIN-10a):
    the unanswered ones arrive with the addressee (MIN-4). An **answer rate** is
    therefore deliberately not reported: computed over answered questions alone it
    would be 100% by construction, which is not a fact about the ministry.
    """
    per = period_sql(period, "pb.period_number")
    rows = db.execute(
        f"""SELECT julianday(substr(pb.event_date, 1, 10))
                   - julianday(substr(b.submitted_date, 1, 10)) AS days
            FROM portfolio_bill pb JOIN bill b ON b.id = pb.bill_id
            WHERE pb.portfolio_slug = ? AND pb.role = 'answered'
              AND {_WRITTEN_QUESTION_SQL}
              AND pb.event_date IS NOT NULL AND b.submitted_date IS NOT NULL
              {('AND ' + per) if per else ''}""", (slug,)).fetchall()
    # A negative span means the two dates disagree about which came first — an
    # upstream data slip, not a ministry that answered before it was asked.
    days = sorted(r["days"] for r in rows if r["days"] is not None and r["days"] >= 0)
    if not days:
        return None
    mid = len(days) // 2
    median = days[mid] if len(days) % 2 else (days[mid - 1] + days[mid]) / 2
    return {"median_days": round(median, 1), "n": len(days)}


@router.get("/{slug}")
def portfolio_detail(
    slug: str,
    period: Optional[List[int]] = Query(None),
    db: sqlite3.Connection = Depends(get_db),
):
    """One tárca's profile (MIN-6): who held it, what it answered and submitted,
    how much it spoke, and how long it took to answer.

    The iromány lists themselves come from `/api/v1/bills?portfolio=<slug>` — the
    same list endpoint, filters and row shape the rest of the site uses (MIN-7).
    """
    _require_tables(db)
    row = db.execute("SELECT slug, name, kind FROM portfolio WHERE slug = ?",
                     (slug,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    counts = _counts(db, period).get(slug, {})
    coverage = _speech_coverage(db)
    scope = period_list(period)
    return {
        "slug": row["slug"], "name": row["name"], "kind": row["kind"],
        "answered": counts.get("answered", 0),
        "submitted": counts.get("submitted", 0),
        "speeches": counts.get("speeches", 0),
        "holders": _holders(db, period, [slug]).get(slug, []),
        "response_time": _response_days(db, slug, period),
        # Every label the corpus uses for this tárca, so the page can show what it
        # was collated from and a reader can check the grouping (TRUST-1).
        "aliases": [r["label"] for r in db.execute(
            "SELECT label FROM portfolio_alias WHERE portfolio_slug = ? ORDER BY label",
            (slug,))],
        # Which of the cycles in scope can carry speeches at all (MIN-10).
        "speech_coverage": coverage,
        "speech_coverage_partial": bool(
            [p for p in (scope or coverage) if p not in coverage]),
    }


@router.get("/{slug}/trend")
def portfolio_trend(
    slug: str,
    period: Optional[List[int]] = Query(None),
    db: sqlite3.Connection = Depends(get_db),
):
    """Questions answered by this tárca per calendar year — the profile's
    over-time chart (MIN-6). A separate aggregate from the profile so it never
    slows it (cf. WCLOUD-5/SEA-8), and cached per (slug, scope).

    Shaped as the search trend's ``{period, hits}`` buckets so the site's one
    histogram component renders it unchanged (SEA-8)."""
    _require_tables(db)

    def compute():
        per = period_sql(period, "pb.period_number")
        rows = db.execute(
            f"""SELECT substr(pb.event_date, 1, 4) AS y, COUNT(*) AS c
                FROM portfolio_bill pb
                WHERE pb.portfolio_slug = ? AND pb.role = 'answered'
                  AND pb.event_date IS NOT NULL {('AND ' + per) if per else ''}
                GROUP BY y ORDER BY y""", (slug,)).fetchall()
        buckets = {r["y"]: r["c"] for r in rows if r["y"]}
        if not buckets:
            return {"buckets": [], "granularity": "year"}
        # Quiet years render as zero, not as gaps (SEA-8's rule); the component
        # fills the gaps itself, but only within the range it is given.
        years = range(int(min(buckets)), int(max(buckets)) + 1)
        return {"granularity": "year",
                "buckets": [{"period": str(y), "hits": buckets.get(str(y), 0)}
                            for y in years]}

    return cached_aggregate("portfolio_trend", (slug, period_key(period)), compute)


@router.get("/{slug}/speeches")
def portfolio_speeches(
    slug: str,
    period: Optional[List[int]] = Query(None),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
):
    """The plenary speeches given in this tárca's offices, newest first (MIN-6).

    Procedural/chairing speeches are excluded as everywhere else (STAT-1), and
    each row carries the office the speaker actually spoke in — someone promoted
    mid-cycle is shown in the post they held then, not the one they hold now."""
    _require_tables(db)
    per = period_sql(period, "s.period_number")
    where = "ps.portfolio_slug = ? AND s.procedural = 0" + ((" AND " + per) if per else "")
    total = db.execute(
        f"""SELECT COUNT(*) AS c FROM portfolio_speech ps
            JOIN speech s ON s.uid = ps.speech_uid WHERE {where}""", (slug,)).fetchone()["c"]
    rows = db.execute(
        f"""SELECT s.uid, s.speaker_label, s.speaker_office, s.person_id, s.duration,
                   s.felszolalas_tipus, s.has_text, ss.date, ss.id AS session_id,
                   p.label AS person_name, ai.title AS agenda_title,
                   f.label AS faction_label, f.color AS faction_color
            FROM portfolio_speech ps
            JOIN speech s ON s.uid = ps.speech_uid
            JOIN session ss ON ss.id = s.session_id
            LEFT JOIN person p ON p.person_id = s.person_id
            LEFT JOIN agenda_item ai ON ai.id = s.agenda_item_id
            LEFT JOIN faction f ON f.id = s.faction_id
            WHERE {where}
            ORDER BY ss.date DESC, s.speech_index DESC
            LIMIT ? OFFSET ?""", (slug, limit, offset)).fetchall()
    return {
        "total": total, "limit": limit, "offset": offset,
        "speeches": [{
            "uid": r["uid"], "date": r["date"], "session_id": r["session_id"],
            "person_id": r["person_id"],
            "name": r["person_name"] or r["speaker_label"],
            "office": r["speaker_office"],
            "agenda_title": r["agenda_title"],
            "type": r["felszolalas_tipus"],
            "duration": r["duration"], "has_text": bool(r["has_text"]),
            "faction": ({"label": r["faction_label"], "color": r["faction_color"]}
                        if r["faction_label"] else None),
        } for r in rows],
    }
