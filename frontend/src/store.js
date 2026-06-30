// Minimal global store (Vue reactive) holding the site manifest from /api/v1/meta.
// The enabled-module list drives which nav entries show and which module routes
// are reachable (EXT-4/EXT-6) — a module disabled in the backend simply vanishes.
//
// It also owns the **global electoral cycle** (period scope). One header control
// sets it for the whole site; every period-aware view reads `store.cycle` instead
// of carrying its own period filter. `null` means "all cycles"; a number is an
// electoral-period number. The choice is persisted so it survives a reload, and
// defaults to the latest cycle on first visit.
import { reactive } from 'vue'
import { api } from './api.js'

const CYCLE_KEY = 'parlamonitor.cycle'

export const store = reactive({
  meta: null,
  loaded: false,
  failed: false,
  cycle: null, // null = all cycles; otherwise an electoral-period number
  moduleEnabled(name) {
    if (!this.meta) return true // optimistic before load
    return this.meta.modules.some((m) => m.name === name)
  },
})

// Read the saved cycle: `undefined` = nothing saved, `null` = explicit "all",
// otherwise the saved period number.
function savedCycle() {
  try {
    const raw = localStorage.getItem(CYCLE_KEY)
    if (raw === null) return undefined
    if (raw === 'all') return null
    const n = Number(raw)
    return Number.isFinite(n) ? n : undefined
  } catch {
    return undefined
  }
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

// Human label for the currently selected cycle, or null when scope is "all
// cycles" (the caller supplies its own "all" wording). Used to make the active
// scope explicit on period-aware pages.
export function currentCycleLabel() {
  if (store.cycle === null) return null
  const p = (store.meta && store.meta.periods || []).find((x) => x.number === store.cycle)
  return p ? periodLabel(p) : String(store.cycle)
}

// Set the global cycle (number = a period, null = all) and persist it.
export function setCycle(value) {
  store.cycle = value
  try {
    localStorage.setItem(CYCLE_KEY, value === null ? 'all' : String(value))
  } catch {
    /* localStorage unavailable (private mode) — in-memory state still works */
  }
}

// Initialise the global cycle once the available periods are known: honour a
// valid saved choice, otherwise default to the latest cycle (periods are sorted
// newest-first by /meta).
function initCycle(meta) {
  const periods = meta.periods || []
  const saved = savedCycle()
  if (saved === null) {
    store.cycle = null // explicit "all"
  } else if (saved !== undefined && periods.some((p) => p.number === saved)) {
    store.cycle = saved
  } else {
    store.cycle = periods.length ? periods[0].number : null
  }
}

let inflight = null
export function loadMeta() {
  if (store.loaded) return Promise.resolve(store.meta)
  if (inflight) return inflight
  inflight = api
    .meta()
    .then((m) => {
      store.meta = m
      initCycle(m)
      store.loaded = true
      return m
    })
    .catch((e) => {
      store.failed = true
      throw e
    })
  return inflight
}
