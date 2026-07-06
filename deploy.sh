#!/usr/bin/env bash
# Zero-downtime code/image deploy for Parlamonitor's (podman-)compose stack.
#
# DATA updates are already zero-downtime: the `sync` sidecar swaps the SQLite DB
# atomically and the read-only API picks it up on its next request. This script
# handles the OTHER kind of update — new application code / SPA — which would
# otherwise mean recreating the serving container and dropping the published
# port for a few seconds.
#
# It does a blue-green swap behind the in-stack Caddy load balancer (see
# docker/Caddyfile). Exactly one color (app_blue / app_green) serves at a time:
#   1. build the new image from the current checkout            (no downtime)
#   2. ensure the DB is present via `init`, OFF the serving path (no downtime)
#   3. start the IDLE color from the new image, wait for /health (no downtime)
#   4. stop + remove the OLD color — Caddy has already been balancing across
#      both and drains to the new color with no dropped requests
# If the new color never turns healthy, the old one keeps serving and the script
# exits non-zero, so a bad build cannot take the site down.
#
# Usage:
#   ./deploy.sh                 # build the current checkout, then swap
#   ./deploy.sh --no-build      # swap to the already-built :latest (e.g. re-flip)
#   ./deploy.sh --rollback      # restore the previous image and swap back to it
#   ./deploy.sh --status        # print which color is serving, then exit
#
# Env overrides:
#   COMPOSE="docker compose"    # force a specific compose command
#   ENGINE="docker"             # force a specific container engine (image ops)
#   DEPLOY_WAIT_TIMEOUT=120     # seconds to wait for the new color's healthcheck
#   PARLAMONITOR_PORT=8000      # host port caddy publishes (for the smoke test)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# --- pick a compose command -------------------------------------------------
# Prefer the podman compose plugin (delegates to docker compose; best profile /
# healthcheck support), then podman-compose, then docker compose.
if [ -n "${COMPOSE:-}" ]; then
  :
elif podman compose version >/dev/null 2>&1; then
  COMPOSE="podman compose"
elif command -v podman-compose >/dev/null 2>&1; then
  COMPOSE="podman-compose"
elif docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
else
  echo "error: no compose command found (podman compose / podman-compose / docker compose)" >&2
  exit 1
fi
# The `serve` profile gates the two colors so a bare `up` never starts them;
# every color-targeting compose call must enable it.
SERVE="$COMPOSE --profile serve"

# --- pick a container engine (for image tag/inspect used by rollback) -------
# Follow whichever engine the compose command drives, so the rollback image is
# looked up in the same image store the build wrote to.
if [ -n "${ENGINE:-}" ]; then
  :
elif [ "${COMPOSE#podman}" != "$COMPOSE" ] && command -v podman >/dev/null 2>&1; then
  ENGINE="podman"
elif [ "${COMPOSE#docker}" != "$COMPOSE" ] && command -v docker >/dev/null 2>&1; then
  ENGINE="docker"
elif command -v podman >/dev/null 2>&1; then
  ENGINE="podman"
elif command -v docker >/dev/null 2>&1; then
  ENGINE="docker"
else
  ENGINE=""   # rollback image bookkeeping is skipped if neither is present
fi

IMAGE="parlamonitor:latest"
ROLLBACK_IMAGE="parlamonitor:rollback"
PORT="${PARLAMONITOR_PORT:-8000}"
HEALTH_URL="http://localhost:${PORT}/api/v1/health"
WAIT_TIMEOUT="${DEPLOY_WAIT_TIMEOUT:-120}"

BUILD=1
ROLLBACK=0
STATUS_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --no-build)  BUILD=0 ;;
    --rollback)  ROLLBACK=1; BUILD=0 ;;
    --status)    STATUS_ONLY=1 ;;
    -h|--help)   sed -n '2,40{/^#/!q;s/^# \{0,1\}//p}' "$0"; exit 0 ;;
    *)           echo "error: unknown argument: $arg (try --help)" >&2; exit 2 ;;
  esac
done

image_exists() { [ -n "$ENGINE" ] && $ENGINE image inspect "$1" >/dev/null 2>&1; }

# --- which color is serving now? --------------------------------------------
color_running() {  # $1 = blue|green ; 0 if that color's container is running
  $SERVE ps "app_$1" 2>/dev/null | grep -qiE 'up|running|healthy'
}

if color_running green; then ACTIVE=green
elif color_running blue; then ACTIVE=blue
else ACTIVE=none
fi

if [ "$STATUS_ONLY" = "1" ]; then
  echo "active color: $ACTIVE"
  exit 0
fi

case "$ACTIVE" in
  blue)  TARGET=green ;;
  green) TARGET=blue ;;
  none)  TARGET=blue ;;   # cold start
esac

echo "==> compose  : $COMPOSE"
echo "==> engine   : ${ENGINE:-<none>}"
echo "==> active   : $ACTIVE"
echo "==> deploying: $TARGET"

# --- rollback: restore the previously-deployed image ------------------------
if [ "$ROLLBACK" = "1" ]; then
  if ! image_exists "$ROLLBACK_IMAGE"; then
    echo "error: no $ROLLBACK_IMAGE saved — nothing to roll back to." >&2
    exit 1
  fi
  echo "==> rollback: restoring $ROLLBACK_IMAGE -> $IMAGE"
  $ENGINE tag "$ROLLBACK_IMAGE" "$IMAGE"
fi

# --- build the new image from the current checkout --------------------------
if [ "$BUILD" = "1" ]; then
  # Save the current image so `--rollback` can restore it after a bad deploy.
  if image_exists "$IMAGE"; then
    echo "==> saving current image as $ROLLBACK_IMAGE (for --rollback)"
    $ENGINE tag "$IMAGE" "$ROLLBACK_IMAGE" || true
  fi
  echo "==> building $IMAGE from current checkout"
  $SERVE build "app_$TARGET"
fi

# --- ensure the DB exists, OFF the serving critical path --------------------
# `ensure-db` is a no-op when the DB is already in the volume; on a truly cold
# start it builds it here (can be minutes) so the new color serves immediately
# — its healthcheck is never held hostage by a first build.
echo "==> ensuring database is built (init)"
$COMPOSE run --rm --no-deps init

# --- make sure the front proxy is up (no-op if already running) -------------
echo "==> ensuring caddy front proxy is up"
$COMPOSE up -d --no-deps caddy

# --- start the idle color from the new image --------------------------------
echo "==> starting app_$TARGET"
$SERVE up -d --no-deps --force-recreate "app_$TARGET"

# --- wait for the new color to become healthy -------------------------------
echo "==> waiting for app_$TARGET to pass /api/v1/health (timeout ${WAIT_TIMEOUT}s)"
deadline=$(( $(date +%s) + WAIT_TIMEOUT ))
healthy=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  if $SERVE exec -T "app_$TARGET" python -c \
      "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/v1/health',timeout=3).status==200 else 1)" \
      >/dev/null 2>&1; then
    healthy=1; break
  fi
  sleep 2
done

if [ "$healthy" != "1" ]; then
  echo "" >&2
  echo "error: app_$TARGET did not become healthy within ${WAIT_TIMEOUT}s." >&2
  echo "       The old color ($ACTIVE) is still serving — the site is unaffected." >&2
  echo "       Inspect:  $COMPOSE logs --tail=80 app_$TARGET" >&2
  echo "       Discard:  $SERVE rm -sf app_$TARGET" >&2
  exit 1
fi
echo "==> app_$TARGET is healthy"

# --- refresh the sync sidecar onto the new image (harmless restart) ---------
echo "==> refreshing sync sidecar onto the new image"
$COMPOSE up -d --no-deps --force-recreate sync || \
  echo "warning: could not refresh sync (continuing — the swap itself is done)" >&2

# --- cut over: stop + remove the old color ----------------------------------
if [ "$ACTIVE" != "none" ] && [ "$ACTIVE" != "$TARGET" ]; then
  echo "==> retiring old color app_$ACTIVE"
  # Caddy's active health checks + request retries have it out of rotation
  # within ~2s and retry in-flight requests to the new color, so this is clean.
  $SERVE rm -sf "app_$ACTIVE"
fi

# --- final smoke test through the front proxy -------------------------------
if command -v curl >/dev/null 2>&1; then
  if curl -fsS --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
    echo "==> OK: $HEALTH_URL is serving (via caddy -> app_$TARGET)"
  else
    echo "note: could not reach $HEALTH_URL from the host — normal if the port" >&2
    echo "      is bound to 127.0.0.1 behind a proxy; verify through your proxy." >&2
  fi
fi

echo "==> deploy complete — now serving color: app_$TARGET"
