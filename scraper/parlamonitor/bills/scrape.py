"""Scrape the cycle's bills from the Felicitas ``iromany`` API.

A browsable list of a cycle's bills with their number, title, type, status,
submission date, text PDF link and submitters, **plus per-bill detail**: the
legislative event history, committee events, votes, deadlines, negotiating
committees, justification/background documents and the non-self-standing motion
summary. The submitters carry the ``personID`` that joins straight to an MP
profile (EXT-2) — no name matching needed.

One paged list query yields the bills; each bill then gets ~9 small detail
sub-queries (``bill_detail``), all politely throttled (SCR-4). Detail fetching
can be skipped (``with_detail=False``) for a fast list-only refresh.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..config import Paths
from ..felicitas import FelicitasClient

logger = logging.getLogger(__name__)

# Felicitas ``fotipus`` codes worth scraping for basic support. "T" =
# törvényjavaslat (law proposals); "H" = határozati javaslat (resolution
# proposals). Default to "T" — the bills people mean by "törvényjavaslat".
DEFAULT_MAIN_TYPES = ("T",)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_bills(felicitas: FelicitasClient, cycle: int, *,
                main_types: tuple[str, ...] = DEFAULT_MAIN_TYPES,
                with_detail: bool = True) -> dict:
    """Build the bills registry for ``cycle`` across ``main_types``.

    When ``with_detail`` is set (the default) each bill is enriched with its
    full ``adatlap`` detail (events, votes, committees, deadlines, documents,
    motion summary) under ``rec["detail"]``."""
    records: list[dict] = []
    for mt in main_types:
        chunk = felicitas.bills(cycle, main_type=mt)
        logger.info("Cycle %s bills (fotipus=%s): %d", cycle, mt, len(chunk))
        records.extend(chunk)
    # Most-recent first, like the portal's default ordering.
    records.sort(key=lambda r: r.get("billNumberSort") or 0, reverse=True)

    if with_detail:
        for i, rec in enumerate(records, 1):
            bid = rec.get("billId")
            if not bid:
                continue
            try:
                rec["detail"] = felicitas.bill_detail(bid)
            except Exception:  # one bad bill must not abort the whole cycle (SCR-5)
                logger.exception("Detail fetch failed for bill %s (%s)",
                                 rec.get("billNumber"), bid)
                rec["detail"] = None
            logger.info("Bill detail %d/%d (%s)", i, len(records),
                        rec.get("billNumber"))

    return {
        "meta": {
            "cycle": cycle,
            "mainTypes": list(main_types),
            "scrapedAt": _now_iso(),
            "source": "felicitas-iromany-api",
            "count": len(records),
            "withDetail": with_detail,
        },
        "data": records,
    }


def save_bills(paths: Paths, cycle: int, registry: dict) -> None:
    out = paths.bills_file(cycle)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(registry, indent=2, ensure_ascii=False))
    tmp.replace(out)
    logger.info("Wrote %s (%d bills)", out, registry["meta"]["count"])
