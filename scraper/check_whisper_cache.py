"""Report which Whisper word caches in a data dir are usable — and rebase the ones
that only need re-anchoring (TIM-1).

A cached transcription is keyed by a fingerprint of the day's recording URL plus
the model tag (:func:`parlamonitor.whisper_align.cache_fingerprint`), so a cache
whose day bundle has since been re-cut upstream — or that was produced by a
different ``PARLAMONITOR_WHISPER_MODEL`` — is silently ignored and its sitting
falls back to the positional estimate. That silence is the point of this script:
after copying `whisper-<session>.json` files between machines there is otherwise no
way to tell a cache that will be *used* from one that will be *skipped*.

Not every stale cache needs a GPU to repair. parlament.hu serves a sitting as a
**cut** of one continuous daily recording, and the cut's bounds are right there in
the URL — ``/vod/smil:<date>.<time>.<startMs>.<endMs>.smil/`` (the same numbers the
``playseq`` twin spells as ``offset1``/``offset2``). When only those bounds moved,
the audio is identical and the cached words are still exactly right; they are just
measured from a ``t=0`` that has shifted. ``--rebase`` shifts them by the difference
and re-fingerprints, which costs nothing and is exact to the millisecond. A cache
whose recording *base* (date+time) differs is a genuinely different recording — no
shift can repair that, and it is left alone to be re-transcribed.

    python check_whisper_cache.py <data_dir> [--rebase] [--session ID] [--model M]

Exits non-zero if any cache is still unusable after the run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from parlamonitor import whisper_align                       # noqa: E402
from parlamonitor.config import Paths, whisper_model         # noqa: E402

# /vod/smil:20260713.124621.1031192.23291570.smil/playlist.m3u8
#           └ base: the continuous recording ┘ └ the cut, in ms ┘
_SMIL_RE = re.compile(r"/vod/smil:(\d{8}\.\d{6})\.(\d+)\.(\d+)\.smil/")


def parse_cut(m3u8: str | None) -> tuple[str, float, float] | None:
    """``(recording base, start, end)`` in seconds for a day's HLS URL, or None if
    it is not the smil form this repair understands."""
    m = _SMIL_RE.search(m3u8 or "")
    if not m:
        return None
    return m.group(1), int(m.group(2)) / 1000.0, int(m.group(3)) / 1000.0


def rebase_words(words: list[list], shift: float) -> list[list]:
    """Re-anchor day-absolute word times onto a re-cut stream. ``shift`` is added to
    every timestamp; a word that lands before the new ``t=0`` is clamped to it
    (only the first word or two can, and only when the cut moved later)."""
    out = []
    for w in words:
        s, e = max(float(w[0]) + shift, 0.0), max(float(w[1]) + shift, 0.0)
        out.append([round(s, 3), round(e, 3), w[2]])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir", type=Path, help="the scraper data directory")
    ap.add_argument("--model", default=None,
                    help="Whisper model the caches should be keyed to "
                         "(default: PARLAMONITOR_WHISPER_MODEL or large-v3-turbo)")
    ap.add_argument("--rebase", action="store_true",
                    help="repair caches whose sitting was merely re-cut from the "
                         "same recording, by shifting their word times onto the "
                         "new cut and re-fingerprinting (rewrites the cache file)")
    ap.add_argument("--session", action="append", default=None,
                    help="limit to this sitting id (repeatable)")
    args = ap.parse_args(argv)

    paths = Paths(args.data_dir)
    tag = whisper_align.method_tag(args.model or whisper_model())

    ok = rebased = stale = orphan = 0
    for cache_path in sorted(paths.raw_plenary.glob("whisper-*.json")):
        session = cache_path.name[len("whisper-"):-len(".json")]
        if args.session and session not in args.session:
            continue
        raw_path = paths.raw_day(session)
        if not raw_path.exists():
            print(f"NO RAW  {session}  (no day bundle to align against)")
            orphan += 1
            continue
        blob = json.loads(cache_path.read_text())
        m3u8 = (json.loads(raw_path.read_text()).get("video") or {}).get("m3u8")
        if blob.get("fingerprint") == whisper_align.cache_fingerprint(m3u8, tag):
            print(f"OK      {session}  {blob.get('wordCount')} words")
            ok += 1
            continue

        if blob.get("model") != tag:
            print(f"STALE   {session}  (model changed — re-transcribe)")
            print(f"          cache model={blob.get('model')!r} wanted={tag!r}")
            stale += 1
            continue

        was, now = parse_cut(blob.get("m3u8")), parse_cut(m3u8)
        if not was or not now or was[0] != now[0]:
            print(f"STALE   {session}  (different recording — re-transcribe)")
            print(f"          cache m3u8={blob.get('m3u8')}")
            print(f"          raw   m3u8={m3u8}")
            stale += 1
            continue

        # Same continuous recording, different cut: t=0 moved by (old - new).
        shift = was[1] - now[1]
        detail = (f"re-cut {shift:+.3f}s, duration "
                  f"{was[2] - was[1]:.3f}s → {now[2] - now[1]:.3f}s")
        if not args.rebase:
            print(f"STALE   {session}  ({detail}) — repairable with --rebase")
            stale += 1
            continue
        whisper_align.save_words_cache(
            cache_path, m3u8=m3u8, model_tag=tag,
            words=rebase_words(blob.get("words") or [], shift))
        print(f"REBASED {session}  ({detail})")
        rebased += 1

    parts = [f"{ok} usable", f"{rebased} rebased", f"{stale} stale",
             f"{orphan} without a day bundle"]
    print("\n" + ", ".join(parts))
    if rebased:
        print("Rebased sittings still need a re-transform to reach the DB — see "
              "DEPLOYMENT.md, 'Reusing a Whisper cache copied from another machine'.")
    return 1 if (stale or orphan) else 0


if __name__ == "__main__":
    raise SystemExit(main())
