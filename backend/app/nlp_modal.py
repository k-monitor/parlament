"""Offload the word-cloud NER/lemmatization to Modal (modal.com) — WCLOUD-6.

The HuSpaCy pipeline (tagger + lemmatizer + NER) is the one genuinely heavy step
in the build. On a small production host it dominates the build/update time. This
client hands that work to a Modal app running the **same** ``app.nlp`` logic on
Modal's workers (GPU or many CPUs), so the host only orchestrates.

Credit is kept low by design:

* the on-disk fingerprint cache (``loader.rebuild_session_word_counts``) means
  only sittings whose transcript actually changed are ever sent — a normal
  incremental update ships **one** sitting, a first build ships them once;
* sittings are packed into a few fat **batches** (``modal_batch_sentences``) and
  dispatched with ``.map`` so a bounded pool of warm containers (the deployed
  class caps ``max_containers``) chews through them in parallel — total CPU work,
  hence credit, is ~the same as one container, but wall-clock is far shorter;
* the model loads once per container (``@enter``) and the app scales to zero
  between runs, so an idle deployment costs nothing.

Because the Modal service runs the identical ``app.nlp`` module and the same
pinned model, its output — and therefore :func:`method_tag` — matches the local
``huspacy`` backend, so the disk cache and DB are interchangeable between them.

Auth uses the standard Modal env (``MODAL_TOKEN_ID`` / ``MODAL_TOKEN_SECRET``) or
``~/.modal.toml``. "modal" is opt-in via ``PARLAMONITOR_WORDCLOUD_BACKEND=modal``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from . import nlp
from .config import settings

logger = logging.getLogger("parlamonitor.nlp_modal")

_SERVICE_CLASS = "NlpService"


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
        logger.warning("wordcloud_backend=modal but the `modal` client is not "
                       "installed (pip install modal)")
        return False
    if not _has_credentials():
        logger.warning("wordcloud_backend=modal but no Modal credentials found "
                       "(set MODAL_TOKEN_ID/MODAL_TOKEN_SECRET or run `modal token new`)")
        return False
    return True


def method_tag(model: str | None = None) -> str:
    """Same tag as the local HuSpaCy backend, so the on-disk cache / DB built one
    way is reused by the other (identical model + logic → identical output)."""
    return nlp.method_tag(model)


def _service(app_name: str | None = None):
    import modal
    app = app_name or settings.modal_app_name
    try:
        cls = modal.Cls.from_name(app, _SERVICE_CLASS)      # modal >= 0.72
    except AttributeError:  # pragma: no cover - older client
        cls = modal.Cls.lookup(app, _SERVICE_CLASS)
    return cls()


def _chunks(misses, batch_sentences):
    """Group (sid, fp, texts) misses into batches of ~``batch_sentences`` sentences
    (a single oversized sitting forms its own batch)."""
    batch, n = [], 0
    for m in misses:
        batch.append(m)
        n += len(m[2])
        if n >= batch_sentences:
            yield batch
            batch, n = [], 0
    if batch:
        yield batch


def _words(result: dict) -> dict:
    ents = set(result.get("entities") or [])
    return {w: [c, "entity" if w in ents else "term"]
            for w, c in (result.get("counts") or {}).items()}


def extract(misses, *, batch_sentences: int | None = None,
            app_name: str | None = None):
    """Yield ``(sid, fp, words)`` for each miss (a ``(sid, fp, texts)`` triple),
    in order, running the HuSpaCy analysis on Modal. Batches are dispatched with
    ``.map`` so the deployed pool of warm containers processes them in parallel;
    results come back in input order. ``app_name`` picks the deployed service
    (default the primary one; the archive-model app for old cycles)."""
    batch_sentences = batch_sentences or settings.modal_batch_sentences
    svc = _service(app_name)
    chunks = list(_chunks(misses, batch_sentences))
    if not chunks:
        return
    payloads = [[texts for (_sid, _fp, texts) in chunk] for chunk in chunks]
    logger.info("Modal NLP (%s): %d sitting(s) in %d batch(es) (~%d sentences/batch)",
                app_name or settings.modal_app_name,
                sum(len(c) for c in chunks), len(chunks), batch_sentences)
    # Drive the loop off the .map() generator (not as zip's 2nd arg): zip stops
    # when `chunks` is exhausted and leaves the Modal generator suspended at its
    # final yield, which Modal then tears down off-task ("aclose(): asynchronous
    # generator is already running"). Iterating it directly drains it to
    # StopIteration so it closes cleanly. One result batch per input payload, so
    # its index lines up with `chunks`.
    for i, results in enumerate(svc.analyze_sessions.map(payloads)):
        for (sid, fp, _texts), result in zip(chunks[i], results):
            yield sid, fp, _words(result)


def extract_spans(misses, *, batch_sentences: int | None = None,
                  app_name: str | None = None):
    """Yield ``(sid, fp, per_sentence_spans)`` for each miss (a ``(sid, fp, texts)``
    triple), running HuSpaCy PERSON + ORGANISATION span extraction on Modal (NEL,
    §10). ``per_sentence_spans`` is a list — one entry per input sentence, in order
    — of ``[surface, start, end, key, kind]`` spans, exactly what
    ``app.nlp.entity_spans`` yields. Batched + ``.map``-dispatched like
    :func:`extract`; ``app_name`` picks the deployed service."""
    batch_sentences = batch_sentences or settings.modal_batch_sentences
    svc = _service(app_name)
    chunks = list(_chunks(misses, batch_sentences))
    if not chunks:
        return
    payloads = [[texts for (_sid, _fp, texts) in chunk] for chunk in chunks]
    logger.info("Modal NLP spans (%s): %d sitting(s) in %d batch(es)",
                app_name or settings.modal_app_name,
                sum(len(c) for c in chunks), len(chunks))
    # Drive the loop off the .map() generator so it drains to StopIteration and
    # closes in-task — see the note in extract() (zip would leave it suspended,
    # triggering "aclose(): asynchronous generator is already running").
    for i, spans_batch in enumerate(svc.analyze_sessions_spans.map(payloads)):
        for (sid, fp, _texts), spans in zip(chunks[i], spans_batch):
            yield sid, fp, spans
