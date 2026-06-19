"""Fetch one sitting day's raw proceedings bundle from the Felicitas JSON API.

This is the **download stage**: it talks to parlament.hu and writes a single
source-faithful ``raw-<session>-day.json`` per sitting, doing no interpretation
beyond stitching the three plenary queries together. Parsing/segmentation/timing
all happen later (``transform.py``), so the network step and the build step can
be run and tested independently (requirements ING-1).

Per day it collects, for every speech:

* the agenda act it belongs to, its join number, type, committee and any bill
  references (from ``ulesnapok-aktusok-query``);
* the speaker, the linking ``person_id`` (``kepviseloId``), and the full speech
  text as HTML (from ``ulesnap-felszolalas-adata-query``);
* the whole-day HLS recording URL + duration (from ``ulesnapok-video-query``);
* best-effort real per-speech day-stream offsets — recorded for provenance and
  the future precise-timing stage, **not** used by v1 timing (TIM-1 / §10).

Idempotent: a sitting whose raw file already exists is skipped unless it is the
most recent (still-live) sitting or ``force`` is set (SCR-1 / SCR-2).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from ..config import Paths, session_id
from ..felicitas import FelicitasClient

logger = logging.getLogger(__name__)

# The cycle-wide ülésnap ordinal is the parenthesised number in datumFelirat,
# e.g. "2026.06.09.(7)" → sitting 7 (matches the speech caption "7. ülésnap").
_FELIRAT_NUM_RE = re.compile(r"\((\d+)\)\s*$")


def sitting_number(day: dict) -> int | None:
    m = _FELIRAT_NUM_RE.search(day.get("datum_felirat") or "")
    if m:
        return int(m.group(1))
    return day.get("day_in_session")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def scrape_day(felicitas: FelicitasClient, cycle: int, day: dict, *,
               resolve_offsets: bool = True) -> dict | None:
    """Build the raw bundle for one session day. ``None`` if it has no speeches."""
    day_uuid = day["uuid"]
    speeches = felicitas.day_speeches(day_uuid)
    if not speeches:
        return None

    video = felicitas.day_video(day_uuid)
    day_off1 = (video or {}).get("day_off1")

    enriched: list[dict] = []
    for sp in speeches:
        uuid = sp.get("speech_uuid")
        detail = felicitas.speech_text(uuid) if uuid else None
        if detail:
            # The detail query is authoritative for text + a few fields the
            # listing leaves blank (role, committee name).
            sp = {**sp,
                  "text_html": detail.get("html", ""),
                  "caption": detail.get("caption"),
                  "role": detail.get("role"),
                  "committee": detail.get("committee"),
                  # The detail query gives a clean type string; the listing's is
                  # a nested table we may have failed to resolve.
                  "type": detail.get("type") or sp.get("type"),
                  "duration": sp.get("duration") or detail.get("duration")}
        else:
            sp = {**sp, "text_html": ""}

        # Real per-speech offsets — provenance only for v1 (kept for §10 swap).
        if resolve_offsets and uuid and day_off1 is not None:
            offs = felicitas.speech_offsets(uuid)
            if offs and offs[1] > offs[0]:
                sp["video_off_start"] = round(offs[0] - day_off1, 3)
                sp["video_off_end"] = round(offs[1] - day_off1, 3)
        enriched.append(sp)

    sitting = sitting_number(day)
    return {
        "cycle": cycle,
        "sitting": sitting,
        "session": session_id(cycle, sitting) if sitting else None,
        "date": day.get("date"),
        "datum_felirat": day.get("datum_felirat"),
        "ules": day.get("ules"),
        "ulesszak": day.get("ulesszak"),
        "day_uuid": day_uuid,
        "duration_s": day.get("duration_s"),
        "debate_s": day.get("debate_s"),
        "video": video,
        "scraped_at": _now_iso(),
        "source": "felicitas-json",
        "speeches": enriched,
    }


def _write_json(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)


def download_period(felicitas: FelicitasClient, paths: Paths, cycle: int,
                    start: str, end: str, *, force: bool = False,
                    resolve_offsets: bool = True) -> list[str]:
    """Download every sitting day of ``cycle`` in ``[start, end]`` to raw files.

    Returns the list of session keys that were (re)written this run."""
    days = felicitas.session_days(cycle, start, end)
    if not days:
        logger.info("No session days for cycle %s in [%s, %s]", cycle, start, end)
        return []

    # Most-recent sitting may still be in progress — always refresh it.
    latest_date = max((d.get("date") or "") for d in days)
    written: list[str] = []
    for day in sorted(days, key=lambda d: d.get("date") or ""):
        sitting = sitting_number(day)
        if not sitting:
            logger.warning("Skipping day with no resolvable ülésnap number: %s",
                           day.get("date"))
            continue
        session = session_id(cycle, sitting)
        raw_path = paths.raw_day(session)
        is_latest = (day.get("date") == latest_date)
        if raw_path.exists() and not force and not is_latest:
            continue

        bundle = scrape_day(felicitas, cycle, day, resolve_offsets=resolve_offsets)
        if bundle is None:
            logger.info("Sitting %s (%s) has no speeches; skipping",
                        session, day.get("date"))
            continue
        _write_json(raw_path, bundle)
        n_text = sum(1 for s in bundle["speeches"] if s.get("text_html"))
        logger.info("Saved %s (%s): %d speeches, %d with text",
                    session, day.get("date"), len(bundle["speeches"]), n_text)
        written.append(session)
    return written
