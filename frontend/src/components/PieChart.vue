<script setup>
// Dependency-free SVG pie chart with a legend, matching the BarChart/TrendChart
// conventions. `segments` = [{ key, label, value, color, to? }]; zero-value
// segments are dropped from the pie but kept in the legend so every category
// stays visible and comparable. A segment with a `to` (a router location) turns
// its legend row into a filter link (the legend already carries every label +
// value in text, so it doubles as the accessible data view). When a single
// segment holds the whole total (a common case — an MP who voted in every roll
// call), the wedge is drawn as a full circle so the arc math stays valid.
import { computed } from 'vue'
const props = defineProps({
  segments: { type: Array, default: () => [] },
  caption: { type: String, default: '' },
  // Small label rendered under the total in the pie's centre (e.g. "vote").
  totalLabel: { type: String, default: '' },
  // Extra legend rows [{ key?, label, value, color }] shown below a divider but
  // EXCLUDED from the total, the wedges and the percentages — for a category
  // that belongs to the picture yet must not skew the 100% base (e.g. roll-call
  // votes from before an MP took their seat).
  extra: { type: Array, default: () => [] },
  // Optional caption under the extra rows explaining why they don't count.
  extraNote: { type: String, default: '' },
})

const total = computed(() =>
  props.segments.reduce((s, seg) => s + (seg.value || 0), 0))

function pct(v) {
  return total.value ? (v / total.value) * 100 : 0
}

// Cumulative wedges, one per non-zero segment, starting at 12 o'clock.
const R = 54
const CX = 60
const CY = 60
const slices = computed(() => {
  const active = props.segments.filter((s) => (s.value || 0) > 0)
  const out = []
  let angle = -Math.PI / 2
  for (const seg of active) {
    const frac = seg.value / total.value
    const start = angle
    const end = angle + frac * Math.PI * 2
    angle = end
    out.push({ ...seg, path: wedge(start, end), full: active.length === 1 })
  }
  return out
})

function wedge(start, end) {
  const large = end - start > Math.PI ? 1 : 0
  const x1 = CX + R * Math.cos(start), y1 = CY + R * Math.sin(start)
  const x2 = CX + R * Math.cos(end), y2 = CY + R * Math.sin(end)
  return `M ${CX} ${CY} L ${x1.toFixed(3)} ${y1.toFixed(3)} ` +
    `A ${R} ${R} 0 ${large} 1 ${x2.toFixed(3)} ${y2.toFixed(3)} Z`
}

function fmtPct(v) {
  const p = pct(v)
  // No decimals for whole-ish shares; one decimal for small slivers.
  return (p >= 10 || p === 0 ? Math.round(p) : p.toFixed(1)) + '%'
}
</script>

<template>
  <figure v-if="total > 0" class="pie" style="margin:0;">
    <figcaption v-if="caption" class="small soft" style="margin-bottom:.6rem;">{{ caption }}</figcaption>

    <div class="pie-wrap">
      <svg viewBox="0 0 120 120" class="pie-svg" role="img" :aria-label="caption">
        <template v-for="(s, i) in slices" :key="s.key || i">
          <circle v-if="s.full" :cx="CX" :cy="CY" :r="R" :fill="s.color" />
          <path v-else :d="s.path" :fill="s.color" stroke="var(--surface, #fff)" stroke-width="1" />
        </template>
        <!-- Doughnut hole carrying the total, so the pie doubles as a KPI. -->
        <circle :cx="CX" :cy="CY" r="30" fill="var(--surface, #fff)" />
        <text :x="CX" :y="totalLabel ? CY - 2 : CY + 4" class="pie-total"
              text-anchor="middle">{{ total.toLocaleString('hu-HU') }}</text>
        <text v-if="totalLabel" :x="CX" :y="CY + 13" class="pie-total-lbl"
              text-anchor="middle">{{ totalLabel }}</text>
      </svg>

      <ul class="legend">
        <li v-for="(s, i) in segments" :key="s.key || i" :class="{ zero: !s.value }">
          <component :is="(s.to && s.value) ? 'router-link' : 'span'"
                     :to="(s.to && s.value) ? s.to : undefined"
                     class="lg-row" :class="{ 'lg-link': s.to && s.value }">
            <span class="swatch" :style="{ background: s.color }" aria-hidden="true"></span>
            <span class="lg-label">{{ s.label }}</span>
            <span class="lg-val">{{ (s.value || 0).toLocaleString('hu-HU') }}</span>
            <span class="lg-pct">{{ fmtPct(s.value || 0) }}</span>
          </component>
        </li>
        <template v-if="extra.length">
          <li class="lg-sep" role="presentation"></li>
          <li v-for="(s, i) in extra" :key="s.key || ('x' + i)" class="lg-extra">
            <span class="lg-row">
              <span class="swatch" :style="{ background: s.color }" aria-hidden="true"></span>
              <span class="lg-label">{{ s.label }}</span>
              <span class="lg-val">{{ (s.value || 0).toLocaleString('hu-HU') }}</span>
              <span class="lg-pct" aria-hidden="true">—</span>
            </span>
          </li>
          <li v-if="extraNote" class="lg-note small soft">{{ extraNote }}</li>
        </template>
      </ul>
    </div>
  </figure>
</template>

<style scoped>
.pie-wrap { display: flex; align-items: center; gap: 1.2rem; flex-wrap: wrap; }
.pie-svg { width: 132px; height: 132px; flex: none; }
.pie-total { font-size: 17px; font-weight: 800; fill: var(--ink); font-variant-numeric: tabular-nums; }
.pie-total-lbl { font-size: 8.5px; fill: var(--ink-soft); text-transform: uppercase; letter-spacing: .04em; }

.legend { list-style: none; margin: 0; padding: 0; flex: 1; min-width: 190px; display: flex; flex-direction: column; gap: .2rem; }
.legend li.zero { opacity: .5; }
.lg-row { display: grid; grid-template-columns: auto 1fr auto auto; align-items: baseline; gap: .5rem; font-size: .84rem; }
/* A segment that carries a `to` becomes a filter link: keep the row layout,
   just add an affordance (hover surface + the label turns accent). */
.lg-link { text-decoration: none; color: inherit; padding: .18rem .35rem; margin: -.18rem -.35rem; border-radius: 6px; }
.lg-link:hover { background: var(--accent-soft); }
.lg-link:hover .lg-label { color: var(--accent); }
.swatch { width: .72rem; height: .72rem; border-radius: 3px; flex: none; align-self: center; }
.lg-label { color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lg-val { font-variant-numeric: tabular-nums; font-weight: 700; color: var(--ink); }
.lg-pct { font-variant-numeric: tabular-nums; color: var(--ink-soft); min-width: 3ch; text-align: right; }
/* Extra rows: shown but not part of the 100% — muted, under a hairline. */
.lg-sep { height: 1px; background: var(--line); margin: .35rem 0; }
.legend li.lg-extra .lg-row { color: var(--ink-soft); }
.legend li.lg-extra .lg-val, .legend li.lg-extra .lg-label { color: var(--ink-soft); font-weight: 500; }
.legend li.lg-extra .lg-pct { opacity: .5; }
.lg-note { display: block; margin-top: .15rem; line-height: 1.3; }
</style>
