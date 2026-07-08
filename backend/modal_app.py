"""Modal (modal.com) service that runs the word-cloud NER/lemmatization off-box.

The HuSpaCy pipeline is the one heavy step in the DB build (WCLOUD-6). On a small
production host it dominates build/update time, so this deploys it to Modal, where
a bounded pool of warm workers chews through the sittings in parallel and then
scales to zero. The host selects it with ``PARLAMONITOR_WORDCLOUD_BACKEND=modal``
(client: ``app/nlp_modal.py``).

It runs the **same** ``app.nlp`` module and the same pinned model as the local
``huspacy`` backend, so its output — and the disk/DB cache keyed on it — is
identical and interchangeable between local and Modal runs.

Deploy (run from the ``backend/`` directory so the local ``app`` package is found):

    pip install modal
    modal token new                 # once, to authenticate
    modal deploy modal_app.py       # builds the image (bakes in the model), deploys

Smoke-test the deployed service:

    modal run modal_app.py

Cost control: the model loads once per container (``@enter``); containers are
capped (``PARLAMONITOR_MODAL_MAX_CONTAINERS``) and shut down after
``scaledown_window`` idle, so an idle deployment costs nothing and a run's credit
tracks the actual sentences processed. The client only ever sends cache-miss
sittings, batched.

Tunables (env at *deploy* time):
  PARLAMONITOR_MODAL_APP             app name (must match the client)  [parlamonitor-nlp]
  PARLAMONITOR_HUSPACY_MODEL         HuSpaCy model to bake in          [hu_core_news_md]
  PARLAMONITOR_MODAL_GPU             GPU type, e.g. "T4"/"A10G", or empty for CPU  [CPU]
  PARLAMONITOR_MODAL_CPU             CPU cores per container           [1.0]
  PARLAMONITOR_MODAL_MAX_CONTAINERS  parallelism / credit ceiling      [10]
"""

from __future__ import annotations

import os

import modal

APP_NAME = os.environ.get("PARLAMONITOR_MODAL_APP", "parlamonitor-nlp").strip()
MODEL = os.environ.get("PARLAMONITOR_HUSPACY_MODEL", "hu_core_news_md").strip()
# Pin the model wheel version label used when renaming the versionless HF wheel so
# pip accepts it (the installed version comes from the wheel's own METADATA, not
# this label). Matches the version verified locally.
MODEL_WHEEL_VERSION = os.environ.get("PARLAMONITOR_MODAL_MODEL_VERSION", "3.8.1").strip()
GPU = os.environ.get("PARLAMONITOR_MODAL_GPU") or None      # None => CPU
CPU = float(os.environ.get("PARLAMONITOR_MODAL_CPU", "1.0"))
MAX_CONTAINERS = int(os.environ.get("PARLAMONITOR_MODAL_MAX_CONTAINERS", "10"))

app = modal.App(APP_NAME)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("curl")
    # `click` is imported by huspacy's components when spaCy loads the model; the
    # slim image doesn't pull it transitively, so name it explicitly.
    .pip_install("spacy==3.8.13", "huspacy==0.5.1", "click")
    .run_commands(
        # Install the HuSpaCy model into the image (baked in, so containers start
        # fast). The HuggingFace wheel filename is versionless and pip rejects it,
        # so download + rename to a valid version label; fall back to
        # huspacy.download if the direct URL ever moves.
        f"curl -fsSL -o /tmp/model.whl "
        f"https://huggingface.co/huspacy/{MODEL}/resolve/main/{MODEL}-any-py3-none-any.whl "
        f"&& cp /tmp/model.whl /tmp/{MODEL}-{MODEL_WHEEL_VERSION}-py3-none-any.whl "
        f"&& pip install /tmp/{MODEL}-{MODEL_WHEEL_VERSION}-py3-none-any.whl "
        f"|| python -c \"import huspacy; huspacy.download('{MODEL}')\""
    )
    # The service runs the project's own extraction logic, so its output matches
    # the local backend exactly. Ship the backend `app` package into the image.
    .env({"PARLAMONITOR_HUSPACY_MODEL": MODEL})
    .add_local_python_source("app")
)


@app.cls(
    image=image,
    gpu=GPU,
    cpu=CPU,
    max_containers=MAX_CONTAINERS,
    scaledown_window=60,   # scale to zero ~1 min after the last call (no idle cost)
    timeout=3600,
)
class NlpService:
    @modal.enter()
    def _load(self):
        # Warm the model once per container; reused across every batch it handles.
        try:
            import spacy
            spacy.prefer_gpu()   # no-op without a GPU (or without cupy installed)
        except Exception:
            pass
        from app import nlp
        nlp.get_nlp()

    @modal.method()
    def analyze_sessions(self, sessions: list[list[str]]) -> list[dict]:
        """Analyze a batch of sittings. ``sessions`` is a list of per-sitting
        sentence lists; returns, per sitting, ``{"counts": {term: n}, "entities":
        [term, ...]}`` — exactly what ``app.nlp.analyze_counts`` produces."""
        from app import nlp
        out = []
        for texts in sessions:
            counts, entity_words = nlp.analyze_counts(texts, batch_size=256)
            out.append({"counts": dict(counts), "entities": sorted(entity_words)})
        return out

    @modal.method()
    def analyze_sessions_spans(self, sessions: list[list[str]]) -> list[list]:
        """Extract PERSON + ORGANISATION mention spans for a batch of sittings
        (NEL, §10). ``sessions`` is a list of per-sitting sentence lists; returns,
        per sitting, a list (per sentence, in order) of ``[surface, start, end, key,
        kind]`` spans — exactly what ``app.nlp.entity_spans`` yields."""
        from app import nlp
        out = []
        for texts in sessions:
            out.append([[list(s) for s in per_sent]
                        for per_sent in nlp.entity_spans(texts, batch_size=256)])
        return out

    @modal.method()
    def method_tag(self) -> str:
        from app import nlp
        return nlp.method_tag()


@app.local_entrypoint()
def main():
    """`modal run modal_app.py` — quick end-to-end check of the deployed logic."""
    svc = NlpService()
    sample = [
        ["Orbán Viktor ma a Parlamentben beszélt a költségvetési törvényről.",
         "A törvényjavaslatot a képviselők többsége támogatta."],
    ]
    result = svc.analyze_sessions.remote(sample)
    print("method:", svc.method_tag.remote())
    print("result:", result)
