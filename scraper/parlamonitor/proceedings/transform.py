"""Transform a raw day bundle into a published *session record*.

This is the parse + merge + classify step. Because the Felicitas JSON backend
already delivers media metadata and speech text together per speech, there is no
separate two-stream join to perform (the reference's ``originID`` composite-key
merge collapses to a single pass here). Each raw speech becomes one speech entry
with the logical shape the requirements fix (§3.1), reusing the field names the
reference output established so downstream consumers stay compatible.

The timing stage (``parlamonitor.timing``) runs last and is deliberately separate
and swappable (TIM-4): this module produces fully-segmented, untimed speeches,
and timing stamps day-absolute offsets onto the sentences.
"""

from __future__ import annotations

import base64
import gzip
import json
import re
from datetime import datetime, timedelta

from .. import agenda as agenda_mod
from ..names import build_person
from ..segment import html_to_text, split_sentences
from ..timing import SPEECH_OFFSET_METHOD, WHISPER_METHOD, apply_timing

PARLIAMENT = "HU"
CREATOR = "Magyar Országgyűlés"
LICENSE = "https://www.parlament.hu/web/guest/felhasznalasi-feltetelek"
LANGUAGE = "HU-hu"

_HMS_RE = re.compile(r"^(?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?$")


def origin_id(cycle: int, sitting: int, sorszam) -> str:
    return f"{cycle}-{sitting}-{sorszam}"


# Public proceedings portal. The legacy per-speech ``naplo_fadat`` permalink no
# longer resolves for anonymous clients (returns 404), so the "view original on
# parlament.hu" link (VIE-7) points at the working proceedings page instead — a
# real, resolvable origin for the data rather than a dead deep link.
PROCEEDINGS_PAGE = "https://www.parlament.hu/web/guest/orszaggyulesi-naplo"


def source_page(cycle: int, sitting: int, sorszam) -> str:
    """Origin reference for a speech — the public proceedings portal (VIE-7)."""
    return PROCEEDINGS_PAGE


# The public sitting-day view is a single-page app whose whole navigation state
# lives in a `#page=` fragment: a gzip-compressed, url-safe-base64-encoded JSON
# blob prefixed with `cv1gzb-`. Rebuilding it here lets the "view on parlament.hu"
# link (VIE-7) land on the exact day's speech listing rather than a generic page.
PLENARY_DAY_BASE = "https://www.parlament.hu/ulesnapok-ulesidok"
_PLENARY_FRAGMENT_PREFIX = "cv1gzb-"
# The new SPA keys the day on a Felicitas UUID (`pUlesnapId`). The legacy cycle-42
# backend numbered days with plain integers, which this page can't resolve — those
# fall back to the generic proceedings portal instead of a dead deep link.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _plenary_page_fragment(state: dict) -> str:
    payload = json.dumps(state, separators=(",", ":"), ensure_ascii=False)
    gz = gzip.compress(payload.encode("utf-8"))
    b64 = base64.b64encode(gz).decode("ascii").translate(str.maketrans("/+", "_-"))
    return _PLENARY_FRAGMENT_PREFIX + b64


def plenary_day_page_url(day_uuid: str | None) -> str | None:
    """Deep link to a sitting day's speech listing on parlament.hu (VIE-7)."""
    if not day_uuid or not _UUID_RE.match(str(day_uuid)):
        return None
    ds = {"type": "datasource",
          "content": {"parameters": {"pUlesnapId": day_uuid}, "open": True,
                      "openPossibleCounts": False, "state": {"page": 0}}}
    param = {"type": "parameter", "content": {"pUlesnapId": day_uuid}}
    state = {
        "page": "plenarisulesexportok/ulesnap-felszolalasai-with-contract/"
                "ulesnap-felszolalasai-with-contract",
        "binding": {
            "felszolalasDao.dataSource": ds,
            "felszolalasDao.parameter": param,
            "ulesnapMegnevezesDao.dataSource": ds,
            "ulesnapMegnevezesDao.parameter": param,
        },
        "globals": {},
    }
    return f"{PLENARY_DAY_BASE}#page={_plenary_page_fragment(state)}"


def _iso_datetime(date: str, clock: str | None) -> str:
    if not date:
        return "1900-01-01T00:00:00"
    if clock and "T" in clock:
        return clock
    if clock:
        m = _HMS_RE.match(clock.strip())
        if m:
            return f"{date}T{int(m['h']):02d}:{int(m['m']):02d}:{int(m['s'] or 0):02d}"
    return f"{date}T00:00:00"


def _add_seconds(start_iso: str, seconds) -> str:
    try:
        return (datetime.fromisoformat(start_iso)
                + timedelta(seconds=float(seconds or 0))).isoformat()
    except (ValueError, TypeError):
        return start_iso


def _agenda_item(sp: dict) -> dict:
    aktus = (sp.get("aktus") or "").strip()
    topic, bills = agenda_mod.split_topic_and_bills(aktus)
    item = {
        "title": topic or (sp.get("type") or "").strip(),
        "officialTitle": aktus or topic,
    }
    if bills or sp.get("bills"):
        # Bill references kept for the future Bills module (EXT-2); deduped.
        item["billReferences"] = sorted(set((sp.get("bills") or []) + bills))
    # Classify the ACT primarily from its OWN name, so every speech under it
    # classifies the same and lands in one agenda item (the loader groups by
    # act identity, not type). Feeding the per-speech type as a signal for a
    # NAMED act was the old bug: it split one act into several sections whenever
    # its speeches had different types — every interpelláció became a
    # "questioning_of_the_government" section plus a stray "qa" one, and
    # "Személyes érintettség" split into a regular and an ügyrendi half (one
    # section each on parlament.hu). We fall back to the per-speech type ONLY
    # when the act name carries no type signal (the generic `regular` result,
    # e.g. "Személyes érintettség" or "Az ülés napirendjének megállapítása"), so
    # structural sections keep a meaningful type. The per-speech type is still
    # recorded on the speech itself (felszolalasTipusa).
    native, core = agenda_mod.classify(aktus)
    if core == agenda_mod.CORE_REGULAR and native is None:
        native, core = agenda_mod.classify(sp.get("type"))
    item["type"] = core
    if native:
        item["nativeType"] = native
    return item


def _speech_entry(cycle: int, sitting: int, sp: dict, date: str,
                  day_video: dict | None) -> dict:
    sorszam = sp.get("sorszam")
    oid = origin_id(cycle, sitting, sorszam)
    person = build_person(sp.get("speaker") or "", person_id=sp.get("person_id"),
                          office=sp.get("role"))

    text = html_to_text(sp.get("text_html") or "")
    sentences = split_sentences(text)

    start = _iso_datetime(date, sp.get("kezdete"))
    duration = sp.get("duration")
    end = _add_seconds(start, duration)

    m3u8 = (day_video or {}).get("m3u8") or ""
    page = source_page(cycle, sitting, sorszam)

    media = {
        "videoFileURI": m3u8 or page,   # whole-day stream (VIE-2); never empty
        "sourcePage": page,
        "duration": duration,
        "creator": CREATOR,
        "license": LICENSE,
        "originMediaID": oid,
    }
    # Real per-speech offsets, provenance only for v1 (TIM-1 uses the positional
    # estimate); a future stage (§10) can consume these directly.
    if sp.get("video_off_start") is not None and sp.get("video_off_end") is not None:
        media["videoStart"] = float(sp["video_off_start"])
        media["videoEnd"] = float(sp["video_off_end"])

    debug = {
        "originSpeechID": oid,
        "speechUUID": sp.get("speech_uuid"),
        "felszolalasTipusa": sp.get("type"),
        "source": "felicitas-json",
        "confidence": 1.0 if sentences else 0.5,
    }
    # Other agenda items this one physical speech was also listed under in the
    # source (see _dedup_speeches) — kept for provenance; the speech itself is
    # filed under its first agenda item only.
    if sp.get("_merged_aktus"):
        debug["mergedAgendaItems"] = sp["_merged_aktus"]
    if not sentences:
        # Video-only / no transcript — published in degraded form, flagged, never
        # dropped (SCR-5 / VIE-8).
        debug["confidence_reason"] = "no-proceedings-text"
    if sp.get("from_roster"):
        # Recovered from the day's flat speech roster: parlament.hu links it to no
        # agenda act, so it sits under its neighbour's (felicitas._merge_roster).
        debug["agendaItemInferred"] = True
    if sp.get("person_id"):
        debug["personID"] = sp["person_id"]
    if sp.get("committee_id"):
        debug["committeeID"] = sp["committee_id"]

    entry = {
        "parliament": PARLIAMENT,
        "electoralPeriod": {"number": cycle},
        "session": {"number": sitting},
        "agendaItem": _agenda_item(sp),
        "dateStart": start,
        "dateEnd": end,
        "originID": oid,
        "people": [person],
        "media": media,
        "textContents": [{
            "type": "proceedings",
            "sourceURI": page,
            "creator": CREATOR,
            "license": LICENSE,
            "language": LANGUAGE,
            "originTextID": oid,
            "textBody": [{
                "speech_id": oid,
                "type": "speech",
                "speaker": person["label"],
                "speakerstatus": person["context"],
                "text": text,
                "sentences": sentences,
            }],
        }] if sentences else [],
        "documents": [],
        "debug": debug,
    }
    return entry


def _dedup_speeches(speeches: list[dict]) -> list[dict]:
    """Collapse the source's per-agenda-item duplication of a single speech.

    The Felicitas backend lists the SAME physical speech once for every agenda
    item (``aktus``) it is linked to — identical ``speech_uuid``, video offsets
    and text, differing only in ``aktus``. Left as-is, a chair's opening/closing
    remarks show 2-3× in a row (measured: 189 of 205 sitting days affected). We
    keep the FIRST occurrence (document order) and record the other agenda items
    it spanned in ``_merged_aktus``; the speech is filed under its first agenda
    item. Keyed on ``speech_uuid`` + video offsets so a hypothetical uuid reuse
    with genuinely different content stays separate; falls back to ``sorszam``
    when the source omits a uuid (e.g. legacy cycle-42 days).
    """
    kept: list[dict] = []
    seen: dict = {}
    for sp in speeches:
        uuid = sp.get("speech_uuid")
        key = ((uuid, sp.get("video_off_start"), sp.get("video_off_end"))
               if uuid else ("sorszam", sp.get("sorszam")))
        first = seen.get(key)
        if first is None:
            seen[key] = sp
            kept.append(sp)
        else:
            aktus = (sp.get("aktus") or "").strip()
            if aktus and aktus != (first.get("aktus") or "").strip():
                first.setdefault("_merged_aktus", []).append(aktus)
    return kept


def transform_day(raw: dict, *, words: list | None = None,
                  force_timing: bool = True) -> dict:
    """Turn a raw day bundle (``scrape.scrape_day`` output) into a session record.

    ``words`` is the day's Whisper ``[start, end, text]`` transcription (day-absolute
    seconds), produced and cached by the align stage; when given, sentence timing is
    word-accurate forced alignment (TIM-1), otherwise it is the positional estimate.
    """
    cycle = int(raw["cycle"])
    sitting = int(raw["sitting"])
    date = raw.get("date") or ""
    day_video = raw.get("video")

    speeches = [s for s in raw.get("speeches", []) if s.get("sorszam") is not None]
    speeches.sort(key=lambda s: int(s["sorszam"]))
    speeches = _dedup_speeches(speeches)

    entries = [_speech_entry(cycle, sitting, sp, date, day_video) for sp in speeches]
    for i, e in enumerate(entries, start=1):
        e["speechIndex"] = i

    # Whisper forced alignment when a transcription is available, else the
    # positional character estimate (TIM-1 / TIM-4).
    apply_timing(entries, words=words, force=force_timing)
    timing_method = WHISPER_METHOD if words else SPEECH_OFFSET_METHOD

    if entries:
        date_start = min(e["dateStart"] for e in entries)
        date_end = max(e["dateEnd"] for e in entries)
    else:
        date_start, date_end = f"{date}T00:00:00", f"{date}T23:59:59"
    for e in entries:
        e["session"]["dateStart"] = date_start
        e["session"]["dateEnd"] = date_end

    n_text = sum(1 for e in entries if e["textContents"])
    # Day status, in order of readiness:
    #   scheduled       — announced sitting, no speeches listed yet ("coming").
    #   awaiting_media  — held & speeches listed, but parlament.hu has published
    #                     NO per-speech timing/video window (recording not yet
    #                     segmented, so the whole-day-offset guard in scrape.py
    #                     left every speech without offsets) AND no transcript.
    #                     There is nothing meaningful to show — no durations, no
    #                     per-speech clips, no text — so the UI presents it as
    #                     not-yet-ready/disabled instead of a misleading page.
    #                     Re-scraped automatically until ready (_awaiting_content).
    #   published       — has per-speech media and/or transcript to browse (its
    #                     transcript may still lag per speech, VIE-8).
    has_media = any((e.get("media") or {}).get("videoStart") is not None
                    for e in entries)
    if not entries:
        status = "scheduled"
    elif not has_media and n_text == 0:
        status = "awaiting_media"
    else:
        status = "published"
    return {
        "meta": {
            "session": raw.get("session") or f"{cycle}{sitting:03d}",
            "schemaVersion": "1.0",
            "parliament": PARLIAMENT,
            "electoralPeriod": cycle,
            "sitting": sitting,
            "date": date,
            "status": status,
            "dateStart": date_start,
            "dateEnd": date_end,
            "source": raw.get("source", "felicitas-json"),
            "sourcePage": plenary_day_page_url(raw.get("day_uuid")),
            "sourceScrapedAt": raw.get("scraped_at"),
            "dayVideoURI": (day_video or {}).get("m3u8"),
            # The whole-day VOD sometimes needs its `playseq` endpoint pinged
            # before the stream starts serving segments; the player hits this
            # first to "activate" the stream (carried through for the viewer).
            "dayVideoPlayseq": (day_video or {}).get("playseq"),
            "counts": {"speeches": len(entries), "withText": n_text},
            "timingMethod": timing_method,
            "processing": {"transform": datetime.now().isoformat("T", "seconds")},
        },
        "data": entries,
    }
