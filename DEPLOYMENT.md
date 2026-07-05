# Deploying Parlamonitor with Docker Compose

Parlamonitor runs from **one image** (OPS-1) as three small services that share
two volumes:

- **`init`** — a one-shot builder that creates the read-only SQLite + FTS5
  database in the persistent volume on first boot (or `REBUILD_DB=1`), then
  exits. Keeping the build here — rather than inside the API — means the possibly
  long first build never holds up the app's healthcheck.
- **`app`** — the FastAPI backend serving the versioned API, the MP photos and
  the built Vue SPA from a single stateless process (one uvicorn worker per
  core). It reads the SQLite DB **read-only** and re-stats the file on every
  request, so a swapped-in DB is picked up on the next request with no restart.
- **`sync`** — a sidecar that keeps the data in step with `parlament.hu`
  (see [Continuous sync](#continuous-sync-keeping-in-step-with-parlamenthu)).
  It cheaply checks the latest cycle for changes, re-scrapes **only what
  changed**, and updates the DB **incrementally**, swapping the new file in
  atomically. Optional — drive it from an external cron instead if you prefer.

`app` and `sync` both wait for `init` to finish, so they start against a
ready database and only ever read / incrementally update it.

```
┌──────────── app (uvicorn → FastAPI) ────────────┐     ┌────────── sync (sidecar) ──────────┐
│  /               → Vue SPA                       │     │  loop every N min (or cron):        │
│  /api/v1/…       → JSON API   (opens DB ro,      │     │   1. parlamonitor sync  (scrape     │
│  /media/photos/… → MP portraits  per request)    │     │      only changed items, latest     │
│  builds /db/parlamonitor.db on first boot        │     │      cycle → /data/processed/*.json)│
└──────────────────────────────────────────────────┘     │   2. app.loader --update  (reload   │
        ▲ ./data (ro)          ▲ dbdata (rw)              │      only changed files → atomic     │
        │                      │  ◄───── atomic swap ─────│      swap of /db/parlamonitor.db)   │
   scraper output         runtime DB (shared)             └─────────────────────────────────────┘
                                                             ▲ ./data (rw)   ▲ dbdata (rw)
```

Because the swap is atomic and the API notices the replaced file (new inode) on
its next request, updates are **zero-downtime**: no restart, no dropped
requests.

## Prerequisites

- Docker Engine 24+ with the Compose v2 plugin (`docker compose`, not the legacy
  `docker-compose`).
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

```bash
docker compose up -d --build
docker compose logs -f app        # watch: DB build, then "Uvicorn running"
```

Then open <http://localhost:8000> — the SPA, with API docs at
<http://localhost:8000/api/docs> and the health probe at
`/api/v1/health`.

> **First boot builds the database and can take several minutes** (the HuSpaCy
> word-cloud lemmatization over the whole corpus is the slow part — see
> [First-boot build time](#first-boot-build-time-huspacy)). This runs in the
> one-shot **`init`** service; `app` and `sync` only start **after it finishes**,
> so watch `docker compose logs -f init`. Subsequent boots reuse the DB and start
> in seconds.

## What the containers do on boot

Three services from one image, in order (`docker/entrypoint.sh`):

1. **`init`** (`ensure-db`) — if `/db/parlamonitor.db` is missing (or
   `REBUILD_DB=1`), runs `python -m app.loader /data /db/parlamonitor.db` — the
   **only** DB writer (ING-2), atomic swap (DB-4) — then **exits**. Both `app`
   and `sync` wait for it to **complete** (compose `service_completed_successfully`),
   so the long first build never blocks the API's healthcheck.
2. **`app`** (`serve`) — execs `uvicorn app.main:app` (the DB already exists, so
   it starts immediately and is healthy within seconds).
3. **`sync`** (`sync-loop`) — the continuous updater (see above).

The DB lives in the `dbdata` named volume, so it survives restarts and is **not**
rebuilt on every boot. The word-cloud cache also persists there, so even an
interrupted first build resumes cheaply.

### First-boot build time (HuSpaCy)

The word cloud lemmatizes every sitting's transcript with a neural model, which
is minutes-to-tens-of-minutes the **first** time (cached on disk afterward). To
make the very first boot fast, build with the dependency-free tokenizer and
switch to HuSpaCy later:

```bash
PARLAMONITOR_WORDCLOUD_BACKEND=regex docker compose up -d   # fast first build
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
| `PARLAMONITOR_MODAL_GPU` | _(none → CPU)_ | GPU type e.g. `T4`/`A10G`; only worth it with a transformer model (`hu_core_news_trf`) |
| `PARLAMONITOR_MODAL_CPU` | `1.0` | CPU cores per worker |
| `PARLAMONITOR_MODAL_MAX_CONTAINERS` | `10` | worker pool ceiling (parallelism + credit cap) |
| `PARLAMONITOR_HUSPACY_MODEL` | `hu_core_news_md` | model baked into the image (must match the host's tag) |
| `PARLAMONITOR_MODAL_BATCH_SENTENCES` | `5000` | sentences per remote batch (host-side) |

> The default `hu_core_news_md` is a CPU model — GPU gives it little benefit, so
> **CPU is both cheaper and the right default**. Reach for a GPU only if you also
> switch to the transformer model, which changes the output (and invalidates the
> cache, so pair it with a full rebuild).

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
— rebuild it (`docker compose up -d --build`) after enabling this the first time.

Verify a one-shot pass egresses through the host:

```bash
docker compose up -d --build             # (re)build so paramiko is present
docker compose run --rm sync sync        # watch the log for "SSH proxy up: …"
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
docker compose run --rm app update
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
| `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` | — | Modal auth (required when backend=`modal`) |
| `PARLAMONITOR_MODAL_APP` | `parlamonitor-nlp` | deployed Modal app name (must match `modal_app.py`) |
| `PARLAMONITOR_MODAL_BATCH_SENTENCES` | `5000` | sentences per Modal batch (host side) |

## Common operations

**Incrementally update the DB** after new data landed under `./data` (only the
changed sittings/registries are reloaded, then atomically swapped in — normally
the `sync` service does this for you):

```bash
docker compose run --rm app update      # fast, zero-downtime; no-op if nothing changed
```

**Full rebuild** (e.g. after a schema change or to force a clean import):

```bash
docker compose run --rm app loader      # one-shot rebuild, then exits
# or rebuild on the next start:
REBUILD_DB=1 docker compose up -d
```

**Update the application code / SPA** (rebuilds the image, keeps the DB volume):

```bash
git pull
docker compose up -d --build
```

**Run the test suite inside the image:**

```bash
docker compose run --rm app pytest      # any non-serve/loader arg is exec'd verbatim
```

**Open a shell / stop / wipe:**

```bash
docker compose run --rm app sh
docker compose down                     # stop & remove containers (DB volume kept)
docker compose down -v                  # also delete the dbdata volume
```

## Running behind a reverse proxy (TLS)

The container speaks plain HTTP on port 8000. For a public deployment terminate
TLS at a reverse proxy (Caddy, nginx, Traefik) and proxy to `app:8000`. Bind the
published port to localhost so only the proxy reaches it:

```yaml
    ports:
      - "127.0.0.1:8000:8000"
```

Minimal Caddy example:

```caddy
parlamonitor.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

When the public origin differs from the container, set
`PARLAMONITOR_CORS_ORIGINS` to that origin only if you also serve the SPA from a
different host; the bundled single-process setup is same-origin and needs none.

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
