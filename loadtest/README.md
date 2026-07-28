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

# price the multi-cycle scope: every action spans 2–3 cycles
BASE_URL=https://parlamonitor.hu MODE=origin SCOPE=multi PEAK_RPS=100 \
  k6 run loadtest/k6-loadtest.js
```

**Finding the knee:** run `MODE=origin` and raise `PEAK_RPS` (100 → 200 → 400 …)
until the `http_req_duration p(95)` threshold goes red. The last passing rate,
minus headroom, is one replica's capacity. Divide your expected peak origin req/s
(peak users × miss-rate × req/user) by that to get replica count.

Read the summary: `http_reqs` = true req/s (an iteration fires 1–3 calls), the
`grp_*` trends break latency down per endpoint (search/trend are the expensive
ones), and `http_req_failed` should stay <1%.

### Cycle scope (multi-cycle selection)

The header's cycle chooser can put **several electoral cycles in scope at once**,
and `period` is a repeatable query param (`?period=42&period=43`; omitted = all
cycles). Scope width is a cost driver — `period IN (…)` instead of `= n`,
per-cycle aggregates summed rather than read, a search trend axis spanning every
selected cycle, and distinct-counts that can't be summed — so each action picks a
scope and every request it fires carries it, tagged `scope:single|multi|all`
(`none` for endpoints with no period param). The k6 summary then reports one p95
per scope width:

```text
{ scope:single }...: p(95)=172ms      ← the default (latest cycle only)
{ scope:multi }....: p(95)=233ms      ← 2–3 cycles
{ scope:all }......: p(95)=276ms      ← no period filter
```

Each width has its own threshold: `P95_MS` for single (and for the period-free
endpoints), `P95_MULTI_MS` (default 2 × `P95_MS`) and `P95_ALL_MS` (default 3 ×)
for the wider ones. The aggregate `http_req_duration` threshold uses the widest
budget, since the widest requests land in its p95 band — read the verdict from the
per-scope lines.

Knobs: `SCOPE=mix` (default) models real traffic — mostly the latest cycle alone,
`MULTI_PCT`% (25) spanning 2–3 cycles, `ALL_PCT`% (8) unscoped. `SCOPE=latest`,
`SCOPE=multi` or `SCOPE=all` pin one width, which is how you A/B what a wider
scope actually costs: run the same `PEAK_RPS` twice and compare. `vegeta-origin.sh`
takes the same `SCOPE` env var.

Two things to keep in mind:

- **Hit ratio.** In `MODE=mixed` the URL space multiplies by the scope variants in
  play, so the CDN hit ratio is lower than a single-cycle run would suggest. The
  test biases multi-cycle scopes toward the newest cycles (the hot combinations a
  reader reaches for when comparing to the previous cycle) with a tail of
  arbitrary combinations, which is roughly how that space is populated in reality.
- **Your DB needs the cycles.** Scopes are built from `/meta`, and MP profiles are
  scoped to cycles the MP actually served in (bootstrapped per cycle in `setup()`).
  Against a DB holding one cycle, `setup()` warns and no multi-cycle path is
  exercised — the `{ scope:multi }` threshold then passes with no samples.

## vegeta (quick blunt origin hammer)

Install: https://github.com/tsenart/vegeta

```bash
./loadtest/vegeta-origin.sh https://parlamonitor.hu 100 60s

# same, but every request spans the two newest cycles
SCOPE=multi ./loadtest/vegeta-origin.sh https://parlamonitor.hu 100 60s
```

Constant 100 req/s of cache-busted search+trend for 60s. Flat latency histogram =
the box holds that rate; a fat p99 tail = you found the knee below it. Cycle
scopes are read from `/meta` and mixed as above (`SCOPE=mix|latest|multi|all`);
unlike k6 there is no per-scope breakdown, so pin one width per run to compare.

## Interpreting against capacity planning

- Test **origin-direct** (BASE_URL = the origin hostname, bypassing Cloudflare) to
  measure the raw box, and **through the CDN with `MODE=origin`** to measure the
  full all-miss path including edge overhead.
- Watch the origin host meanwhile: CPU, and especially that the 1.2 GB DB stays
  in page cache (`free -h` — if `buff/cache` can't hold it, reads go to disk and
  latency spikes under load; that's the shared-VM RAM-contention symptom).
- A warm-mmap origin should do search/profile queries in single-to-low-tens of ms
  and sustain a few hundred cache-miss req/s per 2–4 vCPU replica.
