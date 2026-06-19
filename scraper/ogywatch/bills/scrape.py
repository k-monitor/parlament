"""Scrape the cycle's bills from the Felicitas ``iromany`` API.

Basic support (the acceptance probe in requirements §7): a browsable list of a
cycle's bills with their number, title, type, status, submission date, text PDF
link and submitters. The submitters carry the ``personID`` that joins straight
to an MP profile (EXT-2) — no name matching needed.

A single paged query feeds the whole module; there is no per-bill detail fetch
in v1, so this stage is fast and polite (SCR-4).
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
                main_types: tuple[str, ...] = DEFAULT_MAIN_TYPES) -> dict:
    """Build the bills registry for ``cycle`` across ``main_types``."""
    records: list[dict] = []
    for mt in main_types:
        chunk = felicitas.bills(cycle, main_type=mt)
        logger.info("Cycle %s bills (fotipus=%s): %d", cycle, mt, len(chunk))
        records.extend(chunk)
    # Most-recent first, like the portal's default ordering.
    records.sort(key=lambda r: r.get("billNumberSort") or 0, reverse=True)
    return {
        "meta": {
            "cycle": cycle,
            "mainTypes": list(main_types),
            "scrapedAt": _now_iso(),
            "source": "felicitas-iromany-api",
            "count": len(records),
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
