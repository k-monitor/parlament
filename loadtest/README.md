# Load testing Parlamonitor

The serving tier is read-only and stateless, so scaling to thousands of users is
mostly about two numbers:

1. **CDN hit ratio** — how much traffic the edge absorbs (measured in the
   Cloudflare dashboard, not here). Climbs with volume; already working for
   `/api`, assets, and photos.
2. **Origin ceiling** — req/s the box sustains for *cache-misses* before p95
   latency degrades. That's what these scripts measure.

> ⚠️ These hit a live API and generate real DB load. Point them at a **staging
> instance or a dedicated serving replica**, or run during a quiet window. Both
> scripts default `BASE_URL` to localhost so you never nuke prod by accident.

## k6 (primary — realistic mix, self-bootstrapping)

Install: `brew install k6` / `sudo apt install k6` / https://k6.io/docs/get-started/installation/

```bash
# smoke test against local dev
k6 run loadtest/k6-loadtest.js

# size the origin: all requests miss the edge; ramp to 300 actions/s, hold 8m
BASE_URL=https://parlamonitor.hu MODE=origin PEAK_RPS=300 DURATION=8m \
  k6 run loadtest/k6-loadtest.js

# realistic path (lets the CDN cache the hot set) — model steady-state prod
BASE_URL=https://parlamonitor.hu MODE=mixed PEAK_RPS=150 \
  k6 run loadtest/k6-loadtest.js
```

**Finding the knee:** run `MODE=origin` and raise `PEAK_RPS` (100 → 200 → 400 …)
until the `http_req_duration p(95)` threshold goes red. The last passing rate,
minus headroom, is one replica's capacity. Divide your expected peak origin req/s
(peak users × miss-rate × req/user) by that to get replica count.

Read the summary: `http_reqs` = true req/s (an iteration fires 1–3 calls), the
`grp_*` trends break latency down per endpoint (search/trend are the expensive
ones), and `http_req_failed` should stay <1%.

## vegeta (quick blunt origin hammer)

Install: https://github.com/tsenart/vegeta

```bash
./loadtest/vegeta-origin.sh https://parlamonitor.hu 100 60s
```

Constant 100 req/s of cache-busted search+trend for 60s. Flat latency histogram =
the box holds that rate; a fat p99 tail = you found the knee below it.

## Interpreting against capacity planning

- Test **origin-direct** (BASE_URL = the origin hostname, bypassing Cloudflare) to
  measure the raw box, and **through the CDN with `MODE=origin`** to measure the
  full all-miss path including edge overhead.
- Watch the origin host meanwhile: CPU, and especially that the 1.2 GB DB stays
  in page cache (`free -h` — if `buff/cache` can't hold it, reads go to disk and
  latency spikes under load; that's the shared-VM RAM-contention symptom).
- A warm-mmap origin should do search/profile queries in single-to-low-tens of ms
  and sustain a few hundred cache-miss req/s per 2–4 vCPU replica.
