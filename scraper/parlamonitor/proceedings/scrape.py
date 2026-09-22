"""Fetch one sitting day's raw proceedings bundle from the Felicitas JSON API.

This is the **download stage**: it talks to parlament.hu and writes a single
source-faithful ``raw-<session>-day.json`` per sitting, doing no interpretation
beyond stitching the three plenary queries together. Parsing/segmentation/timing
all happen later (``transform.py``), so the network step and the build step can
be run and tested independently (requirements ING-1).

Per day it collects, for every speech:

* the agenda act it belongs to, its join number, type, committee and any bill
  references (from ``aktus-query`` over the day's acts, completed from
  ``ulesnap-adatlap-aktusolatlan-query``), the act named in full again by
  :func:`label_acts`;
* the speaker, the linking ``person_id`` (``kepviseloId``), and the full speech
  text as HTML (from ``felszolalas-adatlap-query``);
* the whole-day HLS recording URL + duration (from ``ulesnap-video-query``);
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
from collections import Counter
from datetime import datetime, timedelta, timezone

from ..config import Paths, session_cycle, session_id
from ..felicitas import FelicitasClient

logger = logging.getLogger(__name__)

# The cycle-wide ülésnap ordinal is what the session key is built from, so it must
# be STABLE for the life of a cycle: change a day's number and it silently becomes a
# different session (new id, and the id it took over gets overwritten).
#
# parlament.hu publishes that ordinal as the parenthesised number in datumFelirat,
# e.g. "2026.06.09.(7)" → sitting 7 (matches the speech caption "7. ülésnap"). That
# field can come back empty (it did for the whole of cycle 43 in 2026-07), so the
# ordinal is otherwise derived from the day's position in the cycle's date-ordered
# day list — which is exactly what the published numbering counts.
#
# NEVER fall back to the per-day fields ``sorszamUlesszakonBelul`` /
# ``sorszamUlesenBelul``: both restart at 1 with each ülésszak (spring/summer/…),
# so using one as a cycle-wide key renumbers the cycle mid-flight. That is the
# 2026-07 outage: the summer session's days were renumbered 1..16, overwrote the
# spring session's 43001..43016, and left the old 43017..43024 behind as duplicates.
_FELIRAT_NUM_RE = re.compile(r"\((\d+)\)\s*$")


def _felirat_number(day: dict) -> int | None:
    m = _FELIRAT_NUM_RE.search(day.get("datum_felirat") or "")
    return int(m.group(1)) if m else None


def _day_order_key(day: dict) -> tuple:
    return (day.get("date") or "", day.get("day_in_ules") or 0,
            day.get("uuid") or "")


def number_days(days: list[dict]) -> list[dict]:
    """Stamp every day of a **whole cycle** with its cycle-wide ülésnap ordinal
    (``day["sitting"]``), date-ordered, and return the list in that order.

    Must be given the cycle's complete day list — an ordinal is a position in it,
    so numbering a sub-range would produce ids that collide with the real ones.
    Use :func:`cycle_days` rather than calling this on a hand-picked window.

    The source's own ordinal (datumFelirat) wins for every day that carries one —
    a day that doesn't yet (typically the newest announced sitting: parlament.hu
    assigns the label days after listing the day itself) gets the next free
    number after the highest published one, rather than dragging every already-
    labelled day's number down to fill the gap. Mixing sources only when they
    cannot collide (see the sorted-order check below) keeps a temporary label
    gap on the live edge from renumbering the whole cycle underneath it — the
    2026-08 recurrence of the 2026-07 outage: an unlabelled upcoming day (always
    in the fetch window, see :func:`cycle_days`'s horizon) intermittently threw
    away every real ordinal and wrote a fresh, wrong session key for every day,
    each poll it happened, without ever cleaning up the previous wrong batch.
    """
    ordered = sorted(days, key=_day_order_key)
    published = [_felirat_number(d) for d in ordered]
    labelled = [n for n in published if n]
    numbers = None
    if labelled and len(set(labelled)) == len(labelled):
        next_n = max(labelled) + 1
        numbers = []
        for n in published:
            if n:
                numbers.append(n)
            else:
                numbers.append(next_n)
                next_n += 1
        # A synthesized number must land strictly after every published one in
        # date order, or an unlabelled day sorts BEFORE a labelled one with a
        # lower ordinal (a corrupt/ambiguous labelling, not the common gap at
        # the live edge) — fall through to full positional numbering instead.
        if numbers != sorted(numbers):
            numbers = None
    if numbers is None:
        if any(published):
            logger.warning("datumFelirat carries an ülésnap ordinal for only "
                           "%d/%d days; numbering the cycle by date order instead",
                           len(labelled), len(ordered))
        numbers = list(range(1, len(ordered) + 1))
    for day, n in zip(ordered, numbers):
        day["sitting"] = n
    return ordered


def cycle_days(felicitas: FelicitasClient, cycle: int, start: str,
               end: str) -> list[dict]:
    """The cycle's sitting days in ``[start, end]``, each numbered (``"sitting"``).

    Numbering always spans the whole cycle — a narrowed window is applied only
    *after* the ordinals are assigned — so scraping a sub-range can never shift
    them (see :func:`number_days`)."""
    rng = felicitas.cycle_ranges().get(cycle) or {}
    num_start = min(start, rng.get("start") or start)
    num_end = max(end, rng.get("end") or end)
    days = number_days(felicitas.session_days(cycle, num_start, num_end))
    return [d for d in days if start <= (d.get("date") or "") <= end]


def sitting_number(day: dict) -> int | None:
    """The cycle-wide ülésnap ordinal of a day numbered by :func:`number_days`,
    falling back to the source's own ordinal for a day that was never numbered."""
    n = day.get("sitting")
    if isinstance(n, int) and n > 0:
        return n
    return _felirat_number(day)


def _held_bundle(raw_path) -> dict:
    """The raw bundle already downloaded to ``raw_path`` ({} if none/unreadable)."""
    try:
        return json.loads(raw_path.read_text()) or {}
    except (OSError, ValueError):
        return {}


def held_date(raw_path) -> str | None:
    """The sitting date the raw file at ``raw_path`` already holds, if any."""
    return _held_bundle(raw_path).get("date")


def renumbered(raw_path, date: str | None, uuid: str | None = None) -> str | None:
    """The date ``raw_path`` holds when that is NOT ``date`` — i.e. the source now
    gives this session key to a *different* sitting day.

    The guard against a repeat of the 2026-07 renumbering outage: writing through
    such a mismatch destroys the day the key used to mean, so callers refuse and
    say so instead, and a genuine renumbering has to be waved through explicitly.

    ``uuid`` is the upstream day id of the sitting about to be written: when it
    matches the one already on disk this is the *same* sitting whose date moved
    (parlament.hu re-scheduling an announced day in place), not one day's key
    being handed to another, so there is nothing to protect and the write goes
    ahead."""
    if not raw_path.exists():
        return None
    held = _held_bundle(raw_path)
    if uuid and held.get("day_uuid") == uuid:
        return None
    held_on = held.get("date")
    return held_on if (held_on and date and held_on != date) else None


# --- cancelled sittings ----------------------------------------------------
# parlament.hu announces sittings days ahead, and sometimes CANCELS one before it
# is held: the day drops out of ``ulesnapok-query`` entirely and the ülésnap
# ordinals it held move to the days that remain (2026-08: the announced 22./23.
# ülésnap of 08-03/08-04 disappeared and 08-10/08-11 became 22./23. instead).
#
# Nothing else in the pipeline ever removes a sitting — the loader's incremental
# update only adds and replaces — so without pruning BOTH halves break: the
# cancelled day is served as an upcoming sitting for ever, and the day that
# inherited its number can never be scraped either, because :func:`renumbered`
# (rightly) refuses to write a session key another date already holds. Deleting
# the vanished day's files fixes both at once: the key is free again, and the
# loader prunes the DB row whose processed file is gone.

def _held_sessions(paths: Paths, cycle: int):
    """``(session key, raw bundle)`` for every downloaded day of ``cycle``."""
    for path in sorted(paths.raw_plenary.glob("raw-*-day.json")):
        session = path.name[len("raw-"):-len("-day.json")]
        if session_cycle(session) != int(cycle):
            continue
        yield session, _held_bundle(path)


def cancelled_sessions(paths: Paths, cycle: int, days: list[dict], start: str,
                       end: str, *, prune_held: bool = False) -> list[str]:
    """Session keys held on disk whose sitting day the source no longer lists.

    ``days`` is what upstream currently says about ``[start, end]`` (as returned
    by :func:`cycle_days`). Deliberately conservative, because the cost of a
    false positive is deleting real archive:

    * an empty day list is a failed/short answer, never evidence of a deletion;
    * only a day inside the window actually asked about can be missing from it;
    * a day whose date OR upstream uuid is still listed is not gone — it merely
      moved or was renumbered, which :func:`renumbered` already guards;
    * a day that already has speeches is not something parlament.hu cancels; that
      is an upstream glitch, so it is reported and kept unless ``prune_held``.
    """
    if not days:
        return []
    live_dates = {d.get("date") for d in days if d.get("date")}
    live_uuids = {d.get("uuid") for d in days if d.get("uuid")}
    gone: list[str] = []
    for session, raw in _held_sessions(paths, cycle):
        date = raw.get("date") or ""
        if not (start <= date <= end):
            continue
        if date in live_dates or raw.get("day_uuid") in live_uuids:
            continue
        if raw.get("speeches") and not prune_held:
            logger.error("Sitting %s (%s) has vanished from the source day list "
                         "but holds %d speeches — keeping it. Delete its raw and "
                         "processed files by hand if the removal is genuine.",
                         session, date, len(raw["speeches"]))
            continue
        gone.append(session)
    return gone


def prune_cancelled(paths: Paths, cycle: int, days: list[dict], start: str,
                    end: str, *, prune_held: bool = False) -> list[str]:
    """Delete the local files of every sitting :func:`cancelled_sessions` finds,
    returning the session keys pruned (the loader drops their rows on its next
    update, and their ülésnap numbers are free for the days that inherited them).
    """
    gone = cancelled_sessions(paths, cycle, days, start, end,
                              prune_held=prune_held)
    for session in gone:
        for path in (paths.raw_day(session), paths.session_file(session),
                     paths.whisper_cache(session)):
            path.unlink(missing_ok=True)
        logger.warning("Pruned sitting %s: the source no longer lists that day "
                       "(cancelled sitting)", session)
    return gone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_ACT_CODE_RE = re.compile(r"\b[A-ZÁÉÍÓÖŐÚÜŰ]/\d+")


def bill_titles(paths: Paths, cycle: int) -> dict[str, str]:
    """``billNumber`` → title from the cycle's bills registry, for :func:`label_acts`.

    Empty when the registry has not been scraped yet — the label then keeps the
    bill's number without its title, and the next re-scrape of the day (a sitting
    in progress is re-listed on every sync) fills it in."""
    try:
        data = json.loads(paths.bills_file(cycle).read_text())
    except (OSError, ValueError):
        logger.info("No bills registry for cycle %s yet: agenda acts will be "
                    "labelled by bill number only", cycle)
        return {}
    return {b.get("billNumber"): b.get("title")
            for b in (data.get("data") or [])
            if b.get("billNumber") and b.get("title")}


def label_acts(speeches: list[dict], titles: dict[str, str] | None) -> list[dict]:
    """Name each speech's agenda act in full, the way the source used to.

    Until the 2026-09 restructuring the day listing named an act completely —
    ``"Általános vita (T/667) Az egyes adótörvények … módosításáról"`` — and that
    one string is what everything downstream reads: ``transform._agenda_item``
    splits it into the agenda item's topic and bill references, and the loader
    groups a sitting's speeches into sections by (title, officialTitle). The new
    API gives only the act's TYPE (``"Általános vita"``), which is neither
    descriptive nor unique within a day: three bills' general debates would collapse
    into one section, in one another's speech order.

    So the label is rebuilt from the act's own bill reference — its speeches carry
    it in the nested type table — plus that bill's title from the cycle registry.
    The spelling is the archive's own: every one of the 16 930 archived act labels
    of cycles 40-43 that names a bill is ``"<act type> (<code>) <title>"``, and all
    10 090 distinct ones of cycles 41-43 carry exactly the title the registry holds
    for that number. What the rebuild cannot restore is upstream's own rewording of
    the act TYPE (2026-09 shortened e.g. "Összevont vita folytatása és lezárása" to
    "Összevont vita"), which cost one distinction: on 1 archived day in 456 two acts
    of the same bill would now share a label and merge into one section. An act with
    no bill reference (``"Ülésnap megnyitása"``, ``"Napirend előtti felszólalások"``)
    keeps its bare name, as it always had — and collided with its like just the same
    before. Acts are labelled by ``aktus_id``, so the speeches that inherited an act
    from a neighbour (``felicitas._merge_roster``) are labelled with it.
    """
    codes: dict[str, Counter] = {}
    for sp in speeches:
        aktus_id = sp.get("aktus_id")
        if aktus_id:
            codes.setdefault(aktus_id, Counter()).update(sp.get("bills") or [])
    for sp in speeches:
        aktus_id, name = sp.get("aktus_id"), (sp.get("aktus") or "").strip()
        # A label that already names a bill is a full one (an archived bundle
        # re-read, or a source that went back to serving them).
        if not aktus_id or not name or _ACT_CODE_RE.search(name):
            continue
        seen = codes.get(aktus_id)
        if not seen:
            continue
        # The act's own bill: the one its speeches refer to most (an act names
        # exactly one — no archived label carries two).
        code = min(seen, key=lambda c: (-seen[c], c))
        title = (titles or {}).get(code)
        sp["aktus"] = f"{name} ({code}) {title}" if title else f"{name} ({code})"
    return speeches


# When a sitting's per-speech video is not yet segmented — a recently-held day
# whose whole-day recording is published but not yet cut into per-speech clips —
# ``felszolalas-video-query`` echoes the WHOLE-DAY recording window ``[day_off1,
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


# What a speech's own two requests (detail text + video offsets) contribute;
# everything else on a speech comes from the day listing, which is always fetched
# fresh. See :func:`_reuse_detail`.
_DETAIL_FIELDS = ("text_html", "caption", "role", "committee",
                  "video_off_start", "video_off_end")


def _reuse_detail(sp: dict, cached: dict) -> dict:
    """Graft an already-downloaded speech's detail onto its freshly listed self.

    A published speech's text and video window do not change, so a re-scrape whose
    point is to pick up speeches the listing previously MISSED (see
    ``felicitas._merge_roster``) can keep what it already holds and spend its
    requests on the new speeches only — two requests saved per speech, the
    difference between hours and days over the whole archive."""
    out = {**sp, **{k: cached[k] for k in _DETAIL_FIELDS if k in cached}}
    # Same precedence the live path applies (see scrape_day).
    out["type"] = sp.get("type") or cached.get("type")
    out["duration"] = sp.get("duration") or cached.get("duration")
    if sp.get("from_roster") and cached.get("speaker"):
        out["speaker"] = cached["speaker"]
    return out


def _prior_speeches(raw_path) -> dict:
    """An already-downloaded day's speeches with text, keyed by speech UUID —
    the reuse pool for :func:`_reuse_detail`."""
    try:
        raw = json.loads(raw_path.read_text())
    except (OSError, ValueError):
        return {}
    return {s["speech_uuid"]: s for s in (raw.get("speeches") or [])
            if s.get("speech_uuid") and s.get("text_html")}


def scrape_day(felicitas: FelicitasClient, cycle: int, day: dict, *,
               resolve_offsets: bool = True, prior: dict | None = None,
               titles: dict[str, str] | None = None) -> dict:
    """Build the raw bundle for one session day.

    Always returns a bundle. A day parlament.hu already lists but for which no
    speeches exist yet — an **announced/upcoming sitting**, listed before any
    recording or transcript is available — yields a placeholder bundle
    (``speeches: []``). The transform marks such a day ``scheduled`` so the site
    can show that a sitting is coming instead of silently dropping it (rather than
    the old behaviour of returning ``None`` and losing the day).

    ``prior`` is an optional uuid → already-downloaded-speech map whose text and
    offsets are reused instead of re-requested (:func:`_reuse_detail`); ``titles``
    an optional bill-number → title map used to name the agenda acts in full
    (:func:`label_acts`, :func:`bill_titles`)."""
    day_uuid = day["uuid"]
    speeches = label_acts(felicitas.day_speeches(day_uuid), titles)
    video = felicitas.day_video(day_uuid)
    day_off1 = (video or {}).get("day_off1")
    day_off2 = (video or {}).get("day_off2")

    enriched: list[dict] = []
    for sp in speeches:
        uuid = sp.get("speech_uuid")
        cached = prior.get(uuid) if (prior and uuid) else None
        if cached:
            enriched.append(_reuse_detail(sp, cached))
            continue
        detail = felicitas.speech_text(uuid) if uuid else None
        if detail:
            # The detail query is authoritative for text + a few fields the
            # listing leaves blank (role, committee name).
            sp = {**sp,
                  "text_html": detail.get("html", ""),
                  "caption": detail.get("caption"),
                  "role": detail.get("role"),
                  "committee": detail.get("committee"),
                  # A speech recovered from the flat day roster (felicitas
                  # `_merge_roster`) has only the bare speaker name; the detail
                  # query's carries the faction suffix ("Név (TISZA)") the person
                  # builder needs, so that speech is not left faction-less.
                  "speaker": ((detail.get("speaker") or sp.get("speaker"))
                              if sp.get("from_roster") else sp.get("speaker")),
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
                    resolve_offsets: bool = True,
                    reuse_text: bool = False,
                    allow_renumber: bool = False,
                    prune: bool = True) -> list[str]:
    """Download every sitting day of ``cycle`` in ``[start, end]`` to raw files.

    Returns the list of session keys that were (re)written this run. Days still
    awaiting a recording or transcript are re-scraped even if their file exists, so
    a late-published transcript is picked up (see :func:`_awaiting_content`).

    ``reuse_text`` re-lists every day but keeps the speech text/offsets already in
    its raw file (:func:`_reuse_detail`), so the run only pays for the speeches it
    did not have. That is the cheap way to backfill an archive after a
    listing-completeness fix; drop it when the point is to re-download.

    ``allow_renumber`` waves through a day whose session key is already held by a
    *different* date (:func:`renumbered`) — needed to repair a cycle numbered
    wrongly, refused by default so a source glitch cannot overwrite the archive.

    ``prune`` drops the local files of announced sittings the source has since
    cancelled (:func:`prune_cancelled`); it runs before anything is downloaded so
    the day that inherited a cancelled day's ülésnap number is free to be
    scraped in the same pass."""
    days = cycle_days(felicitas, cycle, start, end)
    if not days:
        logger.info("No session days for cycle %s in [%s, %s]", cycle, start, end)
        return []
    titles = bill_titles(paths, cycle)
    if prune:
        prune_cancelled(paths, cycle, days, start, end)

    # Most-recent sitting may still be in progress — always refresh it, unless
    # even the latest day is past the publication-lag window (a completed cycle),
    # in which case it too is done and need not be re-downloaded every run.
    latest_date = max((d.get("date") or "") for d in days)
    latest_is_live = not _past_text_grace(latest_date)
    written: list[str] = []
    for day in days:
        sitting = sitting_number(day)
        if not sitting:
            logger.warning("Skipping day with no resolvable ülésnap number: %s",
                           day.get("date"))
            continue
        session = session_id(cycle, sitting)
        raw_path = paths.raw_day(session)
        if not allow_renumber:
            held = renumbered(raw_path, day.get("date"), day.get("uuid"))
            if held:
                logger.error("Refusing to renumber %s: it holds %s but the source "
                             "now numbers %s as ülésnap %d. Nothing written — check "
                             "the numbering, then re-run with --allow-renumber.",
                             session, held, day.get("date"), sitting)
                continue
        is_latest = latest_is_live and (day.get("date") == latest_date)
        if (raw_path.exists() and not force and not is_latest
                and not _awaiting_content(raw_path)):
            continue

        prior = _prior_speeches(raw_path) if reuse_text else None
        bundle = scrape_day(felicitas, cycle, day, resolve_offsets=resolve_offsets,
                            prior=prior, titles=titles)
        _write_json(raw_path, bundle)
        n_text = sum(1 for s in bundle["speeches"] if s.get("text_html"))
        if not bundle["speeches"]:
            logger.info("Saved %s (%s): announced sitting, no speeches yet "
                        "(scheduled placeholder)", session, day.get("date"))
        else:
            logger.info("Saved %s (%s): %d speeches, %d with text%s",
                        session, day.get("date"), len(bundle["speeches"]), n_text,
                        f", {len(prior)} reused" if prior else "")
        written.append(session)
    return written
