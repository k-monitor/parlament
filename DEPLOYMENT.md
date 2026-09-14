# Deploying Parlamonitor with Docker Compose

Parlamonitor runs from **one image** (OPS-1) as a small set of services around
two shared volumes. There are **two kinds of update**, and both are
zero-downtime:

- **data updates** — handled continuously by the `sync` sidecar (atomic DB swap
  picked up per request);
- **code / SPA updates** — handled by [`./deploy.sh`](deploy.sh), which does a
  **blue-green swap** of the serving container behind an in-stack reverse proxy.

The moving parts. [`./deploy.sh`](deploy.sh) manages the **serving tier**
(caddy + the two colors) with plain `podman`, so the blue-green swap works the
same on old `podman-compose` as on the modern `podman compose`/`docker compose`
provider. Compose manages the **data tier** (`init`, `sync`). Everything shares
one **external** network (`parlamonitor`) and one **external** DB volume
(`parlamonitor_dbdata`) that `./deploy.sh` owns.

- **`caddy`** *(compose)* — the stable front. It **owns the published port** and
  health-check-balances across the two serving "colors", reaching them by their
  network aliases `app_blue` / `app_green`. Because nothing else binds the port,
  swapping the serving container is invisible to whatever is in front of it
  (Cloudflare, a host TLS proxy, or nothing). See [`docker/Caddyfile`](docker/Caddyfile).
- **`parlamonitor_app_blue` / `_green`** *(podman, run by `./deploy.sh`)* — the
  two interchangeable serving colors: the FastAPI backend serving the versioned
  API, the MP photos and the built Vue SPA from a single stateless process (one
  uvicorn worker per core). It reads the SQLite DB **read-only** and re-stats the
  file on every request, so a swapped-in DB is picked up on the next request with
  no restart. **Only one color runs at a time**; `./deploy.sh` flips between them.
- **`init`** *(compose)* — a one-shot builder that creates the read-only SQLite +
  FTS5 database in the persistent volume on first boot (or `REBUILD_DB=1`), then
  exits. `./deploy.sh` runs it **before** starting a color, so the possibly long
  first build never holds up a serving healthcheck. Also the target of one-off DB
  commands (`compose run --rm init <update|loader|pytest|sh>`).
- **`sync`** *(compose)* — a sidecar that keeps the data in step with
  `parlament.hu` (see [Continuous sync](#continuous-sync-keeping-in-step-with-parlamenthu)).
  It cheaply checks the latest cycle for changes, re-scrapes **only what
  changed**, and updates the DB **incrementally**, swapping the new file in
  atomically. Each pass then optionally
  [announces on Bluesky](#announcing-on-bluesky) what it just made available — a
  sitting day that is now fully processed, and the haikus said on it. Optional —
  drive it from an external cron instead if you prefer.

```
                        ┌── parlamonitor_app_blue (uvicorn → FastAPI) ──┐
 Cloudflare / proxy ─▶ caddy ─┤  /            → Vue SPA                     │   ┌──── sync (sidecar) ─────────┐
   (one stable port)  :8000│  /api/v1/…    → JSON API (DB ro,            │   │ loop every N min (or cron): │
      health-check LB  │   │  /media/…     → photos)  per request        │   │  1. parlamonitor sync       │
      + retry across   │   └─────────────────────────────────────────────┘   │     (scrape only changed)   │
      the two colors   └── parlamonitor_app_green (idle; deploy flips to it)│   │  2. app.loader --update     │
                                    ▲ ./data (ro)   ▲ dbdata (rw)              │     (reload changed → atomic│
                                    │               │ ◄──── atomic swap ───────│      swap of the runtime DB)│
                               scraper output   runtime DB (shared)            └─────────────────────────────┘
                                                                                  ▲ ./data (rw)  ▲ dbdata (rw)
```

Because the DB swap is atomic and the API notices the replaced file (new inode)
on its next request, **data** updates are zero-downtime. And because `caddy`
stays bound to the port while `./deploy.sh` starts the new color, health-checks
it, then retires the old one, **code** updates are zero-downtime too: no restart,
no dropped requests.

## Prerequisites

- **Podman 4+** (rootless is fine) with **`podman-compose`** *or* the
  `podman compose` provider — `./deploy.sh` auto-detects which, and drives the
  serving colors with plain `podman` so it does not depend on compose features.
  Docker Engine 24+ with `docker compose` also works (`ENGINE`/`COMPOSE` are
  overridable).
- Scraper output present under [`data/`](data/):
  - `data/processed/*-session.json`, `data/processed/representatives-*.json`,
    `data/processed/bills-*.json` — **required**, used to build the DB.
  - `data/processed/advocates-*.json` — optional nationality-advocate
    (*nemzetiségi szószóló*) registries; loaded if present (REP-9).
  - `data/media/photos/*.jpg` — optional MP/advocate portraits; served if present.

  Produce/refresh them with the scraper (see [scraper/README.md](scraper/README.md)):

  ```bash
  python -m parlamonitor proceedings     --cycle 43 ./data
  python -m parlamonitor representatives --cycle 43 --photos ./data
  python -m parlamonitor bills           --cycle 43 ./data
  python -m parlamonitor advocates       --all-cycles ./data
  ```

  The `advocates` stage is **purely additive** on an existing deployment: it
  writes new files only, and the next `app.loader --update` loads them (adding the
  two `person` columns in place) without a rebuild or a restart.

## Quick start

`./deploy.sh` is the entry point for **both** the first bring-up and every later
update — it builds the image, ensures the DB, starts a serving color behind
`caddy`, and (on updates) retires the old color once the new one is healthy:

```bash
./deploy.sh                          # build + bring up caddy, a color, init, sync
podman-compose logs -f init          # first boot: watch the DB build
podman logs -f parlamonitor_app_blue # then watch the active color ("Uvicorn running")
```

Then open <http://localhost:8000> — the SPA, with API docs at
<http://localhost:8000/api/docs> and the health probe at
`/api/v1/health`.

> **First boot builds the database and can take several minutes** (the HuSpaCy
> word-cloud lemmatization over the whole corpus is the slow part — see
> [First-boot build time](#first-boot-build-time-huspacy)). `deploy.sh` runs this
> in the one-shot **`init`** service and only starts a serving color **after it
> finishes**, so watch `podman-compose logs -f init`. Subsequent runs reuse the
> DB and start in seconds.
>
> **Note.** `./deploy.sh` is the bootstrap: it creates the external network +
> volume, then brings everything up. A bare `podman-compose up -d` only starts
> the data-tier plumbing (`init`, `sync`) and `caddy` — the serving colors are
> run by `./deploy.sh`, so a bare `up` never serves on its own.

## What the containers do on boot

From one image (`docker/entrypoint.sh`), orchestrated by `./deploy.sh`:

1. **`init`** (`ensure-db`) — if `/db/parlamonitor.db` is missing (or
   `REBUILD_DB=1`), runs `python -m app.loader /data /db/parlamonitor.db` — the
   **only** DB writer (ING-2), atomic swap (DB-4) — then **exits**. `./deploy.sh`
   runs this to completion **before** starting a color, so the long first build
   never blocks a healthcheck.
2. **`caddy`** — starts immediately and load-balances across the colors, taking
   whichever is down out of rotation via its active health checks.
3. **`parlamonitor_app_blue` / `_green`** (`serve`) — the color `./deploy.sh`
   started execs `uvicorn app.main:app` (the DB already exists, so it starts
   immediately and is healthy within seconds).
4. **`sync`** (`sync-loop`) — the continuous updater (see above).

The DB lives in the `dbdata` named volume, so it survives restarts and is **not**
rebuilt on every boot. The word-cloud cache also persists there, so even an
interrupted first build resumes cheaply.

### First-boot build time (HuSpaCy)

The word cloud lemmatizes every sitting's transcript with a neural model, which
is minutes-to-tens-of-minutes the **first** time (cached on disk afterward). To
make the very first boot fast, build with the dependency-free tokenizer and
switch to HuSpaCy later:

```bash
PARLAMONITOR_WORDCLOUD_BACKEND=regex ./deploy.sh   # fast first build
```

(The word cloud is lower quality under `regex`; a later full rebuild with
`huspacy` upgrades it. Changing the backend invalidates the word-cloud cache, so
do it with a full rebuild — `REBUILD_DB=1` — not a live update.)

### Offloading the word-cloud NLP to Modal (GPU/CPU)

On a host without the CPU/RAM for the HuSpaCy pipeline, offload it to
[Modal](https://modal.com): the `init` and `sync` services keep orchestrating,
but the NER/lemmatization runs on Modal workers and the host only ships text and
stores the results. It runs the **same** `app.nlp` code and pinned model, so the
output — and the on-disk cache — is identical to the local `huspacy` backend.

**One-time deploy** of the Modal service (from the `backend/` directory, so the
local `app` package is bundled into the image):

```bash
cd backend
pip install modal
modal token new                 # authenticate (writes ~/.modal.toml)
modal deploy modal_app.py       # builds the image (bakes in the model) + deploys
modal run modal_app.py          # optional: smoke-test the deployed service
```

> **Re-deploy after changing `modal_app.py` or anything under `backend/app/` that
> the service calls.** The deployed image bundles the `app` package, so a new
> service method (e.g. `analyze_sessions_lemmas`, which the readability /
> lexical-diversity pass of §5.7 calls) only exists once you re-run
> `modal deploy modal_app.py` — for **both** apps, if you run the archive one
> below. Until then that pass falls back to readability only: LIX/RIX still land
> (they need no model), TTR/MATTR stay empty.

**Enable it** in `.env`, then `up` (the `init` build, and every `sync`, now use
Modal):

```dotenv
PARLAMONITOR_WORDCLOUD_BACKEND=modal
MODAL_TOKEN_ID=ak-…             # from `modal token new` / the Modal dashboard
MODAL_TOKEN_SECRET=as-…
# PARLAMONITOR_MODAL_APP=parlamonitor-nlp   # must match modal_app.py's app name
```

```bash
docker compose up -d            # init builds the DB, offloading NLP to Modal
```

**How the $30 credit is stretched** (the design goal):

- The fingerprint cache means **only sittings whose transcript changed are ever
  sent** — a first build ships them once, a routine `sync` ships one. Re-running
  changes nothing → sends nothing.
- Sittings are packed into fat batches (`PARLAMONITOR_MODAL_BATCH_SENTENCES`) and
  dispatched in parallel across a **bounded** pool of workers, each of which
  **loads the model once** and is reused. Total compute (≈ credit) is about the
  same as one worker; only wall-clock shrinks.
- The app **scales to zero** ~1 min after the last call, so an idle deployment
  costs nothing. A full first build of the whole corpus is well under a dollar of
  CPU; incremental updates are fractions of a cent.

**Tunables** (set before `modal deploy` — they shape the deployed image/class):

| Variable | Default | Meaning |
| --- | --- | --- |
| `PARLAMONITOR_MODAL_GPU` | `T4` for a `*_trf` model, CPU otherwise | GPU type e.g. `T4`/`A10G`; set **empty** to force CPU |
| `PARLAMONITOR_MODAL_CPU` | `2.0` with GPU, `1.0` CPU-only | CPU cores per worker |
| `PARLAMONITOR_MODAL_MEMORY` | `6144` for `*_trf`, `2048` otherwise | MiB of RAM per worker |
| `PARLAMONITOR_MODAL_MAX_CONTAINERS` | `10` | worker pool ceiling (parallelism + credit cap) |
| `PARLAMONITOR_HUSPACY_MODEL` | `hu_core_news_trf` | model baked into the image (must match the host's tag) |
| `PARLAMONITOR_MODAL_BATCH_SENTENCES` | `5000` | sentences per remote batch (host-side) |

> The default model is the transformer (`hu_core_news_trf`) — best NER/lemma
> accuracy, and the whole reason the pipeline is offloaded: it is too heavy for a
> small host, and on Modal a T4 chews through it. The deploy defaults track the
> model: a `*_trf` model gets a T4 + torch/cupy baked in on a Python 3.11 /
> spaCy 3.7 image (the trf build's own requirement — its 2023-era dependency
> wheels stop at py3.11, so it effectively runs *only* on Modal), while a CPU
> model (`hu_core_news_md`/`_lg`) deploys the cheap py3.12 CPU image (GPU gives
> those little benefit).
>
> The model name is part of the cache/method tag, so **switching a model
> invalidates the affected sittings' word-cloud + entity caches** and re-NERs
> them on the next build/update; afterwards incremental updates are pennies
> again. Keep the host's model env vars in sync with what was deployed to Modal
> — a mismatch would file results under the wrong cache tag.

#### Per-cycle model split (transformer only where it pays)

The loader picks the model **per sitting**: the newest electoral period uses
`PARLAMONITOR_HUSPACY_MODEL` (default `hu_core_news_trf`, served by the
`PARLAMONITOR_MODAL_APP` deployment), every earlier — frozen — period uses the
cheaper `PARLAMONITOR_HUSPACY_MODEL_ARCHIVE` (default `hu_core_news_md`, served
by `PARLAMONITOR_MODAL_APP_ARCHIVE`). Archive transcripts never change, so their
cached md results are reused forever and the expensive T4 only ever processes
the live cycle; a pre-split cache/DB keeps its md entries for the archive as-is
(nothing recomputes). When a new cycle starts, the previous one re-NERs once
with the archive model as it ages out (cheap, automatic). Set the archive model
equal to the primary to use one model everywhere.

Deploy the **second, CPU** Modal app for the archive model (same file):

```bash
cd backend
PARLAMONITOR_MODAL_APP=parlamonitor-nlp-md \
PARLAMONITOR_HUSPACY_MODEL=hu_core_news_md modal deploy modal_app.py
```

**Without Docker** (host scrapes/loads directly): the same three env vars +
`python -m app.loader --update <data> <db>` (or `build`) offload to Modal.

#### Mirroring the iromány document files (`PARLAMONITOR_DOCUMENTS`)

The scraper can mirror the actual document files behind the irományok — the
bill texts, amendments, committee reports, justification and background files —
and extract their text for NLP (DOC-1). **It is off by default and should stay
off on the server.** It is the largest thing the pipeline can put on disk:
cycle 43 alone is 861 documents totalling ~870 MB of PDF, and it buys the live
site nothing, because the bill views link to `parlament.hu` for the document
rather than serving a copy (LEGAL-1 / BILL-2). (Measured: a full pass over
cycle 43 downloaded 861 documents / 868 MB and stored 3.96 MB of xz-compressed
text — 6.7 MB of actual disk, once 4 KB blocks are counted.)

There is one reason to want it on the server anyway, and it is worth knowing
about before you decide: **it is the only way a newly submitted iromány gets a
topic chip here.** The iromány half of the CAP pass (TOPIC-8) reads document
text, and a server with no mirror can only replay `parlacap-cache.json` — so
every iromány submitted since that cache was last built shows no label until
someone reclassifies on a GPU box and ships a new one. `text` retention costs
~4 MB per cycle on disk and makes the loop self-sustaining: the sync mirrors the
text, the reconcile behind it classifies the new irományok on Modal, and no
hand-shipped cache sits in the middle. What it does *not* save is bandwidth —
see the pacing note below.

Where it *is* wanted — that, or a dev box or an analysis host doing NLP over the
legislative text — turn it on with the retention that matches the use:

```dotenv
PARLAMONITOR_DOCUMENTS=text        # extracted text only  — ~4 MB per cycle
# PARLAMONITOR_DOCUMENTS=all       # text + the source PDFs — ~872 MB per cycle
# PARLAMONITOR_DOCUMENTS_MAX_MB=10 # …and skip the outsized scans
```

or run the stage by hand without touching the sync's configuration at all:

```bash
podman-compose run --rm sync documents --cycle 43 --documents text
```

Keeping only the text is the intended opt-in: it is ~1% of the size of the PDFs
it came from and is the only part any NLP pass reads. Compressing the PDFs
instead is not worth it — their streams are already deflated, so gzip/xz/zstd
all land near 82% of the original. Note that `text` still *downloads* every
PDF; the saving is disk, not bandwidth or politeness budget (SCR-4 applies
either way: a first full pass over cycle 43 is 861 requests, ~21 min at
`PARLAMONITOR_SLEEP=0.5`, and pulls the whole 868 MB down the wire).

Text extraction needs **`pdftotext`** (poppler-utils). The runtime image
carries it (the `runtime` stage installs it beside `flock`), because the iromány
topic pass has no other way to reach the document text; it costs ~15 MB and does
nothing at all while `PARLAMONITOR_DOCUMENTS=off`. On a dev checkout running the
scraper directly, install it from your distro's packages — without it a
`text`/`all` run still completes but logs that it could extract nothing, and a
re-run once poppler is present backfills the text without re-fetching any PDF it
kept.

The mirror is incremental: published documents never change, so a second pass
costs no requests. Its store is `data/documents/<cycle>/`, entirely regenerable
— deleting it is always safe.

**The sync is paced; a hand-run `documents` is not.** Switching the mirror on
does not start from the two irományok that arrived since the last poll — it
starts from every document of the cycle that was never mirrored, which for
cycle 43 is 861 files and ~868 MB even at `text` retention (the text is what is
*kept*; the PDF is still downloaded to get it). Draining that inside one sync
pass would hold the sync lockfile, delay the DB reconcile queued behind it and
spend the whole politeness budget in one burst. So a sync pass fetches at most
`PARLAMONITOR_DOCUMENTS_PER_SYNC` new documents (default 25, `0` = no cap) and
leaves the rest to the passes after it — at the default half-hour poll that is
~1 200 documents a day, so a cold cycle settles inside a day and every pass
after that is back to the handful that are genuinely new. Each pass logs what it
left behind:

```
docs={'fetched': 25, 'reused': 140, 'pending': 696, 'errors': 0, 'bytesStored': 761234}
```

`pending` counting down to 0 is the backlog draining; `pending: 0` with a small
`fetched` is the steady state. To fill the backlog in one go instead, run the
stage by hand (`--documents text`, no budget) — or `--documents-per-pass 0` /
`PARLAMONITOR_DOCUMENTS_PER_SYNC=0` to uncap the sync itself.

#### Classifying irományok

The iromány half of the pass (TOPIC-8) runs inside the same
`--reclassify-topics` command and lands in the same `parlacap-cache.json` (under
a `bills` key, alongside `sessions`), so the workflow above already covers it —
with one prerequisite: **it reads the document text the scraper mirrors**, so the
GPU box needs that mirror before it can classify anything.

```bash
# on the box that will classify: mirror the documents first (DOC-1), then run
# the pass. `text` retention is enough — ~4 MB per cycle.
cd scraper
PARLAMONITOR_DOCUMENTS=text python -m parlamonitor documents --cycle 43 ../data
cd ../backend
python -m app.loader ../data parlamonitor.db --reclassify-topics --period 43 -v
```

Note the `../data` positional: that is where the pass looks for
`documents/<cycle>/`, and `PARLAMONITOR_DOCUMENTS_DIR` overrides it. Cycle 43 is
356 documents / ~10 800 blocks and takes about two minutes on a 12 GB card.

On the **server** nothing changes and nothing extra is shipped: the same
`parlacap-cache.json` now carries both halves, and because a server has no
document mirror to fingerprint against, each iromány's cached rows are replayed
as they stand. A server that is given no cache simply shows no chips —
`features.bill_topics` in `/api/v1/meta` goes false and the UI hides them,
exactly as it does for speeches.

The cost of that arrangement is that it only covers irományok the cache knows
about: anything submitted after the last GPU run stays unlabelled until the next
one. If that gap matters more than the ~4 MB per cycle, give the server the
mirror instead — `PARLAMONITOR_DOCUMENTS=text` in `.env` — and its own sync
classifies new irományok on Modal as they arrive, with the shipped cache still
covering everything older. The two coexist: the cache is keyed per iromány, so a
locally classified document simply adds an entry beside the imported ones.

Set `PARLAMONITOR_BILL_TOPICS=0` to switch the iromány pass off while leaving the
speech topics alone.

#### Which cycles may spend Modal credit (`PARLAMONITOR_MODAL_CYCLES`)

Modal time is metered, and the one thing that can drain a month's credit in a
single run is a **backfill of the archive**: hundreds of frozen sitting days that
all miss the cache at once — and, on the Whisper side, hundreds of whole-day
recordings on a GPU — for cycles nobody is watching, leaving the *live* cycle
unprocessed when the budget runs out. So the offload is scoped by electoral cycle:

| Value | Meaning |
| --- | --- |
| `latest` (default) | only the newest electoral cycle is ever sent to Modal |
| `all` | every cycle (the old behaviour) |
| `43` / `42,43` | exactly these cycle numbers |

One variable covers **both** offloads — the backend's word-cloud/NER
(`backend/app/config.py`) and the scraper's Whisper transcription
(`scraper/parlamonitor/config.py`) — so there is a single place to cap Modal spend.
What an out-of-scope sitting gets instead:

- **word cloud** — the local HuSpaCy model if one is installed, else the regex
  tokenizer. A sitting that already has a **cached** HuSpaCy cloud for unchanged
  text keeps it: the fallback never overwrites a better result it can't reproduce.
- **entity mentions** — skipped (there is no non-neural fallback), so those
  transcripts render without inline links until a local model is installed or the
  scope is widened. Already-cached spans are still reused, and the skip is
  non-destructive.
- **sentence timing** — the positional character estimate (TIM-3), the same
  degradation as a host with no Whisper backend at all. Cached transcriptions are
  still used, so archive days already transcribed keep their word-accurate timing.

Backfilling one old cycle *on purpose* — e.g. to give cycle 42 real entity links —
is a one-off widening, not a permanent setting:

```bash
PARLAMONITOR_MODAL_CYCLES=42 podman-compose run --rm init reextract-entities --period 42
```

> **The current cycle's model must actually be reachable.** `hu_core_news_trf`
> installs nowhere but the Modal image — it is *not* in the container — so with
> `PARLAMONITOR_WORDCLOUD_BACKEND` left at `auto` (which never selects Modal) or
> with the `MODAL_TOKEN_*` pair missing, the newest cycle has **no usable model**
> and every one of its sittings is **skipped**: no entity mentions, so the newest
> sitting days' transcripts render with no MP-profile and no K-Monitor badges at
> all (the archive keeps working — its cached md spans are still valid). The
> loader logs this as a warning naming the affected sittings; grep the `init`/`sync`
> logs for `no usable model`. Checking the served DB directly:
>
> ```bash
> # entity coverage per cycle (the image has no sqlite3 CLI, so go through python)
> podman-compose run --rm init python -c '
> import os, sqlite3
> c = sqlite3.connect("file:%s?mode=ro" % os.environ["PARLAMONITOR_DB"], uri=True)
> for row in c.execute("""
>   SELECT s.period_number, COUNT(DISTINCT s.id),
>          COUNT(DISTINCT CASE WHEN e.id IS NOT NULL THEN s.id END)
>   FROM session s
>   LEFT JOIN speech sp ON sp.session_id = s.id
>   LEFT JOIN sentence se ON se.speech_id = sp.uid
>   LEFT JOIN entity e ON e.sentence_id = se.id
>   GROUP BY 1 ORDER BY 1"""):
>     print("cycle %s: %s sittings, %s with entity mentions" % row)'
> ```
>
> A cycle whose mention count is short of its sitting count was skipped.

#### Re-running the NLP for one cycle (`reextract-entities`)

A model becoming available changes **no source file**, and `--update` only ever
revisits sittings whose file changed — so it will not backfill sittings that were
skipped. Rather than a full `REBUILD_DB=1`, re-run just the NLP over the DB that
is already built, scoped to one cycle:

```bash
podman-compose run --rm init reextract-entities --period 43
```

It snapshots the live DB, re-extracts mentions for that cycle's sittings (any whose
cached spans still fingerprint-match are reused, so re-runs are free), re-resolves
`entity_link`, and swaps the result in atomically — zero downtime, same writer lock
as every other loader run, no scrape and no JSON reload. Omit `--period` to cover
every sitting. Directly: `python -m app.loader --reextract-entities --period 43
<data> <db>`.

> **After an extraction-logic bump the cache is cold.** `loader._ENTITY_LOGIC` is
> part of each sitting's cache fingerprint, so raising it (most recently `ent-v3`:
> store **every** NER label — LOC and MISC alongside PER/ORG — for later analysis)
> makes every sitting a miss and the "re-runs are free" line above stops holding
> until each cycle has been re-extracted once. Two consequences worth planning for:
>
> - **Redeploy the Modal apps first.** The workers run the `app` package shipped at
>   deploy time (`add_local_python_source`), so until `modal deploy modal_app.py`
>   runs — once per app, primary *and* archive — they still return the old labels
>   under the new tag. Then `reextract-entities --period 43`; widen
>   `PARLAMONITOR_MODAL_CYCLES` per cycle to backfill the archive, at archive cost.
> - **Nothing breaks in the meantime.** A cycle that can't reach its model is
>   skipped non-destructively, so it keeps its previous mentions and its inline
>   links; and since only PER/ORG are ever resolved, cycles at different extraction
>   versions look identical on the site.

#### Re-measuring speech readability / diversity (`remeasure-speeches`)

The same escape hatch for the per-speech language metrics (§5.7), needed for the
same reason — a `saphes` upgrade, a changed `PARLAMONITOR_LIX_THRESHOLD` or
`PARLAMONITOR_MATTR_WINDOW`, or a lemmatizer finally becoming reachable all change
what every stored score means while touching **no source file**:

```bash
podman-compose run --rm init remeasure-speeches --period 43
```

Snapshot-and-swap like the above, cached per sitting, and the **readability half
needs no model at all** — so this works on a host with no HuSpaCy and no Modal, it
just leaves TTR/MATTR empty.

#### Classifying CAP policy topics (`reclassify-topics`)

Every speech carries a policy-topic label (§5.8) — one of the 21 [CAP major
topics](https://www.comparativeagendas.net/pages/master-codebook) plus "Other" —
produced by [`classla/ParlaCAP-Topic-Classifier`](https://huggingface.co/classla/ParlaCAP-Topic-Classifier),
an XLM-R-large model fine-tuned on 29 ParlaMint corpora (ParlaMint-HU among them).
Speeches are classified **per paragraph**, and the paragraph labels are folded into
one speech-level topic when the API answers a request.

**Irományok are classified too** (TOPIC-8), by the same model at the same
threshold, from the text of the document each one links to. One extra
prerequisite applies to that half and is covered [below](#classifying-iromanyok):
it reads the scraper's [document mirror](#mirroring-the-iromany-document-files-parlamonitor_documents),
so it only produces anything on a host that has one — everywhere else it replays
the shipped cache.

Two things follow from that, and they are what makes this pass different from the
others on this page:

- **The confidence threshold is not baked in.** Raw per-paragraph predictions are
  stored; `PARLAMONITOR_PARLACAP_THRESHOLD` (default `0.90`) is applied when a
  request is served. Retuning it is a **restart of `web`** — not a
  reclassification, not even a rebuild:

  ```bash
  podman-compose up -d web        # after editing the env var
  ```

  Raising it shows fewer, better labels; lowering it shows more, worse ones. On
  this corpus, measured against a held-out coded sample: `0.60` labels 90 % of
  paragraphs at 74 % accuracy, `0.90` labels 69 % at 81 %, `0.95` labels 62 % at
  83 %. A speech no paragraph of which clears the bar simply shows no topic badge.

- **Classification needs a GPU; the server does not.** The pass is cache-driven,
  so the model runs **once**, wherever a card is, and the result travels as one
  JSON file. This is the intended workflow.

##### Shipping the cache to the server

`parlacap-cache.json` lives beside the DB (in the `db` volume). It holds one entry
per sitting, keyed by a fingerprint of that sitting's text **and** the method, so
it is safe to copy between machines: a sitting whose transcript has since changed
simply misses and is reclassified, never silently mislabelled.

```bash
# 1. on the GPU box: classify the corpus (once; ~70 min for the full archive on a
#    12 GB card, and re-runs are free because every sitting hits the cache)
cd backend
pip install torch transformers          # only needed here, never on the server
python -m app.loader ../data parlamonitor.db --reclassify-topics -v

# 2. ship the cache (~29 MB, compresses to ~7 MB)
gzip -c parlacap-cache.json > parlacap-cache.json.gz
scp parlacap-cache.json.gz you@server:~/
```

```bash
# 3. on the server: unpack it INTO the DB volume. The serving colors are started
#    by deploy.sh rather than by compose, so address the volume itself — a
#    throwaway container works whichever color is currently up.
ssh you@server && cd ~/parlament
podman run --rm \
  -v parlamonitor_dbdata:/db \
  -v "$HOME:/in:ro" \
  parlamonitor:latest \
  sh -c 'gunzip -c /in/parlacap-cache.json.gz > /db/parlacap-cache.json'

# 4. replay it into the DB (no model needed — every sitting is a cache hit) and
#    swap the freshly annotated DB in
podman-compose run --rm init reclassify-topics
```

Step 4 is a plain DB pass — it reads the cache, writes `speech_topic`, and swaps
the DB in under the loader's writer lock (so it is safe while `sync` runs).
`torch` is never imported, because no sitting misses. The serving colors reopen
the swapped DB on their own (the connection pool retires on the file's identity
changing), so **no restart is needed** for the labels to appear.

> **Check it landed.** `/api/v1/meta` reports coverage and the threshold in force:
>
> ```bash
> curl -s https://your.host/api/v1/meta | jq '.features.speech_topics, .speech_topics'
> ```
>
> `features.speech_topics` is `false` when the pass is off **or** the DB carries no
> predictions — the SPA then hides the badge rather than rendering blank chips.

> **When a re-run is actually needed.** Only when the *predictions* would change: a
> different `PARLAMONITOR_PARLACAP_MODEL`, or changed
> `PARLAMONITOR_PARLACAP_TARGET_WORDS` / `_MAX_WORDS` / `_MAX_LENGTH`, all of which
> are part of the cache's method tag and correctly invalidate it. The threshold is
> deliberately *not* in that tag.

##### New sittings: the Modal offload

The archive is a one-off, but sittings keep arriving. The `sync` sidecar's
`--update` runs the topic pass for the days it loads, and on a server with no
torch those days would stay unlabelled — so classification of *new* sittings is
offloaded to Modal, exactly as the word cloud's NER is (WCLOUD-6).

```bash
# once, from backend/ — bakes the 2.2 GB checkpoint into the image
pip install modal && modal token new
modal deploy parlacap_modal_app.py
modal run parlacap_modal_app.py          # smoke test: prints two labelled snippets
```

Then point the server at it (`docker-compose.yml`, the `x-nlp-env` block already
carries `MODAL_TOKEN_*`):

```yaml
PARLAMONITOR_PARLACAP_BACKEND: modal
```

What that buys, and what keeps it cheap:

- **Only the newest cycle is ever dispatched.** `PARLAMONITOR_MODAL_CYCLES`
  (default `latest`, shared with the HuSpaCy and Whisper offloads) gates this pass
  too. It is the guard that matters most here: the archive is ~600k blocks, and
  without it a rebuild on the server would try to buy the whole corpus. An
  out-of-scope sitting that misses the cache is left unlabelled instead — which is
  the correct answer, because the GPU box already has it.
- **A sitting day is ~1000 blocks**, seconds on a T4. Containers cap at
  `PARLAMONITOR_PARLACAP_MODAL_MAX_CONTAINERS` (4) and scale to zero after a
  minute idle, so an idle deployment costs nothing.
- **The cache is shared.** The Modal service runs the same `app.parlacap` module
  against the same pinned model, so its output and its method tag are identical to
  the local backend's. A day classified on Modal and the archive classified on
  your GPU box live in one `parlacap-cache.json`; neither invalidates the other,
  and you can still copy the file in either direction.
- **Failure is never fatal.** An outage, an expired token or a service deployed on
  a different model costs that run its *new* topics and nothing else — the build
  finishes, existing labels stay put, and the next `--update` retries. A service
  whose method tag disagrees with the host's is refused outright rather than
  allowed to file predictions under a tag that does not describe them.

> **`PARLAMONITOR_PARLACAP_BACKEND`** follows the same rule as
> `PARLAMONITOR_WORDCLOUD_BACKEND`: **`auto` never selects Modal.** Spending
> credit is always an explicit choice — and a GPU box usually holds Modal
> credentials already (for the HuSpaCy and Whisper offloads), so an `auto` that
> reached for Modal would quietly bill a 600k-block archive backfill to a service
> that may not even be deployed. So: your GPU box wants `auto` (it finds the local
> torch install) or `local`; the server wants `modal`, which is what
> `docker-compose.yml` defaults it to, since nothing else can work there. `off`
> stops classifying without disabling the feature, so the DB keeps serving the
> labels it already has.

#### Backfilling the nationality advocates (`advocates`)

The **szószóló** registries (REP-9) are a new source file per cycle. The `sync`
sidecar keeps the **latest** cycle's registry fresh on its own (reps cadence, so
within `PARLAMONITOR_SYNC_REPS_MAX_AGE`), but the earlier cycles' rosters are
closed and are never probed — they are backfilled **once**, after deploying an
image that has the stage:

```bash
./deploy.sh                                          # 1. ship the new image
podman-compose run --rm sync advocates --all-cycles  # 2. scrape cycles 40+ into ./data
podman-compose run --rm init update                  # 3. load them (no rebuild)
```

Step 2 runs via the **`sync`** service, not `init`: it needs the read-write
`./data` mount and the scrape egress config (politeness knobs, proxy, SSH tunnel).
It writes `data/processed/advocates-<cycle>.json` plus ~13 portraits per cycle and
touches nothing else — about 10 minutes at the default 1 s delay. Step 3 is the
ordinary incremental update: it reloads only those new files, adds the two
`person` columns in place, and swaps the DB in with no downtime. (Skipping step 3
is harmless too — the next sync pass does it.) Add `--cycle N` instead of
`--all-cycles` for a single cycle, `--no-photos` to skip portraits.

#### Backfilling the office terms (`officeholders`)

The **office-holder registry** (*tisztségviselők*, REP-2a) dates every government /
House office a person held — and is the only source that dates a **non-MP**
minister's or state secretary's office at all. It is **cycle-less**: one pass
backfills the whole archive, so an already-scraped corpus needs this once, after
deploying an image that has the stage:

```bash
./deploy.sh                                     # 1. ship the new image
podman-compose run --rm sync officeholders       # 2. scrape the registry into ./data
podman-compose run --rm init update             # 3. load it (no rebuild)
```

Step 2 runs via the **`sync`** service (read-write `./data` + scrape egress, as
above) and costs a handful of requests — a few seconds. It writes
`data/processed/officeholders.json` and touches nothing else; the already-scraped
sittings, rosters, bills and votes are all left alone. Step 3 is the ordinary
incremental update: the new file is unknown to the DB's `load_state`, so it is
treated as changed, `person_office` is **created in place** on the existing DB, the
terms load for every person already in the corpus, and the result swaps in with no
downtime. Nothing else is reloaded and no aggregate is rebuilt.

Both steps are optional in the sense that the **sync sidecar does them by itself**
on the representatives' cadence (`PARLAMONITOR_SYNC_REPS_MAX_AGE`, 12 h by default),
so a deployment left alone backfills within half a day; running them explicitly is
just how you get it now. Afterwards a reshuffle is picked up automatically, and the
office dates on every profile are read straight from these terms — there is no
per-profile migration to run. Add `--skip-officeholders` to
`PARLAMONITOR_SYNC_ARGS` to opt the sidecar out.

#### Reusing a Whisper cache copied from another machine

Sentence timing is **Whisper forced alignment** (TIM-1) wherever a transcription
exists and the positional character estimate everywhere else. Those transcriptions
are expensive to make but trivially **portable**: each is a plain
`data/original/plenary/whisper-<session>.json` holding the day's word timings. So a
machine that already has them — a dev box, or a run made while the Modal app was
reachable — can hand them to a deployment that cannot transcribe at all, and that
deployment gets word-accurate timing for those sittings.

Copying the files in is *not enough on its own*, for two reasons, both silent:

- **A host with no Whisper backend never even reads the cache.** With
  `PARLAMONITOR_TIMING_BACKEND=auto` (the default) and neither `MODAL_TOKEN_*` nor
  a local `faster-whisper`, the backend resolves to `character` and `ensure_words()`
  returns *before* it looks at any cache. An **explicit** backend name is honoured
  as-is, so `whisper-local` is what makes the copied words load — and nothing needs
  installing, because every sitting is a cache hit and no day reaches the backend
  (a miss is logged per-day and falls back to the estimate, never fatally).
- **Only sittings whose raw bundle is newer than their session JSON are rebuilt**,
  and copying a cache changes neither. Touch the day bundles you have words for.

```bash
cd ~/parlament

# 1. which copied caches will actually be used? (runs on the host, no deps)
#    A cache is keyed by the recording URL + model, so a day re-cut upstream since
#    it was transcribed, or a different PARLAMONITOR_WHISPER_MODEL, reports STALE.
python scraper/check_whisper_cache.py data
#    A "re-cut" STALE is repairable in place, without a GPU — see below:
python scraper/check_whisper_cache.py data --rebase

# 2. mark exactly those days for rebuild
cd data/original/plenary
for f in whisper-*.json; do s=${f#whisper-}; touch "raw-${s%.json}-day.json"; done
cd ~/parlament

# 3. re-transform (needs read-write /data; --cycle is required by argparse but
#    unused here — the transform sweeps every pending day, all cycles at once)
podman stop parlament_sync_1            # the sidecar doesn't share the scraper's lock
podman run --rm \
  -v "$PWD/data:/data" \
  -e PARLAMONITOR_DATA_DIR=/data \
  -e PARLAMONITOR_TIMING_BACKEND=whisper-local \
  parlamonitor:latest \
  sh -c 'cd /app/scraper && python -m parlamonitor proceedings --cycle 43 --transform-only /data'

# 4. reload the rewritten sittings into the DB, then resume the sidecar
podman run --rm --env-file .env \
  -v "$PWD/data:/data:ro" -v parlamonitor_dbdata:/db \
  -e PARLAMONITOR_DB=/db/parlamonitor.db -e PARLAMONITOR_DATA_DIR=/data \
  parlamonitor:latest update
podman start parlament_sync_1
```

Step 3 logs `Built 43015: 287 speeches (whisper-forced-alignment)` per rebuilt day.
**Do not add `--force`**: it is threaded straight into `ensure_words(force=True)`,
which bypasses the cache and tries to *re-transcribe* — on a host with no backend
that means falling right back to positional timing, i.e. the exact opposite of the
point. Step 4 is the ordinary incremental update; the alignment changes only each
sentence's `timeStart`/`timeEnd` and not its text, so the word-cloud/entity caches
in the `dbdata` volume are keyed on unchanged fingerprints and hit — no NLP is
recomputed and no Modal credit is spent. (`--env-file .env` is only insurance for
the case one of them misses; drop it if you have no `.env`.)

These use plain `podman run` rather than `podman-compose run --rm sync …` because
podman-compose 1.0.6 cannot run one-off commands on the `sync` service at all —
see [the gotcha below](#notes--gotchas). Verify afterwards:

```bash
podman run --rm -v parlamonitor_dbdata:/db parlamonitor:latest \
  sqlite3 /db/parlamonitor.db 'SELECT timing_method, count(*) FROM session GROUP BY 1'
```

The sittings you copied words for read `whisper-forced-alignment`; the rest stay
`felicitas-speech-offset`.

##### When one sitting still isn't aligned

Three different things look identical from the outside — a sitting that "should"
have Whisper timing and doesn't. `check_whisper_cache.py` tells them apart:

- **`STALE … (re-cut ±X s)`** — upstream re-trimmed the sitting out of the same
  continuous daily recording. The audio is unchanged, so the cached words are still
  right; only the `t=0` they are measured from moved (the smil URL carries the cut's
  bounds in ms: `…/smil:<date>.<time>.<startMs>.<endMs>.smil/`). `--rebase` shifts
  the words by the difference and re-fingerprints — free, exact, no GPU. Re-run
  steps 2–4 afterwards. Trims are typically sub-second (sitting 43016 moved 192 ms),
  which is exactly why it is worth repairing rather than re-transcribing.
- **`STALE … (different recording | model changed)`** — nothing can be shifted into
  place; the day must be re-transcribed on a host that has a backend. Delete the
  cache, `touch` the raw bundle, and run step 3 there with `--timing-backend
  whisper-modal` and `MODAL_TOKEN_*` set (cycle 43 is inside the default
  `PARLAMONITOR_MODAL_CYCLES=latest` scope). Then copy the new `whisper-*.json` over
  and run steps 2–4 on the deployment.
- **`OK`, yet the sitting is still untimed** — the cache is fine and the *transcript*
  is missing. Check the day: a sitting whose speeches carry no text has nothing to
  align, whatever its words look like. Note that `meta.timingMethod` (and so the DB's
  `session.timing_method`) reports the method that was *available*, not that anything
  aligned — so it reads `whisper-forced-alignment` for such a day too. Count the
  actual text instead:

  ```bash
  python -c 'import json;r=json.load(open("data/processed/43016-session.json"));print(r["meta"]["counts"])'
  # {"speeches": 152, "withText": 0}  → the transcript is missing, not the alignment
  ```

  There is **nothing to do** here: parlament.hu publishes the recording and the
  speech listing days before the jegyzőkönyv, and the sync sidecar chases exactly
  this (`incomplete_text` → a one-request probe per poll, with no day cutoff — see
  [`sync.py`](scraper/parlamonitor/sync.py)), so the day re-scrapes itself the poll
  after the text lands and the existing cache is then aligned against it. A day
  stuck text-less for weeks means upstream still hasn't published it — confirm with
  the sidecar's own log rather than deleting anything.

To keep the copied-cache path working for caches copied in *later*, set
`PARLAMONITOR_TIMING_BACKEND=whisper-local` in `.env` permanently: it costs nothing
(the sidecar's genuinely new sittings just log one `Local transcription failed`
warning and time positionally, exactly as they do under `auto`), but it means a
future `whisper-*.json` is picked up on the next sync instead of being ignored.

## Continuous sync (keeping in step with parlament.hu)

The bundled **`sync`** service keeps the deployment current without a full
rebuild and without downtime. Every `PARLAMONITOR_SYNC_INTERVAL` seconds it runs
[`docker/sync-once.sh`](docker/sync-once.sh), which does two cheap steps (plus an
optional third):

1. **`parlamonitor sync`** — probes the **latest cycle** with a couple of
   list-level Felicitas queries and re-scrapes **only what changed**: a sitting
   day is re-fetched only when it is new, its duration changed, or (for the still
   live latest day) its one-request speech listing changed; bills/votes reuse the
   on-disk detail cache so an unchanged cycle costs only the list query;
   representatives and nationality advocates refresh on a slow cadence
   (`PARLAMONITOR_SYNC_REPS_MAX_AGE`).
   When nothing changed it writes nothing (SCR-2/SCR-4).
2. **`app.loader --update`** — compares each `processed/*.json` against what the
   DB was last built from (a `load_state` table) and reloads **only the changed
   files** into a private snapshot of the live DB, rebuilds the (SQL-only)
   aggregates, and **atomically swaps** the result in (DB-4). It is a fast no-op
   when nothing is newer.
3. **`app.social`** — [announces on Bluesky](#announcing-on-bluesky) what step 2
   just made available: a sitting day that has become **fully processed**, and the
   haikus said on it. A no-op unless `PARLAMONITOR_BLUESKY_AUTH` is set, and never
   fatal — the DB is already updated and serving, so a failed post is logged and
   retried on the next pass.

Because the API re-stats the **read-only** DB per request, the swap is picked up on
the next request — **no restart, no downtime, and only the changed sittings are
reprocessed** (not the whole corpus). An idle poll is a handful of requests and
touches neither the data files nor the DB.

The `sync` service `depends_on` the `app` being healthy, so it starts only after
the initial DB build and therefore only ever does incremental updates.

**Tune the cadence / politeness** in `.env`:

```dotenv
PARLAMONITOR_SYNC_INTERVAL=1800          # seconds between polls (default 30 min)
PARLAMONITOR_SYNC_REPS_MAX_AGE=43200     # refresh the MP registry at most every 12h
PARLAMONITOR_SLEEP=1.0                   # politeness delay between requests (SCR-4)
# PARLAMONITOR_SYNC_CYCLE=43             # pin a cycle (default: auto-detect latest)
# PARLAMONITOR_PROXY=socks5h://…         # or route via a proxy / SSH host
```

**Run one pass on demand** (e.g. to test, or right after deploy):

```bash
docker compose run --rm sync sync        # a single scrape+update pass, then exits
```

### When parlament.hu rate-limits the sync

`parlament.hu` answers a client it considers too eager with a **CAPTCHA challenge
page** instead of data (HTTP 200, HTML — so only the body gives it away). The
scraper treats that as a wall rather than an error: it stands off for **10
minutes** and retries the same request. Expect log lines like

```text
WARNING parlamonitor.http_client: GET …/p-ciklus hit the CAPTCHA wall
  (CAPTCHA challenge page returned for …); sleeping 600s, then retrying (1/9)
```

A pass can therefore sit idle for up to ~1.5 h — that is deliberate, not a hang,
and the `flock` means no second pass piles up behind it. After
`PARLAMONITOR_CAPTCHA_RETRIES` fruitless stand-offs (default 9) the pass gives up
with exit status **3**, distinct from the `1` that means "some items failed"; the
sidecar loop logs it and simply polls again later. Nothing is lost either way —
the scrape is idempotent and resumes where it stopped, and `sync-once.sh` still
reconciles whatever was written into the DB.

If it happens routinely rather than occasionally, the fix is politeness, not more
retries: raise `PARLAMONITOR_SLEEP`, lengthen `PARLAMONITOR_SYNC_INTERVAL`, or
give the scrape a fixed egress IP via the SSH tunnel below.

### Tunnelling the scraper through an SSH host

If `parlament.hu` is only reachable from a specific egress IP, route the
scraper's traffic through an SSH host you control. The `sync` service opens one
persistent SSH connection (key-based) and forwards every request over it, so the
connection to `parlament.hu` originates from the SSH host — no system-wide SOCKS
daemon needed. Set it up entirely from `.env`; it maps one-to-one to an
`~/.ssh/config` host:

```dotenv
# ~/.ssh/config              →  .env
# Host ahalo
#   HostName ahalo.hu        PARLAMONITOR_SSH_HOST=ahalo.hu
#   Port 2267                PARLAMONITOR_SSH_PORT=2267
#   User autokmdb            PARLAMONITOR_SSH_USER=autokmdb
#   IdentityFile /home/optv/autokmdb_key
#                            PARLAMONITOR_SSH_KEY_FILE=/home/optv/autokmdb_key
#   PubkeyAcceptedKeyTypes +ssh-rsa   ← handled automatically, no setting needed
```

The private key is bind-mounted **read-only** into the container (at
`/run/secrets/ssh_key`), and `PARLAMONITOR_SSH_KEY` is pointed at it
automatically once `PARLAMONITOR_SSH_HOST` is set. Legacy pre-7.2 OpenSSH servers
that only accept the `ssh-rsa` (RSA-SHA1) signature are detected and retried
transparently, so no `PubkeyAcceptedKeyTypes` equivalent is required. Host-key
checking is trust-on-first-use unless you point `PARLAMONITOR_SSH_KNOWN_HOSTS` at
a `known_hosts` file. The tunnel needs `paramiko`, which is bundled in the image
— run `./deploy.sh` after enabling this the first time (it rebuilds the image and
refreshes the `sync` sidecar onto it).

Verify a one-shot pass egresses through the host:

```bash
./deploy.sh                              # rebuild so paramiko is present + refresh sync
podman-compose run --rm sync sync        # watch the log for "SSH proxy up: …"
```

**Reading a tunnel failure.** A log line like

```text
POST https://www.parlament.hu/… failed (… ProxyError('Unable to connect to proxy',
OSError('Tunnel connection failed: 502 ssh channel open failed: …')))
```

comes from the scraper's *own* local proxy, not from `parlament.hu` — the site was
never reached. The text after `502` is the reason:

| Reason phrase | What it means |
| --- | --- |
| `ssh channel open failed: … Connect failed` | the SSH host itself could not reach `parlament.hu:443` (blocked/dropped egress IP, or DNS on that host) |
| `ssh channel open failed: … administratively prohibited` | sshd on that host has `AllowTcpForwarding no` |
| `ssh reconnect failed: …` | the SSH connection dropped **and** could not be re-established (host down, key/auth broken) |

A transport that merely dropped is re-dialled automatically (`SSH transport … is
down; reconnecting` → `SSH transport reconnected`) and the request retried, so a
rebooted SSH host no longer poisons the rest of the run. Reproduce the first case
by hand from the SSH host itself:

```bash
ssh -p 2267 user@host 'curl -sS -o /dev/null -w "%{http_code}\n" https://www.parlament.hu/'
```

### Alternative: external cron instead of the sidecar

If you'd rather schedule from the host, comment out the `sync` service and run
the same one-shot from cron — nothing else changes (the app still picks up the
swap automatically):

```cron
*/30 * * * * docker compose -f /srv/parlamonitor/docker-compose.yml run --rm sync sync >> /var/log/parlamonitor-sync.log 2>&1
```

Or run the scraper on the host (outside Docker) and only the loader in the
container — the two halves are decoupled by the `processed/*.json` files:

```bash
# host: refresh the JSON (writes only what changed)
python -m parlamonitor sync ./data
# container: reconcile the DB (fast, atomic, zero-downtime)
podman-compose run --rm init update
# optional: announce what that made available (a no-op without _BLUESKY_AUTH)
podman-compose run --rm init announce
```

Note the second form runs `update`, not the full `sync` pass, so the
[Bluesky announcement](#announcing-on-bluesky) step is **not** included — add the
`announce` line above if you use it. The first form (`run --rm sync sync`) runs
`sync-once.sh` and therefore announces on its own.

The `sync-once.sh` guard is an `flock`, so overlapping cron runs simply skip.

## Configuration

All settings are environment-driven (OPS-4). Override them in `docker-compose.yml`
or, more simply, via a `.env` file next to it:

```dotenv
# .env
PARLAMONITOR_PORT=8000
PARLAMONITOR_CORS_ORIGINS=https://parlamonitor.example.org
PARLAMONITOR_MODULES=                 # empty = all (proceedings,representatives,bills,votes,portfolios,settlements)
REBUILD_DB=0
PARLAMONITOR_SYNC_INTERVAL=1800       # continuous-sync poll interval (seconds)
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `PARLAMONITOR_PORT` | `8000` | host port mapped to the container's 8000 |
| `PARLAMONITOR_CORS_ORIGINS` | `http://localhost:8000` | allowed SPA origins (only relevant if the SPA is hosted separately; same-origin needs nothing) |
| `PARLAMONITOR_MODULES` | _(empty → all)_ | comma list of enabled modules (EXT-6) |
| `PARLAMONITOR_SITE_CYCLES` | _(empty → all)_ | which electoral cycles the site **shows** — `43`, `42,43` (§4A CYC-7; see [Serving one cycle](#serving-only-some-electoral-cycles)) |
| `REBUILD_DB` | `0` | set to `1` to rebuild the DB from `/data` on next start |
| `PARLAMONITOR_DB` | `/db/parlamonitor.db` | DB path inside the container |
| `PARLAMONITOR_DATA_DIR` | `/data` | scraper-output mount (read-only for `app`, read-write for `sync`) |
| `PARLAMONITOR_PHOTOS_DIR` | `/data/media/photos` | MP portrait directory |
| `PARLAMONITOR_FRONTEND_DIST` | `/app/frontend/dist` | built SPA served by the backend |
| `PARLAMONITOR_MAX_SEARCH_TOTAL` | `5000` | cap on reported search totals |
| `PARLAMONITOR_MIN_PREFIX_LEN` | `2` | shortest search term still expanded to a **prefix** term (`word*`). Below it the term is matched exactly: `a*` matches 5.9M of the corpus's 9.0M sentences and costs ~2.8 s to merge, where exact `a` costs 0.7 ms. The default disarms only single characters; two-letter terms ("EU") keep their suffixes |
| `PARLAMONITOR_SEARCH_TIMEOUT` | `20` | wall-clock ceiling in seconds on the SQL behind one search request; over budget it is abandoned and answered `503` + `Retry-After` rather than holding a worker thread. A backstop, not a policy: on the 10-cycle corpus the worst *reachable* query (a very common two-letter prefix across all cycles) measures ~7 s, so this leaves real headroom under load. Tighten only after measuring your own corpus; `0` disables |
| `PARLAMONITOR_SEARCH_ANALYTICS` | `1` | privacy-friendly search-keyword logging (PRIV-1); `0` to disable (see [Search analytics](#search-analytics-privacy-friendly)) |
| `PARLAMONITOR_ANALYTICS_DB` | `/analytics/search-analytics.db` | where the aggregated search stats are written (bind-mounted from `./analytics` on the host) |
| `PARLAMONITOR_ANALYTICS_CSV` | `1` | daily CSV export of the aggregated stats (PRIV-1); `0` to keep only the SQLite store |
| `PARLAMONITOR_ANALYTICS_CSV_DIR` | `/analytics/csv` | directory the per-day CSV files land in (bind-mounted from `./analytics/csv` on the host) |
| `PARLAMONITOR_WEB_WORKERS` | _(one per core)_ | uvicorn worker processes (see [Handling high traffic](#handling-high-traffic-cloudflare--tuning)) |
| `PARLAMONITOR_FORWARDED_ALLOW_IPS` | `*` | which proxy IPs uvicorn trusts `X-Forwarded-*` from |
| `PARLAMONITOR_API_CACHE_CONTROL` | `public, max-age=60, s-maxage=300, stale-while-revalidate=600` | Cache-Control stamped on API responses |
| `PARLAMONITOR_HTML_CACHE_CONTROL` | `public, max-age=0, s-maxage=300, stale-while-revalidate=600` | Cache-Control stamped on the SPA shell / OG share cards |
| `PARLAMONITOR_SQLITE_MMAP_SIZE` | `1073741824` (1 GiB) | per-connection SQLite `mmap_size` ceiling in bytes; raise above the DB file's size so the whole DB is memory-mapped instead of read() for its tail |
| `PARLAMONITOR_QUERY_CACHE_TTL` | `300` | seconds to memoize the expensive read-only aggregates (the search **result page**, its trend/breakdown, module `/facets`); `0` disables the in-process cache |
| `PARLAMONITOR_QUERY_CACHE_SIZE` | `256` | max distinct entries kept per cached endpoint. `/search` keys include the **page**, so paging through one search fills several entries where an aggregate fills one — raise this on a deployment with heavy search traffic |
| **Constituency lookup** (REP-10) | | the "Ki a képviselőm?" page; the only feature reading a source other than `parlament.hu` |
| `PARLAMONITOR_EVK_LOOKUP` | `1` | `0` hides the page and 404s its endpoints. It also governs the **settlement-mention module's** geography (§6D TEL-5), which reads the same source: with the lookup off, the loader keeps whatever register it already stored and, on a first build, the Települések pages report "not built" instead of an empty country — the mentions themselves are unaffected |
| `PARLAMONITOR_VTR_BASE_URL` | `https://vtr.valasztas.hu/ogy2026/data` | National Election Office data tree; its own `config.json` names the current data version, so only this base changes for the next election |
| `PARLAMONITOR_VTR_CACHE_TTL` | `604800` (7 days) | how long a downloaded file is trusted; the electoral map is fixed between elections, and a *stale* copy is still served when a re-fetch fails |
| `PARLAMONITOR_VTR_CACHE_DIR` | _(`vtr-cache/` beside the DB)_ | on the standard deploy this lands on the `/db` volume, so a restart costs no re-fetch |
| `PARLAMONITOR_VTR_TIMEOUT` | `20` | seconds per upstream request |
| `PARLAMONITOR_VTR_USER_AGENT` | `Parlamonitor/1.0 (…)` | descriptive UA sent upstream |
| `PARLAMONITOR_H3_RESOLUTIONS` | `4,5,6` | which H3 cell sizes the **segmented** settlement map is precomputed for (§6D TEL-15) — over Hungary 4 ≈ 70 cells, 5 ≈ 400, 6 ≈ 1 800. Readers are **not** offered the choice: the page uses the middle of this set, so a single value here (`5`) pins the served size and skips the rest. Needs the `h3` package; without it the loader skips the cell table and the segmented option never appears |
| `PARLAMONITOR_OEVK_TOLERANCE` | `0.002` (≈220 m) | how coarsely constituency boundaries are generalised for the settlement map's **constituency** binning (§6D TEL-16), in degrees. The office draws them for a street-level map — 99 000 vertices over the 106 of them — where that view shows the whole country at once; the default is a third of a pixel at a national zoom and stores ~250 KB. Raise it to trade fidelity for payload, `0` to serve them verbatim (~2 MB). Needs no extra package: without reachable geometry the binning is simply not offered |
| `PARLAMONITOR_MAP_TILE_URL` | `https://tile.openstreetmap.org/{z}/{x}/{y}.png` | basemap for the constituency picker. **Check the provider's usage policy against your traffic** — point this at your own tile server or a paid provider if OSM's doesn't fit |
| `PARLAMONITOR_MAP_TILE_ATTRIBUTION` | `© OpenStreetMap` (linked) | attribution rendered on the map; must match whatever `MAP_TILE_URL` serves |
| `PARLAMONITOR_MAP_MAX_ZOOM` | `18` | maximum zoom offered by the tile layer |
| **`sync` service** | | |
| `PARLAMONITOR_SYNC_INTERVAL` | `1800` | seconds between continuous-sync polls |
| `PARLAMONITOR_SYNC_CYCLE` | _(auto)_ | pin a cycle to watch (default: the latest) |
| `PARLAMONITOR_SYNC_REPS_MAX_AGE` | `43200` | refresh the MP registry at most this often (s) |
| `PARLAMONITOR_SYNC_ARGS` | _(none)_ | extra flags for `parlamonitor sync` (e.g. `--no-offsets`) |
| `PARLAMONITOR_SLEEP` | `1.0` | politeness delay between scraper requests (SCR-4) |
| `PARLAMONITOR_CAPTCHA_RETRIES` | `9` | when rate-limited (CAPTCHA page instead of data): 10-minute retries (~1.5h) before the pass is abandoned — see [rate limiting](#when-parlamenthu-rate-limits-the-sync) |
| `PARLAMONITOR_PROXY` | — | SOCKS5/HTTP proxy URL for scraper traffic |
| `PARLAMONITOR_SSH_HOST` | — | SSH host to tunnel scraper traffic through (enables the tunnel; see [SSH tunnel](#tunnelling-the-scraper-through-an-ssh-host)) |
| `PARLAMONITOR_SSH_PORT` | `22` | SSH port |
| `PARLAMONITOR_SSH_USER` | — | SSH login user |
| `PARLAMONITOR_SSH_KEY_FILE` | — | host path to the private key (bind-mounted read-only into `sync`) |
| `PARLAMONITOR_SSH_KEY_PASSPHRASE` | — | passphrase, if the key is encrypted |
| `PARLAMONITOR_SSH_KNOWN_HOSTS` | — | known_hosts path for strict host-key checking (default: trust-on-first-use) |
| **Iromány document mirror** | | (DOC-1; off by default — see [document files](#mirroring-the-iromany-document-files-parlamonitor_documents)) |
| `PARLAMONITOR_DOCUMENTS` | `off` | what to keep of each iromány document file: `off` / `text` / `pdf` / `all`. **Never `pdf`/`all` on the server** — ~870 MB per cycle. `text` (~4 MB) is a defensible server opt-in: it is what lets a newly submitted iromány be classified here instead of waiting for a hand-shipped cache |
| `PARLAMONITOR_DOCUMENTS_COMPRESSION` | `xz` | codec for the stored text: `xz` / `gzip` / `none` |
| `PARLAMONITOR_DOCUMENTS_MAX_MB` | `0` | skip any single document above this size (`0` = no limit) |
| `PARLAMONITOR_DOCUMENTS_PER_SYNC` | `25` | how many NEW documents one **sync pass** may fetch (`0` = no cap). Paces the backlog a freshly enabled mirror faces; a hand-run `documents` command is never capped by it |
| **Word-cloud NLP** | | (used by `init` + `sync`) |
| `PARLAMONITOR_WORDCLOUD_BACKEND` | `auto` | `auto`/`huspacy`/`regex`/`modal` term extraction (WCLOUD-6) |
| `PARLAMONITOR_HUSPACY_MODEL` | `hu_core_news_trf` | model for the newest cycle — must match the primary Modal image |
| `PARLAMONITOR_HUSPACY_MODEL_ARCHIVE` | `hu_core_news_md` | model for frozen earlier cycles — must match the archive Modal image |
| `PARLAMONITOR_HUSPACY_GPU` | `0` | `1` moves the **local** HuSpaCy pipeline onto a CUDA GPU (`spacy.prefer_gpu()` after preloading torch+cupy). For running a build/backfill on a machine that has a GPU instead of paying for Modal; a no-op — logged — where no GPU is usable |
| `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` | — | Modal auth (required when backend=`modal`) |
| `PARLAMONITOR_MODAL_APP` | `parlamonitor-nlp` | deployed Modal app for the newest cycle's model |
| `PARLAMONITOR_MODAL_APP_ARCHIVE` | `parlamonitor-nlp-md` | deployed Modal app for the archive model (only reachable when the scope is widened) |
| `PARLAMONITOR_MODAL_BATCH_SENTENCES` | `5000` | sentences per Modal batch (host side) |
| `PARLAMONITOR_MODAL_CYCLES` | `latest` | which cycles may use Modal at all — `latest`/`all`/`43,42`; applies to the NLP **and** Whisper offloads ([details](#which-cycles-may-spend-modal-credit-parlamonitor_modal_cycles)) |
| **Speech language metrics** | | (§5.7; used by `init` + `sync`) |
| `PARLAMONITOR_SPEECH_METRICS` | `1` | measure + serve per-speech readability & lexical diversity; `0` skips the pass and the SPA hides the annotations |
| `PARLAMONITOR_LIX_THRESHOLD` | `8` | LIX long-word threshold (calibrated for Hungarian; Björnsson's `6` saturates here). Changing it changes what every stored score means — follow with `--remeasure-speeches` |
| `PARLAMONITOR_LIX_LENGTH_POLICY` | `nfc` | `nfc`/`graphemes`/`codepoints`/`hu-letters` word-length counting |
| `PARLAMONITOR_MATTR_WINDOW` | `100` | MATTR sliding window in lemmas; shorter speeches report no MATTR |
| `PARLAMONITOR_READABILITY_MIN_WORDS` | `50` | speeches shorter than this are not scored at all |
| **CAP policy topics** | | (§5.8; classified by `init`/`sync`, [details](#classifying-cap-policy-topics-reclassify-topics)) |
| `PARLAMONITOR_PARLACAP` | `1` | classify + serve per-speech policy topics; `0` skips the pass and the SPA hides the badge |
| `PARLAMONITOR_PARLACAP_THRESHOLD` | `0.90` | confidence a paragraph must reach to count toward its speech's topic. **Applied on read** — retuning it is a `web` restart, never a reclassification or a rebuild |
| `PARLAMONITOR_PARLACAP_MODEL` | `classla/ParlaCAP-Topic-Classifier` | the classifier. Part of the cache's method tag, so changing it re-classifies everything |
| `PARLAMONITOR_PARLACAP_BACKEND` | `modal` in compose, `auto` in code | where classification runs. **`auto` never selects Modal** (metered spend is always explicit) — it uses a local torch install or declines; `modal` opts in and degrades to local; `local` pins it; `off` stops classifying. Not part of the method tag — both backends produce identical output and share one cache |
| `PARLAMONITOR_PARLACAP_MODAL_APP` | `parlamonitor-parlacap` | deployed Modal app name (must match `parlacap_modal_app.py`) |
| `PARLAMONITOR_PARLACAP_MODAL_BATCH` | `1000` | blocks per Modal request; one sitting day is roughly this |
| `PARLAMONITOR_PARLACAP_MIN_WORDS` | `12` | blocks shorter than this are never sent to the model |
| `PARLAMONITOR_PARLACAP_TARGET_WORDS` | `60` | size at which a block closes on a paragraph boundary; also in the method tag |
| `PARLAMONITOR_PARLACAP_MAX_WORDS` | `220` | hard block cap in words, sized to the model's 512 tokens (~1.9 tokens/word here); also in the method tag |
| `PARLAMONITOR_PARLACAP_MAX_LENGTH` | `512` | tokenizer truncation length; also in the method tag |
| `PARLAMONITOR_PARLACAP_BATCH_SIZE` | `32` | inference batch (GPU host only) |
| `PARLAMONITOR_PARLACAP_DEVICE` | _(auto)_ | `cuda`/`cpu`/`cuda:1`; blank picks CUDA when present |
| `PARLAMONITOR_PARLACAP_FP16` | `1` | half precision on CUDA — halves time and memory; the softmax stays fp32 |
| `PARLAMONITOR_BILL_TOPICS` | `1` | also classify **irományok** from their document text (TOPIC-8); `0` leaves speech topics untouched and hides the chip on bill pages |
| `PARLAMONITOR_DOCUMENTS_DIR` | _(data dir)_ | where the scraper's mirrored document text lives; unset → `documents/` under the loader's data directory. A server that has no mirror needs nothing here — it replays the cache |
| **Bluesky announcements** (§8.7) | | posted by the `sync` pass; see [Announcing on Bluesky](#announcing-on-bluesky) |
| `PARLAMONITOR_BLUESKY_AUTH` | — | `handle:app-password` — the credential **and** the on switch; unset = the bot never runs. Use an [app password](https://bsky.app/settings/app-passwords), never the account password |
| `PARLAMONITOR_SITE_URL` | _(request's own origin)_ | required for posting: the canonical origin the posts link to (shared with the OG share cards) |
| `PARLAMONITOR_BLUESKY_ANNOUNCE` | `1` | `0` keeps the account configured but stops posting (embargo, debugging) |
| `PARLAMONITOR_BLUESKY_DRY_RUN` | `0` | `1` decides + logs the posts, sends nothing, remembers nothing |
| `PARLAMONITOR_BLUESKY_HAIKUS` | `1` | post accidental 5-7-5 haikus from MPs' speeches (SOC-4); `0` for sitting-day posts only |
| `PARLAMONITOR_BLUESKY_HAIKU_PER_DAY` | `1` | poems posted per sitting **day** (not per pass, so an instalment-by-instalment transcript can't multiply it) |
| `PARLAMONITOR_BLUESKY_HAIKU_MPS_ONLY` | `1` | require a mandate-holding member; `0` also includes ministers/advocates who spoke |
| `PARLAMONITOR_BLUESKY_MAX_AGE_DAYS` | `30` | how far back a day may be and still count as news; also bounds the work (only these days are queried/scanned) |
| `PARLAMONITOR_BLUESKY_MAX_POSTS` | `4` | hard cap per pass — the flood backstop; the rest waits for the next pass |
| `PARLAMONITOR_BLUESKY_POST_INTERVAL` | `1` | seconds to wait **between** posts in one pass (never before the first). Posts stamped with the same `createdAt` collapse in the AppView's author feed — only the last of each shows on the profile — and spacing also keeps a burst clear of the PDS write limits. `0` disables the wait |
| `PARLAMONITOR_BLUESKY_STATE` | _(`bluesky-state.json` beside the DB)_ | the bot's memory of what it already said. On the `/db` volume by default, so it survives restarts **and** `REBUILD_DB` |
| `PARLAMONITOR_BLUESKY_SERVICE` | `https://bsky.social` | the PDS to post to (change only for a self-hosted PDS) |
| `PARLAMONITOR_BLUESKY_LANG` | `hu` | declared post language, so clients don't offer to translate Hungarian into Hungarian |

The diversity half rides the **same Modal deployment, model routing and cycle
scope** as the word cloud, so it needs no separate credit budget — but it does
need the [re-deploy](#offloading-the-word-cloud-nlp-to-modal-gpucpu) noted above,
since it calls a service method that older images don't carry. Its results land in
`speech-metrics-cache.json` next to the DB (the mounted volume), alongside the
word-cloud and entity caches.

## Serving only some electoral cycles

`PARLAMONITOR_SITE_CYCLES` runs the site as a **window onto part of the corpus**
(§4A CYC-7). Empty — the default — serves every cycle in the DB; name cycles to
serve only those:

```dotenv
# .env
PARLAMONITOR_SITE_CYCLES=43        # only the current cycle
# PARLAMONITOR_SITE_CYCLES=42,43   # the current one and the previous one
```

`./deploy.sh` picks it up on the next color swap — no rebuild, no re-load.

Inside the window the site behaves as though the other cycles were never loaded:

- the header's cycle chooser offers only these cycles, and "all cycles" means
  these cycles;
- every list, search, chart, ranking and statistic is clamped to them — including
  a hand-written `?period=` naming a cycle outside the window, which is answered
  over the window rather than over the corpus;
- the homepage's headline totals count only them;
- a sitting, felszólalás, iromány or szavazás outside them answers **404**, in
  the API and in the shared/crawled share card alike;
- `sitemap.xml` and its children stop listing those pages, so a crawler is never
  handed a URL the site refuses.

Two things deliberately stay whole. **Profiles** remain readable whatever the
window — a biography is not cycle-scoped (REP-2), and an MP's page is often the
target of an inbound link — though their statistics are the window's and they are
no longer advertised in the sitemap unless the person sat or spoke inside it. And
the **DB keeps every cycle it was built with**: this is a serving-time window, so
widening or removing it brings the rest straight back on the next restart.

One page is outside the window's reach: the **constituency lookup** ("Ki a
képviselőm?", REP-10) answers for the cycle the election office's map elects and
says so on the page — which matches the window in the normal case of serving the
current cycle, but not if you window to an older one. Set
`PARLAMONITOR_EVK_LOOKUP=0` there to drop the page entirely.

Not to be confused with the two other cycle settings: `PARLAMONITOR_MODAL_CYCLES`
caps which cycles may spend Modal credit on NLP, and `PARLAMONITOR_SYNC_CYCLE`
picks which cycle the scraper watches. Both are about *building* the DB; this one
is only about what is *shown*.

## Search analytics (privacy-friendly)

The API keeps a **GDPR-friendly, aggregated log of what people search for** — the
search *keywords*, the *filters* combined with them, and whether the results
answered them — to help improve coverage and the search itself (PRIV-1). It is
designed to be non-personal by construction:

- **No IP addresses, user agents, cookies or session identifiers** are read or
  stored. The endpoints never even inspect the request's network metadata.
- **No exact timestamps.** Events are counted into **whole-hour buckets** (UTC);
  the finest time resolution ever persisted is "term X was searched N times in the
  14:00–15:00 hour".
- Only the **aggregate counts** per `(hour, search box, keyword, filters,
  zero-result flag)` tuple are written — there is no per-request row to correlate
  back to anyone.

**Every search box on the site** is counted, not just the transcript search: the
`source` column says which one (`proceedings`, `bills`, `representatives`,
`officials`, `votes`, `portfolios`). The filters several boxes share keep their
own columns (`date_from`, `date_to`, `period`, `person_id`, `faction_id`,
`agenda_type`, `sort`); a box's own filters are folded into one canonical
`filters` string, e.g. `main_type=T;status=elfogadott` — sorted by name, listing
only what was set to something other than its default, with `=`/`;` inside a
value percent-escaped. (The bills page and the "egyéb irományok" page are one
endpoint under different `main_type` filters, so both record as `bills` and are
told apart by that string.)

Beside the plain count each bucket carries the **search-quality measures**:
`results` (how many hits the query matched — `NULL` where it was never measured),
`paged` (how many of those searches asked for a page past the first), `clicks`
(how many ended with a result being opened) and `click_rank_sum` (the sum of
those results' 1-based positions). So:

- `clicks / searches` — **click-through rate**;
- `click_rank_sum / clicks` — **mean first-click rank**: 1.0 means readers take
  the top hit, higher means they scroll past it.

The click half comes from an anonymous `POST /api/v1/search/click` ping the SPA
fires when a result is opened (the only write the API accepts). It carries the
same query + filters the search did — so it lands on that search's bucket — and
no identifier of any kind; one executed search contributes at most one click.
Because a search response can be served from the CDN cache while the ping always
reaches the origin, treat the ratio as indicative, not exact.

It writes to a **separate SQLite file** (never the read-only content DB), flushed
once an hour. `./deploy.sh` bind-mounts the host directory **`./analytics`** to
`/analytics` on the serving containers, so the file is readable **from outside the
container**:

```bash
# on the host, next to docker-compose.yml
sqlite3 ./analytics/search-analytics.db \
  'SELECT hour, source, query, period, filters, zero_results, searches, clicks
     FROM search_query_hourly ORDER BY hour DESC, searches DESC LIMIT 20;'
```

### Daily CSV export

For readers who would rather not touch SQLite, the same aggregates are also
exported to **plain CSV, once per UTC day**, into **`./analytics/csv/`** on the
host — one file per day, `search-analytics-YYYY-MM-DD.csv`:

```bash
# on the host — no sqlite3 needed
column -t -s, ./analytics/csv/search-analytics-2026-07-21.csv | less
```

Each file holds every `(hour, search box, keyword, filters, zero-result flag,
counts)` bucket whose hour falls on that day, with the column header on the first
line. Columns are only ever **appended**, and a file whose header predates a new
column is rewritten once, so every file in the directory always carries the same
columns and the whole directory can be concatenated. A day's
file is finalised the run after the day ends (its last hour flushes shortly after
midnight UTC), so **each completed day's CSV is available by the following day**;
the in-progress day stays in the live SQLite file until it completes. Files are
written atomically, and — like the DB — the several worker/color processes all
produce the same content, so concurrent writes are safe. Turn the export off with
`PARLAMONITOR_ANALYTICS_CSV=0` (the SQLite store stays), or redirect it with
`PARLAMONITOR_ANALYTICS_CSV_DIR`.

More ways to read the SQLite store directly:

```bash
# most-searched keywords overall, per search box
sqlite3 ./analytics/search-analytics.db \
  'SELECT source, query, SUM(searches) AS n FROM search_query_hourly
     GROUP BY source, query ORDER BY n DESC LIMIT 20;'

# searches that found nothing (coverage gaps)
sqlite3 ./analytics/search-analytics.db \
  'SELECT source, query, SUM(searches) AS n FROM search_query_hourly
     WHERE zero_results=1 GROUP BY source, query ORDER BY n DESC LIMIT 20;'

# where the search is doing badly: often searched, rarely clicked, or clicked
# only far down the list (>=20 searches, worst mean first-click rank first)
sqlite3 ./analytics/search-analytics.db \
  'SELECT source, query, SUM(searches) AS n,
          ROUND(1.0*SUM(clicks)/SUM(searches), 2) AS ctr,
          ROUND(1.0*SUM(click_rank_sum)/NULLIF(SUM(clicks),0), 1) AS mean_rank,
          SUM(paged) AS paged
     FROM search_query_hourly GROUP BY source, query
    HAVING n >= 20 ORDER BY mean_rank DESC NULLS LAST, ctr LIMIT 20;'
```

The table columns are `hour, query, date_from, date_to, period, person_id,
faction_id, agenda_type, sort, zero_results, searches, source, filters, results,
paged, clicks, click_rank_sum` (filter columns are `''` when the filter was not
used). Multiple uvicorn workers and both blue/green colors write the same file
concurrently; every write is an accumulating UPSERT, so their counts add up rather
than clobber. Turn the whole thing off with `PARLAMONITOR_SEARCH_ANALYTICS=0` in
`.env` — the click ping then still answers `204`, it just counts nothing. The
privacy notice on the site's "About" page discloses this logging (PRIV-1).

A store written by an older build is **migrated in place** on first open: its
rows keep their counts and read as what they were (transcript searches with no
module filters), and their `results` stays `NULL` — "never measured" rather than
a fabricated zero. Nothing to run by hand; the columns are added the first time a
serving container opens the file.

## Announcing on Bluesky

The `sync` pass can post to a **Bluesky** account when it has something genuinely
new to report (§8.7):

- a **sitting day that is now fully processed** — every speech has both its
  transcript and its per-speech video window, which is exactly when the site stops
  badging the day "Részben feldolgozva". `parlament.hu` publishes a day in
  instalments over several days, so this is the one moment at which "you can now
  read and watch all of it" is true;
- an **accidental haiku** — a transcript sentence of an MP's speech that happens to
  be 5-7-5 in Hungarian syllables, quoted verbatim and linked to the sentence.

### Setting it up

Create an **app password** on bsky.app (Settings → Privacy and security → App
passwords) — never use the account password; an app password is scoped and
revocable. Then one variable carries the credential, and its presence is the
feature's on switch:

```dotenv
# .env
PARLAMONITOR_BLUESKY_AUTH=parlamonitor.bsky.social:abcd-efgh-ijkl-mnop
# Posts link to the site, so the canonical public origin is required (the OG
# share cards already use it):
PARLAMONITOR_SITE_URL=https://parlamonitor.example.org
```

Check the credential first — this only logs in, and reports the identifier and the
*shape* of the password it read (never the password itself):

```bash
podman-compose run --rm init announce --check-auth
```

A `401 Invalid identifier or password` here means one of four things: the identifier
is not a handle, the app password was revoked or regenerated, the account password
was used instead of an [app password](https://bsky.app/settings/app-passwords), or
the value never reached the container (`podman-compose config | grep BLUESKY_AUTH`).
Without this, the only symptom is one 401 per sync pass, retried forever — nothing
is lost, but nothing is posted either.

The identifier is the trap: a handle is a **full domain**, so it is
`parlamonitor.bsky.social`, never a bare `parlamonitor` — and the PDS rejects the
bare word with the exact same message as a bad password. `--check-auth` (and the
sync log) calls that out on its own.

Then try what it would say — this decides the posts, prints them, and touches
neither the network nor its state file:

```bash
podman-compose run --rm init announce --dry-run
```

Then let the sidecar pick it up (`./deploy.sh` refreshes `sync` onto the new
image), or run one pass by hand:

```bash
podman-compose run --rm init announce
podman-compose logs -f sync | grep -i bluesky   # watch it in the sidecar
```

### What keeps it from flooding the feed

The first run **posts nothing**: with no state file yet it records the recent days
as already-announced, so a fresh deploy — or a wiped volume — cannot dump a backlog
into the feed. To announce that window instead, run once with `--backfill`.

Beyond that: only days inside `PARLAMONITOR_BLUESKY_MAX_AGE_DAYS` (30) are
considered at all, at most `PARLAMONITOR_BLUESKY_MAX_POSTS` (4) go out per pass with
the rest deferred to the next one, and the haiku quota is counted **per sitting
day**, so a transcript arriving in five instalments still yields one poem.

### The state file

`bluesky-state.json` lives beside the DB — on the `/db` volume, so it survives
restarts, code deploys **and** `REBUILD_DB=1`. That is the point: the content DB is
regenerable (DB-3), so a memory kept inside it would re-announce the whole corpus
after a rebuild. It records the announced day ids and a hash per posted poem, and no
credentials. Deleting it is safe but means the next pass re-seeds (and so stays
quiet about anything already published).

```bash
# what has been announced so far
podman exec parlamonitor_sync cat /db/bluesky-state.json | head -40
```

Posting can never break the pipeline it rides on: the announcer reads the DB
read-only, holds no lock, and runs *after* the incremental update has been swapped
in, so a failed post leaves a correctly updated site and an entry to retry on the
next pass. To stop it while keeping the account configured, set
`PARLAMONITOR_BLUESKY_ANNOUNCE=0`.

## Common operations

**Incrementally update the DB** after new data landed under `./data` (only the
changed sittings/registries are reloaded, then atomically swapped in — normally
the `sync` service does this for you):

```bash
podman-compose run --rm init update     # fast, zero-downtime; no-op if nothing changed
```

**Full rebuild** (e.g. after a schema change or to force a clean import — atomic
swap, so the running color picks it up live with no restart):

```bash
podman-compose run --rm init loader     # one-shot rebuild, then exits
# or force it on the next init run:
REBUILD_DB=1 podman-compose run --rm init
```

**After changing which speech types are procedural** (`DEFAULT_PROCEDURAL_SPEECH_TYPES`
in [`backend/app/config.py`](backend/app/config.py), or
`PARLAMONITOR_PROCEDURAL_SPEECH_TYPES`). `speech.procedural` is decided once, at
insert time, so a config change does **not** retroactively re-flag speeches that
are already loaded — and `update`/`sync` won't notice either, since they key off
changed files and a config change touches none. Deploy the new code first (the
flag is derived from the image's config), then re-flag the existing DB in place:

```bash
git pull && ./deploy.sh                        # the new type list must be in the image
podman-compose run --rm init migrate-procedural # re-flags + rebuilds the aggregates, in place
```

It prints how many speeches it flipped and how many are now excluded, so a run
that reports 0 changes is still verifiable. Then purge the CDN cache
([Cloudflare setup](#cloudflare-setup)) — the API responses in the edge cache
still carry the old numbers.

> Word clouds are built from *non-procedural* text only, so the affected sittings
> keep word counts matching the old rule until either
> `podman-compose run --rm init migrate-procedural --wordcloud` (re-runs NER for
> just those sittings) or the next full rebuild. The cache in the `dbdata` volume
> makes a `REBUILD_DB=1` rebuild the cheaper option when many sittings changed.

**Update the application code / SPA** — the zero-downtime blue-green swap
([`deploy.sh`](deploy.sh)); see [Zero-downtime code deploys](#zero-downtime-code-deploys):

```bash
git pull
./deploy.sh                             # build, start the idle color, health-check, retire the old
./deploy.sh --rollback                  # if the new build misbehaves: swap back to the previous image
```

**Run the test suite inside the image:**

```bash
podman-compose run --rm init pytest     # any non-serve/loader arg is exec'd verbatim
```

**Open a shell / stop / wipe:**

```bash
podman-compose run --rm init sh
./deploy.sh --stop                      # stop both serving colors (caddy stays up)
podman-compose down                     # stop caddy + the init/sync plumbing
# full wipe (external net + volume are owned by deploy.sh, not compose):
./deploy.sh --stop && podman-compose down
podman network rm parlamonitor; podman volume rm parlamonitor_dbdata
```

## Zero-downtime code deploys

Data changes ship live via `sync` (atomic DB swap). **Application code / SPA**
changes ship via [`./deploy.sh`](deploy.sh), a **blue-green swap** behind the
in-stack `caddy` proxy. The serving process runs as two interchangeable colors,
`parlamonitor_app_blue` and `parlamonitor_app_green` (run with plain `podman`, so
this works on any podman/compose version); exactly one serves at a time and
`caddy` stays bound to the port throughout, so the swap never drops a request.

```bash
git pull
./deploy.sh                 # build the checkout, then swap
./deploy.sh --status        # which color is serving right now?
./deploy.sh --rollback      # restore the previous image and swap back to it
./deploy.sh --no-build      # re-flip to the already-built :latest (no rebuild)
./deploy.sh --stop          # stop both colors (maintenance); caddy stays up
```

What a run does:

1. **Ensure** the external network (`parlamonitor`) + DB volume
   (`parlamonitor_dbdata`) exist, creating them if missing.
2. **Build** the new image from the current checkout (no downtime). The image
   in use is first tagged `parlamonitor:rollback` so `--rollback` can restore it.
3. **`init`** ensures the DB exists — off the serving path, so a first build
   never blocks a healthcheck (a no-op when the DB is already in the volume).
4. **Start the idle color** from the new image and **poll `/api/v1/health`**
   until it passes (up to `DEPLOY_WAIT_TIMEOUT`, default 120 s).
5. **Retire the old color**. `caddy` had been balancing across both with active
   health checks + per-request retries, so it drains to the new color with **no
   dropped requests**.

If the new color never turns healthy, **the old one keeps serving** and the
script exits non-zero — a bad build cannot take the site down. Inspect it with
`podman logs --tail=80 parlamonitor_app_<color>` and discard with
`podman rm -f parlamonitor_app_<color>`.

The swap only touches the serving colors + `sync`; `caddy`, `init` and the DB
volume are untouched, so data and in-flight edge caches are unaffected. The
colors and `caddy` carry `--restart unless-stopped`, so they come back after a
host reboot — enable that for rootless podman with
`systemctl --user enable --now podman-restart.service` and
`loginctl enable-linger $USER`.

## Running behind a reverse proxy (TLS)

The stack's own `caddy` speaks plain HTTP on the published port
(`${PARLAMONITOR_PORT:-8000}`) and does **not** terminate TLS — for a public
deployment, terminate TLS in front of it and proxy to that port. Bind the
published port to localhost so only your proxy reaches it — edit the `caddy`
service's port mapping in `docker-compose.yml`:

```yaml
  caddy:
    ports:
      - "127.0.0.1:8000:8000"
```

Minimal host-Caddy example (a second Caddy in front is fine — this one just does
TLS; the in-stack one does the blue-green balancing):

```caddy
parlamonitor.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

Alternatively, let the **in-stack** `caddy` terminate TLS itself: in
[`docker/Caddyfile`](docker/Caddyfile) drop `auto_https off`, change the `:8000`
site address to your hostname, and publish `443` on the `caddy` service. Then no
second proxy is needed.

When the public origin differs from the container, set
`PARLAMONITOR_CORS_ORIGINS` to that origin only if you also serve the SPA from a
different host; the bundled setup is same-origin and needs none.

## Handling high traffic (Cloudflare + tuning)

The data changes at most once per sync interval (default 30 min), and every
endpoint is a read — so the right way to absorb a traffic spike is to serve
almost everything from a cache in front of the origin, and to let the origin
use all its cores for the misses. The app does its half of this out of the box:

- **Cache-Control on everything** (`backend/app/caching.py`): hashed
  `/assets/…` bundles are `immutable` (cached for a year), MP photos for a
  day, API responses for 60 s in the browser and **5 min at a shared cache**
  (`s-maxage=300`) with `stale-while-revalidate` so an expiring entry is
  refreshed in the background instead of stampeding the origin. The SPA shell
  and the OG share cards are edge-cacheable but never browser-cached, so a
  deploy shows up on reload. Only `/api/v1/health` is `no-store`. Tune with
  `PARLAMONITOR_API_CACHE_CONTROL` / `PARLAMONITOR_HTML_CACHE_CONTROL` — keep
  `s-maxage` comfortably under `PARLAMONITOR_SYNC_INTERVAL`.
- **gzip** on responses over 1 KiB — session listings and search results
  compress ~10×, which matters because the origin's uplink is usually the
  first thing a spike saturates (Cloudflare pulls whatever the origin sends).
- **One uvicorn worker per core** (the API is read-only over one SQLite file,
  so workers share it safely). Pin the count with `PARLAMONITOR_WEB_WORKERS`.
- **Warm read path**: each worker thread keeps its SQLite connection (and its
  page cache) across requests and `mmap`s the DB, so all workers read one
  shared copy of hot pages from the OS page cache. A synced-in DB is still
  picked up on the very next request (the swap changes the file's inode).

### Cloudflare setup

With the DNS record proxied (orange cloud), Cloudflare caches the static
assets by default — but **not** the API JSON or the HTML shell. Two Cache
Rules turn those on (Rules → Cache Rules, order matters):

1. **Never cache health** — When `URI Path equals /api/v1/health` →
   _Bypass cache_. (Belt-and-braces: the `no-store` header already prevents
   caching, but a bypass rule keeps it out of any "cache everything" match.)
2. **Cache the site** — When `Hostname equals parlamonitor.example.org` →
   _Eligible for cache_, Edge TTL: **"Use cache-control header if present"**.
   This makes `/api/…` and the HTML shell cacheable while the origin's
   `s-maxage`/`no-store` headers keep control of how long — so a config
   change on the origin never needs a dashboard edit.

With that in place a hot endpoint costs the origin one request per 5 minutes
per Cloudflare data center; everything else is served from the edge. Also
worth enabling:

- **Tiered Cache** (Caching → Tiered Cache): edge misses fill from an upper
  Cloudflare tier instead of each data center hitting the VM separately —
  during a spike this collapses global traffic to ~1 origin fetch per URL per
  TTL.
- **Rate limiting** (Security → WAF → Rate limiting rules): the FTS search
  endpoints (`/api/v1/*/search…`) are the only genuinely expensive requests;
  a rule like _60 requests / minute / IP on `/api/`_ stops one client from
  monopolizing the origin. Search totals are already capped server-side
  (`PARLAMONITOR_MAX_SEARCH_TOTAL`).
- **Compression** (Speed → Optimization): Brotli/gzip toward visitors is on by
  default; leave it — Cloudflare re-encodes the origin's gzip.
- **SSL/TLS: Full (strict)** with an origin certificate, so the
  Cloudflare→origin hop is also encrypted (pair with the reverse proxy above).

Two origin-side settings complete the picture:

```dotenv
# .env — canonical public origin: correct absolute og:url/og:image in share
# cards regardless of what the proxy forwards.
PARLAMONITOR_SITE_URL=https://parlamonitor.example.org
PARLAMONITOR_CORS_ORIGINS=https://parlamonitor.example.org
```

And **lock the origin down** so traffic can't bypass the cache: bind the
published port to localhost (see the reverse-proxy section) and, in the Azure
Network Security Group, allow 443 only from [Cloudflare's IP
ranges](https://www.cloudflare.com/ips/) (Azure's `Internet` service tag
otherwise lets anyone hit the VM directly).

**After a deploy** the hashed assets are new URLs (no purge needed) and the
HTML/API entries age out within their ≤5-min TTL on their own. Purge only when
you need a change visible instantly: Caching → Purge → _Purge everything_ (or
just the shell URLs).

### Search engines (robots.txt, sitemaps, Search Console)

The origin serves both crawler entry points itself, generated from the DB — no
files to maintain, nothing to regenerate after a sync:

| URL | What it is |
| --- | --- |
| `/robots.txt` | Opens the content pages, closes the faceted browse URLs (`?person=`, `?sponsor=`, …) and the `/api/` + `/embed/` namespaces, and names the sitemap. |
| `/sitemap.xml` | Sitemap **index** over `/sitemap-<section>-<n>.xml` (core, sessions, speeches, representatives, bills, documents, votes) — ~260 000 URLs in ≤25 000-URL files. |

`PARLAMONITOR_SITE_URL` (set above) is what makes the URLs inside them absolute
and on the right host — **without it they are built from whatever `Host` the
proxy forwards**, which is how a sitemap ends up advertising `http://localhost`.

Each child sitemap is built on first request and cached in-process until the
next loader run (keyed on `build_meta`), and served with a 24 h edge TTL. The
deepest speeches page costs the origin a ~200 000-row scan; that is once per
sync, not once per crawler hit.

Two things to check when you first put this behind Cloudflare:

- **Cloudflare can serve its own `robots.txt`.** With the managed robots.txt /
  AI-crawler-control feature on, the edge may answer `/robots.txt` itself and
  the origin's file never appears (`curl https://…/robots.txt` and look for the
  `Sitemap:` line). If it is missing, either turn the managed file off, or add
  the `Sitemap:` line in the Cloudflare dashboard as well.
- **Sitemaps must not be cached as the SPA shell.** They carry an extension, so
  the app's static handler would 404 them if the SEO routes were not registered
  first; a 404 is never stamped `immutable`, but if you ever see HTML at a
  `/sitemap-*.xml` URL, purge it before Google fetches it again.

In **Google Search Console** (once per property):

1. Sitemaps → submit `sitemap.xml` (the index; the children are discovered).
2. Pages → for each reported reason ("Page with redirect", "Alternate page with
   proper canonical tag") → _Validate fix_ once the deploy is live. Validation
   takes days-to-weeks and re-crawls the affected sample.
3. Expect indexed-page counts to move over **weeks**, not hours — discovery of a
   260 000-page corpus is rate-limited by crawl budget, and the sitemap's
   `lastmod` is what steers it at the recent sittings first.

### If the VM itself becomes the bottleneck

Cache misses are cheap (read-only SQLite, mmap'd), so a single small VM goes a
long way once the cache hit rate is high. If `docker stats` still shows the
`app` container pinned:

- **More cores** helps linearly — workers default to one per core. The `sync`
  sidecar's NLP is the only heavy background load; offload it to Modal (see
  above) so cores stay free for serving.
- **RAM**: the ~300 MB DB should fit in the page cache next to the workers;
  on a <2 GB VM prefer fewer workers over swapping (watch `Swp` in `htop` —
  a swapping SQLite mmap is much slower than a cold read).
- **Lengthen the TTLs** (`PARLAMONITOR_API_CACHE_CONTROL`) — doubling
  `s-maxage` halves origin traffic for the same content freshness bound.
- The app is a stateless reader, so it also scales horizontally: any number
  of `app` containers can share the `dbdata` volume read-only behind a load
  balancer — but with Cloudflare in front, a bigger single VM is almost
  always the simpler win.

## Notes & gotchas

- **Data mount:** the `app` mounts `./data` **read-only** and never writes it;
  the `sync` sidecar mounts it **read-write** so the scraper can refresh the JSON
  + portraits. The runtime DB lives in the separate `dbdata` volume, shared by
  both — the `sync` writes it (atomic swap), the `app` only reads it.
- **Photos are optional.** If `data/media/photos/` is absent, the
  `/media/photos` mount is simply skipped and profile portraits fall back
  gracefully.
- **Backups:** the DB is fully regenerable from `data/processed/`, so back up
  `data/` (or the scraper) rather than the `dbdata` volume.
- **`podman-compose run` cannot touch the `sync` service** (podman-compose 1.0.6).
  It insists on "recreating" the `depends_on` target first, and `podman rm
  parlament_init_1` then fails with *"has dependent containers which must be
  removed before it"* — podman-compose implements `depends_on` as `--requires`, so
  `sync` pins `init` in place; it finally crashes in its own `down` path with
  `AttributeError: 'Namespace' object has no attribute 'remove_orphans'`. Nothing is
  damaged (it only stops `init`, a one-shot that had already exited; `caddy`, `sync`
  and the serving colors are untouched). Run one-offs with **plain `podman run`**
  instead — the same image with the mounts `deploy.sh` uses:

  ```bash
  podman run --rm -v "$PWD/data:/data" -e PARLAMONITOR_DATA_DIR=/data \
    parlamonitor:latest <entrypoint-command-or-sh -c '…'>          # scrape side, rw data
  podman run --rm -v "$PWD/data:/data:ro" -v parlamonitor_dbdata:/db \
    -e PARLAMONITOR_DB=/db/parlamonitor.db -e PARLAMONITOR_DATA_DIR=/data \
    parlamonitor:latest update                                      # DB side
  ```

  The image's entrypoint execs any unrecognised argument verbatim, but its WORKDIR
  is `/app/backend` — a scraper command therefore needs `sh -c 'cd /app/scraper && …'`.
  To get `podman-compose run` working again, `podman rm -f --depend
  parlament_init_1` unties the knot, but it removes `parlament_sync_1` with it, so
  follow up with `podman-compose up -d sync`.
- The image pins **Node 18** for the SPA build and **Python 3.12** for the
  runtime.
