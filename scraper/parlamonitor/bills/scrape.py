"""Scrape the cycle's irományok (parliamentary documents) from the Felicitas
``iromany`` API.

A browsable list of a cycle's irományok — törvényjavaslatok (bills) **and every
other type** (határozati javaslatok, interpellációk, kérdések, beszámolók, …) —
with their number, title, type, status, submission date, text PDF link and
submitters, **plus per-document detail**: the legislative event history,
committee events, votes, deadlines, negotiating committees,
justification/background documents and the non-self-standing motion summary.
The submitters carry the ``personID`` that joins straight to an MP profile
(EXT-2) — no name matching needed.

One paged list query yields every document (each tagged with its ``mainType``
from the iromány-number prefix); each then gets ~9 small detail sub-queries
(``bill_detail``), all politely throttled (SCR-4). Detail fetching can be
skipped (``with_detail=False``) for a fast list-only refresh.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..config import Paths
from ..detail_cache import fill_details, load_cache
from ..felicitas import FelicitasClient

logger = logging.getLogger(__name__)

# Felicitas ``fotipus`` codes (the iromány-number prefix): "T" =
# törvényjavaslat (bills), "H" = határozati javaslat, "I" = interpelláció,
# "K" = kérdés, "A" = azonnali kérdés, "B" = beszámoló/jelentés, etc. The
# default (``None``) fetches **every** type in one query; pass a tuple to
# restrict to specific prefixes.
DEFAULT_MAIN_TYPES = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _bill_fingerprint(rec: dict) -> tuple:
    """Cache key for a bill's detail, taken from its cheap list row.

    A bill's detail only changes as the document advances, which is reflected in
    its status and the legislative-stage diagram; so once those are frozen
    (finished or dormant bill) the cached detail is reused, while any progression
    busts the cache and re-fetches the adatlap."""
    return (
        rec.get("status"),
        tuple((s.get("key"), s.get("done")) for s in (rec.get("stages") or [])),
        rec.get("submittedDate"),
        len(rec.get("sponsors") or []),
    )


def fetch_bills(felicitas: FelicitasClient, cycle: int, *,
                main_types: tuple[str, ...] | None = DEFAULT_MAIN_TYPES,
                with_detail: bool = True, cache_path=None,
                force: bool = False) -> dict:
    """Build the irományok registry for ``cycle``.

    With ``main_types`` ``None`` (the default) **all** iromány types are fetched
    in one query; pass a tuple of ``fotipus`` prefixes to restrict it. When
    ``with_detail`` is set (the default) each document is enriched with its full
    ``adatlap`` detail (events, votes, committees, deadlines, documents, motion
    summary) under ``rec["detail"]``.

    ``cache_path`` (the previously-written ``bills-<cycle>.json``) enables
    **incremental** detail fetching: a bill whose status/stage diagram is
    unchanged reuses its cached detail, so a frequent re-run only fetches new or
    advanced bills (SCR-2). ``force`` re-fetches every detail regardless."""
    records: list[dict] = []
    if main_types:
        for mt in main_types:
            chunk = felicitas.bills(cycle, main_type=mt)
            logger.info("Cycle %s irományok (fotipus=%s): %d", cycle, mt, len(chunk))
            records.extend(chunk)
    else:
        records = felicitas.bills(cycle)  # every type in one query
        logger.info("Cycle %s irományok (all types): %d", cycle, len(records))
    # Most-recent first, like the portal's default ordering.
    records.sort(key=lambda r: r.get("billNumberSort") or 0, reverse=True)

    fetched = reused = 0
    if with_detail:
        cache = load_cache(cache_path, "billId", _bill_fingerprint) if cache_path else {}
        fetched, reused = fill_details(
            records, key="billId", fingerprint=_bill_fingerprint,
            fetch=felicitas.bill_detail, cache=cache, force=force, label="Bill")

    return {
        "meta": {
            "cycle": cycle,
            "mainTypes": list(main_types) if main_types else "all",
            "scrapedAt": _now_iso(),
            "source": "felicitas-iromany-api",
            "count": len(records),
            "withDetail": with_detail,
            "detailFetched": fetched,
            "detailReused": reused,
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
