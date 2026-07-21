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
// USAGE:
//   # gentle smoke against local dev
//   k6 run loadtest/k6-loadtest.js
//
//   # size the origin: ramp arrival rate until p95 crosses the threshold
//   BASE_URL=https://parlamonitor.hu MODE=origin PEAK_RPS=300 DURATION=8m \
//     k6 run loadtest/k6-loadtest.js
//
// ENV KNOBS:
//   BASE_URL   default http://127.0.0.1:8000   (safety: does NOT default to prod)
//   MODE       mixed | origin                  (default mixed)
//   PEAK_RPS   target actions/sec at the ramp peak (default 50)
//   DURATION   hold time at peak                (default 3m)
//   P95_MS     latency threshold in ms          (default 500)

import http from 'k6/http'
import { check } from 'k6'
import { Trend, Counter } from 'k6/metrics'

const BASE = (__ENV.BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
const API = `${BASE}/api/v1`
const MODE = (__ENV.MODE || 'mixed').toLowerCase()
const PEAK_RPS = Number(__ENV.PEAK_RPS || 50)
const DURATION = __ENV.DURATION || '3m'
const P95_MS = Number(__ENV.P95_MS || 500)

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
    // The capacity verdict: if p95 crosses this at PEAK_RPS, that's your knee.
    http_req_failed: ['rate<0.01'],
    http_req_duration: [`p(95)<${P95_MS}`],
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

function req(name, path, params) {
  const q = params
    ? '?' + Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== null && v !== '')
        .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')
    : ''
  const res = http.get(bust(API + path + q), {
    tags: { name },
    headers: { 'Accept-Encoding': 'gzip, br' },
  })
  const ok = check(res, { [`${name} 2xx`]: (r) => r.status >= 200 && r.status < 300 })
  ;(byGroup[name] || byGroup.meta).add(res.timings.duration)
  ok ? okRate.add(1) : errRate.add(1)
  return res
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
  const periods = (meta.periods || []).map((p) => p.number).filter((n) => n != null)
  const period = periods.length ? Math.max(...periods) : undefined

  const reps = pluckIds(jget(`${API}/representatives?period=${period ?? ''}&limit=50`),
                        ['id', 'person_id', 'uid'])
  const sessions = pluckIds(jget(`${API}/proceedings/sessions?limit=50`),
                            ['id', 'uid', 'number'])
  const bills = pluckIds(jget(`${API}/bills?limit=50`), ['id', 'uid', 'number'])
  const votes = pluckIds(jget(`${API}/votes?limit=50`), ['id', 'uid'])

  console.log(`[setup] period=${period} reps=${reps.length} sessions=${sessions.length} ` +
              `bills=${bills.length} votes=${votes.length} | MODE=${MODE} BASE=${BASE}`)
  return { period, reps, sessions, bills, votes }
}

// --- the weighted user-action mix ------------------------------------------

export default function (data) {
  const r = Math.random()
  const period = data.period

  if (r < 0.45) {
    // Search page: the dominant, most expensive path (FTS). The SPA fires the
    // result list plus the trend + breakdown aggregations together.
    const q = pick(TERMS)
    req('search', '/proceedings/search', { q, period, sort: 'relevance', limit: 20 })
    if (Math.random() < 0.7) req('trend', '/proceedings/search/trend', { q, period })
    if (Math.random() < 0.4) req('search', '/proceedings/search/breakdown', { q, period })
  } else if (r < 0.55) {
    // Type-ahead: several suggest calls as someone types a prefix.
    const t = pick(TERMS).replace(/"/g, '')
    req('suggest', '/proceedings/suggest', { q: t.slice(0, 3) })
    req('suggest', '/proceedings/suggest', { q: t.slice(0, 5) })
  } else if (r < 0.72 && data.reps.length) {
    // MP profile: list detail + statistics + recent speeches (mirrors the SPA).
    const id = pick(data.reps)
    req('rep', `/representatives/${id}`, { period })
    req('rep', `/representatives/${id}/statistics`, { period })
    if (Math.random() < 0.6) req('rep', `/representatives/${id}/speeches`, { limit: 20 })
  } else if (r < 0.82 && data.sessions.length) {
    const id = pick(data.sessions)
    req('sessions', `/proceedings/sessions/${id}`)
    if (Math.random() < 0.5) req('sessions', `/proceedings/sessions/${id}/wordcloud`, { limit: 60 })
  } else if (r < 0.9 && data.bills.length) {
    req('bills', '/bills', { period, limit: 20 })
    if (Math.random() < 0.5) req('bills', '/bills/questions/sankey', { period, include_type: true })
  } else if (r < 0.97 && data.votes.length) {
    req('votes', '/votes', { period, limit: 20 })
    if (Math.random() < 0.5) req('votes', '/votes/cohesion', { period })
  } else {
    req('meta', '/meta')
  }
}
