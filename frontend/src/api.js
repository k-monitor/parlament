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
  // bills
  bills: (params) => get('/bills', params),
  bill: (id) => get(`/bills/${id}`),
  billFacets: (params) => get('/bills/facets', params),
  questionsSankey: (period, includeType = true) =>
    get('/bills/questions/sankey', { period, include_type: includeType }),
  questionsList: (params) => get('/bills/questions/list', params),
  // votes
  votes: (params) => get('/votes', params),
  vote: (id) => get(`/votes/${id}`),
  voteFacets: (params) => get('/votes/facets', params),
  voteCohesion: (params) => get('/votes/cohesion', params),
}
