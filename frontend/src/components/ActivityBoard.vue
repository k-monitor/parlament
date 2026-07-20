<script setup>
// GitHub-style contribution heatmap for a representative (REP-8). Renders the
// per-day activity (`days` = [{date, speeches, documents, total}]) as week
// columns × weekday rows, each cell shaded by the day's total activity. It is
// dependency-free (CSS grid of <div>s); each cell carries its date and exact
// counts in a hover title and the grid a screen-reader summary (A11Y-1) —
// colour/intensity is never the only carrier of meaning.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatDate } from '../format.js'

const props = defineProps({
  days: { type: Array, default: () => [] },          // chronological day records
  documentsAvailable: { type: Boolean, default: true },
  // First day to show ("YYYY-MM-DD"): the selected cycle's start, so the board
  // always begins at the cycle's first day rather than the MP's first active day
  // (leading inactive days render as empty cells). Null = start at first activity.
  from: { type: String, default: null },
})
const { t, locale } = useI18n()

const byDate = computed(() => {
  const m = new Map()
  for (const d of props.days) m.set(d.date, d)
  return m
})
const maxTotal = computed(() => props.days.reduce((m, d) => Math.max(m, d.total), 0))

// Five-step intensity scale (0 = empty, 1–4 increasing), bucketed against the
// busiest day so the scale adapts to each MP's range.
function level(total) {
  if (!total) return 0
  return Math.min(4, Math.ceil((total / (maxTotal.value || 1)) * 4))
}

// UTC date arithmetic on "YYYY-MM-DD" keys (no timezone drift).
const DAY = 86400000
function parse(s) { const [y, m, d] = s.split('-').map(Number); return Date.UTC(y, m - 1, d) }
function key(ts) { return new Date(ts).toISOString().slice(0, 10) }
function mondayIndex(ts) { return (new Date(ts).getUTCDay() + 6) % 7 } // Mon=0 … Sun=6

const monthFmt = computed(() => new Intl.DateTimeFormat(locale.value, { month: 'short', timeZone: 'UTC' }))

// Cap the board to at most this many days so a long cycle (or "all cycles")
// never produces an unbounded grid.
const MAX_SPAN_DAYS = 200

// The shown date window [start, last]: ends at the last active day, begins at the
// selected cycle's first day (props.from) — but never spans more than
// MAX_SPAN_DAYS, in which case it keeps the most recent days. Drives the grid,
// the table and the summary so they always agree on what is shown.
const range = computed(() => {
  if (!props.days.length) return null
  const dates = props.days.map((d) => d.date).sort()
  const last = parse(dates[dates.length - 1])
  let start = parse(dates[0])
  if (props.from) start = Math.min(start, parse(props.from))
  const earliest = last - (MAX_SPAN_DAYS - 1) * DAY
  if (start < earliest) start = earliest   // clamp to the trailing 200-day window
  return { start, last }
})

// Build the week columns spanning the shown window, snapped to whole weeks
// (Monday-first) so the grid is rectangular. Each week also carries the month
// label to print above it when a new month begins.
const weeks = computed(() => {
  if (!range.value) return []
  const { start: rangeStart, last } = range.value
  let cur = rangeStart - mondayIndex(rangeStart) * DAY
  const out = []
  let prevMonth = null
  while (cur <= last) {
    const cells = []
    for (let r = 0; r < 7; r++) {
      const ts = cur + r * DAY
      const k = key(ts)
      const rec = byDate.value.get(k)
      cells.push({
        k, ts,
        inRange: ts >= rangeStart && ts <= last,
        total: rec ? rec.total : 0,
        speeches: rec ? rec.speeches : 0,
        documents: rec ? rec.documents : 0,
      })
    }
    const mDate = new Date(cur)
    const m = mDate.getUTCMonth()
    let monthLabel = ''
    if (m !== prevMonth) {
      monthLabel = monthFmt.value.format(mDate)
      if (m === 0 || prevMonth === null) monthLabel = `’${String(mDate.getUTCFullYear()).slice(2)} ` + monthLabel
      prevMonth = m
    }
    out.push({ start: cur, cells, monthLabel })
    cur += 7 * DAY
  }
  return out
})

// Weekday row labels (Mon/Wed/Fri shown, like GitHub) in the active locale.
const weekdayLabels = computed(() => {
  const fmt = new Intl.DateTimeFormat(locale.value, { weekday: 'short', timeZone: 'UTC' })
  // 2024-01-01 is a Monday.
  return [0, 1, 2, 3, 4, 5, 6].map((i) => fmt.format(new Date(Date.UTC(2024, 0, 1 + i))))
})

function cellTitle(c) {
  if (!c.inRange) return ''
  let s = `${formatDate(c.k)} — ${c.speeches} ${t('reps.speeches')}`
  if (props.documentsAvailable) s += `, ${c.documents} ${t('profile.activityDocs')}`
  return s
}

// The active days within the shown window — drives the board's summary count.
const activeDays = computed(() => {
  if (!range.value) return []
  const startKey = key(range.value.start)
  return props.days.filter((d) => d.total > 0 && d.date >= startKey)
})

const summary = computed(() =>
  t('profile.activityAriaLabel', { days: activeDays.value.length }))
</script>

<template>
  <figure class="board" v-if="days.length">
    <figcaption class="cap">{{ $t('profile.activity') }}</figcaption>
    <div class="board-main">
      <div class="scroll">
        <div class="grid-wrap" role="img" :aria-label="summary">
          <div class="months-row">
            <div class="wd-spacer"></div>
            <div class="months">
              <div v-for="(w, wi) in weeks" :key="wi" class="mcell">{{ w.monthLabel }}</div>
            </div>
          </div>
          <div class="body">
            <div class="weekdays">
              <span v-for="(lbl, i) in weekdayLabels" :key="i" class="wd">{{ i % 2 === 1 ? lbl : '' }}</span>
            </div>
            <div class="weeks">
              <div v-for="(w, wi) in weeks" :key="wi" class="week">
                <span v-for="c in w.cells" :key="c.k"
                      class="cell" :class="'l' + (c.inRange ? level(c.total) : 0)"
                      :data-empty="!c.inRange ? '' : null"
                      :title="cellTitle(c)"></span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- right rail: help icon (the popup explains the intensity scale) -->
      <div class="aside">
        <button type="button" class="help" :title="$t('profile.activityHelp')"
                :aria-label="$t('profile.activityHelp')">
          <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
            <circle cx="8" cy="8" r="7.2" fill="none" stroke="currentColor" stroke-width="1.3" />
            <text x="8" y="11.7" text-anchor="middle" font-size="10" font-weight="700" fill="currentColor">?</text>
          </svg>
        </button>
      </div>
    </div>
    <p class="note">{{ $t('profile.activityWindow') }}</p>
  </figure>
</template>

<style scoped>
.board { margin: 0; }
.cap { font-size: .82rem; font-weight: 600; color: var(--ink-soft); margin: 0 0 .4rem; }
.note { font-size: .68rem; color: var(--ink-faint); margin: .35rem 0 0; }
.board-main { display: flex; gap: .55rem; align-items: flex-start; }
.scroll { overflow-x: auto; padding-bottom: .4rem; min-width: 0; }
/* right rail: help icon (its popup explains the intensity scale) */
.aside { display: flex; flex-direction: column; align-items: center; gap: .5rem; padding-top: 2px; flex: none; }
.help { display: inline-flex; padding: 0; border: 0; background: none; cursor: help; color: var(--ink-faint); line-height: 0; }
.help:hover { color: var(--accent); }
.grid-wrap { display: inline-flex; flex-direction: column; gap: 4px; }
.months-row { display: flex; gap: 4px; }
.body { display: flex; gap: 4px; }
.wd-spacer, .weekdays { width: 30px; flex: none; }
.weekdays { display: flex; flex-direction: column; gap: 3px; }
.wd { height: 12px; font-size: .62rem; line-height: 12px; color: var(--ink-faint); white-space: nowrap; }
.months { display: flex; gap: 3px; }
.mcell { width: 12px; flex: none; font-size: .68rem; color: var(--ink-faint); white-space: nowrap; overflow: visible; }
.weeks { display: flex; gap: 3px; }
.week { display: flex; flex-direction: column; gap: 3px; }
.cell { width: 12px; height: 12px; border-radius: 2px; background: var(--accent); flex: none; }
/* intensity steps — accent at increasing opacity; l0 is a faint empty cell */
.cell.l0 { background: var(--line); opacity: .55; }
.cell.l1 { opacity: .3; }
.cell.l2 { opacity: .5; }
.cell.l3 { opacity: .72; }
.cell.l4 { opacity: 1; }
.cell[data-empty] { background: transparent; opacity: 1; }
</style>
