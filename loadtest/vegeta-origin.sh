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
#
# Install: https://github.com/tsenart/vegeta  (or: go install github.com/tsenart/vegeta/v12@latest)
set -euo pipefail

BASE="${1:?usage: vegeta-origin.sh BASE_URL [RATE] [DURATION]}"
RATE="${2:-50}"
DURATION="${3:-30s}"
API="${BASE%/}/api/v1"

TERMS=(költségvetés egészségügy oktatás korrupció infláció nyugdíj energia rezsi \
       adó migráció honvédelem Ukrajna gazdaság minimálbér lakhatás klíma egyetem \
       kórház rendőrség bíróság választás alkotmány pedagógus forint benzin vasút)

# Build a targets file: a cache-busted search + trend for each term, cycled.
targets="$(mktemp)"
trap 'rm -f "$targets"' EXIT
for i in $(seq 1 200); do
  t="${TERMS[$((RANDOM % ${#TERMS[@]}))]}"
  q=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$t")
  cb="${RANDOM}${i}$(date +%s%N)"
  echo "GET ${API}/proceedings/search?q=${q}&sort=relevance&limit=20&_cb=${cb}"
  echo "GET ${API}/proceedings/search/trend?q=${q}&_cb=${cb}b"
done > "$targets"

echo "== vegeta: ${RATE}/s for ${DURATION} against ${API} (all cache-miss) =="
vegeta attack -targets="$targets" -rate="${RATE}" -duration="${DURATION}" -header="Accept-Encoding: gzip" \
  | tee /tmp/vegeta.bin \
  | vegeta report

echo
echo "Latency histogram:"
vegeta report -type='hist[0,10ms,25ms,50ms,100ms,250ms,500ms,1s,2s]' /tmp/vegeta.bin
