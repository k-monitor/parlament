"""Sentence ↔ video timing — per-speech offsets with intra-speech estimate.

The signature feature is clicking a sentence to seek the day's video to the
moment it was spoken (requirements §5.2). Timing is **day-absolute** seconds into
the whole-day HLS stream (TIM-2), so a click seeks into that stream.

This is a **distinct, swappable stage** (TIM-4): it consumes a session record and
only writes per-sentence ``timeStart``/``timeEnd`` plus provenance, never touching
the fetch/parse/segment stages or the data shape.

Two methods, picked per speech:

* **``felicitas-speech-offset`` (primary).** The Felicitas backend gives each
  speech its true ``[videoStart, videoEnd]`` window in the day stream (captured
  by the scraper). We anchor each speech to that exact window and distribute its
  sentences inside it **in proportion to character position**. Error is therefore
  bounded *within a single speech* (a few seconds) and never accumulates across
  the day — this is the §10 enhancement, now the default.

* **``estimated-day-offset`` (fallback).** For any speech missing real offsets we
  fall back to the original whole-day positional estimate: distribute the day
  duration across the whole transcript by character position. Less precise (drift
  accumulates), used only when no offsets are available.

Both are estimates at the *sentence* level, so every aligned sentence is stamped
with reduced ``confidence`` and the chosen ``align-method`` for the UI to
disclose imprecision (TIM-3 / VIE-6).
"""

from __future__ import annotations

import re

SPEECH_OFFSET_METHOD = "felicitas-speech-offset"
ALIGN_METHOD = "estimated-day-offset"            # whole-day fallback
NO_TIMING = "none"

# Speech boundaries are measured, only the within-speech position is estimated,
# so this method is more trustworthy than the pure whole-day estimate.
SPEECH_OFFSET_CONFIDENCE = 0.9
ESTIMATED_CONFIDENCE = 0.7

# …/vod/smil:20260601.124141.1332144.30513190.smil/playlist.m3u8
#                          ^^^^^^^ off1ms  ^^^^^^^^ off2ms
_SMIL_RE = re.compile(r"smil:\d+\.\d+\.(\d+)\.(\d+)\.smil")


def smil_span_seconds(video_uri: str | None) -> float | None:
    """Length (seconds) of the served day stream, from its smil offsets, or None."""
    if not video_uri:
        return None
    m = _SMIL_RE.search(video_uri)
    if not m:
        return None
    span = (int(m.group(2)) - int(m.group(1))) / 1000.0
    return span if span > 0 else None


def _iter_sentences(speech: dict):
    for content in speech.get("textContents", []):
        for body in content.get("textBody", []):
            if body.get("type") == "speech":
                for s in body.get("sentences", []):
                    yield s


def day_duration_seconds(speeches: list[dict]) -> float | None:
    """Resolve the day-stream duration: prefer the smil span of the day video,
    else fall back to the summed per-speech durations."""
    for s in speeches:
        span = smil_span_seconds((s.get("media") or {}).get("videoFileURI"))
        if span is not None:
            return span
    total = sum(float((s.get("media") or {}).get("duration") or 0) for s in speeches)
    return total or None


def _char_weight(sent: dict) -> int:
    return max(len((sent.get("text") or "").strip()), 1)


def _distribute(sents: list[dict], start: float, end: float) -> None:
    """Lay ``sents`` end-to-end across ``[start, end]`` by character share."""
    total = sum(_char_weight(s) for s in sents)
    span = max(end - start, 0.0)
    if total <= 0 or span <= 0:
        for s in sents:
            s["timeStart"] = round(start, 3)
            s["timeEnd"] = round(end, 3)
        return
    cursor = start
    scale = span / total
    for s in sents:
        seg = _char_weight(s) * scale
        s["timeStart"] = round(cursor, 3)
        s["timeEnd"] = round(min(cursor + seg, end), 3)
        cursor += seg


def _speech_offsets(sp: dict) -> tuple[float, float] | None:
    """Real ``[videoStart, videoEnd]`` for a speech, if usable."""
    m = sp.get("media") or {}
    vs, ve = m.get("videoStart"), m.get("videoEnd")
    if vs is None or ve is None:
        return None
    try:
        vs, ve = float(vs), float(ve)
    except (TypeError, ValueError):
        return None
    return (vs, ve) if ve > vs else None


def apply_timing(speeches: list[dict], *, force: bool = False) -> list[dict]:
    """Stamp day-absolute ``timeStart``/``timeEnd`` on every sentence of one
    sitting (in place). Prefers real per-speech offsets; falls back to the
    whole-day positional estimate where offsets are missing.

    ``speeches`` must be in spoken order. Returns the same list. A speech whose
    sentences already carry timing is skipped unless ``force``."""
    speeches = sorted(speeches, key=lambda s: s.get("speechIndex", 0))

    # Precompute whole-day positional offsets (the fallback) keyed by sentence
    # identity, so a speech without real offsets still gets day-absolute timing.
    duration = day_duration_seconds(speeches)
    total_chars = sum(_char_weight(s) for sp in speeches for s in _iter_sentences(sp))
    legacy: dict[int, tuple[float, float]] = {}
    if duration and total_chars:
        cursor, scale = 0.0, duration / total_chars
        for sp in speeches:
            for s in _iter_sentences(sp):
                seg = _char_weight(s) * scale
                legacy[id(s)] = (round(cursor, 3), round(cursor + seg, 3))
                cursor += seg

    for sp in speeches:
        sents = list(_iter_sentences(sp))
        if not sents:
            continue
        already = all(s.get("timeStart") is not None for s in sents)
        if already and not force:
            continue

        offsets = _speech_offsets(sp)
        if offsets is not None:
            _distribute(sents, offsets[0], offsets[1])
            method, conf = SPEECH_OFFSET_METHOD, SPEECH_OFFSET_CONFIDENCE
        elif legacy:
            for s in sents:
                start, end = legacy[id(s)]
                s["timeStart"], s["timeEnd"] = start, end
            method, conf = ALIGN_METHOD, ESTIMATED_CONFIDENCE
        else:
            method, conf = NO_TIMING, None

        debug = sp.setdefault("debug", {})
        debug["align-method"] = method
        if conf is not None:
            debug["confidence"] = min(debug.get("confidence", 1.0), conf)
        sp.setdefault("media", {})["aligned"] = method != NO_TIMING

    return speeches
