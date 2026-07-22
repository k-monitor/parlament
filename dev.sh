#!/usr/bin/env bash
# Start the dev backend (FastAPI/uvicorn) and frontend (Vite) together.
# Backend: http://localhost:8000  ·  Frontend: http://localhost:5173 (proxies /api + /media → 8000)
# Ctrl+C stops both.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

# --- backend ---------------------------------------------------------------
if [[ ! -x "$BACKEND/.venv/bin/uvicorn" ]]; then
  echo "error: $BACKEND/.venv not found — create it and install backend/requirements.txt" >&2
  exit 1
fi

# --- preflight: backend port -----------------------------------------------
# If 8000 is already taken (e.g. a leftover uvicorn), uvicorn dies with
# "Address already in use" and the whole script tears down — surface it clearly.
if ss -ltn 2>/dev/null | grep -q ':8000 '; then
  echo "error: port 8000 is already in use. Stop the process holding it:" >&2
  echo "  ss -ltnp | grep ':8000'   # find the PID, then: kill <pid>" >&2
  exit 1
fi

# --- frontend deps ---------------------------------------------------------
if [[ ! -d "$FRONTEND/node_modules" ]]; then
  echo "==> installing frontend deps (node_modules missing)"
  (cd "$FRONTEND" && npm install)
fi

pids=()
cleanup() {
  trap - INT TERM EXIT
  echo
  echo "==> stopping dev servers"
  kill "${pids[@]}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "==> backend  → http://localhost:8000  (uvicorn --reload)"
(cd "$BACKEND" && exec .venv/bin/uvicorn app.main:app --reload --port 8000) &
pids+=($!)

echo "==> frontend → http://localhost:5173  (vite)"
(cd "$FRONTEND" && exec npm run dev) &
pids+=($!)

# If either process exits, tear everything down.
wait -n
