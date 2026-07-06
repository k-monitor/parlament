#!/usr/bin/env bash
# Zero-downtime code/image deploy for Parlamonitor.
#
# DATA updates are already zero-downtime: the `sync` sidecar swaps the SQLite DB
# atomically and the read-only API picks it up on its next request. This script
# handles the OTHER kind of update — new application code / SPA — which would
# otherwise mean recreating the serving container and dropping the published
# port for a few seconds.
#
# It does a blue-green swap. The two serving colors (app_blue / app_green) are
# run with plain `podman` (works the same on any podman / compose version); the
# stable plumbing (caddy front, init, sync) is run with compose. The colors join
# caddy's compose network (detected at deploy time) and mount the same external
# DB volume this script owns. Exactly one color serves at a time; caddy load-
# balances across both with active health checks + request retries, so the swap
# drops no requests:
#   1. build the new image from the current checkout            (no downtime)
#   2. ensure the DB is present via `init`, OFF the serving path (no downtime)
#   3. start the IDLE color from the new image, wait for /health (no downtime)
#   4. retire the OLD color — caddy has been balancing across both and drains
#      to the new color
# If the new color never turns healthy, the old one keeps serving and the script
# exits non-zero, so a bad build cannot take the site down.
#
# Usage:
#   ./deploy.sh                 # build the current checkout, then swap
#   ./deploy.sh --no-build      # swap to the already-built :latest (e.g. re-flip)
#   ./deploy.sh --rollback      # restore the previous image and swap back to it
#   ./deploy.sh --status        # print which color is serving, then exit
#   ./deploy.sh --stop          # stop BOTH colors (maintenance); caddy stays up
#
# Env overrides:
#   COMPOSE="podman compose"    # force a specific compose command (plumbing)
#   ENGINE="podman"             # force a specific container engine (colors)
#   DEPLOY_WAIT_TIMEOUT=120     # seconds to wait for the new color's healthcheck
#   PARLAMONITOR_PORT=8000      # host port caddy publishes (for the smoke test)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# --- fixed resource names ---------------------------------------------------
VOL="parlamonitor_dbdata"          # external DB volume (matches docker-compose.yml)
IMAGE="parlamonitor:latest"
ROLLBACK_IMAGE="parlamonitor:rollback"
BLUE_CTR="parlamonitor_app_blue"
GREEN_CTR="parlamonitor_app_green"
NET=""                             # caddy's compose network, detected at deploy time

# --- pick a container engine (runs the colors + builds the image) -----------
if [ -n "${ENGINE:-}" ]; then
  :
elif command -v podman >/dev/null 2>&1; then
  ENGINE="podman"
elif command -v docker >/dev/null 2>&1; then
  ENGINE="docker"
else
  echo "error: neither podman nor docker found." >&2
  exit 1
fi

# --- pick a compose command (runs caddy / init / sync) ----------------------
if [ -n "${COMPOSE:-}" ]; then
  :
elif [ "$ENGINE" = "podman" ] && podman compose version >/dev/null 2>&1; then
  COMPOSE="podman compose"
elif command -v podman-compose >/dev/null 2>&1; then
  COMPOSE="podman-compose"
elif [ "$ENGINE" = "podman" ] && podman-compose >/dev/null 2>&1; then
  COMPOSE="podman-compose"
elif docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
else
  echo "error: no compose command found (podman compose / podman-compose / docker compose)." >&2
  exit 1
fi

PORT="${PARLAMONITOR_PORT:-8000}"
HEALTH_URL="http://localhost:${PORT}/api/v1/health"
WAIT_TIMEOUT="${DEPLOY_WAIT_TIMEOUT:-120}"

BUILD=1; ROLLBACK=0; STATUS_ONLY=0; STOP_ALL=0
for arg in "$@"; do
  case "$arg" in
    --no-build)  BUILD=0 ;;
    --rollback)  ROLLBACK=1; BUILD=0 ;;
    --status)    STATUS_ONLY=1 ;;
    --stop)      STOP_ALL=1 ;;
    -h|--help)   sed -n '2,40{/^#/!q;s/^# \{0,1\}//p}' "$0"; exit 0 ;;
    *)           echo "error: unknown argument: $arg (try --help)" >&2; exit 2 ;;
  esac
done

image_exists() { $ENGINE image inspect "$1" >/dev/null 2>&1; }
ctr_of()  { case "$1" in blue) echo "$BLUE_CTR" ;; green) echo "$GREEN_CTR" ;; esac; }
ctr_running() {  # $1 = exact container name ; 0 if running (portable podman/docker)
  $ENGINE ps --filter "status=running" --format '{{.Names}}' 2>/dev/null | grep -qx "$1"
}
color_running() { ctr_running "$(ctr_of "$1")"; }  # $1 = blue|green
caddy_cid() { $ENGINE ps -q --filter "name=caddy" --filter "status=running" 2>/dev/null | head -1; }
caddy_net() {  # the (compose-created) network the running caddy is attached to
  local cid; cid="$(caddy_cid)"; [ -n "$cid" ] || return 1
  $ENGINE inspect "$cid" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}' \
    2>/dev/null | grep -v '^$' | grep -vi '^podman$' | head -1
}

# --- which color is serving now? --------------------------------------------
if color_running green; then ACTIVE=green
elif color_running blue; then ACTIVE=blue
else ACTIVE=none
fi

if [ "$STATUS_ONLY" = "1" ]; then
  echo "active color: $ACTIVE"
  exit 0
fi

if [ "$STOP_ALL" = "1" ]; then
  echo "==> stopping both colors (caddy stays up; site will 502 until next deploy)"
  $ENGINE rm -f "$BLUE_CTR" "$GREEN_CTR" >/dev/null 2>&1 || true
  echo "==> done."
  exit 0
fi

case "$ACTIVE" in
  blue)  TARGET=green ;;
  green) TARGET=blue ;;
  none)  TARGET=blue ;;   # cold start
esac
TARGET_CTR="$(ctr_of "$TARGET")"

echo "==> engine   : $ENGINE"
echo "==> compose  : $COMPOSE"
echo "==> active   : $ACTIVE"
echo "==> deploying: $TARGET"

# --- ensure the shared external DB volume exists ----------------------------
$ENGINE volume inspect "$VOL" >/dev/null 2>&1 || { echo "==> creating volume $VOL"; $ENGINE volume create "$VOL" >/dev/null; }

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
  if image_exists "$IMAGE"; then
    echo "==> saving current image as $ROLLBACK_IMAGE (for --rollback)"
    $ENGINE tag "$IMAGE" "$ROLLBACK_IMAGE" || true
  fi
  echo "==> building $IMAGE from current checkout"
  $ENGINE build -t "$IMAGE" -f Dockerfile .
fi

if ! image_exists "$IMAGE"; then
  echo "error: image $IMAGE does not exist — run without --no-build to build it." >&2
  exit 1
fi

# --- ensure the DB exists, OFF the serving critical path --------------------
# `ensure-db` is a no-op when the DB is already in the volume; on a truly cold
# start it builds it here (can be minutes) so the new color serves immediately.
echo "==> ensuring database is built (init)"
$COMPOSE run --rm init

# --- make sure the front proxy is up, WITHOUT recreating it -----------------
# (recreating caddy would drop the published port — the one thing we must never
# do). Only `up` it when it isn't already running.
if [ -n "$(caddy_cid)" ]; then
  echo "==> caddy already running (leaving it bound to the port)"
else
  echo "==> starting caddy front proxy"
  $COMPOSE up -d caddy
fi

# --- detect the network caddy is on; the colors join it ---------------------
NET="$(caddy_net || true)"
if [ -z "$NET" ]; then
  echo "error: could not determine caddy's network — is caddy running?" >&2
  echo "       check: $COMPOSE logs --tail=50 caddy" >&2
  exit 1
fi
echo "==> serving network: $NET"

# --- collect the serving env for the color (same defaults as compose) -------
# Read .env so operator overrides apply; the container path defaults
# (PARLAMONITOR_DB / DATA_DIR / PHOTOS_DIR / FRONTEND_DIST) come from the image.
set -a; [ -f "$ROOT/.env" ] && . "$ROOT/.env" || true; set +a
color_env=(
  -e "PARLAMONITOR_MODULES=${PARLAMONITOR_MODULES:-}"
  -e "PARLAMONITOR_CORS_ORIGINS=${PARLAMONITOR_CORS_ORIGINS:-http://localhost:${PORT}}"
  -e "PARLAMONITOR_SITE_URL=${PARLAMONITOR_SITE_URL:-}"
  -e "PARLAMONITOR_API_CACHE_CONTROL=${PARLAMONITOR_API_CACHE_CONTROL:-}"
  -e "PARLAMONITOR_HTML_CACHE_CONTROL=${PARLAMONITOR_HTML_CACHE_CONTROL:-}"
)
[ -n "${PARLAMONITOR_WEB_WORKERS:-}" ]        && color_env+=(-e "PARLAMONITOR_WEB_WORKERS=${PARLAMONITOR_WEB_WORKERS}")
[ -n "${PARLAMONITOR_FORWARDED_ALLOW_IPS:-}" ] && color_env+=(-e "PARLAMONITOR_FORWARDED_ALLOW_IPS=${PARLAMONITOR_FORWARDED_ALLOW_IPS}")

# --- start the idle color from the new image --------------------------------
echo "==> starting $TARGET_CTR"
$ENGINE rm -f "$TARGET_CTR" >/dev/null 2>&1 || true   # clear any stale/failed leftover
$ENGINE run -d \
  --name "$TARGET_CTR" \
  --network "$NET" --network-alias "app_$TARGET" \
  --restart unless-stopped \
  -v "$ROOT/data:/data:ro" \
  -v "$VOL:/db" \
  "${color_env[@]}" \
  "$IMAGE" serve >/dev/null

# --- wait for the new color to become healthy ------------------------------
echo "==> waiting for $TARGET_CTR to pass /api/v1/health (timeout ${WAIT_TIMEOUT}s)"
deadline=$(( $(date +%s) + WAIT_TIMEOUT ))
healthy=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  if $ENGINE exec "$TARGET_CTR" python -c \
      "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/v1/health',timeout=3).status==200 else 1)" \
      >/dev/null 2>&1; then
    healthy=1; break
  fi
  # bail early if the container died on boot
  ctr_running "$TARGET_CTR" || { echo "   (container exited during boot)"; break; }
  sleep 2
done

if [ "$healthy" != "1" ]; then
  echo "" >&2
  echo "error: $TARGET_CTR did not become healthy within ${WAIT_TIMEOUT}s." >&2
  echo "       The old color ($ACTIVE) is still serving — the site is unaffected." >&2
  echo "       Inspect: $ENGINE logs --tail=80 $TARGET_CTR" >&2
  echo "       Discard: $ENGINE rm -f $TARGET_CTR" >&2
  exit 1
fi
echo "==> $TARGET_CTR is healthy"

# --- refresh the sync sidecar onto the new image ----------------------------
echo "==> refreshing sync sidecar onto the new image"
sync_ctrs="$($ENGINE ps -aq --filter 'name=_sync_' 2>/dev/null || true)"
[ -n "$sync_ctrs" ] && $ENGINE rm -f $sync_ctrs >/dev/null 2>&1 || true
$COMPOSE up -d sync || echo "warning: could not (re)start sync — the swap itself is done" >&2

# --- cut over: retire the old color -----------------------------------------
if [ "$ACTIVE" != "none" ] && [ "$ACTIVE" != "$TARGET" ]; then
  OLD_CTR="$(ctr_of "$ACTIVE")"
  echo "==> retiring old color $OLD_CTR"
  # caddy's active health checks + request retries have it out of rotation
  # within ~2s and retry in-flight requests to the new color, so this is clean.
  $ENGINE stop -t 10 "$OLD_CTR" >/dev/null 2>&1 || true
  $ENGINE rm -f "$OLD_CTR"      >/dev/null 2>&1 || true
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

echo "==> deploy complete — now serving color: app_$TARGET ($TARGET_CTR)"
