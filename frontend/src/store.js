// Minimal global store (Vue reactive) holding the site manifest from /api/v1/meta.
// The enabled-module list drives which nav entries show and which module routes
// are reachable (EXT-4/EXT-6) — a module disabled in the backend simply vanishes.
//
// It also owns the **global electoral cycle scope** (period scope). One header
// control sets it for the whole site; every period-aware view reads
// `store.cycles` instead of carrying its own period filter. It is an array of
// electoral-period numbers: several cycles can be in scope at once, and the
// empty array means "all cycles" (no period filter). The API takes it verbatim
// as the repeatable `period` query param. The choice is persisted so it survives
// a reload, and defaults to the latest cycle on first visit.
import { reactive } from 'vue'
import { api } from './api.js'

const CYCLE_KEY = 'parlamonitor.cycle'

export const store = reactive({
  meta: null,
  loaded: false,
  failed: false,
  cycles: [], // [] = all cycles; otherwise the electoral-period numbers in scope
  // Which list the currently-open person profile belongs to, as that list's route
  // name: 'representatives' (MPs), 'advocates' (nemzetiségi szószólók, REP-9),
  // 'speakers' (the other speakers, REP-12) or 'officials' (an office holder who
  // never spoke here, REP-11). One `/representatives/:id` route serves all four,
  // and only the profile response says which — so the profile view publishes it
  // here for the app shell, which cannot know it, to highlight the right sub-tab.
  // The first three are category chips of one tab (Felszólalók), so they all
  // highlight it; the value stays this precise because it is also the list the
  // profile's back link returns to — chip and all. Null while nothing (or nothing
  // yet loaded) is open.
  profileTab: null,
  moduleEnabled(name) {
    if (!this.meta) return true // optimistic before load
    return this.meta.modules.some((m) => m.name === name)
  },
  // Optional capabilities inside an enabled module, advertised by /meta — e.g. the
  // constituency lookup (REP-10), which depends on an external source and can be
  // switched off on its own. Optimistic before load, like moduleEnabled; a backend
  // predating the flag reports nothing, so the feature is treated as on rather than
  // silently disappearing.
  featureEnabled(name) {
    if (!this.meta || !this.meta.features) return true
    return this.meta.features[name] !== false
  },
})

// Parse a persisted/URL scope value into a cycle array: `undefined` = nothing
// saved or nothing valid, `[]` = the explicit "all cycles" sentinel, otherwise
// the period numbers that actually exist. Accepts the comma-separated form
// ("43,44") the scope is written in, and a bare number (older saved values).
export function parseCycles(raw, periods) {
  if (raw === undefined || raw === null || raw === '') return undefined
  if (raw === 'all') return []
  const known = new Set((periods || []).map((p) => p.number))
  const nums = String(raw)
    .split(',')
    .map((s) => Number(s.trim()))
    .filter((n) => Number.isFinite(n) && known.has(n))
  return nums.length ? [...new Set(nums)].sort((a, b) => a - b) : undefined
}

// Serialise a cycle scope into the form used in the URL and localStorage.
export function serializeCycles(cycles) {
  return cycles && cycles.length ? cycles.join(',') : 'all'
}

// Display label for an electoral period: its start–end years (e.g. "2018–2024")
// rather than its ordinal number, since the year span is what users recognise.
// An ongoing cycle (no end date) shows its start year with a trailing dash
// ("2026–"). Falls back to the upstream label / number when dates are missing.
export function periodLabel(p) {
  if (!p) return ''
  const start = p.date_start ? String(p.date_start).slice(0, 4) : null
  const end = p.date_end ? String(p.date_end).slice(0, 4) : null
  if (start) return end ? `${start}–${end}` : `${start}–`
  return p.label || String(p.number)
}

// Label for one period number, from the loaded manifest.
export function cycleLabel(number) {
  const p = (store.meta && store.meta.periods || []).find((x) => x.number === number)
  return p ? periodLabel(p) : String(number)
}

// Human label for the current scope — the selected cycles newest-first, comma
// separated ("2026–, 2018–2024") — or null when scope is "all cycles" (the
// caller supplies its own "all" wording). Used to make the active scope explicit
// on period-aware pages.
export function currentCycleLabel() {
  if (!store.cycles.length) return null
  return [...store.cycles].sort((a, b) => b - a).map(cycleLabel).join(', ')
}

// Set the global cycle scope (an array of period numbers, empty = all) and
// persist it. Stored sorted + de-duplicated so the scope has one canonical form.
export function setCycles(values) {
  const nums = [...new Set((values || []).map(Number).filter(Number.isFinite))]
  store.cycles = nums.sort((a, b) => a - b)
  try {
    localStorage.setItem(CYCLE_KEY, serializeCycles(store.cycles))
  } catch {
    /* localStorage unavailable (private mode) — in-memory state still works */
  }
}

// The scope a visitor gets with no saved choice and no `?cycle=` in the URL:
// the latest cycle (periods arrive newest-first from /meta). It is also the
// scope the router leaves *implicit* in the address bar, so the site's default
// pages keep clean, canonical, parameter-free URLs (§SEO-2).
export function defaultCycles(periods) {
  return periods && periods.length ? [periods[0].number] : []
}

// Initialise the global scope once the available periods are known: honour a
// valid saved choice, otherwise default to the latest cycle.
function initCycles(meta) {
  const periods = meta.periods || []
  let saved
  try {
    saved = parseCycles(localStorage.getItem(CYCLE_KEY), periods)
  } catch {
    saved = undefined
  }
  store.cycles = saved !== undefined ? saved : defaultCycles(periods)
}

let inflight = null
export function loadMeta() {
  if (store.loaded) return Promise.resolve(store.meta)
  if (inflight) return inflight
  inflight = api
    .meta()
    .then((m) => {
      store.meta = m
      initCycles(m)
      store.loaded = true
      store.failed = false
      return m
    })
    .catch((e) => {
      store.failed = true
      inflight = null // drop the rejected promise so the next call retries
      throw e
    })
  return inflight
}
