"""Offload Whisper transcription to Modal (modal.com) — the default timing backend.

Forced-alignment sentence timing (:mod:`parlamonitor.whisper_align`) needs word-level
timestamps from an ASR model. Running whisper-large-v3-turbo on a GPU is the one
genuinely heavy step in the proceedings build, so this client hands each sitting's
whole-day recording to a Modal app (``whisper_modal_app.py``) that decodes the audio
and transcribes it on a warm GPU, returning ``[start, end, text]`` words. The host
only orchestrates and does the (cheap, offline) alignment.

Credit is kept low by design, mirroring the word-cloud NLP offload:

* the on-disk fingerprint cache (:func:`whisper_align.ensure_words`) means only
  sittings whose recording actually changed are ever sent — a normal incremental
  sync ships **one** day, a backfill ships each day once, and never again;
* days are dispatched with ``.map`` across a bounded pool of warm containers
  (``max_containers``), so a backfill runs many days in parallel while total GPU
  work — hence credit — tracks the actual audio transcribed;
* each container loads the model once (``@enter``), enables a VAD filter to skip
  silence/breaks, and the app scales to zero between runs, so an idle deployment
  costs nothing.

Auth uses the standard Modal env (``MODAL_TOKEN_ID`` / ``MODAL_TOKEN_SECRET``) or
``~/.modal.toml``. It is opt-in via ``PARLAMONITOR_TIMING_BACKEND`` (``auto`` picks it
when available; ``whisper-modal`` forces it).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from . import config

logger = logging.getLogger("parlamonitor.whisper_modal")

_SERVICE_CLASS = "WhisperService"


def _has_credentials() -> bool:
    if os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"):
        return True
    return (Path.home() / ".modal.toml").exists()


def available() -> bool:
    """Whether the Modal backend can be used: the client is importable and some
    credentials are present. (Does not spin up a container — the deployed app is
    trusted to exist; a genuine call failure surfaces loudly at run time.)"""
    try:
        import modal  # noqa: F401
    except Exception:
        logger.debug("timing backend modal unavailable: `modal` client not installed")
        return False
    if not _has_credentials():
        logger.debug("timing backend modal unavailable: no Modal credentials "
                     "(set MODAL_TOKEN_ID/MODAL_TOKEN_SECRET or run `modal token new`)")
        return False
    return True


def _service():
    import modal
    app = config.whisper_modal_app()
    try:
        cls = modal.Cls.from_name(app, _SERVICE_CLASS)      # modal >= 0.72
    except AttributeError:  # pragma: no cover - older client
        cls = modal.Cls.lookup(app, _SERVICE_CLASS)
    return cls()


def transcribe(misses, *, model: str, language: str):
    """Yield ``(session, words)`` for each ``(session, m3u8, playseq)`` miss, in
    input order, transcribing on Modal.

    Each day is one job; ``.map`` dispatches them across the deployed pool of warm
    GPU containers so a backfill runs in parallel, results returning in order.
    ``model`` is informational here — the container uses the model baked into its
    image at deploy time (kept in step via the shared ``PARLAMONITOR_WHISPER_MODEL``
    env)."""
    svc = _service()
    misses = list(misses)
    if not misses:
        return
    jobs = [{"m3u8": m3u8, "playseq": playseq, "language": language}
            for (_session, m3u8, playseq) in misses]
    logger.info("Modal Whisper: %d sitting(s) dispatched", len(jobs))
    # return_exceptions keeps one bad recording (a decode/ASR error on a single
    # sitting) from aborting a whole backfill batch — that day just gets no words
    # and falls back to the positional estimate (SCR-5).
    # Drive the loop off the .map() generator (not as zip's 2nd arg) so it drains
    # to StopIteration and closes in-task; as zip's 2nd arg it is left suspended
    # when `misses` is exhausted and Modal tears it down off-task ("aclose():
    # asynchronous generator is already running"). One result per job, in order.
    for i, words in enumerate(svc.transcribe.map(jobs, return_exceptions=True)):
        session = misses[i][0]
        if isinstance(words, BaseException):
            logger.warning("Modal transcription failed for %s (%s)", session, words)
            continue
        yield session, (words or [])
