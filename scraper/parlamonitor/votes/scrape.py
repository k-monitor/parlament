"""Scrape the cycle's roll-call votes from the Felicitas ``szavazas`` API.

A browsable list of a cycle's votes with their datetime, voting mode, subject,
result and igen/nem/tartózkodás tallies, each linked to the bill(s) it decided,
**plus per-vote detail**: the per-MP roll call (every representative's individual
vote) and the per-faction breakdown. The per-MP records carry the ``personID``
that joins straight to an MP profile (EXT-2), and the vote subjects carry the
``billId`` that joins to a bill — no name matching needed.

One paged list query yields the votes; each vote then gets three small detail
sub-queries (``vote_detail``), all politely throttled (SCR-4). Detail fetching
can be skipped (``with_detail=False``) for a fast list-only refresh.
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


def fetch_votes(felicitas: FelicitasClient, cycle: int, date_from: str,
                date_to: str, *, with_detail: bool = True) -> dict:
    """Build the votes registry for ``cycle`` over ``[date_from, date_to]``.

    When ``with_detail`` is set (the default) each vote is enriched with its
    per-MP roll call and per-faction breakdown under ``rec["detail"]``. A vote
    with no per-MP roll call (``hasPerMp`` false — e.g. an open/voice vote) still
    gets its header but an empty record list (SCR-5)."""
    records = felicitas.votes(cycle, date_from, date_to)
    logger.info("Cycle %s votes: %d", cycle, len(records))
    # Most-recent first, like the portal's default ordering.
    records.sort(key=lambda r: r.get("datetime") or "", reverse=True)

    if with_detail:
        for i, rec in enumerate(records, 1):
            vid = rec.get("voteId")
            if not vid:
                continue
            try:
                rec["detail"] = felicitas.vote_detail(vid)
            except Exception:  # one bad vote must not abort the whole cycle (SCR-5)
                logger.exception("Detail fetch failed for vote %s", vid)
                rec["detail"] = None
            logger.info("Vote detail %d/%d", i, len(records))

    return {
        "meta": {
            "cycle": cycle,
            "dateFrom": date_from,
            "dateTo": date_to,
            "scrapedAt": _now_iso(),
            "source": "felicitas-szavazas-api",
            "count": len(records),
            "withDetail": with_detail,
        },
        "data": records,
    }


def save_votes(paths: Paths, cycle: int, registry: dict) -> None:
    out = paths.votes_file(cycle)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(registry, indent=2, ensure_ascii=False))
    tmp.replace(out)
    logger.info("Wrote %s (%d votes)", out, registry["meta"]["count"])
