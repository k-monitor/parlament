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
  placeholder (``scheduled``) so the site can show a sitting is coming; if that
  day is later **cancelled** (it drops out of the listing) its files are pruned,
  which also frees the ülésnap number it held for the day announced in its place.
  A finished sitting whose text we already hold is never re-fetched.
* **Committees** — the registry is re-read whole (there is nothing to key a
  per-body cache on) and rewritten only when a count, a published minutes file
  or the newest meeting moved.
* **Bills / votes** — the cheap list query runs, but per-item detail reuses the
  on-disk detail cache (``detail_cache``), so an unchanged cycle spends network
  only on the list; the registry JSON is rewritten only when its contents differ.
* **Representatives** — refreshed on a slow cadence (``reps_max_age``) since the
  per-MP bio/stats drift gradually; the local portraits are preserved without
  re-downloading.
* **Nationality advocates** (nemzetiségi szószólók) — on the same slow cadence as
  the representatives, and only for the latest cycle; a past cycle's advocate
  roster is closed, so it is backfilled once by ``parlamonitor advocates
  --all-cycles`` and then left alone.
* **Office holders** (tisztségviselők) — the cycle-less registry of office terms,
  on the representatives' cadence: a reshuffle closes one term and opens another,
  and for a **non-MP** minister nothing else records it. Rewritten only when its
  contents changed.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from . import whisper_align
from .advocates.scrape import fetch_advocates, save_advocates
from .aktualis.scrape import (fetch_aktualis, load_previous,
                              save_aktualis)
from .bills.scrape import DEFAULT_MAIN_TYPES, fetch_bills, save_bills
from .config import Paths, session_id, timing_backend as _default_timing_backend
from .config import documents_compression as _documents_compression
from .config import documents_max_mb as _documents_max_mb
from .config import documents_per_sync as _documents_per_sync
from .config import documents_retention, whisper_language, whisper_model
from .documents.scrape import fetch_documents, load_registry
from .felicitas import FelicitasClient
from .officeholders.scrape import fetch_office_holders, save_office_holders
from .proceedings.scrape import (_write_json, cycle_days, prune_cancelled,
                                 renumbered, scrape_day, sitting_number)
from .proceedings.transform import transform_day
from .representatives.scrape import (fetch_missing_photos, fetch_representatives,
                                     save_representatives)
from .committees.scrape import (fetch_committees, save_committees,
                                load_previous as load_committees_file)
from .committees.minutes_scrape import (fetch_minutes, meetings_with_minutes,
                                        save_minutes)
from .committees.videos import (fetch_videos, save_videos,
                                load_previous as load_videos_file)
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

# How long after a sitting we keep re-listing it to collect per-speech video
# timings that upstream published only partly (see _media_complete). Generous
# next to the observed lag (hours to a few days), but bounded: past it, a day that
# is still short of 100% is one parlament.hu never finished segmenting, and
# chasing it forever would cost a request per poll per day for nothing.
MEDIA_CHASE_DAYS = 30


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


def _media_complete(speeches: list[dict]) -> bool:
    """Whether EVERY listed speech carries its own slice of the day's recording.

    parlament.hu segments a day's video per speech in instalments: the sitting of
    2026-07-27 sat for days with only its first 110 of 166 speeches timed, the rest
    carrying a bare ``None`` (a finished day is invariably 100% — days 14-19 and 21
    of cycle 43 all are). A day is therefore not "done" just because its text
    arrived: the untimed tail has no duration, no clip and no position in the
    viewer until the rest lands.

    Vacuously true for a day with no speeches yet (an announced sitting has no media
    to be missing — its day-level ``duration_s`` is what signals it has started)."""
    return all(s.get("duration") or s.get("video_off_start") is not None
               for s in speeches)


def _raw_completeness(raw_path) -> tuple[bool, bool]:
    """``(has_text, has_media)`` of the already-downloaded raw day file.

    Used to backfill those signals for a sync-state written before the fix that
    introduced them, so an existing complete day is recognised as done from local
    data (no network) rather than needlessly re-scraped — or, worse, re-listed on
    every poll forever — on the first polls after an upgrade."""
    try:
        raw = json.loads(raw_path.read_text())
    except (OSError, ValueError):
        return False, False
    speeches = raw.get("speeches") or []
    return any(s.get("text_html") for s in speeches), _media_complete(speeches)


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


def _committees_fingerprint(registry: dict) -> str:
    """Signature of the committee registry.

    Counts alone will not do. A committee list barely moves within a term, and
    the changes a pass exists to notice mostly leave every count where it was:
    a seat changing hands (one member out, one in), a committee renamed, a
    bill's committee stage advancing, a jegyzőkönyv finally published, a
    scheduled meeting moved to another room. So each listing contributes a
    digest of the *content* that identifies it, not just its length — cheap
    (a few thousand short strings) and computed once per pass.
    """
    def digest(rows, fields) -> str:
        h = hashlib.sha1()
        for line in sorted("|".join(str(r.get(f)) for f in fields) for r in rows or []):
            h.update(line.encode("utf-8"))
        return h.hexdigest()[:12]

    parts = [
        str(len(registry.get("data") or [])),
        # Renames and type/homepage changes, not just how many bodies there are.
        digest(registry.get("data"),
               ("committeeId", "name", "parentId", "type", "dateEnd")),
        # Who sits where, in what role and for whom — a 1-for-1 swap moves this.
        digest(registry.get("members"),
               ("committeeId", "personID", "role", "factionName")),
        digest(registry.get("terms"),
               ("committeeId", "personID", "kind", "role", "dateStart",
                "dateEnd", "reason")),
        # Includes minutesUrl, so a jegyzőkönyv appearing on a known meeting
        # registers even though nothing was added.
        digest(registry.get("meetings"),
               ("meetingId", "datetime", "quorum", "durationS", "minutesUrl")),
        # `status` is the committee's stage on the bill: it advances without
        # any document being added or removed.
        digest(registry.get("documents"),
               ("committeeId", "billId", "status", "referredAt")),
        digest(registry.get("submissions"),
               ("committeeId", "kind", "billId", "textUrl")),
        # The schedule ahead is state rather than history: a meeting moved to
        # another room, or called off, changes no count either.
        digest(registry.get("upcoming"),
               ("meetingId", "committeeId", "date", "time", "venue", "cancelled")),
    ]
    return ":".join(parts)


# --- proceedings -----------------------------------------------------------

def _sync_proceedings(felicitas: FelicitasClient, paths: Paths, cycle: int,
                      state: dict, *, force: bool, resolve_offsets: bool,
                      timing_backend: str) -> tuple[list[str], list[str]]:
    """Re-scrape only the sitting days that are new or changed. Returns
    ``(written, pruned)``: the session ids whose ``processed`` JSON was
    (re)written, and those dropped because the sitting was cancelled upstream.

    Changed days are batch-transcribed with Whisper (forced-alignment timing, TIM-1)
    before transform; a day with no transcription degrades to positional timing."""
    start, end = _cycle_range(felicitas, cycle)
    # Which cycle is the newest scopes the (metered) Whisper offload — see
    # ensure_words. The client memoizes the cycle list, so this costs no request.
    ranges = felicitas.cycle_ranges()
    newest = max(ranges) if ranges else None
    days = cycle_days(felicitas, cycle, start, end)
    if not days:
        logger.info("No sitting days for cycle %s in [%s, %s]", cycle, start, end)
        return [], []
    latest_date = max((d.get("date") or "") for d in days)
    media_cutoff = (datetime.now(timezone.utc).date()
                    - timedelta(days=MEDIA_CHASE_DAYS)).isoformat()
    proc = state.setdefault("proceedings", {})
    # An announced sitting parlament.hu has since CANCELLED is dropped before
    # anything else: nothing downstream ever removes a day, and its ülésnap number
    # has already moved to the day announced in its place — which the renumbering
    # guard below would refuse to scrape while the cancelled day still held the key.
    pruned = prune_cancelled(paths, cycle, days, start, end)
    for session in pruned:
        proc.pop(session, None)
    changed: list[tuple[str, dict]] = []      # (session, bundle) to (re)build

    for day in days:
        sitting = sitting_number(day)
        if not sitting:
            continue
        session = session_id(cycle, sitting)
        # A session key that already holds a different date means the source has
        # renumbered the cycle under us; scraping on would overwrite that day with
        # this one (the 2026-07 outage). Leave it alone and shout — repairing a
        # real renumbering is a deliberate `proceedings --allow-renumber` run.
        held = renumbered(paths.raw_day(session), day.get("date"), day.get("uuid"))
        if held:
            logger.error("Refusing to renumber %s: it holds %s but the source now "
                         "numbers %s as ülésnap %d — skipping this day.",
                         session, held, day.get("date"), sitting)
            continue
        prev = proc.get(session) or {}
        raw_exists = paths.raw_day(session).exists()
        # Backfill the completeness signals for a sync-state written before they
        # existed, from the raw file on disk, so a poll right after upgrade doesn't
        # re-scrape (or endlessly re-list) every already-complete day — it only
        # wants the genuinely unfinished ones.
        if ("has_text" not in prev or "has_media" not in prev) and raw_exists:
            raw_text, raw_media = _raw_completeness(paths.raw_day(session))
            prev = {"has_text": raw_text, "has_media": raw_media, **prev}
        sig = {"date": day.get("date"),
               "duration_s": day.get("duration_s"),
               "debate_s": day.get("debate_s"),
               # completeness signals; carried forward unless we (re)scrape below.
               "has_text": prev.get("has_text", False),
               "has_media": prev.get("has_media", False),
               "speech_count": prev.get("speech_count", 0)}
        needs = (force or not raw_exists or not prev
                 # An announced sitting can be MOVED (parlament.hu re-dates the day
                 # in place, keeping its uuid and number); nothing else would notice,
                 # since a day with no recording yet has no duration to change.
                 or prev.get("date") != sig["date"]
                 or prev.get("duration_s") != sig["duration_s"]
                 or prev.get("debate_s") != sig["debate_s"])

        is_latest = (day.get("date") == latest_date)
        # A day is re-listed (one cheap request) when it is the still-live latest
        # sitting OR when what we hold of it is unfinished. parlament.hu completes a
        # sitting in instalments and in no fixed order, so BOTH halves have to be
        # chased or a day freezes half-done the moment it stops being the newest:
        #   * text — the recording and the speech listing are published days before
        #     the jegyzőkönyv (the text-lag bug: days stuck video-only forever);
        #   * media — the video is segmented per speech in batches, so a day can
        #     arrive with its transcript complete but only the first N speeches
        #     timed (2026-07-27: 110 of 166), leaving the tail with no duration and
        #     no clip (see _media_complete).
        # Missing media, unlike missing text, IS visible in the listing fingerprint,
        # so no extra probe is needed once we look. Chasing it is bounded to
        # MEDIA_CHASE_DAYS: an old day still short of 100% is one upstream never
        # finished, and re-listing it on every poll until the end of the cycle would
        # be a request per poll per day for nothing (SCR-4 politeness).
        incomplete_text = bool(prev) and not prev.get("has_text")
        incomplete_media = (bool(prev) and not prev.get("has_media")
                            and (day.get("date") or "") >= media_cutoff)
        if is_latest or incomplete_text or incomplete_media:
            listing = felicitas.day_speeches(day["uuid"])
            fp = _listing_fingerprint(listing)
            sig["aktus_fp"] = fp
            if not needs and prev.get("aktus_fp") != fp:
                needs = True          # speeches added/removed/re-timed
            # Speeches are listed but we still have no text: attaching text does not
            # move the listing fingerprint, so probe (one request) whether the
            # transcript has now been published and, if so, re-scrape to pull it in.
            if (not needs and incomplete_text and listing
                    and _text_available(felicitas, listing)):
                needs = True
        elif prev.get("aktus_fp"):
            sig["aktus_fp"] = prev["aktus_fp"]      # carry the last known value

        if needs:
            bundle = scrape_day(felicitas, cycle, day, resolve_offsets=resolve_offsets)
            _write_json(paths.raw_day(session), bundle)
            changed.append((session, bundle))
            sig["has_text"] = _bundle_has_text(bundle)
            sig["has_media"] = _media_complete(bundle.get("speeches") or [])
            sig["speech_count"] = len(bundle.get("speeches") or [])
            sig["aktus_fp"] = _listing_fingerprint(bundle.get("speeches") or [])
        proc[session] = sig

    if not changed:
        return [], pruned

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
            language=whisper_language(), force=force, latest_cycle=newest)
    except Exception:
        logger.exception("Whisper alignment failed; using positional timing")

    written: list[str] = []
    for session, bundle in changed:
        record = transform_day(bundle, words=words_by_session.get(session))
        _write_json(paths.session_file(session), record)
        written.append(session)
        logger.info("Synced sitting %s: %d speeches (%s)", session,
                    len(bundle["speeches"]), record["meta"]["timingMethod"])
    return written, pruned


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


def _sync_documents(felicitas: FelicitasClient, paths: Paths, cycle: int, *,
                    retention: str, compression: str, max_mb: float,
                    limit: int, force: bool) -> dict | bool:
    """Top up the iromány document mirror for this cycle (DOC-1).

    Off unless the run was configured for it, which is what keeps a live sync's
    disk usage exactly as it was. When it is on, the cost of an idle pass is
    nothing: the stage is incremental against its own index, so only documents
    that appeared since the last pass are actually downloaded.

    The exception is the *first* pass after it is switched on, which faces the
    whole cycle's backlog at once — so a sync pass fetches at most ``limit``
    documents (``PARLAMONITOR_DOCUMENTS_PER_SYNC``, 0 = no cap) and leaves the
    rest to the passes after it. Meta carries ``pending``, which is that
    backlog counting down."""
    if retention == "off":
        return False
    registry = load_registry(paths, cycle)
    return fetch_documents(felicitas.http, paths, cycle, registry,
                           retention=retention, compression=compression,
                           max_mb=max_mb, limit=limit or None, force=force)


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


def _sync_committees(felicitas: FelicitasClient, paths: Paths, cycle: int,
                     state: dict, *, force: bool, with_detail: bool) -> bool:
    """Re-read the cycle's committees and rewrite the registry if anything moved.

    Unlike bills and votes there is no per-item detail cache to reuse: the
    per-body requests (the iromány listings) are the bulk of the pass and
    upstream gives nothing to key a cache on. At ~130 requests for a 40-body
    cycle that is still the cheapest of the heavy stages, but it is also the
    one a pass can most usefully do without, which is what ``--no-detail``
    (and ``--skip-committees``) are for."""
    start, end = _cycle_range(felicitas, cycle)
    registry = fetch_committees(felicitas, cycle, start, end,
                                with_detail=with_detail,
                                previous=load_committees_file(paths, cycle))
    fp = _committees_fingerprint(registry)
    if not force and (state.get("committees") or {}).get("fp") == fp:
        return False
    save_committees(paths, cycle, registry)
    state["committees"] = {"fp": fp, "count": registry["meta"]["count"],
                           "at": _now()}
    return True


# How many NEW jegyzőkönyv PDFs one sync pass will fetch. A committee publishes
# its minutes weeks after the sitting and in ones and twos, so in steady state
# this never binds; it is what stops the first pass after a cycle is added from
# turning a sync into a thousand-request scrape (SCR-4).
DEFAULT_MINUTES_LIMIT = 40


def _sync_committee_minutes(http, paths: Paths, cycle: int, state: dict, *,
                            force: bool, limit: int) -> object:
    """Read any newly published jegyzőkönyv into the parsed record (BIZ-15).

    Run straight after the committee registry, because that is where the list of
    minutes to fetch comes from. A pass over a cycle already read costs nothing
    at all — the stored text is the cache and re-parsing is local — so the stage
    is left on rather than gated behind a fingerprint: what it re-reads is
    disk, and what it fetches is only what is new.

    ``limit`` caps how many *new* documents one pass takes. A committee
    publishes its minutes weeks after the sitting and in ones and twos, so the
    cap almost never binds in steady state; it is there so the first pass after
    a cycle is added does not turn a sync into a thousand-request scrape.
    """
    registry = load_committees_file(paths, cycle)
    if not registry:
        return False
    meetings = meetings_with_minutes(registry)
    if not meetings:
        return False
    parsed = fetch_minutes(http, paths, cycle, meetings, limit=limit,
                           force=force)
    counts = parsed["meta"]["counts"]
    # Written whenever anything was fetched OR the parse changed what it holds:
    # a parser fix has to reach the site without waiting for a new sitting.
    previous = paths.committee_minutes_file(cycle)
    if not counts["fetched"] and previous.exists() and \
            _minutes_fingerprint(parsed) == (state.get("committeeMinutes")
                                             or {}).get("fp"):
        return False
    save_minutes(paths, cycle, parsed)
    state["committeeMinutes"] = {"fp": _minutes_fingerprint(parsed),
                                 "count": parsed["meta"]["count"], "at": _now()}
    return {k: counts[k] for k in ("fetched", "reused", "errors", "pending",
                                   "speeches")}


def _minutes_fingerprint(parsed: dict) -> str:
    """Signature of a parsed minutes registry.

    Counts alone would do for "did a new sitting arrive", but not for "did the
    parser start reading them differently", which is the other reason this file
    changes — so the speech and character totals are in it too.
    """
    counts = parsed["meta"]["counts"]
    payload = [parsed["meta"]["count"]] + [
        counts[k] for k in ("meetings", "errors", "speeches", "chars")]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _sync_committee_videos(http, paths: Paths, state: dict, *,
                           force: bool) -> object:
    """Re-read the House's YouTube channel (BIZ-16).

    One request in the normal case: the RSS feed carries the newest 15, which is
    weeks of a channel that posts a handful a week. The backfill is **not** run
    here — it walks the whole upload history and belongs in the explicit
    `committee-videos --backfill` command, which is a one-off. If the feed's
    window is ever overrun (more than 15 posted since the last pass), the gap is
    closed by running that command, not by making every sync pass expensive.
    """
    registry = fetch_videos(http, previous=load_videos_file(paths))
    fp = hashlib.sha256(json.dumps(
        [v["videoId"] for v in registry["data"]],
        sort_keys=True).encode("utf-8")).hexdigest()[:16]
    if not force and (state.get("committeeVideos") or {}).get("fp") == fp:
        return False
    save_videos(paths, registry)
    state["committeeVideos"] = {"fp": fp, "count": registry["meta"]["count"],
                                "at": _now()}
    return {k: registry["meta"]["counts"].get(k)
            for k in ("videos", "committee", "plenary")}


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


def _sync_advocates(felicitas: FelicitasClient, paths: Paths, cycle: int,
                    state: dict, *, force: bool, with_detail: bool,
                    reps_max_age: float) -> bool:
    """Refresh the cycle's nationality-advocate registry on the reps cadence.

    Same slow-cadence reasoning as the representatives: an advocate's bio and
    per-cycle counts drift gradually, and there are only ~13 of them. Portraits are
    topped up through the shared negative-cached helper, so an advocate who already
    has a portrait on disk (or is known to have none) costs no request."""
    prev = state.get("advocates") or {}
    age = _now_ts() - float(prev.get("ts") or 0)
    if not force and prev and age < reps_max_age:
        return False
    registry = fetch_advocates(felicitas, cycle, details=with_detail)
    if not registry["data"]:
        # No advocates in this cycle (or an empty upstream answer): record the
        # check so the cadence holds, but never write an empty registry over one
        # the loader already holds.
        state["advocates"] = {"ts": _now_ts(), "at": _now(), "count": 0}
        return False
    photos_dir = paths.data / "media" / "photos"
    fetch_missing_photos(felicitas, photos_dir,
                         [r.get("personID") for r in registry["data"]])
    for rec in registry["data"]:
        pid = rec.get("personID")
        if pid and (photos_dir / f"{pid}.jpg").exists():
            rec["photoFile"] = f"{pid}.jpg"
    save_advocates(paths, cycle, registry)
    state["advocates"] = {"ts": _now_ts(), "at": _now(),
                          "count": registry["meta"]["count"]}
    return True


def _sync_office_holders(felicitas: FelicitasClient, paths: Paths, state: dict,
                         *, force: bool, reps_max_age: float) -> bool:
    """Refresh the office-holder registry (tisztségviselők) on the reps cadence.

    Cycle-less and cheap (a handful of paged requests for the whole archive), and
    the thing that keeps a **sitting** office current: a reshuffle closes one term
    and opens another, and for a non-MP minister no other source says so. Rewritten
    only when the contents actually changed, so an unchanged registry leaves the
    loader nothing to do."""
    prev = state.get("officeHolders") or {}
    age = _now_ts() - float(prev.get("ts") or 0)
    if not force and prev and age < reps_max_age:
        return False
    registry = fetch_office_holders(felicitas)
    if not registry["data"]:
        # Never write an empty registry over one the loader already holds; the
        # check is still recorded so the cadence holds.
        state["officeHolders"] = {"ts": _now_ts(), "at": _now(), "terms": 0}
        return False
    fp = hashlib.sha256(
        json.dumps(registry["data"], sort_keys=True, ensure_ascii=False)
        .encode()).hexdigest()
    state["officeHolders"] = {"ts": _now_ts(), "at": _now(), "fp": fp,
                              "terms": registry["meta"]["terms"]}
    if not force and prev.get("fp") == fp:
        return False
    save_office_holders(paths, registry)
    return True


def _sync_aktualis(paths: Paths, http, *, force: bool) -> bool:
    """Refresh the Aktuális page and the napirend behind it (NR-1).

    Every pass, on no cadence of its own: the file is a *schedule*, and it is
    worth only what it is worth before the sitting happens — a napirend that
    lands the morning of the sitting and is picked up a day later was never
    published at all, as far as the site is concerned. The cost is one HTML
    request unless the House has issued a new napirend, because the document's
    slug names the sitting it is for. Rewritten only when something actually
    changed, so an unchanged page leaves the loader nothing to do."""
    previous = load_previous(paths)
    registry = fetch_aktualis(http, previous=previous, force=force)
    if not force and previous and previous.get("data") == registry["data"]:
        return False
    save_aktualis(paths, registry)
    return True


# --- orchestration ---------------------------------------------------------

def run_sync(felicitas: FelicitasClient, paths: Paths, cycle: int, *,
             force: bool = False, no_detail: bool = False,
             no_offsets: bool = False, reps_max_age: float = DEFAULT_REPS_MAX_AGE,
             skip_bills: bool = False, skip_votes: bool = False,
             skip_committees: bool = False,
             skip_reps: bool = False, skip_advocates: bool = False,
             skip_office_holders: bool = False, skip_aktualis: bool = False,
             timing_backend: str | None = None,
             documents: str | None = None,
             documents_compression: str | None = None,
             documents_max_mb: float | None = None,
             documents_limit: int | None = None,
             minutes_limit: int = DEFAULT_MINUTES_LIMIT) -> dict:
    """One cheap sync pass over ``cycle``. Each domain is isolated so one failing
    query never aborts the others (SCR-5). Returns a summary of what changed."""
    paths.ensure()
    backend = timing_backend or _default_timing_backend()
    state = load_state(paths.sync_state)
    summary = {"cycle": cycle, "checkedAt": _now(),
               "sessions": [], "removedSessions": [], "bills": False,
               "votes": False, "committees": False,
               "committeeMinutes": False, "committeeVideos": False,
               "representatives": False, "advocates": False,
               "officeHolders": False, "documents": False, "aktualis": False,
               "errors": []}

    try:
        summary["sessions"], summary["removedSessions"] = _sync_proceedings(
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

    # After bills, because it mirrors the files that registry links to.
    retention = documents if documents is not None else documents_retention()
    if retention != "off":
        try:
            summary["documents"] = _sync_documents(
                felicitas, paths, cycle, retention=retention,
                compression=(documents_compression
                             or _documents_compression()),
                max_mb=(documents_max_mb if documents_max_mb is not None
                        else _documents_max_mb()),
                limit=(documents_limit if documents_limit is not None
                       else _documents_per_sync()),
                force=force)
        except Exception as e:
            logger.exception("Document mirror sync failed")
            summary["errors"].append(f"documents: {e}")

    if not skip_votes:
        try:
            summary["votes"] = _sync_votes(felicitas, paths, cycle, state,
                                           force=force, with_detail=not no_detail)
        except Exception as e:
            logger.exception("Votes sync failed")
            summary["errors"].append(f"votes: {e}")

    # After bills and votes: the committee registry's iromány listings are only
    # worth re-reading once the documents they point at are on record.
    if not skip_committees:
        try:
            summary["committees"] = _sync_committees(
                felicitas, paths, cycle, state, force=force,
                with_detail=not no_detail)
        except Exception as e:
            logger.exception("Committees sync failed")
            summary["errors"].append(f"committees: {e}")

        # The jegyzőkönyvek hang off the registry above, so they follow it —
        # and they fail on their own (SCR-5): a committee refresh that worked
        # must not be lost because parlament.hu 404'd one PDF.
        try:
            summary["committeeMinutes"] = _sync_committee_minutes(
                felicitas.http, paths, cycle, state, force=force,
                limit=minutes_limit)
        except Exception as e:
            logger.exception("Committee minutes sync failed")
            summary["errors"].append(f"committee-minutes: {e}")

        # The recordings are not scoped to a cycle and cost one request, so
        # they ride along with the committee stage rather than having a
        # cadence of their own.
        try:
            summary["committeeVideos"] = _sync_committee_videos(
                felicitas.http, paths, state, force=force)
        except Exception as e:
            logger.exception("Committee video sync failed")
            summary["errors"].append(f"committee-videos: {e}")

    if not skip_reps:
        try:
            summary["representatives"] = _sync_representatives(
                felicitas, paths, cycle, state, force=force,
                with_detail=not no_detail, reps_max_age=reps_max_age)
        except Exception as e:
            logger.exception("Representatives sync failed")
            summary["errors"].append(f"representatives: {e}")

    if not skip_advocates:
        try:
            summary["advocates"] = _sync_advocates(
                felicitas, paths, cycle, state, force=force,
                with_detail=not no_detail, reps_max_age=reps_max_age)
        except Exception as e:
            logger.exception("Advocates sync failed")
            summary["errors"].append(f"advocates: {e}")

    if not skip_office_holders:
        # On the representatives' cadence: person enrichment too, just the slice
        # that also covers the non-MPs.
        try:
            summary["officeHolders"] = _sync_office_holders(
                felicitas, paths, state, force=force, reps_max_age=reps_max_age)
        except Exception as e:
            logger.exception("Office-holder sync failed")
            summary["errors"].append(f"officeholders: {e}")

    if not skip_aktualis:
        try:
            summary["aktualis"] = _sync_aktualis(paths, felicitas.http, force=force)
        except Exception as e:
            logger.exception("Aktuális sync failed")
            summary["errors"].append(f"aktualis: {e}")

    state["cycle"] = cycle
    state["lastCheckAt"] = summary["checkedAt"]
    save_state(paths.sync_state, state)

    summary["changed"] = bool(summary["sessions"] or summary["removedSessions"]
                              or summary["bills"] or summary["votes"]
                              or summary["committees"]
                              or summary["committeeMinutes"]
                              or summary["committeeVideos"]
                              or summary["representatives"]
                              or summary["advocates"]
                              or summary["aktualis"])
    return summary
