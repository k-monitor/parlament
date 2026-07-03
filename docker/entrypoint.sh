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
        exec uvicorn app.main:app --host 0.0.0.0 --port 8000
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
    sync-loop)
        exec /usr/local/bin/sync-loop.sh
        ;;
    *)
        exec "$@"
        ;;
esac
