#!/usr/bin/env bash
# Quick constant-rate ORIGIN stress with vegeta — a blunt complement to the k6
# script. Every request gets a unique _cb= param so it misses the Cloudflare edge
# and hits the origin; run it at a fixed rate to see whether the box holds that
# rate with a flat latency profile (good) or starts queuing (p99 climbs → knee).
#
#   ./loadtest/vegeta-origin.sh https://parlamonitor.hu 100 60s
#     arg1 BASE_URL   (required — no prod default, on purpose)
#     arg2 RATE       requests/sec           (default 50)
#     arg3 DURATION   e.g. 30s, 2m           (default 30s)
#     env  SCOPE      mix (default) | latest | multi | all — cycle scope width
#
# Cycle scope: since the reader can put several electoral cycles in scope at once,
# `period` is a repeatable param and its width drives query cost. The targets mix
# widths the way the k6 script does (mostly the latest cycle, some two-cycle, a
# few all-cycles); SCOPE=… pins one width to price it on its own.
#
# Install: https://github.com/tsenart/vegeta  (or: go install github.com/tsenart/vegeta/v12@latest)
set -euo pipefail

BASE="${1:?usage: vegeta-origin.sh BASE_URL [RATE] [DURATION]}"
RATE="${2:-50}"
DURATION="${3:-30s}"
SCOPE="${SCOPE:-mix}"
API="${BASE%/}/api/v1"

TERMS=(költségvetés egészségügy oktatás korrupció infláció nyugdíj energia rezsi \
       adó migráció honvédelem Ukrajna gazdaság minimálbér lakhatás klíma egyetem \
       kórház rendőrség bíróság választás alkotmány pedagógus forint benzin vasút)

# The available cycles, newest-first, from the manifest — so the scopes we send
# are real (an unknown period number would just filter everything out and make
# the queries look artificially cheap).
PERIODS=($(curl -fsS --max-time 10 "${API}/meta" \
  | python3 -c 'import json,sys; print(*sorted((p["number"] for p in json.load(sys.stdin).get("periods",[])), reverse=True))'))
[ "${#PERIODS[@]}" -gt 0 ] || { echo "could not read cycles from ${API}/meta" >&2; exit 1; }
echo "cycles: ${PERIODS[*]}  (scope mode: ${SCOPE})"

# Print the `period` params for one request, per the scope mode.
scope_params() {
  local mode="$SCOPE"
  if [ "$mode" = mix ]; then
    case $((RANDOM % 100)) in
      [0-7]) mode=all ;;                       #  ~8% all cycles
      [0-9]|1[0-9]|2[0-9]|3[0-2]) mode=multi ;; # ~25% two cycles
      *) mode=latest ;;
    esac
  fi
  case "$mode" in
    all) ;;                                    # no period param = every cycle
    latest) printf '&period=%s' "${PERIODS[0]}" ;;
    multi)  # the newest two, ascending (the canonical order the SPA sends)
      if [ "${#PERIODS[@]}" -ge 2 ]; then
        printf '&period=%s&period=%s' "${PERIODS[1]}" "${PERIODS[0]}"
      else printf '&period=%s' "${PERIODS[0]}"; fi ;;
    *) echo "unknown SCOPE=$SCOPE (mix|latest|multi|all)" >&2; exit 1 ;;
  esac
}

# Build a targets file: a cache-busted search + trend for each term, cycled.
targets="$(mktemp)"
trap 'rm -f "$targets"' EXIT
for i in $(seq 1 200); do
  t="${TERMS[$((RANDOM % ${#TERMS[@]}))]}"
  q=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$t")
  cb="${RANDOM}${i}$(date +%s%N)"
  # Both calls of a page view share one scope, as the global chooser dictates.
  p="$(scope_params)"
  echo "GET ${API}/proceedings/search?q=${q}${p}&sort=relevance&limit=20&_cb=${cb}"
  echo "GET ${API}/proceedings/search/trend?q=${q}${p}&_cb=${cb}b"
done > "$targets"

echo "== vegeta: ${RATE}/s for ${DURATION} against ${API} (all cache-miss) =="
vegeta attack -targets="$targets" -rate="${RATE}" -duration="${DURATION}" -header="Accept-Encoding: gzip" \
  | tee /tmp/vegeta.bin \
  | vegeta report

echo
echo "Latency histogram:"
vegeta report -type='hist[0,10ms,25ms,50ms,100ms,250ms,500ms,1s,2s]' /tmp/vegeta.bin
