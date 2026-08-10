// First-click tracking for the site's search boxes (SEA-12 / PRIV-2).
//
// The backend already counts every executed search — the keyword, the filters,
// how many hits it found. What that cannot say is whether the results actually
// answered the question. This adds the missing half: the position of the FIRST
// result the reader opens, sent back with the very query and filters the search
// ran with, so it is counted onto that search's own aggregate row. Read together
// they give a click-through rate and a mean first-click rank (1.0 = the top hit
// answers people).
//
// It stays inside the site's privacy promise (PRIV-1): the ping carries no id,
// no session, no timestamp — only the search it belongs to and a position — and
// one executed search contributes at most one click, so nobody's browsing can be
// followed from row to row.
import { api } from '../api.js'

/**
 * A tracker for one search box. `source` names it for the backend (its list of
 * known boxes is closed — an unknown one is dropped).
 */
export function createSearchClicks(source) {
  let armed = null

  return {
    /**
     * Arm the tracker for a result page that just loaded. `params` must be the
     * object the search itself was fetched with (that is what makes the click
     * land on the same row), and `offset` the index the page starts at, so a hit
     * on page 2 is reported as its position in the whole list. A keyword-less
     * browse has no search quality to measure and is not tracked.
     */
    arm(params, offset = 0) {
      if (!params || !params.q) { armed = null; return }
      // Snapshot the values, arrays included: the global cycle scope is a live
      // reactive array, and a click must report the search that actually ran,
      // not one the reader has since re-scoped.
      const snapshot = {}
      for (const [k, v] of Object.entries(params)) snapshot[k] = Array.isArray(v) ? [...v] : v
      armed = { params: snapshot, offset: Number(offset) || 0 }
    },

    /**
     * Report the reader opening the result at 0-based `index` of the current
     * page. Only the first call per armed search sends anything; the next search
     * re-arms it.
     */
    hit(index) {
      if (!armed) return
      const { params, offset } = armed
      armed = null
      api.searchClick(source, params, offset + Number(index) + 1)
    },
  }
}
