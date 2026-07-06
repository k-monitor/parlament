<script setup>
// Dependency-free SVG pie chart with a legend and an a11y table fallback,
// matching the BarChart/TrendChart conventions. `segments` = [{ key, label,
// value, color }]; zero-value segments are dropped from the pie but kept in the
// legend/table so every category stays visible and comparable. When a single
// segment holds the whole total (a common case — an MP who voted in every
// roll call), the wedge is drawn as a full circle so the arc math stays valid.
import { ref, computed } from 'vue'
const props = defineProps({
  segments: { type: Array, default: () => [] },
  caption: { type: String, default: '' },
  // Small label rendered under the total in the pie's centre (e.g. "vote").
  totalLabel: { type: String, default: '' },
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

const showTable = ref(false)
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
          <span class="swatch" :style="{ background: s.color }" aria-hidden="true"></span>
          <span class="lg-label">{{ s.label }}</span>
          <span class="lg-val">{{ (s.value || 0).toLocaleString('hu-HU') }}</span>
          <span class="lg-pct">{{ fmtPct(s.value || 0) }}</span>
        </li>
      </ul>
    </div>

    <button type="button" class="table-toggle small" @click="showTable = !showTable">
      {{ showTable ? $t('a11y.hideTable') : $t('a11y.showTable') }}
    </button>
    <table v-if="showTable" class="a11y-table small">
      <tbody>
        <tr v-for="(s, i) in segments" :key="s.key || i">
          <th scope="row">{{ s.label }}</th>
          <td>{{ (s.value || 0).toLocaleString('hu-HU') }}</td>
          <td>{{ fmtPct(s.value || 0) }}</td>
        </tr>
      </tbody>
    </table>
  </figure>
</template>

<style scoped>
.pie-wrap { display: flex; align-items: center; gap: 1.2rem; flex-wrap: wrap; }
.pie-svg { width: 132px; height: 132px; flex: none; }
.pie-total { font-size: 17px; font-weight: 800; fill: var(--ink); font-variant-numeric: tabular-nums; }
.pie-total-lbl { font-size: 8.5px; fill: var(--ink-soft); text-transform: uppercase; letter-spacing: .04em; }

.legend { list-style: none; margin: 0; padding: 0; flex: 1; min-width: 190px; display: flex; flex-direction: column; gap: .3rem; }
.legend li { display: grid; grid-template-columns: auto 1fr auto auto; align-items: baseline; gap: .5rem; font-size: .84rem; }
.legend li.zero { opacity: .5; }
.swatch { width: .72rem; height: .72rem; border-radius: 3px; flex: none; align-self: center; }
.lg-label { color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lg-val { font-variant-numeric: tabular-nums; font-weight: 700; color: var(--ink); }
.lg-pct { font-variant-numeric: tabular-nums; color: var(--ink-soft); min-width: 3ch; text-align: right; }

.table-toggle { display: inline-block; margin-top: .7rem; background: none; border: 0; padding: 0; color: var(--accent); cursor: pointer; text-decoration: underline; }
.a11y-table { margin-top: .5rem; border-collapse: collapse; width: 100%; }
.a11y-table th, .a11y-table td { text-align: left; padding: .2rem .5rem .2rem 0; border-bottom: 1px solid var(--line); }
.a11y-table td { font-variant-numeric: tabular-nums; }
</style>
