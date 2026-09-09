#!/usr/bin/env python3
"""Modal (modal.com) service that runs the CAP topic classifier off-box (TOPIC-7).

ParlaCAP is XLM-R-large — 560M parameters and a 2.2 GB checkpoint — which is
exactly the shape of work a small production host cannot do: measured on this
corpus it runs at ~1.5 blocks/s on four CPU threads, so one sitting day (~1000
blocks) would peg the box for eleven minutes while it is also serving the site.
On a T4 the same day is seconds.

So this deploys the classifier to Modal, where warm workers take the sittings the
``sync`` pass has just loaded and then scale to zero. The host selects it with
``PARLAMONITOR_PARLACAP_BACKEND=modal`` (client: ``app/parlacap_modal.py``).

It is a **separate app** from ``modal_app.py`` (the HuSpaCy word-cloud service)
rather than another class in it, for the same reason ``scraper/whisper_modal_app.py``
is separate: the images share nothing. This one is torch + transformers with an
XLM-R checkpoint baked in; that one is spaCy with a HuSpaCy model. Fusing them
would make every word-cloud container pull 2.2 GB of classifier it never loads.

It runs the **same** ``app.parlacap`` module as the local backend, so its output —
and the disk cache keyed on ``parlacap.method_tag()`` — is identical and
interchangeable: a corpus classified locally and a sitting classified on Modal
live in the same ``parlacap-cache.json`` and neither invalidates the other.

Deploy (from the ``backend/`` directory, so the local ``app`` package is found):

    pip install modal
    modal token new                       # once, to authenticate
    modal deploy parlacap_modal_app.py    # bakes the model into the image

Smoke-test the deployed service:

    modal run parlacap_modal_app.py

Cost control: the model loads once per container (``@enter``), containers are
capped (``PARLAMONITOR_PARLACAP_MODAL_MAX_CONTAINERS``) and shut down after
``scaledown_window`` idle, so an idle deployment costs nothing. The client only
ever sends cache-miss sittings, and only those in the cycles
``PARLAMONITOR_MODAL_CYCLES`` allows (default: the newest) — so a full-archive
backfill never reaches Modal at all; that is what the GPU box and a copied cache
file are for.

Tunables (env at *deploy* time):
  PARLAMONITOR_PARLACAP_MODAL_APP             app name (must match the client)
                                              [parlamonitor-parlacap]
  PARLAMONITOR_PARLACAP_MODEL                 model to bake in
                                              [classla/ParlaCAP-Topic-Classifier]
  PARLAMONITOR_PARLACAP_MODAL_GPU             GPU type; EMPTY for CPU       [T4]
  PARLAMONITOR_PARLACAP_MODAL_CPU             cores per container           [2.0]
  PARLAMONITOR_PARLACAP_MODAL_MEMORY          MiB of RAM per container      [8192]
  PARLAMONITOR_PARLACAP_MODAL_MAX_CONTAINERS  parallelism / credit ceiling  [4]
"""

from __future__ import annotations

import os

import modal

APP_NAME = os.environ.get("PARLAMONITOR_PARLACAP_MODAL_APP",
                          "parlamonitor-parlacap").strip()
MODEL = os.environ.get("PARLAMONITOR_PARLACAP_MODEL",
                       "classla/ParlaCAP-Topic-Classifier").strip()
GPU = os.environ.get("PARLAMONITOR_PARLACAP_MODAL_GPU", "T4").strip() or None
CPU = float(os.environ.get("PARLAMONITOR_PARLACAP_MODAL_CPU", "2.0"))
MEMORY = int(os.environ.get("PARLAMONITOR_PARLACAP_MODAL_MEMORY", "8192"))
MAX_CONTAINERS = int(
    os.environ.get("PARLAMONITOR_PARLACAP_MODAL_MAX_CONTAINERS", "4"))

app = modal.App(APP_NAME)

image = (
    modal.Image.debian_slim(python_version="3.12")
    # transformers>=4.40 for the tokenizer/model APIs `app.parlacap` uses; torch
    # is the CUDA build Modal's GPU workers expect. Both are pinned so a rebuild
    # of the image cannot silently change what the model outputs — the cache is
    # keyed on the *method*, not on the library versions, so a change here that
    # moved predictions would go unnoticed.
    .pip_install("torch==2.6.0", "transformers==4.49.0", "sentencepiece==0.2.0")
    .run_commands(
        # Bake the checkpoint into the image so a cold container does not spend a
        # minute pulling 2.2 GB from the Hub before it can answer.
        "python -c \""
        "from transformers import AutoModelForSequenceClassification, AutoTokenizer; "
        f"AutoTokenizer.from_pretrained('{MODEL}'); "
        f"AutoModelForSequenceClassification.from_pretrained('{MODEL}')\""
    )
    .env({
        "PARLAMONITOR_PARLACAP_MODEL": MODEL,
        # The container IS the local backend — without this the service would ask
        # itself to dispatch to Modal and recurse.
        "PARLAMONITOR_PARLACAP_BACKEND": "local",
        "PARLAMONITOR_PARLACAP_DEVICE": "cuda" if GPU else "cpu",
        "PARLAMONITOR_PARLACAP_FP16": "1" if GPU else "0",
        # A GPU container can afford a fatter batch than the default tuned for a
        # 12 GB desktop card; length-sorted batching keeps padding waste low.
        "PARLAMONITOR_PARLACAP_BATCH_SIZE": "64" if GPU else "8",
    })
    # The service runs the project's own classification logic, so its output
    # matches the local backend exactly. Ship the backend `app` package in.
    .add_local_python_source("app")
)


@app.cls(
    image=image,
    gpu=GPU,
    cpu=CPU,
    memory=MEMORY,
    max_containers=MAX_CONTAINERS,
    scaledown_window=60,   # scale to zero ~1 min after the last call (no idle cost)
    timeout=3600,
)
class ParlaCapService:
    @modal.enter()
    def _load(self):
        """Warm the model once per container; reused across every batch it handles."""
        from app import parlacap
        parlacap._load()

    @modal.method()
    def classify_batches(self, batches: list[list[str]]) -> list[list]:
        """Classify a batch of sittings' blocks.

        ``batches`` is a list of per-sitting block-text lists; returns, per
        sitting, one entry per block — ``[label_idx, score, runner_idx,
        runner_score]``, or ``None`` for a block under the minimum length —
        exactly what ``app.parlacap.classify`` produces locally. Tuples are
        returned as lists because they cross a JSON boundary; the client treats
        them positionally either way.
        """
        from app import parlacap
        return [[list(p) if p is not None else None
                 for p in parlacap.classify(texts)]
                for texts in batches]

    @modal.method()
    def method_tag(self) -> str:
        """The deployed service's method tag. The client checks this against its
        own before trusting a result: the tag is what the on-disk cache is keyed
        by, so a service running a different model or block policy than the host
        believes would file its predictions under the host's tag and they would
        never be recomputed."""
        from app import parlacap
        return parlacap.method_tag()


@app.local_entrypoint()
def main():
    """Smoke test: `modal run parlacap_modal_app.py`."""
    svc = ParlaCapService()
    print("method tag:", svc.method_tag.remote())
    texts = [
        "A kórházak finanszírozása és az orvosok bérezése régóta megoldatlan "
        "kérdés, a betegellátás színvonala pedig folyamatosan romlik emiatt.",
        "Az iskolai tankönyvellátás és a pedagógusok béremelése a következő "
        "tanév legfontosabb kérdése lesz a köznevelésben.",
    ]
    from app import parlacap
    for text, pred in zip(texts, svc.classify_batches.remote([texts])[0]):
        if pred is None:
            print("(too short)", text[:60])
        else:
            print(f"{parlacap.LABELS[pred[0]]:<24}{pred[1]:.3f}   {text[:60]}…")
