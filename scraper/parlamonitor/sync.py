"""Continuous, low-load sync of the *latest* electoral cycle (SCR-2 / SCR-4).

A production deployment must stay in step with ``parlament.hu`` without hammering
it. This module implements a **cheap change probe**: on each poll it asks the
Felicitas API only the small, list-level questions ("what sitting days exist and
how long are they?", "what irományok / votes are on record?") and re-scrapes the
expensive per-item detail (speech text, bill adatlap, vote roll call) **only for
the items that actually changed** since the last check. When nothing has changed a
poll costs a handful of requests and writes nothing.

It is deliberately scraper-only: it refreshes the ``processed/*.json`` the loader
reads and records a small ``sync-state.json`` of the last-seen signatures. Turning
the refreshed JSON into the live DB is the loader's incremental ``--update`` step
(``app.loader``), kept separate so the two halves can run/​test independently
(ING-1) and be wired together by cron or a container loop (see ``docker/``).

What each poll checks, cheaply:

* **Proceedings** — one ``ulesnapok-query`` lists the cycle's sitting days with
  their duration; a day is re-scraped when it is new, its duration changed, or
  (for the still-live latest day) its one-request speech listing fingerprint
  changed. Crucially, a day whose **transcript text has not been captured yet** is
  also kept in the re-check set (not just the latest day): parlament.hu publishes
  the recording days before the jegyzőkönyv, so such a day is re-probed each poll —
  one speech-listing request plus one speech-text probe — until its text lands,
  then it is done. An **announced day with no recording yet** is ingested as a
  placeholder (``scheduled``) so the site can show a sitting is coming. A finished
  sitting whose text we already hold is never re-fetched.
* **Bills / votes** — the cheap list query runs, but per-item detail reuses the
  on-disk detail cache (``detail_cache``), so an unchanged cycle spends network
  only on the list; the registry JSON is rewritten only when its contents differ.
* **Representatives** — refreshed on a slow cadence (``reps_max_age``) since the
  per-MP bio/stats drift gradually; the local portraits are preserved without
  re-downloading.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from . import whisper_align
from .bills.scrape import DEFAULT_MAIN_TYPES, fetch_bills, save_bills
from .config import Paths, session_id, timing_backend as _default_timing_backend
from .config import whisper_language, whisper_model
from .felicitas import FelicitasClient
from .proceedings.scrape import _write_json, scrape_day, sitting_number
from .proceedings.transform import transform_day
from .representatives.scrape import fetch_representatives, save_representatives
from .votes.scrape import fetch_votes, save_votes

logger = logging.getLogger(__name__)

# Default cadence for the (comparatively heavy) representative refresh: the roster
# and per-MP bio/stats change slowly, so a poll only re-fetches them once every
# this many seconds (env-overridable via the CLI). 12 hours by default.
DEFAULT_REPS_MAX_AGE = 12 * 3600

# For an ongoing cycle (no end date yet) probe a little past today, so an
# *announced but not-yet-held* sitting day parlament.hu already lists is picked up
# and shown as "upcoming" (a day comes onto the schedule before its date).
UPCOMING_HORIZON_DAYS = 21


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def latest_cycle(felicitas: FelicitasClient) -> int:
    """The most recent electoral cycle number known upstream."""
    ranges = felicitas.cycle_ranges()
    if not ranges:
        raise RuntimeError("Could not resolve any electoral cycle from parlament.hu")
    return max(ranges)


def _cycle_range(felicitas: FelicitasClient, cycle: int) -> tuple[str, str]:
    rng = felicitas.cycle_ranges().get(cycle) or {}
    start = rng.get("start")
    # An ongoing cycle has no end date; probe slightly into the future so an
    # announced upcoming sitting is captured (shown as "coming"), not just past days.
    horizon = (datetime.now(timezone.utc).date()
               + timedelta(days=UPCOMING_HORIZON_DAYS)).isoformat()
    end = rng.get("end") or horizon
    if not start:
        raise RuntimeError(f"No start date known for cycle {cycle}")
    return start, end


def load_state(path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_state(path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    tmp.replace(path)


def _listing_fingerprint(speeches: list[dict]) -> str:
    """Signature of a day's speech listing (count + per-speech uuid / duration /
    order), so speeches added to a sitting are detected without fetching any speech
    text. Note: attaching *text* to already-listed speeches does NOT change this —
    text lag is caught by :func:`_text_available` instead."""
    h = hashlib.sha1()
    for s in speeches:
        h.update(f"{s.get('speech_uuid')}|{s.get('duration')}|{s.get('sorszam')}\n"
                 .encode("utf-8"))
    return f"{len(speeches)}:{h.hexdigest()[:16]}"


def _bundle_has_text(bundle: dict) -> bool:
    """Whether a scraped day bundle captured any transcript text at all."""
    return any(s.get("text_html") for s in (bundle.get("speeches") or []))


def _raw_has_text(raw_path) -> bool:
    """Whether the already-downloaded raw day file holds any transcript text.

    Used to backfill the ``has_text`` signal for a sync-state written before the
    text-lag fix, so an existing complete day is recognised as done from local data
    (no network) rather than needlessly re-scraped on the first poll after upgrade."""
    try:
        raw = json.loads(raw_path.read_text())
    except (OSError, ValueError):
        return False
    return any(s.get("text_html") for s in (raw.get("speeches") or []))


def _text_available(felicitas: FelicitasClient, speeches: list[dict]) -> bool:
    """Cheap probe for whether a day's transcript has been published yet.

    parlament.hu attaches the recording and the speech *listing* days before the
    transcript **text**; when the text lands it lands for the whole day at once,
    not as a trickle. So fetching a few representative speeches' text is enough to
    tell whether the day's jegyzőkönyv is now available — a handful of requests,
    versus re-fetching every speech. Samples a few spread-out positions (not just
    the opening, which can be a terse chairing turn, and not a single speech, which
    could be one of the day's video-only speeches, VIE-8) and stops at the first
    that carries text."""
    uuids = [s.get("speech_uuid") for s in speeches if s.get("speech_uuid")]
    if not uuids:
        return False
    n = len(uuids)
    probe_idxs = sorted({0, n // 2, n - 1})     # first, middle, last (deduped)
    for i in probe_idxs:
        detail = felicitas.speech_text(uuids[i])
        if detail and (detail.get("html") or "").strip():
            return True
    return False


def _bills_fingerprint(records: list[dict]) -> str:
    """Signature of the bills list: any new bill or status/stage progression
    changes it (mirrors the detail cache's per-bill fingerprint, aggregated)."""
    h = hashlib.sha1()
    for r in sorted(records, key=lambda r: r.get("billId") or ""):
        stages = "".join(f"{s.get('key')}{int(bool(s.get('done')))}"
                         for s in (r.get("stages") or []))
        h.update(f"{r.get('billId')}|{r.get('status')}|{stages}\n".encode("utf-8"))
    return f"{len(records)}:{h.hexdigest()[:16]}"


def _votes_fingerprint(records: list[dict]) -> str:
    """Signature of the votes list. Votes are immutable once recorded, so the
    count plus the newest datetime is enough to spot new votes."""
    latest = max((r.get("datetime") or "" for r in records), default="")
    return f"{len(records)}:{latest}"


# --- proceedings -----------------------------------------------------------

def _sync_proceedings(felicitas: FelicitasClient, paths: Paths, cycle: int,
                      state: dict, *, force: bool, resolve_offsets: bool,
                      timing_backend: str) -> list[str]:
    """Re-scrape only the sitting days that are new or changed. Returns the list
    of session ids whose ``processed`` JSON was (re)written.

    Changed days are batch-transcribed with Whisper (forced-alignment timing, TIM-1)
    before transform; a day with no transcription degrades to positional timing."""
    start, end = _cycle_range(felicitas, cycle)
    days = felicitas.session_days(cycle, start, end)
    if not days:
        logger.info("No sitting days for cycle %s in [%s, %s]", cycle, start, end)
        return []
    latest_date = max((d.get("date") or "") for d in days)
    proc = state.setdefault("proceedings", {})
    changed: list[tuple[str, dict]] = []      # (session, bundle) to (re)build

    for day in sorted(days, key=lambda d: d.get("date") or ""):
        sitting = sitting_number(day)
        if not sitting:
            continue
        session = session_id(cycle, sitting)
        prev = proc.get(session) or {}
        raw_exists = paths.raw_day(session).exists()
        # Backfill the has_text signal for a pre-text-lag sync-state from the raw
        # file on disk, so a poll right after upgrade doesn't re-scrape every
        # already-complete day (it only wants the genuinely text-less ones).
        if "has_text" not in prev and raw_exists:
            prev = {**prev, "has_text": _raw_has_text(paths.raw_day(session))}
        sig = {"date": day.get("date"),
               "duration_s": day.get("duration_s"),
               "debate_s": day.get("debate_s"),
               # completeness signals; carried forward unless we (re)scrape below.
               "has_text": prev.get("has_text", False),
               "speech_count": prev.get("speech_count", 0)}
        needs = (force or not raw_exists or not prev
                 or prev.get("duration_s") != sig["duration_s"]
                 or prev.get("debate_s") != sig["debate_s"])

        is_latest = (day.get("date") == latest_date)
        # A day is re-listed (one cheap request) when it is the still-live latest
        # sitting OR when we do not yet hold its transcript text. The latter is the
        # fix for the text-lag bug: parlament.hu publishes the recording (and the
        # speech listing) days before the jegyzőkönyv text, so a day first seen
        # video-only must keep being re-checked until its text is in — not be
        # frozen as "done" the moment the video appeared (which left it stuck
        # text-less once a newer sitting made it no longer the latest day).
        incomplete = bool(prev) and not prev.get("has_text")
        if is_latest or incomplete:
            listing = felicitas.day_speeches(day["uuid"])
            fp = _listing_fingerprint(listing)
            sig["aktus_fp"] = fp
            if not needs and prev.get("aktus_fp") != fp:
                needs = True          # speeches added/removed/re-timed
            # Speeches are listed but we still have no text: attaching text does not
            # move the listing fingerprint, so probe (one request) whether the
            # transcript has now been published and, if so, re-scrape to pull it in.
            if (not needs and incomplete and listing
                    and _text_available(felicitas, listing)):
                needs = True
        elif prev.get("aktus_fp"):
            sig["aktus_fp"] = prev["aktus_fp"]      # carry the last known value

        if needs:
            bundle = scrape_day(felicitas, cycle, day, resolve_offsets=resolve_offsets)
            _write_json(paths.raw_day(session), bundle)
            changed.append((session, bundle))
            sig["has_text"] = _bundle_has_text(bundle)
            sig["speech_count"] = len(bundle.get("speeches") or [])
            sig["aktus_fp"] = _listing_fingerprint(bundle.get("speeches") or [])
        proc[session] = sig

    if not changed:
        return []

    # Transcribe the changed days' recordings (cache-aware, batched, parallel on
    # Modal); alignment failure for any day is isolated so it just falls back to the
    # positional estimate (SCR-5).
    words_by_session: dict[str, list] = {}
    try:
        # Only days that actually have a recording go to the transcriber — an
        # announced/upcoming day (placeholder, no video yet) has nothing to align.
        days_info = [(s, (b.get("video") or {}).get("m3u8"),
                      (b.get("video") or {}).get("playseq"))
                     for s, b in changed if (b.get("video") or {}).get("m3u8")]
        words_by_session = whisper_align.ensure_words(
            paths, days_info, backend=timing_backend, model=whisper_model(),
            language=whisper_language(), force=force)
    except Exception:
        logger.exception("Whisper alignment failed; using positional timing")

    written: list[str] = []
    for session, bundle in changed:
        record = transform_day(bundle, words=words_by_session.get(session))
        _write_json(paths.session_file(session), record)
        written.append(session)
        logger.info("Synced sitting %s: %d speeches (%s)", session,
                    len(bundle["speeches"]), record["meta"]["timingMethod"])
    return written


# --- bills / votes / representatives ---------------------------------------

def _sync_bills(felicitas: FelicitasClient, paths: Paths, cycle: int, state: dict,
                *, force: bool, with_detail: bool) -> bool:
    registry = fetch_bills(felicitas, cycle, main_types=DEFAULT_MAIN_TYPES,
                           with_detail=with_detail,
                           cache_path=paths.bills_file(cycle), force=force)
    fp = _bills_fingerprint(registry["data"])
    if not force and (state.get("bills") or {}).get("fp") == fp:
        return False
    save_bills(paths, cycle, registry)
    state["bills"] = {"fp": fp, "count": registry["meta"]["count"], "at": _now()}
    return True


def _sync_votes(felicitas: FelicitasClient, paths: Paths, cycle: int, state: dict,
                *, force: bool, with_detail: bool) -> bool:
    start, end = _cycle_range(felicitas, cycle)
    registry = fetch_votes(felicitas, cycle, start, end, with_detail=with_detail,
                           cache_path=paths.votes_file(cycle), force=force)
    fp = _votes_fingerprint(registry["data"])
    if not force and (state.get("votes") or {}).get("fp") == fp:
        return False
    save_votes(paths, cycle, registry)
    state["votes"] = {"fp": fp, "count": registry["meta"]["count"], "at": _now()}
    return True


def _sync_representatives(felicitas: FelicitasClient, paths: Paths, cycle: int,
                          state: dict, *, force: bool, with_detail: bool,
                          reps_max_age: float) -> bool:
    prev = state.get("representatives") or {}
    age = _now_ts() - float(prev.get("ts") or 0)
    if not force and prev and age < reps_max_age:
        return False
    registry = fetch_representatives(felicitas, cycle, details=with_detail)
    # Keep the local portraits (served at /media/photos) without re-downloading:
    # point each record at the file already on disk so the loader keeps the link.
    photos_dir = paths.data / "media" / "photos"
    for rec in registry["data"]:
        pid = rec.get("personID")
        if pid and (photos_dir / f"{pid}.jpg").exists():
            rec["photoFile"] = f"{pid}.jpg"
    save_representatives(paths, cycle, registry)
    state["representatives"] = {"ts": _now_ts(), "at": _now(),
                                "count": registry["meta"]["count"]}
    return True


# --- orchestration ---------------------------------------------------------

def run_sync(felicitas: FelicitasClient, paths: Paths, cycle: int, *,
             force: bool = False, no_detail: bool = False,
             no_offsets: bool = False, reps_max_age: float = DEFAULT_REPS_MAX_AGE,
             skip_bills: bool = False, skip_votes: bool = False,
             skip_reps: bool = False, timing_backend: str | None = None) -> dict:
    """One cheap sync pass over ``cycle``. Each domain is isolated so one failing
    query never aborts the others (SCR-5). Returns a summary of what changed."""
    paths.ensure()
    backend = timing_backend or _default_timing_backend()
    state = load_state(paths.sync_state)
    summary = {"cycle": cycle, "checkedAt": _now(),
               "sessions": [], "bills": False, "votes": False,
               "representatives": False, "errors": []}

    try:
        summary["sessions"] = _sync_proceedings(
            felicitas, paths, cycle, state, force=force,
            resolve_offsets=not no_offsets, timing_backend=backend)
    except Exception as e:
        logger.exception("Proceedings sync failed")
        summary["errors"].append(f"proceedings: {e}")

    if not skip_bills:
        try:
            summary["bills"] = _sync_bills(felicitas, paths, cycle, state,
                                           force=force, with_detail=not no_detail)
        except Exception as e:
            logger.exception("Bills sync failed")
            summary["errors"].append(f"bills: {e}")

    if not skip_votes:
        try:
            summary["votes"] = _sync_votes(felicitas, paths, cycle, state,
                                           force=force, with_detail=not no_detail)
        except Exception as e:
            logger.exception("Votes sync failed")
            summary["errors"].append(f"votes: {e}")

    if not skip_reps:
        try:
            summary["representatives"] = _sync_representatives(
                felicitas, paths, cycle, state, force=force,
                with_detail=not no_detail, reps_max_age=reps_max_age)
        except Exception as e:
            logger.exception("Representatives sync failed")
            summary["errors"].append(f"representatives: {e}")

    state["cycle"] = cycle
    state["lastCheckAt"] = summary["checkedAt"]
    save_state(paths.sync_state, state)

    summary["changed"] = bool(summary["sessions"] or summary["bills"]
                              or summary["votes"] or summary["representatives"])
    return summary
