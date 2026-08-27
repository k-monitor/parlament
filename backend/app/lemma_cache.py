"""Shared on-disk store of HuSpaCy lemma streams, keyed by sentence id.

Lemmatizing the transcript is the most expensive thing the build does, and until
now every pass that needed lemmas paid for it separately: the word cloud
(``rebuild_session_word_counts``), and the lexical-diversity half of the speech
metrics (``rebuild_speech_metrics``). They read *the same rows* — every
non-procedural sentence of a sitting — and the metric's measurable subset is
98.7 % of the cloud's set, so the second pass was almost pure waste. On the Modal
backend it was also billed twice.

This module is where that work is kept once. :func:`app.nlp.analyze_all` produces
the cloud's tallies and the diversity lemma streams from a single pipeline pass;
the streams land here, and whoever needs lemmas next — the metrics pass today, a
future feature tomorrow — reads them instead of loading a model.

**Keyed by sentence id, not by position.** The passes disagree about order (the
cloud walks a sitting by sentence id, the metrics pass by speech then sentence)
and about scope (the metric drops speeches under its length floor). A positional
list would only be reusable by a consumer that happened to fetch its rows exactly
the way the writer did; a ``{sentence_id: [lemma, …]}`` map is reusable by any of
them, which is the point of a *shared* cache.

**Layout: one gzipped file per sitting**, ``<cache_dir>/lemma-cache/<id>.json.gz``.
The other loader caches are single JSON documents rewritten in full every ten
sittings, which is fine at their size but not at this one: the full corpus is
~6.2 M sentences and ~113 M tokens, so the streams are on the order of 1.5 GB of
JSON (~300-400 MB gzipped). Rewriting that on every flush would cost more than
the model time it saves. Per-sitting files make a write O(one sitting) and let a
reader touch only what it asks for.

**Validity** is per sitting: an entry records the fingerprint of the sitting's
whole non-procedural sentence set (ids + text) under the lemma method that
produced it (:func:`fingerprint`). Any change to the transcript, the model or the
diversity-lemma rule misses, and the entry is recomputed rather than trusted.
Note this is deliberately the *unfiltered* set even for a consumer that only
wants part of it, so both passes compute the same key for the same sitting.

A small in-process memo sits in front of the files so that an incremental
``--update`` run — where the cloud writes a sitting and the metrics pass reads it
back moments later — never round-trips the disk at all. It is bounded
(:data:`MEMO_SITTINGS`) because a full rebuild would otherwise accumulate the
entire corpus in RAM, which is exactly the 1.5 GB this store exists to keep *on
disk*.

The store is an optimisation and never a source of truth: every entry point
degrades to "no entry" on unreadable, truncated or corrupt data, and a caller
that gets ``None`` simply does the work itself.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
from collections import OrderedDict
from pathlib import Path

from .config import settings

logger = logging.getLogger(__name__)

# Bump when the *stored shape* changes in a way that makes existing files
# unreadable or wrong. It rides in the method tag, so a bump misses every entry
# rather than silently reinterpreting one.
_LOGIC_VERSION = 1

# How many sittings the in-process memo keeps. Enough that an incremental run
# (typically one to three sittings) is served entirely from memory, small enough
# that a 2 582-sitting rebuild never holds more than a few hundred MB of streams.
MEMO_SITTINGS: int = max(0, int(os.environ.get("PARLAMONITOR_LEMMA_MEMO") or 8))

# (cache dir, sid) -> (fingerprint, {sentence_id: [lemma, …]}), MRU last. The
# directory is part of the key because the fingerprint is content-addressed: two
# corpora holding the same sitting text under the same model produce the same
# fingerprint, and a memo keyed on the sitting alone would serve one build's
# streams to the other.
_memo: "OrderedDict[tuple[str, str], tuple[str, dict[int, list[str]]]]" = OrderedDict()


def _memo_key(base: str | Path, session_id: str) -> tuple[str, str]:
    return (str(Path(base)), str(session_id))


def enabled() -> bool:
    return bool(settings.lemma_cache)


def method_tag(model: str | None = None) -> str:
    """Identifies what produced a stream: the model, plus this module's shape.

    Distinct from ``nlp.method_tag`` on purpose — the word cloud's tag names its
    own extraction logic, which can change without changing a single lemma, and
    busting these streams for that would throw away the expensive half."""
    from . import nlp                       # local: keeps import order simple
    return f"lemmas:{nlp.method_tag(model)}:s{_LOGIC_VERSION}"


def fingerprint(method: str, rows) -> str:
    """Key for one sitting: its whole non-procedural sentence set under ``method``.

    ``rows`` is ``[(sentence_id, text), …]``; it is sorted here so that callers
    fetching in different orders (by sentence, by speech) still agree on the key."""
    h = hashlib.sha1(method.encode("utf-8"))
    for sid, text in sorted(rows, key=lambda r: r[0]):
        h.update(b"\x1f")
        h.update(str(sid).encode("utf-8"))
        h.update(b"\x1e")
        h.update((text or "").encode("utf-8"))
    return h.hexdigest()


def cache_dir(base: str | Path) -> Path:
    return Path(base) / "lemma-cache"


def _path(base: str | Path, session_id: str) -> Path:
    # Session ids are digit strings from the source data; keep the filename to a
    # safe basename regardless, so a surprising id can never escape the directory.
    safe = "".join(ch for ch in str(session_id) if ch.isalnum() or ch in "-_")
    return cache_dir(base) / f"{safe}.json.gz"


def _remember(base: str | Path, session_id: str, fp: str,
              lemmas: dict[int, list[str]]) -> None:
    if MEMO_SITTINGS <= 0:
        return
    key = _memo_key(base, session_id)
    _memo[key] = (fp, lemmas)
    _memo.move_to_end(key)
    while len(_memo) > MEMO_SITTINGS:
        _memo.popitem(last=False)


def get(base: str | Path | None, session_id: str,
        fp: str) -> dict[int, list[str]] | None:
    """The sitting's ``{sentence_id: [lemma, …]}``, or ``None`` for a miss.

    A miss is any of: the store switched off, no file, a stale fingerprint, or
    anything unreadable. The caller lemmatizes for itself in every one of those
    cases, so a damaged cache costs time and never correctness."""
    if not enabled() or base is None:
        return None
    key = _memo_key(base, session_id)
    hit = _memo.get(key)
    if hit is not None:
        if hit[0] == fp:
            _memo.move_to_end(key)
            return hit[1]
        # Text or method moved on; the memoized streams describe neither.
        _memo.pop(key, None)
    path = _path(base, session_id)
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            entry = json.load(fh)
    except (OSError, ValueError, EOFError) as exc:
        logger.warning("Could not read lemma cache %s (%s); recomputing", path, exc)
        return None
    if not isinstance(entry, dict) or entry.get("fp") != fp:
        return None
    raw = entry.get("lemmas")
    if not isinstance(raw, dict):
        return None
    try:
        lemmas = {int(k): list(v) for k, v in raw.items()}
    except (TypeError, ValueError):
        logger.warning("Malformed lemma cache %s; recomputing", path)
        return None
    _remember(base, session_id, fp, lemmas)
    return lemmas


def put(base: str | Path | None, session_id: str, fp: str,
        lemmas: dict[int, list[str]], *, model: str | None,
        method: str) -> None:
    """Store one sitting's streams. Best-effort: a failed write is logged and
    dropped, exactly like the other loader caches — the build must not die
    because a cache directory is read-only or a disk filled up.

    Written to a temporary file and renamed, so a run killed mid-write leaves the
    previous entry (or none) rather than a truncated file the next run has to
    detect and discard."""
    if not enabled() or base is None:
        return
    _remember(base, session_id, fp, lemmas)
    path = _path(base, session_id)
    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"fp": fp, "model": model, "method": method,
                   "lemmas": {str(k): v for k, v in lemmas.items()}}
        # mtime=0 so an unchanged sitting produces a byte-identical file.
        with gzip.GzipFile(tmp, "wb", mtime=0) as raw:
            raw.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("Could not write lemma cache %s (%s)", path, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def clear_memo() -> None:
    """Drop the in-process memo (tests, and long-running callers between builds)."""
    _memo.clear()
