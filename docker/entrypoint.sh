#!/bin/sh
# Parlamonitor container entrypoint.
#
# The loader is the only DB writer (ING-2): it reads the scraper's JSON under
# $PARLAMONITOR_DATA_DIR/processed/ and builds the read-only SQLite runtime DB,
# swapping it in atomically (DB-4). We run it on first boot (or when REBUILD_DB=1)
# and then start the API.
#
#   serve            build the DB if missing, then run uvicorn   (default)
#   loader           (re)build the DB and exit
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
    serve)
        if [ ! -f "$DB" ] || [ "${REBUILD_DB:-0}" = "1" ]; then
            build_db
        else
            echo "[entrypoint] reusing existing DB at $DB (set REBUILD_DB=1 to rebuild)"
        fi
        exec uvicorn app.main:app --host 0.0.0.0 --port 8000
        ;;
    loader)
        build_db
        ;;
    *)
        exec "$@"
        ;;
esac
