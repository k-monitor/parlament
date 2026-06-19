# Deploying Parlamonitor with Docker Compose

Parlamonitor runs as **one stateless container** (OPS-1): the FastAPI backend
serves the versioned API, the MP photos, and the built Vue SPA from a single
process. The container reads the scraper's JSON output (mounted read-only) and
builds a read-only SQLite + FTS5 database into a persistent volume on first boot.

```
┌─────────────────────────────── container: parlamonitor ───────────────────────────────┐
│  uvicorn → FastAPI                                                                      │
│    /                → Vue SPA            (PARLAMONITOR_FRONTEND_DIST=/app/frontend/dist)│
│    /api/v1/…        → JSON API                                                          │
│    /media/photos/…  → MP portraits       (/data/media/photos)                           │
│  loader (entrypoint) builds /db/parlamonitor.db from /data/processed/*.json             │
└────────────────────────────────────────────────────────────────────────────────────────┘
        ▲ ./data (ro)                          ▲ dbdata volume (rw, persistent)
   scraper output                         built runtime DB
```

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

The first boot runs the loader (a few seconds) before uvicorn starts; the
compose `healthcheck` has a 40s `start_period` to cover this.

## What the container does on boot

The entrypoint ([`docker/entrypoint.sh`](docker/entrypoint.sh)):

1. If `/db/parlamonitor.db` is missing (or `REBUILD_DB=1`), runs
   `python -m app.loader /data /db/parlamonitor.db` — the **only** DB writer
   (ING-2), atomic swap (DB-4).
2. Execs `uvicorn app.main:app --host 0.0.0.0 --port 8000`.

The DB lives in the `dbdata` named volume, so it survives restarts and is **not**
rebuilt on every boot.

## Configuration

All settings are environment-driven (OPS-4). Override them in `docker-compose.yml`
or, more simply, via a `.env` file next to it:

```dotenv
# .env
PARLAMONITOR_PORT=8000
PARLAMONITOR_CORS_ORIGINS=https://parlamonitor.example.org
PARLAMONITOR_MODULES=                 # empty = all (proceedings,representatives,bills)
REBUILD_DB=0
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `PARLAMONITOR_PORT` | `8000` | host port mapped to the container's 8000 |
| `PARLAMONITOR_CORS_ORIGINS` | `http://localhost:8000` | allowed SPA origins (only relevant if the SPA is hosted separately; same-origin needs nothing) |
| `PARLAMONITOR_MODULES` | _(empty → all)_ | comma list of enabled modules (EXT-6) |
| `REBUILD_DB` | `0` | set to `1` to rebuild the DB from `/data` on next start |
| `PARLAMONITOR_DB` | `/db/parlamonitor.db` | DB path inside the container |
| `PARLAMONITOR_DATA_DIR` | `/data` | scraper-output mount the loader reads |
| `PARLAMONITOR_PHOTOS_DIR` | `/data/media/photos` | MP portrait directory |
| `PARLAMONITOR_FRONTEND_DIST` | `/app/frontend/dist` | built SPA served by the backend |
| `PARLAMONITOR_MAX_SEARCH_TOTAL` | `5000` | cap on reported search totals |

## Common operations

**Rebuild the DB after re-running the scraper** (data already refreshed under `./data`):

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

## Notes & gotchas

- **Data is mounted read-only** (`./data:/data:ro`); the writable runtime DB is
  the separate `dbdata` volume — the loader never mutates the source JSON.
- **Photos are optional.** If `data/media/photos/` is absent, the
  `/media/photos` mount is simply skipped and profile portraits fall back
  gracefully.
- **Backups:** the DB is fully regenerable from `data/processed/`, so back up
  `data/` (or the scraper) rather than the `dbdata` volume.
- The image pins **Node 18** for the SPA build and **Python 3.12** for the
  runtime.
