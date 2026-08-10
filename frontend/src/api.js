// Thin API client for the /api/v1 backend. One place owns the base URL and
// error handling; views call these helpers and never touch fetch directly.

const BASE = import.meta.env.VITE_API_BASE || '/api/v1'

async function get(path, params) {
  const url = new URL(BASE + path, location.origin)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null || v === '') continue
      // Arrays become repeated params (?name=A&name=B) for list-valued queries.
      if (Array.isArray(v)) {
        for (const item of v) if (item != null && item !== '') url.searchParams.append(k, item)
      } else {
        url.searchParams.set(k, v)
      }
    }
  }
  const res = await fetch(url)
  if (!res.ok) {
    const err = new Error(`API ${res.status}`)
    err.status = res.status
    throw err
  }
  return res.json()
}

// The one write the API accepts: the anonymous search-quality ping (PRIV-2).
// It must never disturb the page — every failure is swallowed — and `keepalive`
// lets it complete even if the click that fired it navigates away.
async function post(path, body) {
  try {
    await fetch(BASE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      keepalive: true,
    })
  } catch { /* analytics is best-effort, never a page error */ }
}

// Interjection speaker names recur across many speeches on a sitting day (the
// same MPs heckle repeatedly), so resolutions are cached process-wide: a name
// maps to a person object once resolved, or to `null` once known-unresolvable,
// and is never re-requested.
const _speakerCache = new Map()

async function resolveSpeakers(names) {
  const want = [...new Set(names)].filter((n) => n)
  const need = want.filter((n) => !_speakerCache.has(n))
  if (need.length) {
    const res = await get('/representatives/resolve', { name: need })
    for (const n of need) _speakerCache.set(n, (res.resolved && res.resolved[n]) || null)
  }
  const out = {}
  for (const n of want) {
    const p = _speakerCache.get(n)
    if (p) out[n] = p
  }
  return out
}

export const api = {
  meta: () => get('/meta'),
  resolveSpeakers,
  // Search analytics (PRIV-2): which result a reader opened and where in the
  // list it sat, counted onto the search's own aggregate row. Carries no
  // identifier of any kind — see lib/searchClicks.js.
  searchClick: (source, params, rank) => post('/search/click', { source, params, rank }),
  // proceedings
  search: (params) => get('/proceedings/search', params),
  searchTrend: (params) => get('/proceedings/search/trend', params),
  searchBreakdown: (params) => get('/proceedings/search/breakdown', params),
  suggest: (q) => get('/proceedings/suggest', { q }),
  sessions: (params) => get('/proceedings/sessions', params),
  session: (id) => get(`/proceedings/sessions/${id}`),
  sessionWordcloud: (id, limit) => get(`/proceedings/sessions/${id}/wordcloud`, { limit }),
  sessionTopSpeakers: (id) => get(`/proceedings/sessions/${id}/top-speakers`),
  sessionNewWords: (id) => get(`/proceedings/sessions/${id}/new-words`),
  speech: (uid) => get(`/proceedings/speeches/${uid}`),
  speechText: (uid) => get(`/proceedings/speeches/${uid}/text`),
  // Cropped HLS clip URLs for an arbitrary [start,end] window of a speech, for
  // the client-side exporter (VIE-10). start/end are day-absolute seconds.
  speechClip: (uid, start, end) => get(`/proceedings/speeches/${uid}/clip`, { start, end }),
  // representatives
  representatives: (params) => get('/representatives', params),
  representative: (id, period) => get(`/representatives/${id}`, { period }),
  repStatistics: (id, period) => get(`/representatives/${id}/statistics`, { period }),
  repActivity: (id, period) => get(`/representatives/${id}/activity`, { period }),
  repSpeechDays: (id, period) => get(`/representatives/${id}/speech-days`, { period }),
  repSpeeches: (id, params) => get(`/representatives/${id}/speeches`, params),
  repVoteDays: (id, period) => get(`/representatives/${id}/vote-days`, { period }),
  repVotes: (id, params) => get(`/representatives/${id}/votes`, params),
  factions: (period) => get('/representatives/factions', { period }),
  // Tisztségviselők (REP-11): one row per office term, not per person.
  officials: (params) => get('/representatives/officials', params),
  // Tárcák (§6C). Its own module, but its pages sit in the Representatives tab
  // bar (MIN-5). A tárca's iromány lists are `bills()` with `portfolio=` (MIN-7)
  // rather than endpoints of their own.
  portfolios: (params) => get('/portfolios', params),
  portfolio: (slug, period) => get(`/portfolios/${slug}`, { period }),
  portfolioTrend: (slug, period) => get(`/portfolios/${slug}/trend`, { period }),
  portfolioSpeeches: (slug, params) => get(`/portfolios/${slug}/speeches`, params),
  // "Who represents me?" (REP-10). Not period-scoped: constituency boundaries are
  // redrawn between elections, so the answer belongs to the cycle the boundary data
  // elects, which the response names.
  settlementSearch: (q, limit) => get('/representatives/constituencies/settlements', { q, limit }),
  settlementConstituencies: (maz, taz) =>
    get(`/representatives/constituencies/settlements/${maz}/${taz}`),
  // bills
  bills: (params) => get('/bills', params),
  bill: (id) => get(`/bills/${id}`),
  billFacets: (params) => get('/bills/facets', params),
  questionsSankey: (period, includeType = true, expandOther = false) =>
    get('/bills/questions/sankey',
        { period, include_type: includeType, expand_other: expandOther }),
  questionsList: (params) => get('/bills/questions/list', params),
  // votes
  votes: (params) => get('/votes', params),
  vote: (id) => get(`/votes/${id}`),
  voteFacets: (params) => get('/votes/facets', params),
  voteCohesion: (params) => get('/votes/cohesion', params),
}
