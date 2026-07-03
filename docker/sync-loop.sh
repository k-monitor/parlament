#!/bin/sh
# Run sync-once.sh forever on a fixed interval — the sidecar's default command.
# For an external scheduler instead, run sync-once.sh from cron/systemd-timer and
# don't start this loop.
#
#   PARLAMONITOR_SYNC_INTERVAL        seconds between polls        (default 1800)
#   PARLAMONITOR_SYNC_INITIAL_DELAY   startup delay before first poll (default 0)
set -u

INTERVAL="${PARLAMONITOR_SYNC_INTERVAL:-1800}"
echo "[sync-loop] starting; polling every ${INTERVAL}s"
sleep "${PARLAMONITOR_SYNC_INITIAL_DELAY:-0}"

while true; do
    /usr/local/bin/sync-once.sh || echo "[sync-loop] sync-once exited $? — will retry"
    sleep "$INTERVAL"
done
