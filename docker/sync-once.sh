#!/bin/sh
# One continuous-sync pass: cheaply check parlament.hu for changes in the latest
# cycle, re-scrape only what changed (SCR-2/SCR-4), then incrementally reconcile
# the runtime DB and atomically swap it in (DB-4). The running API picks up the
# new DB on its next request — no restart, no downtime.
#
# Safe to run from cron or a loop: an flock guard makes an overlapping run skip,
# and both halves are cheap no-ops when nothing has changed upstream.
#
#   PARLAMONITOR_DB            runtime DB path        (default /db/parlamonitor.db)
#   PARLAMONITOR_DATA_DIR      scraper output dir     (default /data)
#   PARLAMONITOR_SYNC_CYCLE    cycle to watch         (default: latest, auto)
#   PARLAMONITOR_SYNC_ARGS     extra `sync` flags     (e.g. "--no-offsets")
#   (politeness knobs PARLAMONITOR_SLEEP / _RETRY_COUNT / _PROXY / _SSH_* apply)
#
# A pass that gets rate-limited (parlament.hu serves a CAPTCHA page instead of
# data) retries every 10 minutes, so it may sit idle for up to ~1.5h before
# exiting 3 — deliberate, not a hang. The DB is reconciled either way, with
# whatever the scrape managed to write.
set -eu

DB="${PARLAMONITOR_DB:-/db/parlamonitor.db}"
DATA_DIR="${PARLAMONITOR_DATA_DIR:-/data}"
LOCK="${PARLAMONITOR_SYNC_LOCK:-${DATA_DIR}/parlamonitor-sync.lock}"

# Serialize with any other sync run (re-exec once under a non-blocking flock).
if [ -z "${_SYNC_LOCKED:-}" ]; then
    exec flock -n "$LOCK" env _SYNC_LOCKED=1 "$0" "$@"
fi

CYCLE_ARG=""
if [ -n "${PARLAMONITOR_SYNC_CYCLE:-}" ]; then
    CYCLE_ARG="--cycle ${PARLAMONITOR_SYNC_CYCLE}"
fi

echo "[sync-once] $(date -u +%FT%TZ) checking latest cycle for changes…"
cd /app/scraper
# shellcheck disable=SC2086
python -m parlamonitor sync $CYCLE_ARG ${PARLAMONITOR_SYNC_ARGS:-} "$DATA_DIR" \
    || echo "[sync-once] scraper reported errors (still reconciling what was written)"

echo "[sync-once] reconciling DB incrementally…"
cd /app/backend
python -m app.loader --update "$DATA_DIR" "$DB" -v

# Announce on Bluesky what the reconcile just made available (§8.7): a sitting day
# that is now fully processed, and the haikus said on it. A no-op without
# PARLAMONITOR_BLUESKY_AUTH, and never fatal — the DB is already updated and
# serving, so a failed post must not fail the pass (it retries next time).
echo "[sync-once] checking for anything to announce…"
python -m app.social || echo "[sync-once] announcer exited $? — will retry next pass"

echo "[sync-once] $(date -u +%FT%TZ) done."
