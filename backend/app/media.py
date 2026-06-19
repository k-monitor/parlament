"""Per-speech video clip URLs derived from the day stream (VIE-9).

The Felicitas day recording is served as a SMIL VOD whose name encodes the
clip's ``[start, end]`` offsets **in milliseconds**, e.g.

    https://sgis.parlament.hu:446/vod/smil:20260509.092628.2143172.24318900.smil/playlist.m3u8
                                                          ^^^^^^^ start_ms  ^^^^^^^^ end_ms

paired with an ``archive/playseq.php?…offset1=HHMMSS.fff…offset2=…`` activation
URL the stream server must be hit on once before it serves segments.

A single speech is just a sub-range of that recording, and every aligned speech
carries its real day-relative ``[video_start, video_end]`` offsets (the
``felicitas-speech-offset`` timing anchor). Shifting the day stream's start
offset by those bounds yields a URL the server crops to exactly that speech — so
the viewer plays one speech, not the whole multi-hour day, without any extra
scraping. Verified against the live server: the cropped playlist's duration
equals ``video_end - video_start``.
"""

from __future__ import annotations

import re

# …/vod/smil:<date>.<time>.<start_ms>.<end_ms>.smil/…
_SMIL_RE = re.compile(r"(smil:\d+\.\d+\.)(\d+)\.(\d+)(\.smil)")
# …offset1=HHMMSS.fff<between>offset2=HHMMSS.fff…
_OFF_RE = re.compile(r"(offset1=)([\d.]+)(.*?)(offset2=)([\d.]+)")


def _fmt_offset(seconds: float) -> str:
    """Seconds → a playseq ``HHMMSS.fff`` offset string."""
    ms = round(seconds * 1000)
    whole, msr = divmod(ms, 1000)
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}{m:02d}{s:02d}.{msr:03d}"


def per_speech_clip(day_uri, day_playseq, video_start, video_end):
    """Build ``{"video_uri", "video_playseq"}`` for one speech from the day
    stream URLs and the speech's day-relative offsets.

    Returns ``None`` when a clip can't be derived (no day stream, missing or
    non-positive offsets, or an unrecognised URL shape) — the caller then falls
    back to the whole-day stream.
    """
    if not day_uri or video_start is None or video_end is None:
        return None
    if video_end <= video_start:
        return None
    m = _SMIL_RE.search(day_uri)
    if not m:
        return None

    day_start_ms = int(m.group(2))
    start_ms = day_start_ms + round(video_start * 1000)
    end_ms = day_start_ms + round(video_end * 1000)
    video_uri = (
        day_uri[: m.start()]
        + f"{m.group(1)}{start_ms}.{end_ms}{m.group(4)}"
        + day_uri[m.end():]
    )

    playseq = None
    if day_playseq:
        om = _OFF_RE.search(day_playseq)
        if om:
            day_start_s = day_start_ms / 1000.0
            off1 = _fmt_offset(day_start_s + video_start)
            off2 = _fmt_offset(day_start_s + video_end)
            playseq = (
                day_playseq[: om.start()]
                + f"{om.group(1)}{off1}{om.group(3)}{om.group(4)}{off2}"
                + day_playseq[om.end():]
            )

    return {"video_uri": video_uri, "video_playseq": playseq}
