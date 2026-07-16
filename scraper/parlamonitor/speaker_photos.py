"""Portraits for speakers who are not in the MP roster (REP-2).

Ministers and nationality advocates (nemzetiségi szószólók) speak in plenary but
are **not** returned by the MP roster query, so the roster scrape never fetches
their portrait — their profile shows only a placeholder. This module finds those
non-roster speaker ids from the processed sessions and downloads whatever
portrait the Felicitas image resource has for each: a nationality advocate like
Gallai Gergely resolves to a real portrait; a portrait-less minister 404s and is
negative-cached (``representatives.scrape.fetch_missing_photos``). The loader
then wires the on-disk ``<pid>.jpg`` onto the person row (``wire_nonmp_photos``).
"""
from __future__ import annotations

import json
import logging

from .config import Paths
from .felicitas import FelicitasClient
from .representatives.scrape import fetch_missing_photos

logger = logging.getLogger(__name__)


def _roster_ids(paths: Paths) -> set[str]:
    """Every kepviseloId present in any cycle's MP roster (they get portraits the
    ordinary way, so they must never be treated as a non-roster speaker)."""
    ids: set[str] = set()
    for f in sorted(paths.processed.glob("representatives-*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for r in data.get("data", []):
            if r.get("personID"):
                ids.add(r["personID"])
    return ids


def _session_speaker_ids(paths: Paths, cycle: int | None = None) -> set[str]:
    """Every speaker id seen in the processed sessions (optionally one cycle)."""
    pattern = f"{cycle}*-session.json" if cycle is not None else "*-session.json"
    ids: set[str] = set()
    for f in sorted(paths.processed.glob(pattern)):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for sp in data.get("data", []):
            for p in sp.get("people", []):
                if p.get("personID"):
                    ids.add(p["personID"])
    return ids


def nonroster_speaker_ids(paths: Paths, cycle: int | None = None) -> set[str]:
    """Speaker ids that appear in the transcripts but not in any MP roster."""
    return _session_speaker_ids(paths, cycle) - _roster_ids(paths)


def fetch_nonroster_photos(felicitas: FelicitasClient, paths: Paths,
                           cycle: int | None = None) -> dict:
    """Download portraits for the non-roster speakers (optionally one cycle)."""
    ids = sorted(nonroster_speaker_ids(paths, cycle))
    result = fetch_missing_photos(felicitas, paths.photos, ids)
    result["candidates"] = len(ids)
    logger.info("non-roster speaker portraits: %s", result)
    return result
