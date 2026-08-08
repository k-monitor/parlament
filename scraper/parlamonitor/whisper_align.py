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

from . import config

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
# No real spoken word lasts this long. When Whisper fails to transcribe a passage
# (an off-mic exchange, laughter) it often STRETCHES the neighbouring word to span
# the gap — e.g. a 4.2s "következő" absorbing an untranscribed heckle. Such a word's
# recorded start is meaningless (it's mostly untranscribed audio), so we clamp its
# span to the last MAX_WORD_DUR seconds before its end and free the rest as a gap
# the unspoken/untranscribed sentences can be interpolated into.
MAX_WORD_DUR = 1.5
# A sentence's matched words are split into time-clusters wherever a gap this large
# opens between consecutive matches (after the clamp above). A stretched-word clamp
# turns an absorbed passage into exactly such a gap; the sentence is then anchored
# on its DENSEST cluster, so a stray early match (the spurious "A" before a stretched
# "következő") no longer drags the sentence's start back across the gap.
MAX_INTRA_GAP = 2.5

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

def _spoken_text(sentences: list[dict]) -> list[str]:
    """Per-sentence text with parenthetical content removed.

    Stage directions and heckles in the proceedings — "(Taps.)", "(Közbeszólás:
    …)", "(Derültség a Fidesz padsoraiban.)" — are editorial notes, NOT words the
    speaker utters, so they must not anchor the alignment: matching their tokens
    against the ASR hypothesis drags the timing of the real sentences around them.
    Open-paren state is carried ACROSS sentences because one parenthetical is
    often split over several sentences at its internal full stops (e.g. "(A
    teremben lévők közösen eléneklik a Himnuszt, ezt követően helyet foglalnak.)"
    becomes two sentences). No nesting occurs in the transcripts, so a boolean
    depth is enough. A fully-parenthetical sentence yields "" — it contributes no
    anchor and its span is later interpolated into the gap (:func:`_fill_gaps`)."""
    out: list[str] = []
    in_paren = False
    for s in sentences:
        kept: list[str] = []
        for ch in (s.get("text") or ""):
            if ch == "(":
                in_paren = True
            elif ch == ")":
                in_paren = False
            elif not in_paren:
                kept.append(ch)
        out.append("".join(kept))
    return out


def _flatten_ref(sentences: list[dict]) -> tuple[list[str], list[int]]:
    """Flatten the reference sentences to one token stream, remembering which
    sentence each token came from. Parenthetical stage directions are dropped (see
    :func:`_spoken_text`) so only actually-spoken words anchor the alignment."""
    toks: list[str] = []
    owner: list[int] = []
    for i, spoken in enumerate(_spoken_text(sentences)):
        for t in _norm_tokens(spoken):
            toks.append(t)
            owner.append(i)
    return toks, owner


def _cluster_span(times: list[tuple[float, float]]) -> tuple[float, float]:
    """Reduce a sentence's matched word ``(start, end)`` times to one ``[lo, hi]``.

    The times are in match order (⇒ time order). Where a gap larger than
    :data:`MAX_INTRA_GAP` opens between consecutive matches, the sentence is split
    into clusters; we anchor on the one with the most words and read ``lo``/``hi``
    off it. This drops a stray early match separated from the real run by a gap
    (which a clamped stretched word opens — see :data:`MAX_WORD_DUR`), so the
    sentence's start isn't dragged back across untranscribed audio."""
    clusters: list[list[tuple[float, float]]] = [[times[0]]]
    for prev, cur in zip(times, times[1:]):
        if cur[0] - prev[1] > MAX_INTRA_GAP:
            clusters.append([cur])
        else:
            clusters[-1].append(cur)
    best = max(clusters, key=len)
    return min(t[0] for t in best), max(t[1] for t in best)


def _fill_gaps(spans: list, start: float, end: float, weights: list[int]) -> None:
    """Give every sentence a ``[start, end]`` time. Sentences the ASR matched keep
    their measured span; unmatched runs between two matched anchors (or before the
    first / after the last) are distributed across the free interval by ``weights``
    (spoken-character length — so a non-spoken stage direction claims almost none of
    the gap), then the whole list is clamped monotonic inside ``[start, end]``.

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
        total = sum(weights[i:j])
        cursor, scale = lo, (hi - lo) / total if total else 0.0
        for k in range(i, j):
            w = weights[k] * scale
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
    spoken = _spoken_text(sentences)          # parentheticals dropped (not spoken)
    ref_tokens, owner = _flatten_ref(sentences)
    # Expand multi-token whisper "words" defensively (usually one token each) and
    # clamp implausibly long "words" (see MAX_WORD_DUR): an absorbed passage keeps
    # only its final MAX_WORD_DUR seconds, so the untranscribed audio before it is
    # freed and later opens an intra-sentence cluster gap.
    hyp_tokens: list[str] = []
    hyp_time: list[tuple[float, float]] = []
    for w in words or []:
        wt = _norm_tokens(w[2])
        if not wt:
            continue
        ws, we = float(w[0]), float(w[1])
        if we - ws > MAX_WORD_DUR:
            ws = we - MAX_WORD_DUR
        span = (ws, we)
        for t in wt:
            hyp_tokens.append(t)
            hyp_time.append(span)
    if not ref_tokens or not hyp_tokens:
        return None

    start, end = float(window[0]), float(window[1])
    # Per-sentence matched word times (in match ⇒ time order), reduced to a span by
    # clustering so a stray early match doesn't drag a sentence's start back.
    per_sent: list[list[tuple[float, float]]] = [[] for _ in sentences]
    matched = 0        # all matched ref tokens (monotonic optimal alignment)
    solid = 0          # matched tokens in runs of >=_MIN_RUN (trust signal)

    sm = SequenceMatcher(a=ref_tokens, b=hyp_tokens, autojunk=False)
    for a0, b0, size in sm.get_matching_blocks():
        if size >= _MIN_RUN:
            solid += size
        for k in range(size):
            per_sent[owner[a0 + k]].append(hyp_time[b0 + k])
            matched += 1

    # Trust the alignment if either side is well covered (see MIN_COVERAGE): the
    # transcript is mostly anchored, or the spoken words mostly land in solid runs.
    coverage = matched / len(ref_tokens)
    hyp_coverage = solid / len(hyp_tokens)
    if max(coverage, hyp_coverage) < MIN_COVERAGE:
        logger.debug("alignment coverage ref=%.2f hyp=%.2f below %.2f; falling back",
                     coverage, hyp_coverage, MIN_COVERAGE)
        return None

    spans: list = [_cluster_span(t) if t else None for t in per_sent]
    spans = [list(s) if s is not None else None for s in spans]
    # Interpolate the unmatched sentences weighted by SPOKEN length, so a stage
    # direction (no spoken words) barely occupies the gap and the real spoken lines
    # around it get the time.
    weights = [max(len(spoken[i].strip()), 1) for i in range(len(sentences))]
    _fill_gaps(spans, start, end, weights)
    return [(s, e) for s, e in spans], coverage


# --- transcription backends -----------------------------------------------
# The alignment above is pure and offline; producing the words is the heavy part.
# It runs on Modal (:mod:`parlamonitor.whisper_modal`, the intended default) or a
# local ``faster-whisper``, and every result is cached (:func:`save_words_cache`)
# so a re-run transcribes only genuinely new audio. Heavy deps are imported lazily
# so a clean checkout can still import this module for the alignment path.

_REFERER = {"Referer": "https://www.parlament.hu/web/guest/orszaggyulesi-naplo"}
_local_model = None    # cached faster-whisper model for the local backend
_local_device = "cpu"  # the device it was actually built on (see _get_local_model)


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


def _resolve_device(requested: str) -> str:
    """Turn ``auto`` into the device CTranslate2 will actually use.

    Resolving it here rather than passing ``auto`` straight to ``WhisperModel``
    is what lets the load be logged with a concrete device. CTranslate2 falls
    back to CPU in complete silence when it cannot see a GPU — the usual cause
    being the cuBLAS/cuDNN 9 shared libraries missing rather than the driver —
    and at roughly 20x the wall clock that reads as "slow" instead of
    "misconfigured", which is expensive to discover halfway through a backfill.
    """
    if requested != "auto":
        return requested
    try:
        import ctranslate2                          # lazy (a faster-whisper dep)
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception as exc:
        logger.debug("CTranslate2 device probe failed (%s); assuming CPU", exc)
        return "cpu"


def _get_local_model(model: str):
    global _local_model, _local_device
    if _local_model is None:
        from faster_whisper import WhisperModel      # lazy
        device = _resolve_device(config.whisper_device())
        compute_type = config.whisper_compute_type(device)
        logger.info("Loading faster-whisper %s on %s (compute_type=%s)",
                    model, device, compute_type)
        if device == "cpu":
            logger.warning("Local Whisper is running on the CPU — a cycle's "
                           "backfill will take days. Set "
                           "PARLAMONITOR_WHISPER_DEVICE=cuda to fail loudly "
                           "instead if a GPU was expected.")
        _local_model = WhisperModel(model, device=device, compute_type=compute_type)
        _local_device = device
    return _local_model


def transcribe_local(m3u8: str, playseq: str | None, *, model: str,
                     language: str) -> list[list]:
    """Transcribe one day's recording with a local ``faster-whisper`` model."""
    from faster_whisper import BatchedInferencePipeline     # lazy
    audio = ffmpeg_decode(m3u8, playseq=playseq)
    pipe = BatchedInferencePipeline(_get_local_model(model))
    segments, _info = pipe.transcribe(
        audio, language=language, word_timestamps=True, vad_filter=True,
        batch_size=config.whisper_batch_size(_local_device))
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


def _latest_local_cycle(paths) -> int | None:
    """The newest electoral cycle present in the data directory, read off the
    scraped file names. Used to scope the Modal offload when the caller doesn't
    know the upstream latest cycle: a backfill runs against a corpus that already
    holds the live cycle, so "newest on disk" is the same cycle "latest" means —
    and it costs no request."""
    sessions = [p.name[len("raw-"):-len("-day.json")]
                for p in paths.raw_plenary.glob("raw-*-day.json")]
    sessions += [p.name[:-len("-session.json")]
                 for p in paths.processed.glob("*-session.json")]
    cycles = [c for c in (config.session_cycle(s) for s in sessions) if c is not None]
    return max(cycles) if cycles else None


def in_modal_scope(paths, sessions, latest_cycle: int | None = None) -> set[str]:
    """The subset of ``sessions`` whose electoral cycle may be transcribed on Modal
    (``PARLAMONITOR_MODAL_CYCLES``, default: the newest cycle only).

    ``latest_cycle`` is the authoritative upstream cycle number when the caller
    knows it (the sync does); otherwise the newest cycle on disk stands in. A
    session id that names no cycle stays in scope — it can only be a live day."""
    spec = config.modal_cycles()
    if spec == "all":
        return set(sessions)
    if isinstance(spec, frozenset):
        allowed = spec
    else:
        latest = latest_cycle if latest_cycle is not None else _latest_local_cycle(paths)
        if latest is None:
            return set(sessions)
        allowed = frozenset({latest})
    return {s for s in sessions
            if config.session_cycle(s) is None or config.session_cycle(s) in allowed}


def ensure_words(paths, days: list[tuple[str, str | None, str | None]], *,
                 backend: str, model: str, language: str,
                 force: bool = False,
                 latest_cycle: int | None = None) -> dict[str, list]:
    """Return ``{session: words}`` for every day whose recording could be
    transcribed, populating the on-disk cache as a side effect.

    ``days`` is a list of ``(session, m3u8, playseq)``. Cached days are returned for
    free; cache-miss days are sent to the resolved backend — batched and run in
    parallel on Modal — and their results cached. A day that fails (no recording,
    ASR error) is simply omitted, so the timing stage falls back to the positional
    estimate for it (SCR-5). Returns ``{}`` immediately when the backend resolves to
    ``character``.

    Transcribing on **Modal** costs metered GPU time, so it is scoped to the cycles
    ``PARLAMONITOR_MODAL_CYCLES`` allows (default: the newest one — see
    :func:`config.modal_cycles`). Out-of-scope days are never dispatched and fall
    back to the positional estimate; their *cached* words are still returned, since
    reading the cache costs nothing. ``latest_cycle`` lets a caller that already
    knows the upstream latest cycle pin the scope instead of inferring it from the
    data directory."""
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

    if misses and resolved == "whisper-modal":
        scope = in_modal_scope(paths, [s for (s, _m, _p) in misses], latest_cycle)
        skipped = [s for (s, _m, _p) in misses if s not in scope]
        if skipped:
            logger.info("Skipping Whisper on Modal for %d sitting(s) outside the "
                        "Modal cycle scope (%s): %s%s — their sentence timing uses "
                        "the positional estimate",
                        len(skipped), config.modal_cycles(),
                        ", ".join(sorted(skipped)[:10]),
                        "…" if len(skipped) > 10 else "")
        misses = [m for m in misses if m[0] in scope]

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
