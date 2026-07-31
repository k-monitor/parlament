// Parlamonitor load test (k6).
//
// The serving tier is read-only + stateless, so the capacity question is:
// "how many requests/second can the ORIGIN sustain when Cloudflare passes a
// cache-miss through, before p95 latency degrades?" This script answers that.
//
// It self-bootstraps real IDs from the list endpoints in setup(), then drives a
// weighted mix that mirrors how the SPA actually calls the API (a search page
// fires search + trend + breakdown; opening a profile fires several calls; etc.
// — see frontend/src/api.js). Requests are tagged with a stable `name` so k6
// groups metrics per endpoint instead of exploding on the query string.
//
// TWO MODES (env MODE):
//   mixed  (default) — repeats a fixed pool of hot queries. Exercises the real
//                      CDN+origin path as production would; hit ratio climbs.
//   origin           — appends a unique _cb= param to every request, busting the
//                      Cloudflare edge cache so EVERY request reaches the origin.
//                      This is the one that sizes your box. Point BASE_URL at the
//                      CDN domain to test the full path under all-miss, or at the
//                      origin hostname directly to exclude the edge entirely.
//
// CYCLE SCOPE (§4A): the header's cycle chooser lets a reader put SEVERAL
// electoral cycles in scope at once, and every period-aware endpoint takes
// `period` as a *repeatable* param (?period=42&period=43; omitted = all cycles).
// Scope drives cost, so it is modelled explicitly rather than pinned to the
// latest cycle: a wider scope means `period IN (…)` instead of `= n`, per-cycle
// aggregates summed rather than read, a trend axis spanning several cycles, and
// DISTINCT counts that can't be summed. It also multiplies the URL space, so it
// dilutes the CDN hit ratio in `mixed` mode. Requests carry a `scope` tag
// (single | multi | all | none), and the thresholds below give each its own p95
// budget so the summary reports one knee per scope width.
//
// USAGE:
//   # gentle smoke against local dev
//   k6 run loadtest/k6-loadtest.js
//
//   # size the origin: ramp arrival rate until p95 crosses the threshold
//   BASE_URL=https://parlamonitor.k-monitor.hu MODE=origin PEAK_RPS=300 DURATION=8m \
//     k6 run loadtest/k6-loadtest.js
//
//   # what does a multi-cycle scope cost? force every action to span cycles
//   BASE_URL=https://parlamonitor.k-monitor.hu MODE=origin SCOPE=multi PEAK_RPS=100 \
//     k6 run loadtest/k6-loadtest.js
//
// ENV KNOBS:
//   BASE_URL     default http://127.0.0.1:8000   (safety: does NOT default to prod)
//   MODE         mixed | origin                  (default mixed)
//   PEAK_RPS     target actions/sec at the ramp peak (default 50)
//   DURATION     hold time at peak                (default 3m)
//   P95_MS       latency threshold in ms, single-cycle scope   (default 500)
//   P95_MULTI_MS same for a multi-cycle scope     (default 2 × P95_MS)
//   P95_ALL_MS   same for the all-cycles scope    (default 3 × P95_MS)
//   SCOPE        mix (default) | latest | multi | all — force one scope width
//   MULTI_PCT    % of actions with 2–3 cycles in scope, when SCOPE=mix (default 25)
//   ALL_PCT      % of actions with no period filter, when SCOPE=mix  (default 8)

import http from 'k6/http'
import { check } from 'k6'
import { Trend, Counter } from 'k6/metrics'

const BASE = (__ENV.BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
const API = `${BASE}/api/v1`
const MODE = (__ENV.MODE || 'mixed').toLowerCase()
const PEAK_RPS = Number(__ENV.PEAK_RPS || 50)
const DURATION = __ENV.DURATION || '3m'
const P95_MS = Number(__ENV.P95_MS || 500)
// A wider cycle scope is legitimately more work, so it gets a looser budget —
// otherwise the multi-cycle traffic would set the verdict for the whole run.
const P95_MULTI_MS = Number(__ENV.P95_MULTI_MS || P95_MS * 2)
const P95_ALL_MS = Number(__ENV.P95_ALL_MS || P95_MS * 3)
const SCOPE = (__ENV.SCOPE || 'mix').toLowerCase()
const MULTI_PCT = Number(__ENV.MULTI_PCT || 25)
const ALL_PCT = Number(__ENV.ALL_PCT || 8)

// A typo'd SCOPE would silently fall back to the mix and quietly answer a
// different question than the one asked, so refuse to start.
if (!['mix', 'latest', 'multi', 'all'].includes(SCOPE)) {
  throw new Error(`SCOPE must be mix | latest | multi | all (got "${SCOPE}")`)
}

// Realistic Hungarian policy/political search terms — high enough cardinality to
// exercise FTS without being nonsense. In `origin` mode each also gets a unique
// cache-buster appended, so even repeats miss the edge.
const TERMS = [
  'költségvetés', 'egészségügy', 'oktatás', 'korrupció', 'infláció', 'nyugdíj',
  'energia', 'rezsi', 'adó', 'migráció', 'honvédelem', 'Ukrajna', 'gazdaság',
  'minimálbér', 'lakhatás', 'klíma', 'mezőgazdaság', 'közlekedés', 'egyetem',
  'kórház', 'rendőrség', 'bíróság', 'választás', 'alkotmány', 'önkormányzat',
  'pedagógus', 'orvos', 'család', 'vállalkozás', 'forint', 'benzin', 'vasút',
  'MÁV', 'NAV', 'áfa', 'uniós források', 'gyermekvédelem', 'devizahitel',
  '"korrupció elleni"', '"minimálbér emelés"', 'brüsszel', 'szankció',
]

const okRate = new Counter('app_ok')
const errRate = new Counter('app_err')
const byGroup = {
  meta: new Trend('grp_meta', true),
  search: new Trend('grp_search', true),
  trend: new Trend('grp_trend', true),
  suggest: new Trend('grp_suggest', true),
  sessions: new Trend('grp_sessions', true),
  rep: new Trend('grp_rep', true),
  bills: new Trend('grp_bills', true),
  votes: new Trend('grp_votes', true),
}

export const options = {
  scenarios: {
    ramp: {
      executor: 'ramping-arrival-rate',
      // "action/sec" — one iteration models one user action (a page view),
      // which may fire 1–3 HTTP calls. Watch http_reqs in the summary for the
      // true req/s. Arrival-rate keeps the offered load independent of latency,
      // so a slowing origin shows up as rising latency, not falling throughput.
      startRate: Math.max(1, Math.round(PEAK_RPS * 0.1)),
      timeUnit: '1s',
      preAllocatedVUs: Math.max(20, PEAK_RPS),
      maxVUs: Math.max(50, PEAK_RPS * 6),
      stages: [
        { target: Math.round(PEAK_RPS * 0.3), duration: '1m' },
        { target: Math.round(PEAK_RPS * 0.6), duration: '1m' },
        { target: PEAK_RPS, duration: '1m' },
        { target: PEAK_RPS, duration: DURATION },
        { target: 0, duration: '30s' },
      ],
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    // The capacity verdict: if p95 crosses its budget at PEAK_RPS, that's your
    // knee. The aggregate mixes scope widths, and the widest few percent of
    // requests land squarely in its p95 band — so it gets the widest budget, and
    // the verdict per scope width is read from the sub-metrics below.
    http_req_duration: [`p(95)<${P95_ALL_MS}`],
    // Per-scope-width budgets. These also make k6 print the sub-metric for each
    // scope in the summary, which is the actual multi-cycle read-out: how much a
    // 2–3 cycle scope (or none at all) costs versus a single cycle. A scope that
    // never occurs — e.g. `multi` against a one-cycle dev DB — has no samples and
    // passes vacuously.
    'http_req_duration{scope:none}': [`p(95)<${P95_MS}`],  // period-free endpoints
    'http_req_duration{scope:single}': [`p(95)<${P95_MS}`],
    'http_req_duration{scope:multi}': [`p(95)<${P95_MULTI_MS}`],
    'http_req_duration{scope:all}': [`p(95)<${P95_ALL_MS}`],
  },
  // Keep the summary readable; per-name tags already group the detail.
  summaryTrendStats: ['avg', 'p(50)', 'p(90)', 'p(95)', 'p(99)', 'max'],
}

// --- helpers ---------------------------------------------------------------

function pick(arr) { return arr[Math.floor(Math.random() * arr.length)] }

// Bust the CF edge cache in origin mode (CF keys on the full URL incl. query).
function bust(url) {
  if (MODE !== 'origin') return url
  const cb = `${Date.now()}-${__VU}-${__ITER}-${Math.random().toString(36).slice(2)}`
  return url + (url.includes('?') ? '&' : '?') + `_cb=${cb}`
}

// Build a query string the way frontend/src/api.js does: skip empty values, and
// expand an array into a repeated param (?period=42&period=43). Array order is
// preserved, and scopes are built sorted ascending, so a given scope always
// produces the exact same URL — which is what both the CDN and the backend's
// aggregate cache key on.
function qs(params) {
  if (!params) return ''
  const parts = []
  for (const [k, v] of Object.entries(params)) {
    for (const item of Array.isArray(v) ? v : [v]) {
      if (item === undefined || item === null || item === '') continue
      parts.push(`${k}=${encodeURIComponent(item)}`)
    }
  }
  return parts.length ? '?' + parts.join('&') : ''
}

// `scope` is the cycle scope this request belongs to (see pickScope); pass none
// for endpoints that take no period, and the request is tagged `scope:none`.
function req(name, path, params, scope) {
  const res = http.get(bust(API + path + qs(params)), {
    tags: { name, scope: scope ? scope.kind : 'none' },
    headers: { 'Accept-Encoding': 'gzip, br' },
  })
  const ok = check(res, { [`${name} 2xx`]: (r) => r.status >= 200 && r.status < 300 })
  ;(byGroup[name] || byGroup.meta).add(res.timings.duration)
  ok ? okRate.add(1) : errRate.add(1)
  return res
}

// --- cycle scope ------------------------------------------------------------

// A scope is `{ periods, kind }`: the period numbers to send (ascending, so the
// URL is canonical; empty = send no `period` at all = all cycles) and the width
// label used for tagging.
function scopeOf(periods) {
  const nums = [...new Set(periods)].sort((a, b) => a - b)
  return {
    periods: nums,
    kind: nums.length === 0 ? 'all' : nums.length === 1 ? 'single' : 'multi',
  }
}

// 2–3 cycles in scope. Mostly the newest ones — the chooser's default is the
// latest cycle, so "add the previous one to compare" is the common gesture and
// the hot cache set. The remainder is an arbitrary combination (someone
// comparing two older cycles), which is the long tail that misses the edge.
function multiScope(periods) {
  const k = Math.min(periods.length, Math.random() < 0.3 ? 3 : 2)
  if (Math.random() < 0.8) return scopeOf(periods.slice(0, k))  // periods = newest-first
  const pool = [...periods]
  const out = []
  while (out.length < k && pool.length) {
    out.push(pool.splice(Math.floor(Math.random() * pool.length), 1)[0])
  }
  return scopeOf(out)
}

// One user's cycle scope for one action. In `mix` mode most readers keep the
// default (latest cycle only), a minority widen it, and a few clear it to all
// cycles; SCOPE=latest|multi|all forces one width for A/B measurement.
function pickScope(periods) {
  if (!periods.length) return scopeOf([])
  if (SCOPE === 'all') return scopeOf([])
  if (SCOPE === 'latest') return scopeOf([periods[0]])
  if (SCOPE === 'multi') return multiScope(periods)
  const r = Math.random() * 100
  if (r < ALL_PCT) return scopeOf([])
  if (r < ALL_PCT + MULTI_PCT && periods.length > 1) return multiScope(periods)
  // Single cycle: usually the default (latest), occasionally an older one.
  return scopeOf([Math.random() < 0.85 ? periods[0] : pick(periods)])
}

// Find an array of records in an unknown-shaped list response and pull id-like
// fields, so detail endpoints hit real data regardless of the exact envelope.
function pluckIds(body, keys) {
  let arr = null
  if (Array.isArray(body)) arr = body
  else if (body && typeof body === 'object') {
    for (const k of ['items', 'results', 'data', 'representatives', 'bills',
                      'votes', 'sessions', 'rows']) {
      if (Array.isArray(body[k])) { arr = body[k]; break }
    }
  }
  if (!arr) return []
  const out = []
  for (const row of arr) {
    for (const idk of keys) {
      if (row && row[idk] != null) { out.push(String(row[idk])); break }
    }
  }
  return out
}

function jget(url) {
  const r = http.get(url)
  try { return r.status === 200 ? r.json() : null } catch (_) { return null }
}

// --- bootstrap real IDs ----------------------------------------------------

export function setup() {
  const meta = jget(`${API}/meta`) || {}
  // Newest-first, as /meta returns them — the order pickScope/multiScope assume.
  const periods = (meta.periods || [])
    .map((p) => p.number).filter((n) => n != null)
    .sort((a, b) => b - a)

  // MPs per cycle, so a profile action can be scoped to cycles the MP actually
  // served in. Scoping a profile to a cycle the MP sat out returns empty
  // aggregates — cheap, and nothing like the query a real reader triggers.
  const repsByPeriod = {}
  for (const p of periods) {
    repsByPeriod[p] = pluckIds(jget(`${API}/representatives${qs({ period: p, limit: 50 })}`),
                               ['id', 'person_id', 'uid'])
  }
  const reps = pluckIds(jget(`${API}/representatives${qs({ limit: 50 })}`),
                        ['id', 'person_id', 'uid'])
  const sessions = pluckIds(jget(`${API}/proceedings/sessions?limit=50`),
                            ['id', 'uid', 'number'])
  const bills = pluckIds(jget(`${API}/bills?limit=50`), ['id', 'uid', 'number'])
  const votes = pluckIds(jget(`${API}/votes?limit=50`), ['id', 'uid'])

  const rosters = periods.map((p) => `${p}:${(repsByPeriod[p] || []).length}`).join(' ')
  console.log(`[setup] periods=[${periods.join(',')}] rosters=${rosters} reps=${reps.length} ` +
              `sessions=${sessions.length} bills=${bills.length} votes=${votes.length} | ` +
              `MODE=${MODE} SCOPE=${SCOPE} BASE=${BASE}`)
  if (periods.length < 2) {
    console.warn('[setup] only one electoral cycle in this DB — no multi-cycle scope ' +
                 'will be exercised; run against a full DB to size that path')
  }
  return { periods, reps, repsByPeriod, sessions, bills, votes }
}

// MPs reachable under a scope: the union of the scoped cycles' rosters (or every
// MP when the scope is all cycles).
function repsInScope(data, scope) {
  if (!scope.periods.length) return data.reps
  const out = []
  for (const p of scope.periods) out.push(...(data.repsByPeriod[p] || []))
  return out.length ? out : data.reps
}

// --- the weighted user-action mix ------------------------------------------

export default function (data) {
  const r = Math.random()
  // One scope per action: the chooser is global, so every call an action fires
  // carries the same cycle scope — exactly as the SPA does when it re-fetches a
  // page on a scope change.
  const scope = pickScope(data.periods)
  const period = scope.periods            // [] → `qs` omits the param → all cycles

  if (r < 0.45) {
    // Search page: the dominant, most expensive path (FTS). The SPA fires the
    // result list plus the trend + breakdown aggregations together. A multi-cycle
    // scope widens all three: more matching sentences to rank, and a trend axis
    // spanning every selected cycle rather than one.
    const q = pick(TERMS)
    req('search', '/proceedings/search', { q, period, sort: 'relevance', limit: 20 }, scope)
    if (Math.random() < 0.7) req('trend', '/proceedings/search/trend', { q, period }, scope)
    if (Math.random() < 0.4) req('search', '/proceedings/search/breakdown', { q, period }, scope)
  } else if (r < 0.55) {
    // Type-ahead: several suggest calls as someone types a prefix. Not
    // period-aware (it ranks on the all-cycles aggregate), so no scope.
    const t = pick(TERMS).replace(/"/g, '')
    req('suggest', '/proceedings/suggest', { q: t.slice(0, 3) })
    req('suggest', '/proceedings/suggest', { q: t.slice(0, 5) })
  } else if (r < 0.72) {
    // MP profile: detail + statistics + recent speeches (mirrors the SPA, which
    // scopes every call on the page). Under a multi-cycle scope the statistics
    // are summed over the per-cycle aggregate rows instead of read from one.
    const roster = repsInScope(data, scope)
    if (roster.length) {
      const id = pick(roster)
      req('rep', `/representatives/${id}`, { period }, scope)
      req('rep', `/representatives/${id}/statistics`, { period }, scope)
      if (Math.random() < 0.6) {
        req('rep', `/representatives/${id}/speeches`, { period, limit: 20 }, scope)
      }
    }
  } else if (r < 0.82 && data.sessions.length) {
    // Sittings: arriving via the (period-scoped) list, then opening a day. The
    // detail and its wordcloud are addressed by id, so they carry no scope.
    if (Math.random() < 0.4) req('sessions', '/proceedings/sessions', { period, limit: 50 }, scope)
    const id = pick(data.sessions)
    req('sessions', `/proceedings/sessions/${id}`)
    if (Math.random() < 0.5) req('sessions', `/proceedings/sessions/${id}/wordcloud`, { limit: 60 })
  } else if (r < 0.9 && data.bills.length) {
    req('bills', '/bills', { period, limit: 20 }, scope)
    if (Math.random() < 0.5) {
      req('bills', '/bills/questions/sankey', { period, include_type: true }, scope)
    }
  } else if (r < 0.97 && data.votes.length) {
    req('votes', '/votes', { period, limit: 20 }, scope)
    // Cohesion is the heaviest per-scope aggregate: a DISTINCT-style computation
    // over every selected cycle's per-MP votes, and it can't be summed.
    if (Math.random() < 0.5) req('votes', '/votes/cohesion', { period }, scope)
  } else {
    req('meta', '/meta')
  }
}
