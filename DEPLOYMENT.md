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
  atomically. Optional — drive it from an external cron instead if you prefer.

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
  - `data/media/photos/*.jpg` — optional MP portraits; served if present.

  Produce/refresh them with the scraper (see [scraper/README.md](scraper/README.md)):

  ```bash
  python -m parlamonitor proceedings     --cycle 43 ./data
  python -m parlamonitor representatives --cycle 43 --photos ./data
  python -m parlamonitor bills           --cycle 43 ./data
  ```

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

## Continuous sync (keeping in step with parlament.hu)

The bundled **`sync`** service keeps the deployment current without a full
rebuild and without downtime. Every `PARLAMONITOR_SYNC_INTERVAL` seconds it runs
[`docker/sync-once.sh`](docker/sync-once.sh), which does two cheap steps:

1. **`parlamonitor sync`** — probes the **latest cycle** with a couple of
   list-level Felicitas queries and re-scrapes **only what changed**: a sitting
   day is re-fetched only when it is new, its duration changed, or (for the still
   live latest day) its one-request speech listing changed; bills/votes reuse the
   on-disk detail cache so an unchanged cycle costs only the list query;
   representatives refresh on a slow cadence (`PARLAMONITOR_SYNC_REPS_MAX_AGE`).
   When nothing changed it writes nothing (SCR-2/SCR-4).
2. **`app.loader --update`** — compares each `processed/*.json` against what the
   DB was last built from (a `load_state` table) and reloads **only the changed
   files** into a private snapshot of the live DB, rebuilds the (SQL-only)
   aggregates, and **atomically swaps** the result in (DB-4). It is a fast no-op
   when nothing is newer.

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
```

The `sync-once.sh` guard is an `flock`, so overlapping cron runs simply skip.

## Configuration

All settings are environment-driven (OPS-4). Override them in `docker-compose.yml`
or, more simply, via a `.env` file next to it:

```dotenv
# .env
PARLAMONITOR_PORT=8000
PARLAMONITOR_CORS_ORIGINS=https://parlamonitor.example.org
PARLAMONITOR_MODULES=                 # empty = all (proceedings,representatives,bills,votes)
REBUILD_DB=0
PARLAMONITOR_SYNC_INTERVAL=1800       # continuous-sync poll interval (seconds)
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `PARLAMONITOR_PORT` | `8000` | host port mapped to the container's 8000 |
| `PARLAMONITOR_CORS_ORIGINS` | `http://localhost:8000` | allowed SPA origins (only relevant if the SPA is hosted separately; same-origin needs nothing) |
| `PARLAMONITOR_MODULES` | _(empty → all)_ | comma list of enabled modules (EXT-6) |
| `REBUILD_DB` | `0` | set to `1` to rebuild the DB from `/data` on next start |
| `PARLAMONITOR_DB` | `/db/parlamonitor.db` | DB path inside the container |
| `PARLAMONITOR_DATA_DIR` | `/data` | scraper-output mount (read-only for `app`, read-write for `sync`) |
| `PARLAMONITOR_PHOTOS_DIR` | `/data/media/photos` | MP portrait directory |
| `PARLAMONITOR_FRONTEND_DIST` | `/app/frontend/dist` | built SPA served by the backend |
| `PARLAMONITOR_MAX_SEARCH_TOTAL` | `5000` | cap on reported search totals |
| `PARLAMONITOR_SEARCH_ANALYTICS` | `1` | privacy-friendly search-keyword logging (PRIV-1); `0` to disable (see [Search analytics](#search-analytics-privacy-friendly)) |
| `PARLAMONITOR_ANALYTICS_DB` | `/analytics/search-analytics.db` | where the aggregated search stats are written (bind-mounted from `./analytics` on the host) |
| `PARLAMONITOR_WEB_WORKERS` | _(one per core)_ | uvicorn worker processes (see [Handling high traffic](#handling-high-traffic-cloudflare--tuning)) |
| `PARLAMONITOR_FORWARDED_ALLOW_IPS` | `*` | which proxy IPs uvicorn trusts `X-Forwarded-*` from |
| `PARLAMONITOR_API_CACHE_CONTROL` | `public, max-age=60, s-maxage=300, stale-while-revalidate=600` | Cache-Control stamped on API responses |
| `PARLAMONITOR_HTML_CACHE_CONTROL` | `public, max-age=0, s-maxage=300, stale-while-revalidate=600` | Cache-Control stamped on the SPA shell / OG share cards |
| **`sync` service** | | |
| `PARLAMONITOR_SYNC_INTERVAL` | `1800` | seconds between continuous-sync polls |
| `PARLAMONITOR_SYNC_CYCLE` | _(auto)_ | pin a cycle to watch (default: the latest) |
| `PARLAMONITOR_SYNC_REPS_MAX_AGE` | `43200` | refresh the MP registry at most this often (s) |
| `PARLAMONITOR_SYNC_ARGS` | _(none)_ | extra flags for `parlamonitor sync` (e.g. `--no-offsets`) |
| `PARLAMONITOR_SLEEP` | `1.0` | politeness delay between scraper requests (SCR-4) |
| `PARLAMONITOR_PROXY` | — | SOCKS5/HTTP proxy URL for scraper traffic |
| `PARLAMONITOR_SSH_HOST` | — | SSH host to tunnel scraper traffic through (enables the tunnel; see [SSH tunnel](#tunnelling-the-scraper-through-an-ssh-host)) |
| `PARLAMONITOR_SSH_PORT` | `22` | SSH port |
| `PARLAMONITOR_SSH_USER` | — | SSH login user |
| `PARLAMONITOR_SSH_KEY_FILE` | — | host path to the private key (bind-mounted read-only into `sync`) |
| `PARLAMONITOR_SSH_KEY_PASSPHRASE` | — | passphrase, if the key is encrypted |
| `PARLAMONITOR_SSH_KNOWN_HOSTS` | — | known_hosts path for strict host-key checking (default: trust-on-first-use) |
| **Word-cloud NLP** | | (used by `init` + `sync`) |
| `PARLAMONITOR_WORDCLOUD_BACKEND` | `auto` | `auto`/`huspacy`/`regex`/`modal` term extraction (WCLOUD-6) |
| `PARLAMONITOR_HUSPACY_MODEL` | `hu_core_news_trf` | model for the newest cycle — must match the primary Modal image |
| `PARLAMONITOR_HUSPACY_MODEL_ARCHIVE` | `hu_core_news_md` | model for frozen earlier cycles — must match the archive Modal image |
| `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` | — | Modal auth (required when backend=`modal`) |
| `PARLAMONITOR_MODAL_APP` | `parlamonitor-nlp` | deployed Modal app for the newest cycle's model |
| `PARLAMONITOR_MODAL_APP_ARCHIVE` | `parlamonitor-nlp-md` | deployed Modal app for the archive model |
| `PARLAMONITOR_MODAL_BATCH_SENTENCES` | `5000` | sentences per Modal batch (host side) |

## Search analytics (privacy-friendly)

The API keeps a **GDPR-friendly, aggregated log of what people search for** — the
search *keywords* and the *filters* combined with them — to help improve coverage
and the search itself (PRIV-1). It is designed to be non-personal by construction:

- **No IP addresses, user agents, cookies or session identifiers** are read or
  stored. The endpoint never even inspects the request's network metadata.
- **No exact timestamps.** Events are counted into **whole-hour buckets** (UTC);
  the finest time resolution ever persisted is "term X was searched N times in the
  14:00–15:00 hour".
- Only the **aggregate count** per `(hour, keyword, filters, zero-result flag)`
  tuple is written — there is no per-request row to correlate back to anyone.

It writes to a **separate SQLite file** (never the read-only content DB), flushed
once an hour. `./deploy.sh` bind-mounts the host directory **`./analytics`** to
`/analytics` on the serving containers, so the file is readable **from outside the
container**:

```bash
# on the host, next to docker-compose.yml
sqlite3 ./analytics/search-analytics.db \
  'SELECT hour, query, period, faction_id, zero_results, searches
     FROM search_query_hourly ORDER BY hour DESC, searches DESC LIMIT 20;'

# most-searched keywords overall
sqlite3 ./analytics/search-analytics.db \
  'SELECT query, SUM(searches) AS n FROM search_query_hourly
     GROUP BY query ORDER BY n DESC LIMIT 20;'

# searches that found nothing (coverage gaps)
sqlite3 ./analytics/search-analytics.db \
  'SELECT query, SUM(searches) AS n FROM search_query_hourly
     WHERE zero_results=1 GROUP BY query ORDER BY n DESC LIMIT 20;'
```

The table columns are `hour, query, date_from, date_to, period, person_id,
faction_id, agenda_type, sort, zero_results, searches` (filter columns are `''`
when the filter was not used). Multiple uvicorn workers and both blue/green
colors write the same file concurrently; every write is an accumulating UPSERT, so
their counts add up rather than clobber. Turn the whole thing off with
`PARLAMONITOR_SEARCH_ANALYTICS=0` in `.env`. The privacy notice on the site's
"About" page discloses this logging (PRIV-1).

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
- The image pins **Node 18** for the SPA build and **Python 3.12** for the
  runtime.
