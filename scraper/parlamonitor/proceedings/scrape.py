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
most recent (still-live) sitting, it is still **awaiting content** (an announced
day with no recording yet, or a video-only day whose transcript has not been
published), or ``force`` is set (SCR-1 / SCR-2). parlament.hu publishes a sitting
in stages — first the bare listing, then the recording, then (days later) the
transcript text — so a day is only "done" once its text is in; until then it is
re-scraped so a late-arriving transcript is picked up rather than frozen out.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

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


# When a sitting's per-speech video is not yet segmented — a recently-held day
# whose whole-day recording is published but not yet cut into per-speech clips —
# ``ulesnapok-video-query`` echoes the WHOLE-DAY recording window ``[day_off1,
# day_off2]`` as EVERY speech's offsets instead of that speech's real span. Left
# unfiltered, each speech would be stamped ``videoEnd - videoStart == day
# duration`` (≈15 h), and the loader (which derives a video-only speech's
# duration from that window) would make the sitting-day speaker toplist sum to
# absurd figures (e.g. 152 h on sitting 43015). A real single speech can never
# span the whole recording, so treat offsets that (essentially) do as "not yet
# available" and leave the speech without per-speech offsets.
_WHOLE_DAY_OFFSET_EPS = 1.0  # seconds


def _is_whole_day_window(offs: tuple[float, float],
                         day_off1: float | None,
                         day_off2: float | None) -> bool:
    if day_off1 is None or day_off2 is None:
        return False
    return (offs[0] <= day_off1 + _WHOLE_DAY_OFFSET_EPS
            and offs[1] >= day_off2 - _WHOLE_DAY_OFFSET_EPS)


def scrape_day(felicitas: FelicitasClient, cycle: int, day: dict, *,
               resolve_offsets: bool = True) -> dict:
    """Build the raw bundle for one session day.

    Always returns a bundle. A day parlament.hu already lists but for which no
    speeches exist yet — an **announced/upcoming sitting**, listed before any
    recording or transcript is available — yields a placeholder bundle
    (``speeches: []``). The transform marks such a day ``scheduled`` so the site
    can show that a sitting is coming instead of silently dropping it (rather than
    the old behaviour of returning ``None`` and losing the day)."""
    day_uuid = day["uuid"]
    speeches = felicitas.day_speeches(day_uuid)
    video = felicitas.day_video(day_uuid)
    day_off1 = (video or {}).get("day_off1")
    day_off2 = (video or {}).get("day_off2")

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
        # Skip offsets that merely echo the whole-day recording window (the
        # sitting is not yet segmented per speech): using them would fabricate a
        # ≈day-long duration for every speech (see _is_whole_day_window).
        if resolve_offsets and uuid and day_off1 is not None:
            offs = felicitas.speech_offsets(uuid)
            if (offs and offs[1] > offs[0]
                    and not _is_whole_day_window(offs, day_off1, day_off2)):
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


# parlament.hu publishes a sitting in stages — bare listing, then recording,
# then (days later) the stenographic transcript. Empirically the transcript
# lands within a couple of weeks; past this window a still-missing transcript is
# treated as officially absent (never published) rather than pending. Some older
# sittings genuinely never get one — video-only days, or eras whose record was
# not digitised (e.g. several 2011-autumn sittings in cycle 39). Without this
# gate, every continuation run re-downloads all such days of a completed cycle
# forever. Use ``--force`` to override and re-fetch regardless.
_TEXT_GRACE = timedelta(days=30)


def _past_text_grace(date_str: str | None) -> bool:
    """Whether a sitting is old enough that a missing recording/transcript will
    not arrive any more (see :data:`_TEXT_GRACE`)."""
    if not date_str:
        return False
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - d > _TEXT_GRACE


def _awaiting_content(raw_path) -> bool:
    """Whether an already-downloaded raw day is not yet complete and should be
    re-scraped even though its file exists.

    A day is incomplete while parlament.hu is still populating it: an announced
    sitting with no speeches yet (a placeholder), or a video-only day whose
    transcript text has not been published (every speech's ``text_html`` empty).
    Once at least one speech carries text the day is considered done. A malformed
    file re-scrapes to self-heal.

    A day past the publication-lag window (:func:`_past_text_grace`) is treated as
    done even if empty/text-less: the missing content is officially absent, not
    pending, so a completed cycle is not re-downloaded wholesale on every run."""
    try:
        raw = json.loads(raw_path.read_text())
    except (OSError, ValueError):
        return True
    if _past_text_grace(raw.get("date")):
        return False
    speeches = raw.get("speeches") or []
    if not speeches:
        return True
    return not any(s.get("text_html") for s in speeches)


def download_period(felicitas: FelicitasClient, paths: Paths, cycle: int,
                    start: str, end: str, *, force: bool = False,
                    resolve_offsets: bool = True) -> list[str]:
    """Download every sitting day of ``cycle`` in ``[start, end]`` to raw files.

    Returns the list of session keys that were (re)written this run. Days still
    awaiting a recording or transcript are re-scraped even if their file exists, so
    a late-published transcript is picked up (see :func:`_awaiting_content`)."""
    days = felicitas.session_days(cycle, start, end)
    if not days:
        logger.info("No session days for cycle %s in [%s, %s]", cycle, start, end)
        return []

    # Most-recent sitting may still be in progress — always refresh it, unless
    # even the latest day is past the publication-lag window (a completed cycle),
    # in which case it too is done and need not be re-downloaded every run.
    latest_date = max((d.get("date") or "") for d in days)
    latest_is_live = not _past_text_grace(latest_date)
    written: list[str] = []
    for day in sorted(days, key=lambda d: d.get("date") or ""):
        sitting = sitting_number(day)
        if not sitting:
            logger.warning("Skipping day with no resolvable ülésnap number: %s",
                           day.get("date"))
            continue
        session = session_id(cycle, sitting)
        raw_path = paths.raw_day(session)
        is_latest = latest_is_live and (day.get("date") == latest_date)
        if (raw_path.exists() and not force and not is_latest
                and not _awaiting_content(raw_path)):
            continue

        bundle = scrape_day(felicitas, cycle, day, resolve_offsets=resolve_offsets)
        _write_json(raw_path, bundle)
        n_text = sum(1 for s in bundle["speeches"] if s.get("text_html"))
        if not bundle["speeches"]:
            logger.info("Saved %s (%s): announced sitting, no speeches yet "
                        "(scheduled placeholder)", session, day.get("date"))
        else:
            logger.info("Saved %s (%s): %d speeches, %d with text",
                        session, day.get("date"), len(bundle["speeches"]), n_text)
        written.append(session)
    return written
