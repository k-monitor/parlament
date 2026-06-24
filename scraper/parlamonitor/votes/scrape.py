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
from ..detail_cache import fill_details, load_cache
from ..felicitas import FelicitasClient

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _vote_fingerprint(rec: dict) -> tuple:
    """Cache key for a vote's detail. A roll-call vote is immutable once
    recorded — its result, tallies and per-MP records never change — so the
    fingerprint is frozen and each vote's detail is fetched exactly once."""
    return (rec.get("result"), rec.get("yes"), rec.get("no"),
            rec.get("abstain"), bool(rec.get("hasPerMp")))


def fetch_votes(felicitas: FelicitasClient, cycle: int, date_from: str,
                date_to: str, *, with_detail: bool = True, cache_path=None,
                force: bool = False) -> dict:
    """Build the votes registry for ``cycle`` over ``[date_from, date_to]``.

    When ``with_detail`` is set (the default) each vote is enriched with its
    per-MP roll call and per-faction breakdown under ``rec["detail"]``. A vote
    with no per-MP roll call (``hasPerMp`` false — e.g. an open/voice vote) still
    gets its header but an empty record list (SCR-5).

    ``cache_path`` (the previously-written ``votes-<cycle>.json``) enables
    **incremental** detail fetching: because a recorded vote never changes, only
    votes new since the last run get their detail fetched (SCR-2). ``force``
    re-fetches every detail regardless."""
    records = felicitas.votes(cycle, date_from, date_to)
    logger.info("Cycle %s votes: %d", cycle, len(records))
    # Most-recent first, like the portal's default ordering.
    records.sort(key=lambda r: r.get("datetime") or "", reverse=True)

    fetched = reused = 0
    if with_detail:
        cache = load_cache(cache_path, "voteId", _vote_fingerprint) if cache_path else {}
        fetched, reused = fill_details(
            records, key="voteId", fingerprint=_vote_fingerprint,
            fetch=felicitas.vote_detail, cache=cache, force=force, label="Vote")

    return {
        "meta": {
            "cycle": cycle,
            "dateFrom": date_from,
            "dateTo": date_to,
            "scrapedAt": _now_iso(),
            "source": "felicitas-szavazas-api",
            "count": len(records),
            "withDetail": with_detail,
            "detailFetched": fetched,
            "detailReused": reused,
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
