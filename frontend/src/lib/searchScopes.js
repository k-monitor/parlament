// What the site search searches in (SEA-14): the scope picker beside the search
// box on the search page and on the home page. Several scopes can be picked at
// once, and every one of them is searched on the search page itself — one scope
// as a full, paged result list, several as a section each.
//
// The scopes travel in the URL as repeated `in` params (`in=all` for every
// scope this deployment runs). A plain `/search?q=` link names none and means
// the plenary alone, so every search link already out there keeps its meaning,
// and the single `in=committee` of the first version still reads as it did.
//
// Each scope is answered by an endpoint that already exists — the two full-text
// indexes, and the list endpoints behind the iromány, vote and people pages —
// so this file is also where the search page's filters are translated into each
// endpoint's own parameters, and where it is recorded which filters a scope
// cannot honour, so the page can say so instead of silently dropping them.
import { api } from '../api.js'
import { store } from '../store.js'

// The search page's filters, as named in its own URL (SEA-6), grouped the way
// the page offers and reports them.
export const FILTERS = {
  dates: ['date_from', 'date_to'],
  speaker: ['person_id'],
  faction: ['faction_id'],
  agenda: ['agenda_type'],
}

// The search page's orderings (SEA-10). Every scope ranks by date both ways;
// `ranks` marks the ones that can also put the best match first.
export const SORTS = ['relevance', 'date_desc', 'date_asc']

export const SEARCH_SCOPES = [
  {
    key: 'plenary', module: 'proceedings', source: 'proceedings',
    filters: ['dates', 'speaker', 'faction', 'agenda'], ranks: true,
    // A query-less search is one speaker's speeches (SEA-3).
    runnable: (s) => !!(s.q || s.person_id),
    args: (s) => ({
      q: s.q, period: s.period, date_from: s.date_from, date_to: s.date_to,
      person_id: s.person_id, faction_id: s.faction_id, agenda_type: s.agenda_type,
      sort: s.sort,
    }),
    fetch: (a) => api.search(a),
    read: (r) => ({
      results: r.results, total: r.total, capped: !!r.total_is_capped, query: r.query,
    }),
  },
  {
    // Full text over the committee minutes (BIZ-15). The minutes record no
    // faction id and no agenda type, so neither filter reaches them.
    key: 'committee', module: 'committees', source: 'committee-minutes',
    filters: ['dates', 'speaker'], ranks: true,
    runnable: (s) => !!(s.q || s.person_id),
    args: (s) => ({
      q: s.q, period: s.period, date_from: s.date_from, date_to: s.date_to,
      person_id: s.person_id, sort: s.sort,
    }),
    fetch: (a) => api.committeeSearch(a),
    read: (r) => ({
      results: r.results, total: r.total, capped: !!r.totalIsCapped,
      unavailable: r.tablesReady === false,
    }),
  },
  {
    // Every iromány (BILL-9), matched on its title and number. The speaker
    // filter reads as its submitter — the person's own documents — and the
    // dates as its submission date. "Relevance" is the list's own order, which
    // puts an exactly typed number first.
    key: 'documents', module: 'bills', source: 'bills', route: 'documents',
    filters: ['dates', 'speaker'], ranks: true,
    runnable: (s) => !!(s.q || s.person_id),
    args: (s) => ({
      q: s.q, period: s.period, date_from: s.date_from, date_to: s.date_to,
      sponsor: s.person_id,
      sort: { date_desc: 'date', date_asc: 'date_asc' }[s.sort] || 'number',
    }),
    fetch: (a) => api.bills(a),
    read: (r) => ({ results: r.bills, total: r.total }),
    // The list page carries on with the filters it has: the term and the
    // submitter (it has no date range).
    list: (s) => ({ q: s.q, sponsor: s.person_id }),
  },
  {
    // The votes, matched on their subject and the irományok they decided. The
    // speaker filter reads as a voter: the roll calls the person took part in,
    // each with how they voted.
    key: 'votes', module: 'votes', source: 'votes', route: 'votes',
    filters: ['dates', 'speaker'], ranks: false,
    runnable: (s) => !!(s.q || s.person_id),
    args: (s) => ({
      q: s.q, period: s.period, date_from: s.date_from, date_to: s.date_to,
      person: s.person_id, sort: s.sort === 'date_asc' ? 'date_asc' : 'date_desc',
    }),
    fetch: (a) => api.votes(a),
    read: (r) => ({ results: r.votes, total: r.total }),
    list: (s) => ({ q: s.q, person: s.person_id, date_from: s.date_from, date_to: s.date_to }),
  },
  {
    // Everyone in the speaker register, not just MPs: a name typed here may be
    // an advocate's or a minister's, and the reader cannot be expected to know
    // which of the Felszólalók chips it is under (REP-1). A name is the only
    // thing to search them by, so without a term there is nothing to list; the
    // faction filter narrows it to that faction's MPs, as on the list page.
    // Ordered by time on the floor whatever the page's sort, which is the best
    // stand-in for "the one meant" among namesakes.
    key: 'people', module: 'representatives', source: 'representatives',
    route: 'allSpeakers', filters: ['faction'], ranks: false, sortable: false,
    runnable: (s) => !!s.q,
    args: (s) => ({
      q: s.q, period: s.period, faction_id: s.faction_id, role: 'all',
      sort: 'speaking_time',
    }),
    fetch: (a) => api.representatives(a),
    read: (r) => ({ results: r.representatives, total: r.total }),
    // Not the faction: the all-speakers chip has no filter panel to show it in.
    list: (s) => ({ q: s.q }),
  },
]

export const SCOPE_BY_KEY = Object.fromEntries(SEARCH_SCOPES.map((s) => [s.key, s]))
const DEFAULT = ['plenary']

/** The scopes whose module this deployment runs (EXT-6). */
export function availableScopes() {
  return SEARCH_SCOPES.filter((s) => store.moduleEnabled(s.module))
}

/**
 * The scopes a search-page URL names, in the picker's order. Unknown keys and
 * disabled modules are dropped; nothing left reads as the plenary.
 */
export function scopesFromQuery(query) {
  const raw = [].concat(query.in ?? []).flatMap((v) => String(v).split(','))
  const avail = availableScopes().map((s) => s.key)
  const keys = raw.includes('all') ? avail : avail.filter((k) => raw.includes(k))
  return keys.length ? keys : [...DEFAULT]
}

/** The `in` param for a set of scopes — none for the plenary alone. */
function scopeParam(keys) {
  const avail = availableScopes().map((s) => s.key)
  const picked = avail.filter((k) => keys.includes(k))
  if (!picked.length || (picked.length === 1 && picked[0] === DEFAULT[0])) return {}
  if (picked.length === avail.length && avail.length > 1) return { in: 'all' }
  return { in: picked.length === 1 ? picked[0] : picked }
}

/**
 * The search-page route for a search for `q` in `keys`. `extra` is the page's
 * own filters and sort.
 */
export function scopeTarget(keys, q, extra = {}) {
  const query = {}
  const term = (q || '').trim()
  if (term) query.q = term
  return { name: 'search', query: { ...query, ...extra, ...scopeParam(keys) } }
}

/**
 * The list page a record scope's results continue on — where its own filters,
 * facets and orderings live — carrying over what of the search it can show.
 * Null for the two full-text scopes, whose full list is the search page.
 */
export function listPageTarget(key, state) {
  const scope = SCOPE_BY_KEY[key]
  if (!scope?.list) return null
  const query = Object.fromEntries(Object.entries(scope.list(state))
    .filter(([, v]) => v !== undefined && v !== null && v !== ''))
  return { name: scope.route, query }
}

/** Whether any of `keys` honours the filter group `group` (FILTERS). */
export function scopesHonour(keys, group) {
  return keys.some((k) => SCOPE_BY_KEY[k]?.filters.includes(group))
}

/**
 * The filter groups set in `state` (the page's filters) that the scope `key`
 * cannot apply — what its results have to be read without.
 */
export function ignoredFilters(key, state) {
  const scope = SCOPE_BY_KEY[key]
  return Object.entries(FILTERS)
    .filter(([group, params]) => params.some((p) => state[p]) && !scope.filters.includes(group))
    .map(([group]) => group)
}

/**
 * The orderings to offer for `keys`: relevance only with a term to rank by,
 * and none at all when no picked scope can be reordered.
 */
export function sortOptions(keys, q) {
  if (!keys.some((k) => SCOPE_BY_KEY[k]?.sortable !== false)) return []
  const ranks = !!q && keys.some((k) => SCOPE_BY_KEY[k]?.ranks)
  return ranks ? SORTS : SORTS.slice(1)
}
