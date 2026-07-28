#!/bin/sh
# Parlamonitor container entrypoint.
#
# The loader is the only DB writer (ING-2): it reads the scraper's JSON under
# $PARLAMONITOR_DATA_DIR/processed/ and builds the read-only SQLite runtime DB,
# swapping it in atomically (DB-4). We run it on first boot (or when REBUILD_DB=1)
# and then start the API.
#
#   ensure-db        build the DB if missing (or REBUILD_DB=1), then exit — the
#                    one-shot `init` service; keeps the slow first build OUT of
#                    the serving container so its healthcheck isn't held hostage
#   serve            run uvicorn (builds the DB only if it is still missing)
#   loader           (re)build the DB and exit
#   update           incrementally reconcile the DB to /data and swap (no scrape)
#   sync             one continuous-sync pass (scrape latest cycle + update DB)
#   sync-loop        run `sync` forever on an interval (the sidecar's command)
#   reextract-entities  re-run entity NER + link resolution over the EXISTING DB
#                    and swap it in (no scrape, no JSON reload, no full rebuild).
#                    Scope it to one cycle with --period, e.g.
#                      podman-compose run --rm init reextract-entities --period 43
#                    Needs a reachable model: the current cycle's is Modal-only, so
#                    PARLAMONITOR_WORDCLOUD_BACKEND=modal + MODAL_TOKEN_ID/SECRET.
#   migrate-procedural  re-apply the procedural speech-type rule (STAT-1) to the
#                    EXISTING DB in place and rebuild the aggregates — needed
#                    after changing DEFAULT_PROCEDURAL_SPEECH_TYPES, which a
#                    `sync`/`update` never revisits for already-loaded speeches
#   <anything else>  exec'd verbatim (e.g. `pytest`, `sh`)
set -e

DB="${PARLAMONITOR_DB:-/db/parlamonitor.db}"
DATA_DIR="${PARLAMONITOR_DATA_DIR:-/data}"

build_db() {
    echo "[entrypoint] building DB: $DATA_DIR -> $DB"
    mkdir -p "$(dirname "$DB")"
    python -m app.loader "$DATA_DIR" "$DB"
    echo "[entrypoint] DB build complete"
}

case "${1:-serve}" in
    ensure-db)
        # First-boot / rebuild step. The full build (incl. HuSpaCy word-cloud
        # lemmatization) can take many minutes; running it here — a service the
        # app and sync wait on to *complete* — means the app's healthcheck only
        # ever sees a ready-to-serve process, never a long-building one.
        if [ ! -f "$DB" ] || [ "${REBUILD_DB:-0}" = "1" ]; then
            build_db
        else
            echo "[entrypoint] DB present at $DB; skipping build (REBUILD_DB=1 to force)"
        fi
        ;;
    serve)
        # The init service normally built the DB already; build here only as a
        # safety net if it is somehow still missing (never on REBUILD_DB — that
        # is init's job, so the API never blocks on a multi-minute rebuild).
        if [ ! -f "$DB" ]; then
            echo "[entrypoint] DB missing at $DB — building before serving"
            build_db
        fi
        # One worker per core by default (the API is read-only, so workers
        # share the DB file freely); override with PARLAMONITOR_WEB_WORKERS.
        # --proxy-headers trusts X-Forwarded-Proto/For from the reverse proxy /
        # Cloudflare in front, so redirects and the OG share cards see the real
        # scheme/host (PARLAMONITOR_SITE_URL still wins when set).
        exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
            --workers "${PARLAMONITOR_WEB_WORKERS:-$(nproc 2>/dev/null || echo 2)}" \
            --proxy-headers \
            --forwarded-allow-ips "${PARLAMONITOR_FORWARDED_ALLOW_IPS:-*}"
        ;;
    loader)
        build_db
        ;;
    update)
        echo "[entrypoint] incremental DB update: $DATA_DIR -> $DB"
        python -m app.loader --update "$DATA_DIR" "$DB" -v
        ;;
    sync)
        exec /usr/local/bin/sync-once.sh
        ;;
    reextract-entities)
        # Operates on the DB in the volume, NOT on /data — no scrape, no reload.
        # Takes the loader's writer lock, so it is safe while the sync sidecar runs.
        shift
        echo "[entrypoint] re-extracting entity mentions + links in: $DB"
        exec python -m app.loader --reextract-entities "$DATA_DIR" "$DB" -v "$@"
        ;;
    migrate-procedural)
        # Operates on the DB in the volume, NOT on /data — no scrape, no rebuild.
        # Takes the loader's writer lock, so it is safe while the sync sidecar runs.
        shift
        echo "[entrypoint] re-applying the procedural speech-type rule to: $DB"
        exec python migrate_procedural_types.py "$DB" "$@"
        ;;
    speaker-photos)
        # Download portraits for non-roster speakers (ministers / nationality
        # advocates) into $PHOTOS_DIR; the loader wires them on the next update.
        # Needs a read-write /data mount, so run via the `sync` service, e.g.:
        #   podman-compose run --rm sync speaker-photos --cycle 43
        shift
        cd /app/scraper
        exec python -m parlamonitor speaker-photos "$DATA_DIR" "$@"
        ;;
    sync-loop)
        exec /usr/local/bin/sync-loop.sh
        ;;
    *)
        exec "$@"
        ;;
esac
