"""Incremental per-item detail caching for the list+detail scraper stages.

The bills and votes scrapers each run a cheap **list** query and then an
expensive per-item **detail** bundle (≈9 sub-queries per bill, 3 per vote). A
nightly re-run of a whole cycle re-fetched all of that even when almost nothing
had changed, which made running the scraper *more often* (the goal here)
needlessly heavy on parlament.hu.

This helper lets a run **reuse the detail it already saved** for items whose
list row is unchanged since the previous run, so a frequent re-run only spends
network on the handful of new or changed items (SCR-2 incremental ingestion /
SCR-4 politeness). Correctness rests on a per-item *fingerprint* taken from the
cheap list row:

* a **vote** is immutable once recorded (its result and tallies never change),
  so its fingerprint is frozen and its detail is fetched exactly once — ever;
* a **bill** advances through legislative stages, so its fingerprint covers the
  status + stage diagram; any progression busts the cache and re-fetches detail,
  while a finished (or dormant) bill is skipped.

A full re-fetch is always available via ``force`` (SCR-2 full re-import), and a
missing/unreadable prior file simply degrades to fetching everything.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# A record's cache-relevant fingerprint, derived from its cheap list-row fields
# only (never from ``detail``, which is absent when a fresh row is checked).
Fingerprint = Callable[[dict], object]
# Fetch one item's detail bundle given its key. May raise; callers degrade.
FetchDetail = Callable[[object], dict]


def load_cache(path: Path, key: str, fingerprint: Fingerprint) -> dict:
    """Map ``key`` → ``(detail, fingerprint)`` from a previously-saved registry.

    Reads the prior ``<module>-<cycle>.json`` written by an earlier run. Records
    without a stored ``detail`` are skipped so they get re-fetched. Returns an
    empty cache (→ fetch everything) on the first run or an unreadable file."""
    if not path.exists():
        return {}
    try:
        prev = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        logger.warning("Detail cache %s unreadable (%s); will re-fetch all", path, e)
        return {}
    cache: dict = {}
    for rec in prev.get("data") or []:
        k = rec.get(key)
        if k is None or rec.get("detail") is None:
            continue
        cache[k] = (rec["detail"], fingerprint(rec))
    if cache:
        logger.info("Loaded %d cached detail records from %s", len(cache), path.name)
    return cache


def fill_details(records: list[dict], *, key: str, fingerprint: Fingerprint,
                 fetch: FetchDetail, cache: dict, force: bool = False,
                 label: str = "item") -> tuple[int, int]:
    """Populate ``rec["detail"]`` for every record, reusing the cache where the
    list-row fingerprint is unchanged.

    For each record: if (not ``force``) a cached detail exists with a matching
    fingerprint, reuse it; otherwise fetch fresh. A failed fetch degrades that
    record's detail to ``None`` and never aborts the run (SCR-5). Returns
    ``(fetched, reused)`` counts."""
    fetched = reused = 0
    total = len(records)
    for i, rec in enumerate(records, 1):
        k = rec.get(key)
        if not k:
            continue
        cached = None if force else cache.get(k)
        if cached is not None and cached[1] == fingerprint(rec):
            rec["detail"] = cached[0]
            reused += 1
            continue
        try:
            rec["detail"] = fetch(k)
        except Exception:  # one bad item must not abort the whole cycle (SCR-5)
            logger.exception("Detail fetch failed for %s %s", label, k)
            rec["detail"] = None
        fetched += 1
        logger.info("%s detail %d/%d fetched (%s)", label, i, total, k)
    logger.info("%s detail: %d fetched, %d reused from cache", label, fetched, reused)
    return fetched, reused
