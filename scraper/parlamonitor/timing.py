"""Sentence ↔ video timing — Whisper forced alignment, with a positional fallback.

The signature feature is clicking a sentence to seek the day's video to the
moment it was spoken (requirements §5.2). Timing is **day-absolute** seconds into
the whole-day HLS stream (TIM-2), so a click seeks into that stream.

This is a **distinct, swappable stage** (TIM-4): it consumes a session record and
only writes per-sentence ``timeStart``/``timeEnd`` plus provenance, never touching
the fetch/parse/segment stages or the data shape.

Three methods, picked per speech in order of precision:

* **``whisper-forced-alignment`` (default).** When a Whisper transcription of the
  day is available (produced by :mod:`parlamonitor.whisper_align`'s backend and
  cached on disk), each speech's sentences are aligned to the real spoken-word
  timestamps, so their boundaries are **word-accurate**, not estimated. This is
  the §10 forced-alignment enhancement, now the primary method (TIM-1).

* **``felicitas-speech-offset`` (fallback).** With no Whisper words for a speech,
  we fall back to its true ``[videoStart, videoEnd]`` window (captured by the
  scraper from Felicitas) and distribute its sentences inside it **in proportion
  to character position**. Error is bounded *within a single speech* (a few
  seconds) and never accumulates across the day.

* **``estimated-day-offset`` (last resort).** For any speech missing real offsets
  we fall back to the original whole-day positional estimate: distribute the day
  duration across the whole transcript by character position. Least precise (drift
  accumulates), used only when no offsets are available.

The two positional methods estimate the *sentence* position, so their sentences are
stamped with reduced ``confidence`` for the UI to disclose imprecision (TIM-3 /
VIE-6); the Whisper method is word-accurate and marked as such.
"""

from __future__ import annotations

import re

from . import whisper_align

SPEECH_OFFSET_METHOD = "felicitas-speech-offset"
ALIGN_METHOD = "estimated-day-offset"            # whole-day fallback
WHISPER_METHOD = whisper_align.WHISPER_METHOD    # forced alignment (default)
NO_TIMING = "none"

# Speech boundaries are measured, only the within-speech position is estimated,
# so this method is more trustworthy than the pure whole-day estimate.
SPEECH_OFFSET_CONFIDENCE = 0.9
ESTIMATED_CONFIDENCE = 0.7
WHISPER_CONFIDENCE = whisper_align.WHISPER_CONFIDENCE

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


def _whisper_align_speech(sp: dict, sents: list[dict], words: list) -> bool:
    """Align one speech's sentences to Whisper words within its real offset window.

    Returns True on success (sentences stamped, day-absolute). Needs both real
    per-speech offsets (the window to bound the alignment) and words spoken in it;
    a thin match falls through so the caller uses the positional estimate."""
    offsets = _speech_offsets(sp)
    if offsets is None or not words:
        return False
    in_window = whisper_align.words_in_window(words, offsets[0], offsets[1])
    if not in_window:
        return False
    result = whisper_align.align_speech(sents, in_window, offsets)
    if result is None:
        return False
    spans, _coverage = result
    for s, (ts, te) in zip(sents, spans):
        s["timeStart"], s["timeEnd"] = ts, te
    return True


def apply_timing(speeches: list[dict], *, words: list | None = None,
                 force: bool = False) -> list[dict]:
    """Stamp day-absolute ``timeStart``/``timeEnd`` on every sentence of one
    sitting (in place).

    When ``words`` (a day's Whisper ``[start, end, text]`` list) is supplied, each
    speech is aligned to the real spoken-word timings (``whisper-forced-alignment``);
    a speech the ASR couldn't cover falls back to its real per-speech offset window,
    and a speech without offsets to the whole-day positional estimate. With no
    ``words`` the behaviour is exactly the prior positional pipeline.

    ``speeches`` must be in spoken order. Returns the same list. A speech whose
    sentences already carry timing is skipped unless ``force``."""
    speeches = sorted(speeches, key=lambda s: s.get("speechIndex", 0))

    # Precompute whole-day positional offsets (the last-resort fallback) keyed by
    # sentence identity, so a speech without real offsets still gets day-absolute
    # timing.
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

        if words and _whisper_align_speech(sp, sents, words):
            method, conf = WHISPER_METHOD, WHISPER_CONFIDENCE
        else:
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
