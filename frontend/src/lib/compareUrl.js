// The comparison URL (REP-15) — the one place that owns what a comparison's
// address looks like, shared by the profile's compare link and the comparison
// page itself.
//
// The people compared live *only* in the URL: there is no selection kept
// elsewhere, so the address is the whole state. That is what makes a comparison
// shareable and citable like a profile, lets Back walk the columns the reader
// tried, and means nothing about who is being compared is ever stored on the
// visitor's machine or sent anywhere (PRIV-1).

// Mirrors the backend's `_COMPARE_MAX`. Four columns is what a phone can still
// show side by side; the API caps it too, so a hand-edited URL is bounded there.
export const COMPARE_MAX = 4

// Ids ride in one comma-separated `?ids=` param, the form the site already spells
// multi-valued scope in (`?cycle=43,44`) — short enough to paste into a post, and
// one param the router guard can leave alone. The API takes them repeated
// (`?id=a&id=b`), which api.js does from an array; the two conventions meet here.

export function parseIds(raw) {
  if (!raw) return []
  return [...new Set(String(raw).split(',').map((s) => s.trim()).filter(Boolean))]
    .slice(0, COMPARE_MAX)
}

export function serializeIds(ids) {
  return (ids || []).join(',')
}

/** A route object for comparing `ids` — with none, the comparison page's own
 *  empty state, which is its picker. */
export function compareRoute(ids) {
  const list = ids || []
  return { name: 'compare', query: list.length ? { ids: serializeIds(list) } : {} }
}
