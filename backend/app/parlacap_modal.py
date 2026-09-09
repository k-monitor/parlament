"""Offload CAP topic classification to Modal (modal.com) — TOPIC-7.

The classifier is XLM-R-large: a 2.2 GB checkpoint that wants a GPU. The
production host has neither the disk for the torch stack nor the cores to run it
(measured: ~1.5 blocks/s on four CPU threads, so one sitting day would occupy the
box for eleven minutes). This client hands the work to a Modal app running the
**same** ``app.parlacap`` logic on Modal's GPU workers, so the host only
orchestrates — the same division as the word cloud's ``app/nlp_modal.py``.

Credit is kept low by design, and the design matters more here than it does for
the word cloud, because a full-archive backfill is 600k blocks:

* the on-disk fingerprint cache means only sittings whose text actually changed
  are sent — a normal incremental update ships **one** sitting;
* ``PARLAMONITOR_MODAL_CYCLES`` (default ``latest``) keeps everything but the
  newest cycle away from Modal entirely, so the archive can only ever be
  classified on a GPU box whose cache is then copied in (DEPLOYMENT.md). This is
  the guard that makes the whole arrangement affordable: the ongoing cost is a
  few sitting days a week, never the corpus;
* sittings are packed into batches (``parlacap_modal_batch``) and dispatched with
  ``.map`` so a bounded pool of warm containers works through them in parallel;
* the model loads once per container and the app scales to zero between runs.

Because the service runs the identical module and the same pinned model, its
output — and therefore ``parlacap.method_tag()`` — matches the local backend, so
the disk cache and DB are interchangeable between them. The backend is
deliberately **not** part of the method tag: where a sitting was classified is
not a property of the prediction.

Auth uses the standard Modal env (``MODAL_TOKEN_ID`` / ``MODAL_TOKEN_SECRET``) or
``~/.modal.toml``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .config import settings

logger = logging.getLogger("parlamonitor.parlacap_modal")

_SERVICE_CLASS = "ParlaCapService"


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
        logger.warning("parlacap_backend=modal but the `modal` client is not "
                       "installed (pip install modal)")
        return False
    if not _has_credentials():
        logger.warning("parlacap_backend=modal but no Modal credentials found "
                       "(set MODAL_TOKEN_ID/MODAL_TOKEN_SECRET or run "
                       "`modal token new`)")
        return False
    return True


def _service(app_name: str | None = None):
    import modal
    name = app_name or settings.parlacap_modal_app
    try:
        cls = modal.Cls.from_name(name, _SERVICE_CLASS)      # modal >= 0.72
    except AttributeError:  # pragma: no cover - older client
        cls = modal.Cls.lookup(name, _SERVICE_CLASS)
    return cls()


def _chunks(misses, batch_blocks: int):
    """Group ``(sid, fp, texts)`` misses into batches of ~``batch_blocks`` blocks
    (a single oversized sitting forms its own batch)."""
    batch, n = [], 0
    for m in misses:
        batch.append(m)
        n += len(m[2])
        if n >= batch_blocks:
            yield batch
            batch, n = [], 0
    if batch:
        yield batch


def classify(misses, *, batch_blocks: int | None = None,
             app_name: str | None = None):
    """Yield ``(sid, fp, predictions)`` for each miss (a ``(sid, fp, texts)``
    triple), in order, running the classification on Modal.

    ``predictions`` is one entry per text — ``[label_idx, score, runner_idx,
    runner_score]`` or ``None`` for a block under the minimum length — the same
    shape ``parlacap.classify`` returns locally, so the caller stores it either
    way without caring where it came from.

    The service's method tag is checked once before anything is stored. The tag
    is what the on-disk cache is keyed by, so a deployment running a different
    model or block policy than this host believes would file its predictions
    under this host's tag, and they would then never be recomputed — a silent,
    permanent mislabelling. Cheaper to refuse: a mismatch raises, the pass falls
    back to leaving those sittings unlabelled, and the operator redeploys.
    """
    from . import parlacap

    batch_blocks = batch_blocks or settings.parlacap_modal_batch
    svc = _service(app_name)
    chunks = list(_chunks(misses, batch_blocks))
    if not chunks:
        return

    expected = parlacap.method_tag()
    remote = svc.method_tag.remote()
    if remote != expected:
        raise RuntimeError(
            f"Modal ParlaCAP service reports method '{remote}' but this host "
            f"expects '{expected}'. Redeploy parlacap_modal_app.py (or align the "
            "PARLAMONITOR_PARLACAP_* settings); refusing to cache predictions "
            "under a tag that does not describe them.")

    payloads = [[texts for (_sid, _fp, texts) in chunk] for chunk in chunks]
    logger.info("Modal ParlaCAP (%s): %d sitting(s) in %d batch(es) "
                "(~%d blocks/batch)", app_name or settings.parlacap_modal_app,
                sum(len(c) for c in chunks), len(chunks), batch_blocks)

    # Drive the loop off the .map() generator rather than as zip's second
    # argument: zip stops when `chunks` is exhausted and leaves the Modal
    # generator suspended at its final yield, which Modal then tears down
    # off-task. Iterating it directly drains it to StopIteration so it closes
    # cleanly. One result batch per input payload, so indexes line up.
    for i, results in enumerate(svc.classify_batches.map(payloads)):
        for (sid, fp, _texts), preds in zip(chunks[i], results):
            yield sid, fp, preds
