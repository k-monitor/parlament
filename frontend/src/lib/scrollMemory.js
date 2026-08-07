// Returning the reader to where they were (§4D / BACK-1).
//
// Scrolling deep into a 400-speech sitting day, opening one speech, and then
// being dumped back at the top of the day is the kind of friction that ends a
// browsing session. Two things break naive scroll restoration on this site:
//
//   1. **The destination is empty at the moment the router would restore it.**
//      Every list view fetches its rows after mounting, so when `scrollBehavior`
//      runs (one `nextTick` after the route resolves) the document is a single
//      spinner tall and any offset clamps to 0. By the time the rows land the
//      position is long gone. So we wait for the page to grow tall enough to
//      hold the offset before applying it — and keep the offset applied while
//      the page is still growing, because a sitting day's word cloud, new-words
//      and top-speaker cards each arrive in their own request and slot in
//      *above* the transcript.
//   2. **Half the ways back are pushes, not history pops.** The viewer's "‹ back
//      to the sitting day" link and the day page's "‹ Ülésnapok" link navigate
//      *forward* to the page the reader came from, and vue-router has no saved
//      position for a push. So we keep our own offsets, keyed by fullPath.
//
// Restoring on a push is deliberately limited to walking back up the trail the
// reader actually came down (see `trail` below): landing mid-list out of nowhere
// is worse than landing at the top.

// Offsets of pages we have left, most-recently-recorded last (fullPath -> px).
const positions = new Map()
const MAX_POSITIONS = 40

// The chain of pages the reader descended through, as { path, fullPath }. A
// navigation to a fullPath already on the trail is a *return* — that is what
// earns a restore — and truncates the trail back to it. Anything else extends
// it, except a move *within* the page currently at the tip (same path, different
// query: a filter, a page number, the viewer rewriting `?s=` on every sentence
// click), which swaps the tip so in-page state changes can't crowd the pages
// above out of the trail.
const trail = []
const MAX_TRAIL = 30

// Poll rather than observe: the things that change the page height here are
// route-level renders and late `fetch` responses, so a coarse tick is plenty and
// costs one layout read per 60ms during the (short) restore window only.
const POLL_MS = 60
// How long to wait for the destination to render enough rows to hold the offset.
// Generous — a cold sitting-day request can take seconds — and safe to be so,
// because any sign of the reader taking control cancels the pending restore.
const REACH_MS = 8000
// How long to keep re-applying the offset afterwards, for content that lands
// above the reader and pushes the transcript down.
const HOLD_MS = 1500
// The reader touching the page means they have chosen their own position.
const TAKEOVER = ['wheel', 'touchmove', 'keydown', 'mousedown']

/**
 * Record where the reader was on the page they are leaving. Must run *before*
 * the new view renders (i.e. from a `beforeEach` guard), while the offset still
 * belongs to the old page.
 */
export function rememberScroll(from) {
  if (!from || !from.name) return // first navigation of the session: nothing behind us
  const top = Math.round(window.scrollY || document.documentElement.scrollTop || 0)
  if (top <= 0) return // never scrolled — there is nothing to come back to
  positions.delete(from.fullPath) // re-insert so the map drops the oldest first
  positions.set(from.fullPath, top)
  if (positions.size > MAX_POSITIONS) positions.delete(positions.keys().next().value)
}

/**
 * Decide where an incoming route should land, and advance the trail. Called once
 * per confirmed navigation (from `scrollBehavior`). Returns a scroll position or
 * null for "top of the page".
 */
export function scrollTarget(to, saved) {
  const at = trail.findIndex((e) => e.fullPath === to.fullPath)
  const returning = at >= 0
  const tip = trail[trail.length - 1]
  if (returning) trail.length = at + 1
  else if (tip && tip.path === to.path) trail[trail.length - 1] = { path: to.path, fullPath: to.fullPath }
  else {
    trail.push({ path: to.path, fullPath: to.fullPath })
    if (trail.length > MAX_TRAIL) trail.shift()
  }
  // A history pop carries vue-router's own saved offset — always honour it.
  if (saved && saved.top) return saved
  const remembered = returning ? positions.get(to.fullPath) : undefined
  return remembered ? { top: remembered, left: 0 } : null
}

// Run `tick` on a poll until it returns true, then clean up. `isStale` covers
// the reader navigating on while we wait; the TAKEOVER events cover them
// scrolling themselves, which likewise ends our claim on the scroll position.
function poll(isStale, tick) {
  return new Promise((resolve) => {
    let taken = false
    const takeover = () => { taken = true }
    for (const e of TAKEOVER) window.addEventListener(e, takeover, { passive: true })
    const stop = (ok) => {
      for (const e of TAKEOVER) window.removeEventListener(e, takeover)
      resolve(ok)
    }
    const step = () => {
      if (taken || isStale()) return stop(false)
      const done = tick()
      if (done !== null) return stop(done)
      setTimeout(step, POLL_MS)
    }
    step()
  })
}

/**
 * Resolve once the page is tall enough for `top` to be a real position rather
 * than a clamp to the bottom — i.e. once the view's data has rendered. Resolves
 * false if the reader navigated on, took over the scroll, or the content never
 * arrived, in which case the restore is abandoned rather than applied late.
 */
export function whenReachable(top, isStale) {
  const deadline = performance.now() + REACH_MS
  return poll(isStale, () => {
    if (document.documentElement.scrollHeight - window.innerHeight >= top) return true
    return performance.now() >= deadline ? false : null
  })
}

/**
 * Hold the reader at `top` while the page keeps growing. Sections that arrive in
 * their own request (word cloud, new words, speaker toplist) insert themselves
 * above the transcript; re-applying the saved offset after each such change
 * converges on the place the offset was measured. Stops as soon as the page goes
 * quiet, the reader scrolls, or the grace period runs out.
 */
export function keepInPlace(top, isStale) {
  const deadline = performance.now() + HOLD_MS
  let height = document.documentElement.scrollHeight
  return poll(isStale, () => {
    const h = document.documentElement.scrollHeight
    if (h !== height) {
      height = h
      window.scrollTo(0, top)
    }
    return performance.now() >= deadline ? true : null
  })
}
