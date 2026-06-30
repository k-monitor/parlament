// Thin API client for the /api/v1 backend. One place owns the base URL and
// error handling; views call these helpers and never touch fetch directly.

const BASE = import.meta.env.VITE_API_BASE || '/api/v1'

async function get(path, params) {
  const url = new URL(BASE + path, location.origin)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v)
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

export const api = {
  meta: () => get('/meta'),
  // proceedings
  search: (params) => get('/proceedings/search', params),
  searchTrend: (params) => get('/proceedings/search/trend', params),
  searchBreakdown: (params) => get('/proceedings/search/breakdown', params),
  suggest: (q) => get('/proceedings/suggest', { q }),
  sessions: (period) => get('/proceedings/sessions', { period }),
  session: (id) => get(`/proceedings/sessions/${id}`),
  sessionWordcloud: (id) => get(`/proceedings/sessions/${id}/wordcloud`),
  sessionTopSpeakers: (id) => get(`/proceedings/sessions/${id}/top-speakers`),
  speech: (uid) => get(`/proceedings/speeches/${uid}`),
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
  // votes
  votes: (params) => get('/votes', params),
  vote: (id) => get(`/votes/${id}`),
  voteFacets: (params) => get('/votes/facets', params),
}
