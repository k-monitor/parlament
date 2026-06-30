<script setup>
// Dependency-free SVG bar histogram for a value over time (SEA-8), with the same
// built-in accessible table equivalent as BarChart (REP-6 / A11Y-1).
// `buckets` = [{ period, hits }]; the server picks the interval from the span
// (day / week / month / year) and the client fills the zero gaps so the timeline
// is continuous. Rendered as a dense histogram of thin bars — one slot per
// period — so the same design holds from a handful of buckets to several hundred.
// Geometry is computed in real pixels (via a ResizeObserver) so bars stay crisp.
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
const props = defineProps({
  buckets: { type: Array, default: () => [] },
  granularity: { type: String, default: 'month' }, // 'day' | 'week' | 'month' | 'year'
  caption: { type: String, default: '' },
  unit: { type: String, default: '' },
})
const showTable = ref(false)

const MONTHS = ['', 'jan', 'feb', 'márc', 'ápr', 'máj', 'jún',
  'júl', 'aug', 'szept', 'okt', 'nov', 'dec']

// Tooltip label for one bucket, per granularity.
function fullLabel(g, y, m, d) {
  if (g === 'year') return `${y}`
  if (g === 'month') return `${MONTHS[m]} ${y}`
  return `${y}. ${MONTHS[m]} ${d}.` // day / week (week = its Monday)
}

// Fill the zero-gap periods so a quiet stretch reads as zero, not as missing.
const filled = computed(() => {
  const src = props.buckets
  if (!src.length) return []
  const g = props.granularity
  const byPeriod = new Map(src.map((b) => [b.period, b.hits]))
  const out = []
  const push = (period, y, m, d) =>
    out.push({ period, y, m, d, label: fullLabel(g, y, m, d), hits: byPeriod.get(period) || 0 })

  if (g === 'year') {
    const lo = +src[0].period, hi = +src[src.length - 1].period
    for (let y = lo; y <= hi; y++) push(String(y), y, 1, 1)
  } else if (g === 'month') {
    const [ly, lm] = src[0].period.split('-').map(Number)
    const [hy, hm] = src[src.length - 1].period.split('-').map(Number)
    for (let y = ly, m = lm; y < hy || (y === hy && m <= hm);) {
      push(`${y}-${String(m).padStart(2, '0')}`, y, m, 1)
      if (++m > 12) { m = 1; y++ }
    }
  } else {
    // day / week — step over real dates (week keys are Mondays, so step 7 days).
    const step = g === 'week' ? 7 : 1
    const cur = new Date(`${src[0].period}T00:00:00Z`)
    const end = new Date(`${src[src.length - 1].period}T00:00:00Z`)
    for (let guard = 0; cur <= end && guard < 20000; guard++) {
      push(cur.toISOString().slice(0, 10),
        cur.getUTCFullYear(), cur.getUTCMonth() + 1, cur.getUTCDate())
      cur.setUTCDate(cur.getUTCDate() + step)
    }
  }
  return out
})

const max = computed(() => Math.max(1, ...filled.value.map((b) => b.hits)))

// --- Responsive geometry ---------------------------------------------------
const H = 120          // chart height in px (excludes the axis-label row)
const PAD_TOP = 8      // headroom so the tallest bar isn't flush against the top
const wrap = ref(null)
const w = ref(320)     // container width, kept in sync by the ResizeObserver
let ro = null
onMounted(() => {
  if (!wrap.value) return
  w.value = wrap.value.clientWidth || 320
  ro = new ResizeObserver((entries) => {
    const cw = entries[0]?.contentRect?.width
    if (cw) w.value = cw
  })
  ro.observe(wrap.value)
})
onBeforeUnmount(() => { ro?.disconnect() })

const MAX_BAR_W = 26   // cap so a few-bucket span shows slim bars, not giant blocks
const n = computed(() => filled.value.length)
const slotW = computed(() => w.value / Math.max(1, n.value))
// A 1px gap only once slots are wide enough to afford it; dense spans go solid.
const gap = computed(() => (slotW.value > 4 ? 1 : 0))
const barW = computed(() => Math.max(0.75, Math.min(MAX_BAR_W, slotW.value - gap.value)))

const bars = computed(() => filled.value.map((b, i) => {
  const h = b.hits ? Math.max(1.5, (b.hits / max.value) * (H - PAD_TOP)) : 0
  // Centre the bar in its time-slot so positions stay true even when capped.
  return { ...b, x: i * slotW.value + (slotW.value - barW.value) / 2, y: H - h, h }
}))

// --- Axis labels -----------------------------------------------------------
// Years for long spans, month names for short ones, so labels stay meaningful
// and plentiful at every zoom. Evenly spaced, then de-duplicated.
const tickMode = computed(() => {
  const g = props.granularity
  if (g === 'year') return 'year'
  if (g === 'day' || g === 'week') return 'month'
  return new Set(filled.value.map((b) => b.y)).size > 3 ? 'year' : 'month'
})
function tickText(b) {
  return tickMode.value === 'year' ? `${b.y}` : `${MONTHS[b.m]} ${b.y}`
}
const ticks = computed(() => {
  const f = filled.value
  if (!f.length) return []
  const count = Math.min(f.length, Math.max(2, Math.floor(w.value / 60)))
  const idxs = [...new Set(Array.from({ length: count }, (_, k) =>
    count === 1 ? 0 : Math.round((k * (f.length - 1)) / (count - 1))))]
  const out = []
  let last = null
  for (const i of idxs) {
    const text = tickText(f[i])
    if (text !== last) { out.push({ i, text }); last = text }
  }
  return out
})

// --- Hover interaction -----------------------------------------------------
const hover = ref(-1)
function onMove(e) {
  if (!wrap.value || n.value === 0) return
  const rect = wrap.value.getBoundingClientRect()
  const i = Math.floor((e.clientX - rect.left) / slotW.value)
  hover.value = Math.max(0, Math.min(n.value - 1, i))
}
function onLeave() { hover.value = -1 }
const hoverBar = computed(() => (hover.value >= 0 ? bars.value[hover.value] : null))
const tipPct = computed(() => (hoverBar.value
  ? ((hoverBar.value.x + barW.value / 2) / w.value) * 100 : 0))
</script>

<template>
  <figure v-if="filled.length" class="trend" style="margin:0;">
    <figcaption v-if="caption" class="small soft" style="margin-bottom:.5rem;">{{ caption }}</figcaption>

    <div ref="wrap" class="plot" @mousemove="onMove" @mouseleave="onLeave">
      <svg :viewBox="`0 0 ${w} ${H}`" :height="H" width="100%"
           preserveAspectRatio="none" role="img" :aria-label="caption" class="svg">
        <line :x1="0" :y1="H - 0.5" :x2="w" :y2="H - 0.5" class="baseline" />
        <rect v-for="(b, i) in bars" :key="b.period" v-show="b.h"
              :x="b.x" :y="b.y" :width="barW" :height="b.h"
              class="bar" :class="{ on: i === hover }" />
      </svg>

      <div v-if="hoverBar" class="tip" :style="{ left: tipPct + '%' }">
        <span class="tip-label">{{ hoverBar.label }}</span>
        <span class="tip-val">{{ hoverBar.hits.toLocaleString('hu-HU') }} {{ unit }}</span>
      </div>
    </div>

    <div class="axis" aria-hidden="true">
      <span v-for="t in ticks" :key="t.i" class="axis-lbl"
            :style="{ left: ((t.i * slotW + slotW / 2) / w * 100) + '%' }">{{ t.text }}</span>
    </div>

    <button class="btn secondary small" style="margin-top:.6rem;padding:.3rem .7rem;" @click="showTable = !showTable">
      {{ showTable ? $t('profile.hideTable') : $t('profile.showTable') }}
    </button>
    <table v-if="showTable" class="data" style="margin-top:.6rem;">
      <caption class="visually-hidden">{{ caption }}</caption>
      <thead><tr><th scope="col">#</th><th scope="col">{{ unit }}</th></tr></thead>
      <tbody>
        <tr v-for="b in filled" :key="b.period"><th scope="row">{{ b.label }}</th><td>{{ b.hits }}</td></tr>
      </tbody>
    </table>
  </figure>
</template>

<style scoped>
.plot { position: relative; width: 100%; }
.svg { display: block; }
.baseline { stroke: var(--line); stroke-width: 1; }
.bar { fill: var(--accent); opacity: .82; }
.bar.on { opacity: 1; }
.plot:hover .bar:not(.on) { opacity: .55; }

.tip {
  position: absolute; top: -2px; transform: translateX(-50%);
  background: var(--ink); color: #fff; border-radius: 6px;
  padding: .25rem .5rem; font-size: .7rem; line-height: 1.25; white-space: nowrap;
  pointer-events: none; display: flex; flex-direction: column; align-items: center;
  box-shadow: 0 2px 8px rgba(0, 0, 0, .18); z-index: 2;
}
.tip-label { opacity: .8; }
.tip-val { font-weight: 700; }

.axis { position: relative; height: 1.1rem; margin-top: .35rem; }
.axis-lbl {
  position: absolute; transform: translateX(-50%); top: 0;
  font-size: .62rem; color: var(--ink-soft); white-space: nowrap;
}
</style>
