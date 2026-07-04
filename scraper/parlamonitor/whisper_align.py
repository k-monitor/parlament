"""Sentence↔video timing by **Whisper forced alignment** (the default method).

The character-proportional estimate (:mod:`parlamonitor.timing`) only knows *where*
a speech starts and ends in the day stream and spreads its sentences across that
window by text length. It is right to the neighbourhood of a passage, not the word
(TIM-3). This module makes the timing sentence-accurate: an ASR model
(whisper-large-v3-turbo) transcribes the recording and yields **word-level
timestamps**; we then align that time-stamped hypothesis against the *authoritative*
proceedings text and read off each official sentence's real ``[timeStart, timeEnd]``.

The design keeps three concerns separate so the expensive part is cached and the
cheap part stays offline/testable:

* **transcription** (heavy, GPU) — produced once per day by a backend
  (:mod:`parlamonitor.whisper_modal` on Modal, or local ``faster-whisper``) and
  cached on disk keyed by the recording URL + model, so a re-run never re-transcribes
  an unchanged sitting (mirrors the word-cloud NLP cache);
* **alignment** (cheap, pure Python) — :func:`align_speech` matches the reference
  sentences to the cached words with a token :class:`difflib.SequenceMatcher`; it is
  deterministic and unit-tested with no audio, no GPU, no network;
* **timing** (:mod:`parlamonitor.timing`) — consumes the alignment and stamps the
  sentences, falling back to the character estimate per-speech when no usable words
  exist, so the pipeline degrades gracefully (SCR-5/SCR-6).

All times are **day-absolute seconds into the served whole-day HLS stream** (TIM-2):
Whisper decodes exactly that stream, whose ``t=0`` is the same origin the stored
per-speech ``media.videoStart``/``videoEnd`` are measured from, so the coordinates
line up with zero extra offset math.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
from difflib import SequenceMatcher
from pathlib import Path

logger = logging.getLogger(__name__)

WHISPER_METHOD = "whisper-forced-alignment"
# Sentence boundaries come from real spoken word timings, so this is the most
# trustworthy method; still an alignment (not a hand transcript), hence < 1.0.
WHISPER_CONFIDENCE = 0.98

# Trust threshold for an alignment. It is met when EITHER most of the transcript
# was anchored to spoken words (ref coverage) OR most of the spoken words landed in
# solid runs of the transcript (hyp coverage). The two-sided test matters because a
# speech's window can legitimately hold far more transcript than audio — a
# ceremonial sitting is mostly anthem/applause (VAD-skipped), and the transcript is
# full of "(Taps.)" stage directions — yet the words that WERE spoken still align
# perfectly and should be used (the unspoken lines are then interpolated). Only when
# both sides are thin (wrong window, heavy paraphrase) is the alignment untrusted
# and the caller falls back to the character estimate for that speech.
MIN_COVERAGE = 0.30
# A "solid" match run is ≥2 consecutive tokens; single-token matches (common
# Hungarian function words: a, az, és, hogy…) also occur in a *wrong* alignment, so
# they don't count toward the hyp-coverage trust signal.
_MIN_RUN = 2

# `\w` is Unicode-aware in Python, so this keeps Hungarian accented letters
# (á é í ó ö ő ú ü ű) and digits while dropping punctuation/whitespace.
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _norm_tokens(text: str) -> list[str]:
    """Lower-cased, punctuation-free tokens used for matching only (never shown)."""
    return _TOKEN_RE.findall((text or "").lower())


# --- cache ----------------------------------------------------------------

def cache_fingerprint(m3u8: str | None, model_tag: str) -> str:
    """Identity of a day's transcription: the recording URL (its smil offsets change
    if the recording is re-cut) plus the model tag. A hit means "same audio, same
    model → reuse the words, run no GPU"."""
    h = hashlib.sha1()
    h.update((m3u8 or "").encode("utf-8"))
    h.update(b"\x00")
    h.update((model_tag or "").encode("utf-8"))
    return h.hexdigest()


def load_words_cache(path: Path, *, m3u8: str | None = None,
                     model_tag: str | None = None) -> list[list] | None:
    """Return the cached ``[[start, end, text], ...]`` word list for a day, or None.

    If ``m3u8``/``model_tag`` are given, a cache whose fingerprint no longer matches
    (recording re-cut, or model changed) is treated as a miss so stale words are
    never aligned."""
    try:
        blob = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    if m3u8 is not None and model_tag is not None:
        if blob.get("fingerprint") != cache_fingerprint(m3u8, model_tag):
            return None
    words = blob.get("words")
    return words if isinstance(words, list) else None


def save_words_cache(path: Path, *, m3u8: str | None, model_tag: str,
                     words: list) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = {
        "model": model_tag,
        "m3u8": m3u8,
        "fingerprint": cache_fingerprint(m3u8, model_tag),
        "wordCount": len(words),
        "words": words,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(blob, ensure_ascii=False))
    tmp.replace(path)


def words_in_window(words: list, start: float, end: float) -> list:
    """The subset of a day's ``[start, end, text]`` words spoken inside a speech's
    ``[start, end]`` day-stream window (matched on each word's midpoint, so a word
    straddling a boundary lands on the side it mostly belongs to)."""
    out = []
    for w in words:
        if len(w) < 3:
            continue
        ws, we = float(w[0]), float(w[1])
        mid = (ws + we) / 2.0
        if start <= mid <= end:
            out.append(w)
    return out


# --- alignment ------------------------------------------------------------

def _flatten_ref(sentences: list[dict]) -> tuple[list[str], list[int]]:
    """Flatten the reference sentences to one token stream, remembering which
    sentence each token came from."""
    toks: list[str] = []
    owner: list[int] = []
    for i, s in enumerate(sentences):
        for t in _norm_tokens(s.get("text", "")):
            toks.append(t)
            owner.append(i)
    return toks, owner


def _char_weight(sent: dict) -> int:
    return max(len((sent.get("text") or "").strip()), 1)


def _fill_gaps(spans: list, start: float, end: float, sentences: list[dict]) -> None:
    """Give every sentence a ``[start, end]`` time. Sentences the ASR matched keep
    their measured span; unmatched runs between two matched anchors (or before the
    first / after the last) are distributed across the free interval by character
    length, then the whole list is clamped monotonic inside ``[start, end]``.

    ``spans[i]`` is ``[s, e]`` for a matched sentence or ``None`` for an unmatched
    one; mutated in place to contain no ``None``."""
    n = len(spans)
    i = 0
    while i < n:
        if spans[i] is not None:
            i += 1
            continue
        j = i
        while j < n and spans[j] is None:
            j += 1
        # Free interval [lo, hi] the run [i, j) must be spread across.
        lo = spans[i - 1][1] if i > 0 else start
        hi = spans[j][0] if j < n else end
        if hi < lo:
            hi = lo
        run = sentences[i:j]
        total = sum(_char_weight(s) for s in run)
        cursor, scale = lo, (hi - lo) / total if total else 0.0
        for k in range(i, j):
            w = _char_weight(sentences[k]) * scale
            spans[k] = [cursor, min(cursor + w, hi)]
            cursor += w
        i = j

    # Clamp inside the window and enforce non-decreasing, non-empty spans.
    prev = start
    for k in range(n):
        s, e = spans[k]
        s = min(max(s, prev), end)
        e = min(max(e, s), end)
        spans[k] = [round(s, 3), round(e, 3)]
        prev = s


def align_speech(sentences: list[dict], words: list, window: tuple[float, float]
                 ) -> tuple[list[tuple[float, float]], float] | None:
    """Align one speech's reference ``sentences`` to its Whisper ``words`` (each
    ``[start, end, text]``, day-absolute) within its ``window`` = ``(start, end)``.

    Returns ``(spans, coverage)`` where ``spans[i]`` is the ``(timeStart, timeEnd)``
    for ``sentences[i]`` and ``coverage`` is the fraction of reference tokens the ASR
    matched. Returns ``None`` when there is nothing to align or coverage is below
    :data:`MIN_COVERAGE` (the caller then uses the character estimate for the speech).
    """
    if not sentences:
        return None
    ref_tokens, owner = _flatten_ref(sentences)
    # Expand multi-token whisper "words" defensively (usually one token each).
    hyp_tokens: list[str] = []
    hyp_time: list[tuple[float, float]] = []
    for w in words or []:
        wt = _norm_tokens(w[2])
        if not wt:
            continue
        span = (float(w[0]), float(w[1]))
        for t in wt:
            hyp_tokens.append(t)
            hyp_time.append(span)
    if not ref_tokens or not hyp_tokens:
        return None

    start, end = float(window[0]), float(window[1])
    # Per-sentence accumulator of matched word times.
    lo: list[float | None] = [None] * len(sentences)
    hi: list[float | None] = [None] * len(sentences)
    matched = 0        # all matched ref tokens (monotonic optimal alignment)
    solid = 0          # matched tokens in runs of >=_MIN_RUN (trust signal)

    sm = SequenceMatcher(a=ref_tokens, b=hyp_tokens, autojunk=False)
    for a0, b0, size in sm.get_matching_blocks():
        if size >= _MIN_RUN:
            solid += size
        for k in range(size):
            si = owner[a0 + k]
            ws, we = hyp_time[b0 + k]
            if lo[si] is None or ws < lo[si]:
                lo[si] = ws
            if hi[si] is None or we > hi[si]:
                hi[si] = we
            matched += 1

    # Trust the alignment if either side is well covered (see MIN_COVERAGE): the
    # transcript is mostly anchored, or the spoken words mostly land in solid runs.
    coverage = matched / len(ref_tokens)
    hyp_coverage = solid / len(hyp_tokens)
    if max(coverage, hyp_coverage) < MIN_COVERAGE:
        logger.debug("alignment coverage ref=%.2f hyp=%.2f below %.2f; falling back",
                     coverage, hyp_coverage, MIN_COVERAGE)
        return None

    spans: list = [None if lo[i] is None else [lo[i], hi[i]]
                   for i in range(len(sentences))]
    _fill_gaps(spans, start, end, sentences)
    return [(s, e) for s, e in spans], coverage


# --- transcription backends -----------------------------------------------
# The alignment above is pure and offline; producing the words is the heavy part.
# It runs on Modal (:mod:`parlamonitor.whisper_modal`, the intended default) or a
# local ``faster-whisper``, and every result is cached (:func:`save_words_cache`)
# so a re-run transcribes only genuinely new audio. Heavy deps are imported lazily
# so a clean checkout can still import this module for the alignment path.

_REFERER = {"Referer": "https://www.parlament.hu/web/guest/orszaggyulesi-naplo"}
_local_model = None    # cached faster-whisper model for the local backend


def method_tag(model: str) -> str:
    """Cache identity of a transcription method — the model id. Host and Modal
    compute it identically (both from the configured model), so a cache built one
    way is reused by the other, exactly like the word-cloud NLP tag."""
    return f"whisper:{model}:v1"


def ffmpeg_decode(url: str, *, playseq: str | None = None, referer: bool = True):
    """Decode an HLS ``url`` to a 16 kHz mono float32 numpy array via ffmpeg.

    Shared by the local backend and the Modal service. ``playseq`` (the day
    recording's ``playseq.php`` activation URL) is pinged first when given, because
    an on-demand VOD's playlist can 404 until its stream is activated (the same
    dance the web player does). Raises on failure — the caller degrades to the
    positional estimate."""
    import numpy as np  # lazy: only when actually transcribing

    if playseq:
        try:
            import urllib.request
            req = urllib.request.Request(playseq, headers=_REFERER if referer else {})
            urllib.request.urlopen(req, timeout=60).read()
        except Exception as e:      # activation is best-effort
            logger.debug("playseq activation ping failed (%s)", e)

    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error"]
    if referer:
        cmd += ["-headers", f"Referer: {_REFERER['Referer']}\r\n"]
    cmd += ["-i", url, "-f", "s16le", "-ac", "1", "-ar", "16000", "-"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.decode('utf-8', 'replace')[:400]}")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def segments_to_words(segments) -> list[list]:
    """Flatten faster-whisper segments to ``[[start, end, text], ...]`` words.

    Shared shape between the local and Modal backends and what the cache stores."""
    words: list[list] = []
    for seg in segments:
        for w in (getattr(seg, "words", None) or []):
            if w.start is None or w.end is None:
                continue
            words.append([round(float(w.start), 3), round(float(w.end), 3),
                          (w.word or "").strip()])
    return words


def _get_local_model(model: str):
    global _local_model
    if _local_model is None:
        from faster_whisper import WhisperModel      # lazy
        # int8 keeps it light on CPU and cheap on GPU; auto device picks CUDA if present.
        _local_model = WhisperModel(model, device="auto", compute_type="int8")
    return _local_model


def transcribe_local(m3u8: str, playseq: str | None, *, model: str,
                     language: str) -> list[list]:
    """Transcribe one day's recording with a local ``faster-whisper`` model."""
    from faster_whisper import BatchedInferencePipeline     # lazy
    audio = ffmpeg_decode(m3u8, playseq=playseq)
    pipe = BatchedInferencePipeline(_get_local_model(model))
    segments, _info = pipe.transcribe(
        audio, language=language, word_timestamps=True,
        vad_filter=True, batch_size=8)
    return segments_to_words(segments)


def _local_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        return False
    return shutil.which("ffmpeg") is not None


def resolve_backend(name: str) -> str:
    """Turn ``auto`` into the concrete backend actually available on this host,
    or ``character`` if none is (graceful degradation, SCR-6). An explicit backend
    name is honoured as-is so a misconfiguration surfaces instead of silently
    downgrading."""
    name = (name or "auto").strip().lower()
    if name != "auto":
        return name
    from . import whisper_modal
    if whisper_modal.available():
        return "whisper-modal"
    if _local_available():
        return "whisper-local"
    logger.info("No Whisper backend available; sentence timing uses the "
                "positional character estimate")
    return "character"


def ensure_words(paths, days: list[tuple[str, str | None, str | None]], *,
                 backend: str, model: str, language: str,
                 force: bool = False) -> dict[str, list]:
    """Return ``{session: words}`` for every day whose recording could be
    transcribed, populating the on-disk cache as a side effect.

    ``days`` is a list of ``(session, m3u8, playseq)``. Cached days are returned for
    free; cache-miss days are sent to the resolved backend — batched and run in
    parallel on Modal — and their results cached. A day that fails (no recording,
    ASR error) is simply omitted, so the timing stage falls back to the positional
    estimate for it (SCR-5). Returns ``{}`` immediately when the backend resolves to
    ``character``."""
    resolved = resolve_backend(backend)
    if resolved == "character":
        return {}
    tag = method_tag(model)
    out: dict[str, list] = {}
    misses: list[tuple[str, str, str | None]] = []
    for session, m3u8, playseq in days:
        if not m3u8:
            continue
        if not force:
            cached = load_words_cache(paths.whisper_cache(session),
                                      m3u8=m3u8, model_tag=tag)
            if cached is not None:
                out[session] = cached
                continue
        misses.append((session, m3u8, playseq))

    if not misses:
        return out

    logger.info("Transcribing %d sitting(s) with %s (%s)", len(misses), resolved, tag)
    m3u8_by_session = {s: m for s, m, _ in misses}
    for session, words in _run_backend(resolved, misses, model=model, language=language):
        save_words_cache(paths.whisper_cache(session), m3u8=m3u8_by_session.get(session),
                         model_tag=tag, words=words)
        out[session] = words
    return out


def _run_backend(resolved: str, misses: list, *, model: str, language: str):
    """Yield ``(session, words)`` for each miss, running the chosen backend. Errors
    on one day are isolated (logged, skipped) so one bad recording never sinks the
    whole run (SCR-5)."""
    if resolved == "whisper-modal":
        from . import whisper_modal
        yield from whisper_modal.transcribe(misses, model=model, language=language)
        return
    if resolved == "whisper-local":
        for session, m3u8, playseq in misses:
            try:
                yield session, transcribe_local(m3u8, playseq, model=model,
                                                 language=language)
            except Exception as e:
                logger.warning("Local transcription failed for %s (%s)", session, e)
        return
    raise ValueError(f"unknown timing backend: {resolved!r}")
