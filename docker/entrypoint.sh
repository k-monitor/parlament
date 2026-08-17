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
#                    Modal only serves the cycles PARLAMONITOR_MODAL_CYCLES allows
#                    (default: the newest), so re-extracting an OLDER cycle means
#                    widening it for that one run, e.g.
#                      podman-compose run --rm -e PARLAMONITOR_MODAL_CYCLES=42 \
#                        init reextract-entities --period 42
#   remeasure-speeches  re-run the readability / lexical-diversity measurement
#                    (§5.7) over the EXISTING DB and swap it in. Same escape hatch
#                    as reextract-entities, for the changes --update cannot see: a
#                    saphes upgrade, a different PARLAMONITOR_LIX_THRESHOLD /
#                    _MATTR_WINDOW, or a lemmatizer becoming reachable. Scope with
#                    --period, e.g.
#                      podman-compose run --rm init remeasure-speeches --period 43
#                    Needs NO model for the readability half (LIX/RIX always land);
#                    the diversity half (TTR/MATTR) needs the same Modal/HuSpaCy
#                    setup as the entity pass above.
#   officeholders    scrape the office-holder registry (tisztségviselők) — every
#                    office term with its real dates, MPs and non-MPs alike — into
#                    /data; the one-off backfill for a corpus scraped before the
#                    stage existed, e.g.
#                      podman-compose run --rm sync officeholders
#                    (the sync sidecar then keeps it fresh on its own)
#   advocates        scrape the nationality-advocate registries (szószólók) into
#                    /data; the one-off backfill for the earlier cycles, e.g.
#                      podman-compose run --rm sync advocates --all-cycles
#                    (the sync sidecar keeps the latest cycle fresh on its own)
#   announce         post to Bluesky what the DB now holds (§8.7): a sitting day
#                    that has become fully processed, and the haikus said on it.
#                    The `sync` pass already runs this after each update; use it by
#                    hand to try the configuration out first, e.g.
#                      podman-compose run --rm init announce --dry-run
#                    Reads the DB read-only and remembers what it posted beside it,
#                    so a first run announces nothing (it adopts the current state).
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
    remeasure-speeches)
        # Operates on the DB in the volume, NOT on /data — no scrape, no reload.
        # Takes the loader's writer lock, so it is safe while the sync sidecar runs.
        shift
        echo "[entrypoint] re-measuring speech readability + diversity in: $DB"
        exec python -m app.loader --remeasure-speeches "$DATA_DIR" "$DB" -v "$@"
        ;;
    announce)
        # Reads the DB (read-only) and its own state file beside it; writes nothing
        # to /data. Safe to run while the sync sidecar works — it takes no lock
        # because it changes neither the DB nor the scraper output.
        shift
        echo "[entrypoint] announcing new sitting days / haikus from: $DB"
        exec python -m app.social "$@"
        ;;
    migrate-procedural)
        # Operates on the DB in the volume, NOT on /data — no scrape, no rebuild.
        # Takes the loader's writer lock, so it is safe while the sync sidecar runs.
        shift
        echo "[entrypoint] re-applying the procedural speech-type rule to: $DB"
        exec python migrate_procedural_types.py "$DB" "$@"
        ;;
    advocates)
        # Scrape the nationality-advocate registries (nemzetiségi szószólók) into
        # $DATA_DIR/processed/advocates-<cycle>.json. The sync sidecar already keeps
        # the LATEST cycle's registry fresh; this is the one-off backfill for the
        # earlier cycles (their rosters are closed, so it is run once):
        #   podman-compose run --rm sync advocates --all-cycles
        # Needs a read-write /data mount and the scrape egress config (proxy / SSH
        # tunnel), which is why it runs via the `sync` service, not `init`. The DB
        # picks the files up on the next `update` / sync pass.
        shift
        cd /app/scraper
        exec python -m parlamonitor advocates "$DATA_DIR" "$@"
        ;;
    officeholders)
        # Scrape the office-holder registry (tisztségviselők) into
        # $DATA_DIR/processed/officeholders.json: every office term with its real
        # dates, MPs and non-MPs alike (REP-2a). The sync sidecar refreshes it on the
        # representatives' cadence; this is the one-off backfill for a deployment
        # whose corpus was scraped before the stage existed:
        #   podman-compose run --rm sync officeholders
        # Cycle-less, so it backfills the whole archive in one pass (a handful of
        # requests). Needs a read-write /data mount and the scrape egress config,
        # hence the `sync` service. The DB picks it up on the next `update`/sync.
        shift
        cd /app/scraper
        exec python -m parlamonitor officeholders "$DATA_DIR" "$@"
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
