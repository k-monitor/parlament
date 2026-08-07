"""Report which Whisper word caches in a data dir are actually usable (TIM-1).

A cached transcription is keyed by a fingerprint of the day's recording URL plus
the model tag (:func:`parlamonitor.whisper_align.cache_fingerprint`), so a cache
whose day bundle has since been re-cut upstream — or that was produced by a
different ``PARLAMONITOR_WHISPER_MODEL`` — is silently ignored and its sitting
falls back to the positional estimate. That silence is the point of this script:
after copying `whisper-<session>.json` files between machines there is otherwise
no way to tell a cache that will be *used* from one that will be *skipped*.

    python check_whisper_cache.py <data_dir> [model]

Exits non-zero if any cache is stale or has no day bundle to align against.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from parlamonitor import whisper_align                       # noqa: E402
from parlamonitor.config import Paths, whisper_model         # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    paths = Paths(argv[0])
    model = argv[1] if len(argv) > 1 else whisper_model()
    tag = whisper_align.method_tag(model)

    ok = stale = orphan = 0
    for cache_path in sorted(paths.raw_plenary.glob("whisper-*.json")):
        session = cache_path.name[len("whisper-"):-len(".json")]
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
        stale += 1
        why = "model" if blob.get("m3u8") == m3u8 else "recording re-cut upstream"
        print(f"STALE   {session}  ({why} — the cache will be ignored)")
        if blob.get("model") != tag:
            print(f"          cache model={blob.get('model')!r} wanted={tag!r}")
        if blob.get("m3u8") != m3u8:
            print(f"          cache m3u8={blob.get('m3u8')}")
            print(f"          raw   m3u8={m3u8}")

    print(f"\n{ok} usable, {stale} stale, {orphan} without a day bundle")
    return 1 if (stale or orphan) else 0


if __name__ == "__main__":
    raise SystemExit(main())
