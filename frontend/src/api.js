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
  // The order paper for the sitting that is coming (NR-5). Cycle-less:
  // there is only ever one next sitting.
  upcomingAgenda: () => get('/proceedings/upcoming'),
  session: (id) => get(`/proceedings/sessions/${id}`),
  sessionWordcloud: (id, limit) => get(`/proceedings/sessions/${id}/wordcloud`, { limit }),
  sessionTopSpeakers: (id) => get(`/proceedings/sessions/${id}/top-speakers`),
  sessionNewWords: (id) => get(`/proceedings/sessions/${id}/new-words`),
  speech: (uid) => get(`/proceedings/speeches/${uid}`),
  speechText: (uid) => get(`/proceedings/speeches/${uid}/text`),
  // Cropped HLS clip URLs for an arbitrary [start,end] window of a speech, for
  // the client-side exporter (VIE-10). start/end are day-absolute seconds.
  speechClip: (uid, start, end) => get(`/proceedings/speeches/${uid}/clip`, { start, end }),
  // Témák (TOPIC-9) — the CAP topic mix of the floor, and one topic's owners.
  // The mix carries its own over-time buckets (they come out of the same scan),
  // so the page's chart and its trend are one request; the detail is fetched only
  // when a topic is opened. The document half is `billTopicMix` below: same
  // shape, other module, because the two topic passes read different tables and
  // switch off independently.
  topicMix: (period) => get('/proceedings/topics', { period }),
  topicDetail: (label, period) =>
    get(`/proceedings/topics/${encodeURIComponent(label)}`, { period }),
  // One member's slice of the same blocks (TOPIC-10), for the figure on their
  // profile. Every row carries the floor's own share beside it, so the profile
  // never has to fetch the House mix separately to draw its reference.
  repTopics: (id, period) =>
    get(`/proceedings/topics/representative/${id}`, { period }),
  // representatives
  representatives: (params) => get('/representatives', params),
  representative: (id, period) => get(`/representatives/${id}`, { period }),
  repStatistics: (id, period) => get(`/representatives/${id}/statistics`, { period }),
  repActivity: (id, period) => get(`/representatives/${id}/activity`, { period }),
  repSpeechDays: (id, period) => get(`/representatives/${id}/speech-days`, { period }),
  repSpeeches: (id, params) => get(`/representatives/${id}/speeches`, params),
  repVoteDays: (id, period) => get(`/representatives/${id}/vote-days`, { period }),
  repVotes: (id, params) => get(`/representatives/${id}/votes`, params),
  // Several people side by side (REP-15) — one request for the whole spec sheet,
  // since the page cannot draw a row's bars until every column's figure is in.
  // `ids` is an array; api.js sends it as the repeated `id` param the API takes.
  repCompare: (ids, period) => get('/representatives/compare', { id: ids, period }),
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
  // Bizottságok (§6F). Its own module, but — like Tárcák — its pages live in
  // the Representatives section's tab bar (BIZ-2). The meeting and document
  // lists are paged endpoints of their own rather than part of the sheet: a
  // committee of a full cycle has hundreds of each, and the sheet is what the
  // page needs before it can draw anything.
  committees: (params) => get('/committees', params),
  // What the committees are about to do (BIZ-14) — the committee-side
  // counterpart of the sitting list's upcoming block (NR-5).
  committeesUpcoming: (period) => get('/committees/upcoming', { period }),
  committee: (id) => get(`/committees/${id}`),
  committeeMeetings: (id, params) => get(`/committees/${id}/meetings`, params),
  committeeDocuments: (id, params) => get(`/committees/${id}/documents`, params),
  // One sitting's jegyzőkönyv, whole (BIZ-15). Not paged: it *is* one document,
  // the reader arrives wanting to read it end to end, and paging a transcript
  // breaks both in-page search and linking to a speech.
  // Every committee sitting in the cycle scope, newest first (BIZ-24) — the
  // committee-side counterpart of `sessions` above, and what the sittings
  // page's second tab lists.
  committeeMeetingsAll: (params) => get('/committees/meetings', params),
  committeeMinutes: (meetingId) => get(`/committees/meetings/${meetingId}/minutes`),
  // Just the agenda headings of one sitting (BIZ-4b), for the hover preview on
  // the meeting lists — `committeeMinutes` above would pull the whole
  // transcript to show five titles.
  committeeMeetingAgenda: (meetingId, limit) =>
    get(`/committees/meetings/${meetingId}/agenda`, { limit }),
  // The committee's recordings on the House's own YouTube channel (BIZ-16).
  committeeVideos: (id, params) => get(`/committees/${id}/videos`, params),
  // One person's seats, for the block on their profile (BIZ-7).
  repCommittees: (id, period) => get(`/committees/representative/${id}`, { period }),
  // "Who represents me?" (REP-10). Not period-scoped: constituency boundaries are
  // redrawn between elections, so the answer belongs to the cycle the boundary data
  // elects, which the response names.
  settlementSearch: (q, limit) => get('/representatives/constituencies/settlements', { q, limit }),
  settlementConstituencies: (maz, taz) =>
    get(`/representatives/constituencies/settlements/${maz}/${taz}`),
  // Települések (§6D) — settlement mentions, the map and the blind spots. Distinct
  // from the two `settlement*` helpers above, which belong to REP-10's constituency
  // lookup: those answer "which constituency is this place in", these answer "how
  // often does the House name it".
  settlements: (params) => get('/settlements', params),
  settlement: (maz, taz, period) => get(`/settlements/${maz}/${taz}`, { period }),
  settlementMap: (period) => get('/settlements/map', { period }),
  settlementSummary: (period) => get('/settlements/summary', { period }),
  // The same map binned into an area instead of drawn per place: equal-area H3
  // hexagons (TEL-15) or the single-member constituencies (TEL-16). Asked for only
  // when a reader switches to it, and 503s for a binning the deployment cannot build.
  // The hexagons' cell size is the deployment's choice, not the reader's, so no
  // resolution is sent — the endpoint serves the configured one and names it.
  settlementSegments: (bins, period) =>
    get('/settlements/map/segments', { bins, period }),
  settlementTrend: (maz, taz, period) => get(`/settlements/${maz}/${taz}/trend`, { period }),
  settlementMentions: (maz, taz, params) =>
    get(`/settlements/${maz}/${taz}/mentions`, params),
  settlementReps: (params) => get('/settlements/representatives', params),
  repSettlements: (id, period) => get(`/settlements/representative/${id}`, { period }),
  // bills
  bills: (params) => get('/bills', params),
  bill: (id) => get(`/bills/${id}`),
  billFacets: (params) => get('/bills/facets', params),
  billTopicMix: (period) => get('/bills/topics', { period }),
  questionsSankey: (period, includeType = true, expandOther = false) =>
    get('/bills/questions/sankey',
        { period, include_type: includeType, expand_other: expandOther }),
  questionsList: (params) => get('/bills/questions/list', params),
  // Közbeszólások (§6E) — who interjects over whose speech. The graph is one
  // request per (cycle scope, top N); the list is what one clicked arrow holds.
  interjectionGraph: (period, top, rank) =>
    get('/interjections/graph', { period, top, rank }),
  interjectionList: (params) => get('/interjections/list', params),
  interjectionPartners: (period, person) =>
    get('/interjections/partners', { period, person }),
  // votes
  votes: (params) => get('/votes', params),
  vote: (id) => get(`/votes/${id}`),
  voteFacets: (params) => get('/votes/facets', params),
  voteCohesion: (params) => get('/votes/cohesion', params),
}
