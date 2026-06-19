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

# --- Stage 2: Python runtime (API + SPA + loader) ---
FROM python:3.12-slim AS runtime
WORKDIR /app/backend

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code and the built SPA bundle.
COPY backend/app ./app
COPY --from=frontend /build/dist /app/frontend/dist
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

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
