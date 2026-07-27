# syntax=docker/dockerfile:1
#
# Parlamonitor — single-image deployment.
# The app is designed to run from one stateless process (OPS-1): the FastAPI
# backend serves the versioned API, the MP photos, AND the built Vue SPA. So we
# build the SPA in one stage and copy it into the Python runtime image.

# --- Stage 1: build the Vue 3 / Vite SPA -> dist/ ---
FROM node:18-bookworm-slim AS frontend
WORKDIR /build
# Install deps against the lockfile first for a cached layer.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python runtime (API + SPA + loader + scraper) ---
FROM python:3.12-slim AS runtime
WORKDIR /app/backend

# `flock` (util-linux) guards overlapping cron/loop sync runs (docker/sync-once.sh).
RUN apt-get update && apt-get install -y --no-install-recommends util-linux \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
COPY scraper/requirements.txt /app/scraper/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -r /app/scraper/requirements.txt

# Application code and the built SPA bundle.
COPY backend/app ./app
# One-off in-place DB migrations. The served DB lives in the `dbdata` volume, so
# these are only runnable from INSIDE the image — e.g.
#   podman-compose run --rm init migrate-procedural
COPY backend/migrate_*.py ./
# The scraper package, so the same image can run the continuous sync
# (`python -m parlamonitor sync`) alongside the loader/API (OPS-1/OPS-2).
COPY scraper/parlamonitor /app/scraper/parlamonitor
COPY --from=frontend /build/dist /app/frontend/dist
COPY docker/entrypoint.sh docker/sync-once.sh docker/sync-loop.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/sync-once.sh \
    /usr/local/bin/sync-loop.sh

# Operational config (OPS-4). All paths point at the volumes the compose file
# mounts; override any of these at run time.
ENV PARLAMONITOR_DB=/db/parlamonitor.db \
    PARLAMONITOR_DATA_DIR=/data \
    PARLAMONITOR_PHOTOS_DIR=/data/media/photos \
    PARLAMONITOR_FRONTEND_DIST=/app/frontend/dist

EXPOSE 8000

# entrypoint builds the DB from /data on first boot, then serves.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["serve"]
