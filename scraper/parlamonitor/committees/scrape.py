"""Scrape a cycle's **committees** (*bizottságok*) from the Felicitas API.

The committee registry of one electoral cycle, in the shape the site needs it:
every committee **body** (main committees and their subcommittees) with its type
and dates, who sat on it and in what role, every meeting it held with the minutes
PDF, and the irományok it dealt with and tabled.

Everything comes from parlament.hu's own committee pages — the same queries the
portal's *Bizottságok és albizottságaik*, *Bizottságok tagjai és tisztségviselői*,
*Bizottsági jegyzőkönyvek* and *Bizottságok által tárgyalt irományok* pages run
(``FelicitasClient.committee_*``). No page is scraped as HTML.

The registry also carries the meetings that are **scheduled but have not
happened** (``upcoming``) — the committee-side counterpart of the plenary's
napirend (NR-1). It is state, not history: each pass replaces it.

**Membership is fetched twice, from two different sources, and unioned.** The
roster query answers "who sits on this committee *today*" and the term query
"which seats started or ended during the cycle"; neither alone is the cycle's
membership. Over a closed cycle the term listing is complete (every seat ends
when the term does) but for the running cycle it holds only the churn so far; the
roster is the exact opposite — current, and blind to everyone who already left.
Taking both means a member is missed only if they joined *and* left inside the
running cycle without the registry recording it, which it does record.

Request budget per cycle: two roster + one term + one meeting + one meeting-stats
listing, then three small per-body requests (header sheet, documents dealt with,
documents tabled) — about 130 requests for a 40-body cycle, all politely throttled
(SCR-4). ``with_detail=False`` skips the per-body third of that for a fast refresh.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..config import Paths
from ..felicitas import FelicitasClient

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_previous(paths: Paths, cycle: int) -> dict | None:
    """The registry the last run wrote, for ``with_detail=False`` to carry
    forward (see :func:`fetch_committees`)."""
    f = paths.committees_file(cycle)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Could not read %s (%s) — treating as a first run", f, e)
        return None


# What only the per-body requests can supply. Named once so the skip path and
# the fetch path cannot drift apart.
_DETAIL_FIELDS = ("type", "email", "active", "siteUrl")


def fetch_committees(felicitas: FelicitasClient, cycle: int, date_from: str,
                     date_to: str, *, with_detail: bool = True,
                     as_of: str | None = None,
                     previous: dict | None = None) -> dict:
    """Build the committee registry for ``cycle`` over ``[date_from, date_to]``.

    ``as_of`` is the date the membership snapshot is taken on (default: the end
    of the range — for a closed cycle its last day, for the running one today).

    ``with_detail`` controls the per-body requests: the header sheet (which is
    where a committee's *type* comes from) and its two iromány listings. With
    them skipped, whatever the **previous** registry knew is carried forward
    rather than written out as empty — the loader replaces a cycle wholesale
    (ING-4), so a registry that merely *omits* the detail is indistinguishable
    from one asserting there is none, and a single ``--no-detail`` pass would
    blank every committee's type and delete every document row for the cycle.
    Bills and votes avoid this with a per-item detail cache; committees have
    nothing upstream to key one on, so the whole previous answer is the cache.
    """
    point = as_of or date_to
    bodies = felicitas.committee_bodies(cycle, date_from, date_to)
    logger.info("Cycle %s committees: %d bodies (%d subcommittees)", cycle,
                len(bodies), sum(1 for b in bodies if b["isSubcommittee"]))

    members = felicitas.committee_members(cycle, date_from, date_to, point)
    terms = felicitas.committee_terms(cycle, date_from, date_to)
    meetings = felicitas.committee_meetings(cycle, date_from, date_to)
    stats = {s["committeeId"]: s
             for s in felicitas.committee_meeting_stats(cycle, date_from, date_to)}
    logger.info("Cycle %s committees: %d seats, %d terms, %d meetings",
                cycle, len(members), len(terms), len(meetings))

    documents: list[dict] = []
    submissions: list[dict] = []
    if with_detail:
        for i, body in enumerate(bodies, 1):
            cid = body["committeeId"]
            sheet = felicitas.committee_sheet(cid) or {}
            body.update({"type": sheet.get("type"), "email": sheet.get("email"),
                         "active": sheet.get("active")})
            # The portal publishes a homepage only for a main committee, and only
            # when it says so: linking `/web/guest/<cycle>-<code>` for a body that
            # has none would be a dead link on every row (LEGAL-1 — we link only
            # what we have seen resolve).
            body["siteUrl"] = (_site_url(body) if sheet.get("hasSite") else None)
            documents.extend(
                felicitas.committee_documents(cycle, date_from, date_to, cid))
            submissions.extend(
                felicitas.committee_submissions(cycle, date_from, date_to, cid))
            if i % 10 == 0:
                logger.info("  …%d/%d bodies detailed", i, len(bodies))
        logger.info("Cycle %s committees: %d documents dealt with, %d tabled",
                    cycle, len(documents), len(submissions))
    else:
        documents, submissions = _carry_detail(bodies, previous)

    for body in bodies:
        body["meetingStats"] = stats.get(body["committeeId"])

    # What the committees are ABOUT to do — the one committee-side answer to
    # that question, as the napirend is for the plenary (NR-1). Like it there is
    # no history to keep: the listing is only ever the current schedule, so each
    # pass replaces it wholesale. Only asked for the cycle that is running; a
    # closed cycle has nothing upcoming and upstream answers empty anyway.
    upcoming = felicitas.committee_upcoming(cycle, date_from, date_to)
    if upcoming:
        logger.info("Cycle %s committees: %d scheduled meeting(s) ahead",
                    cycle, len(upcoming))

    return {
        "meta": {
            "cycle": cycle,
            "dateFrom": date_from,
            "dateTo": date_to,
            "membersAsOf": point,
            "scrapedAt": _now_iso(),
            "source": "felicitas-bizottsag-api",
            "count": len(bodies),
            "withDetail": with_detail,
            # Whether the detail in this file was fetched now or carried over,
            # so a reader of the file can tell "not fetched this pass" from
            # "upstream has none".
            "detailCarried": bool(not with_detail and previous),
            "counts": {
                "bodies": len(bodies), "members": len(members),
                "terms": len(terms), "meetings": len(meetings),
                "documents": len(documents), "submissions": len(submissions),
                "upcoming": len(upcoming),
            },
        },
        "data": bodies,
        "members": members,
        "terms": terms,
        "meetings": meetings,
        "documents": documents,
        "submissions": submissions,
        "upcoming": upcoming,
    }


def _carry_detail(bodies: list[dict], previous: dict | None) -> tuple[list, list]:
    """Re-attach the previous run's per-body detail to a ``--no-detail`` pass.

    A body upstream has since added is simply left without detail — that is a
    genuine "not fetched yet", and the next full pass fills it. A body that has
    *gone* takes its documents with it, which is why the lists are filtered
    rather than copied whole: a stale row would point at a committee the
    registry no longer holds, and the loader would drop it on the FK anyway.
    """
    if not previous:
        return [], []
    prior = {b.get("committeeId"): b for b in (previous.get("data") or [])}
    for body in bodies:
        old = prior.get(body["committeeId"])
        if old:
            body.update({k: old.get(k) for k in _DETAIL_FIELDS})
    held = {b["committeeId"] for b in bodies}
    keep = lambda rows: [r for r in rows or [] if r.get("committeeId") in held]
    documents = keep(previous.get("documents"))
    submissions = keep(previous.get("submissions"))
    logger.info("Detail skipped: carried %d document(s) and %d submission(s) "
                "forward from the previous registry",
                len(documents), len(submissions))
    return documents, submissions


def _site_url(body: dict) -> str | None:
    """The committee's own homepage on the portal, or ``None`` for a body that
    has no code to build one from (every subcommittee)."""
    from ..felicitas import COMMITTEE_SITE_BASE
    code, cycle = body.get("code"), body.get("cycle")
    if not code or cycle is None or body.get("isSubcommittee"):
        return None
    return f"{COMMITTEE_SITE_BASE}/{cycle}-{code}"


def save_committees(paths: Paths, cycle: int, registry: dict) -> None:
    out = paths.committees_file(cycle)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(registry, indent=2, ensure_ascii=False))
    tmp.replace(out)
    logger.info("Wrote %s (%d committees)", out, registry["meta"]["count"])
