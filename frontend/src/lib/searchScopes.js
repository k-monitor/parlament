// What the site search searches in (SEA-14): the scope dropdown beside the
// search box on the search page and on the home page.
//
// Two of the scopes are full-text searches over what was SAID — the plenary
// transcript and the committee minutes — and run on the search page itself,
// told apart by `?in=` (absent for the plenary, so every search link already
// out there keeps its meaning). The other three search records that each have
// a list page of their own with the filters, facets and sorting that belong to
// them, so picking one hands the term to that page's `q` rather than drawing a
// thinner copy of its list here.
import { store } from '../store.js'

export const SEARCH_SCOPES = [
  { key: 'plenary', module: 'proceedings' },
  { key: 'committee', module: 'committees' },
  { key: 'documents', module: 'bills', route: 'documents' },
  { key: 'votes', module: 'votes', route: 'votes' },
  // Everyone in the speaker register, not just MPs: a name typed here may be
  // an advocate's or a minister's, and the reader cannot be expected to know
  // which of the Felszólalók chips it is under (REP-1).
  { key: 'people', module: 'representatives', route: 'allSpeakers' },
]

const BY_KEY = Object.fromEntries(SEARCH_SCOPES.map((s) => [s.key, s]))

/** The scopes whose module this deployment runs (EXT-6). */
export function availableScopes() {
  return SEARCH_SCOPES.filter((s) => store.moduleEnabled(s.module))
}

/** Whether a scope is searched on the search page (vs. handed to a list page). */
export function searchesInPlace(key) {
  return !BY_KEY[key]?.route
}

/** The scope a search-page URL names; anything unknown reads as the plenary. */
export function scopeFromQuery(query) {
  return query.in === 'committee' && store.moduleEnabled('committees')
    ? 'committee' : 'plenary'
}

/**
 * Where a search for `q` in `key` goes. `extra` is the search page's own
 * filters, which only its in-place scopes take; a list page starts unfiltered
 * apart from the term.
 */
export function scopeTarget(key, q, extra = {}) {
  const scope = BY_KEY[key] || BY_KEY.plenary
  const term = (q || '').trim()
  if (scope.route) return { name: scope.route, query: term ? { q: term } : {} }
  const query = {}
  if (term) query.q = term
  if (scope.key === 'committee') query.in = 'committee'
  return { name: 'search', query: { ...query, ...extra } }
}
