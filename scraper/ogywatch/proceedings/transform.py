"""Transform a raw day bundle into a published *session record*.

This is the parse + merge + classify step. Because the Felicitas JSON backend
already delivers media metadata and speech text together per speech, there is no
separate two-stream join to perform (the reference's ``originID`` composite-key
merge collapses to a single pass here). Each raw speech becomes one speech entry
with the logical shape the requirements fix (§3.1), reusing the field names the
reference output established so downstream consumers stay compatible.

The timing stage (``ogywatch.timing``) runs last and is deliberately separate
and swappable (TIM-4): this module produces fully-segmented, untimed speeches,
and timing stamps day-absolute offsets onto the sentences.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .. import agenda as agenda_mod
from ..names import build_person
from ..segment import html_to_text, split_sentences
from ..timing import apply_timing

PARLIAMENT = "HU"
CREATOR = "Magyar Országgyűlés"
LICENSE = "https://www.parlament.hu/web/guest/jogi-nyilatkozat"
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
    topic, bills = agenda_mod.split_topic_and_bills(sp.get("aktus") or "")
    item = {
        "title": topic or (sp.get("type") or "").strip(),
        "officialTitle": (sp.get("aktus") or "").strip() or topic,
    }
    if bills or sp.get("bills"):
        # Bill references kept for the future Bills module (EXT-2); deduped.
        item["billReferences"] = sorted(set((sp.get("bills") or []) + bills))
    agenda_mod.annotate(item, sp.get("aktus"), sp.get("type"))
    return item


def _speech_entry(cycle: int, sitting: int, sp: dict, date: str,
                  day_video: dict | None) -> dict:
    sorszam = sp.get("sorszam")
    oid = origin_id(cycle, sitting, sorszam)
    person = build_person(sp.get("speaker") or "", person_id=sp.get("person_id"))

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
    if not sentences:
        # Video-only / no transcript — published in degraded form, flagged, never
        # dropped (SCR-5 / VIE-8).
        debug["confidence_reason"] = "no-proceedings-text"
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


def transform_day(raw: dict, *, force_timing: bool = True) -> dict:
    """Turn a raw day bundle (``scrape.scrape_day`` output) into a session record."""
    cycle = int(raw["cycle"])
    sitting = int(raw["sitting"])
    date = raw.get("date") or ""
    day_video = raw.get("video")

    speeches = [s for s in raw.get("speeches", []) if s.get("sorszam") is not None]
    speeches.sort(key=lambda s: int(s["sorszam"]))

    entries = [_speech_entry(cycle, sitting, sp, date, day_video) for sp in speeches]
    for i, e in enumerate(entries, start=1):
        e["speechIndex"] = i

    # v1 timing: positional/character estimate across the whole day (TIM-1).
    apply_timing(entries, force=force_timing)

    if entries:
        date_start = min(e["dateStart"] for e in entries)
        date_end = max(e["dateEnd"] for e in entries)
    else:
        date_start, date_end = f"{date}T00:00:00", f"{date}T23:59:59"
    for e in entries:
        e["session"]["dateStart"] = date_start
        e["session"]["dateEnd"] = date_end

    n_text = sum(1 for e in entries if e["textContents"])
    return {
        "meta": {
            "session": raw.get("session") or f"{cycle}{sitting:03d}",
            "schemaVersion": "1.0",
            "parliament": PARLIAMENT,
            "electoralPeriod": cycle,
            "sitting": sitting,
            "date": date,
            "dateStart": date_start,
            "dateEnd": date_end,
            "source": raw.get("source", "felicitas-json"),
            "sourceScrapedAt": raw.get("scraped_at"),
            "dayVideoURI": (day_video or {}).get("m3u8"),
            # The whole-day VOD sometimes needs its `playseq` endpoint pinged
            # before the stream starts serving segments; the player hits this
            # first to "activate" the stream (carried through for the viewer).
            "dayVideoPlayseq": (day_video or {}).get("playseq"),
            "counts": {"speeches": len(entries), "withText": n_text},
            "timingMethod": "felicitas-speech-offset",
            "processing": {"transform": datetime.now().isoformat("T", "seconds")},
        },
        "data": entries,
    }
