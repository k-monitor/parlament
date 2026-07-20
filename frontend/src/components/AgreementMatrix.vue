<script setup>
// Dependency-free SVG agreement heatmap. Each cell (i, j) is how often faction i
// and faction j vote the same way (0–1); the diagonal (i = j) is a faction's
// internal cohesion, also surfaced as a % beside its row label. Magnitude is
// carried by fill-opacity over the brand accent, so it reads in the app's single
// (light) theme without a bespoke ramp; colour is never the sole carrier — every
// cell carries its exact number in a hover title and each row shows its cohesion %
// (A11Y-1). Hovering a cell lights up its row and column so a pair is easy to trace.
import { computed, ref } from 'vue'

const props = defineProps({
  // [{ name, color, cohesion, size }] in display order (largest faction first).
  factions: { type: Array, default: () => [] },
  // n×n agreement matrix (0–1, or null where two factions never co-voted).
  matrix: { type: Array, default: () => [] },
  caption: { type: String, default: '' },
  lowLabel: { type: String, default: '' },
  highLabel: { type: String, default: '' },
})

const CELL = 32
const GAP = 3
const LGUT = 138        // left gutter: row labels + cohesion %
const TGUT = 92         // top gutter: rotated column labels
const RGUT = 104        // right gutter: the last rotated column label overflows here
const LEG_H = 52        // legend strip below the grid

const n = computed(() => props.factions.length)
const W = computed(() => LGUT + n.value * CELL + RGUT)
const H = computed(() => TGUT + n.value * CELL + LEG_H)
// Cap the on-screen width so a small matrix (few factions) doesn't balloon when
// stretched to the container; it still shrinks below this on narrow viewports.
const maxW = computed(() => Math.round(W.value * 1.4) + 'px')

function trunc(s, k) {
  s = String(s == null ? '' : s)
  return s.length > k ? s.slice(0, k - 1) + '…' : s
}
function pct(v) { return v == null ? '—' : Math.round(v * 100) + '%' }
// Floor the opacity so even "never together" stays faintly visible as a cell.
function fillOpacity(v) { return v == null ? 0 : 0.06 + 0.94 * v }

const cells = computed(() => {
  const out = []
  const m = props.matrix
  for (let i = 0; i < n.value; i++) {
    for (let j = 0; j < n.value; j++) {
      const v = m[i] ? m[i][j] : null
      out.push({
        key: i + '-' + j, i, j,
        x: LGUT + j * CELL + GAP / 2,
        y: TGUT + i * CELL + GAP / 2,
        v, opacity: fillOpacity(v), diagonal: i === j, empty: v == null,
        title: `${props.factions[i]?.name} ↔ ${props.factions[j]?.name}: ${pct(v)}`,
      })
    }
  }
  return out
})

const rows = computed(() => props.factions.map((f, i) => ({
  i, name: f.name, color: f.color, cohesion: f.cohesion,
  y: TGUT + i * CELL + CELL / 2,
})))
const cols = computed(() => props.factions.map((f, j) => ({
  j, name: f.name, x: LGUT + j * CELL + CELL / 2,
})))

// Crosshair highlight for the hovered cell (row i + column j).
const hi = ref({ i: -1, j: -1 })
function over(i, j) { hi.value = { i, j } }
function out() { hi.value = { i: -1, j: -1 } }
</script>

<template>
  <figure class="matrix" style="margin:0;">
    <div class="scroll">
      <svg :viewBox="`0 0 ${W} ${H}`" class="svg" role="img" :aria-label="caption"
           :style="{ maxWidth: maxW }">
        <defs>
          <linearGradient id="agr-legend" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stop-color="var(--accent)" stop-opacity="0.06" />
            <stop offset="1" stop-color="var(--accent)" stop-opacity="1" />
          </linearGradient>
        </defs>

        <!-- rotated column headers -->
        <text
          v-for="c in cols" :key="'c' + c.j"
          :x="c.x" :y="TGUT - 8"
          :transform="`rotate(-45 ${c.x} ${TGUT - 8})`"
          class="collabel" :class="{ act: hi.j === c.j }"
          text-anchor="start" dominant-baseline="middle"
        >{{ trunc(c.name, 12) }}</text>

        <!-- row headers: colour dot + name + internal-cohesion % -->
        <g v-for="r in rows" :key="'r' + r.i">
          <circle :cx="10" :cy="r.y" r="5" :fill="r.color || 'var(--ink-soft)'" class="dot" />
          <text :x="20" :y="r.y" class="rowlabel" :class="{ act: hi.i === r.i }"
                dominant-baseline="middle">{{ trunc(r.name, 12) }}</text>
          <text :x="LGUT - 8" :y="r.y" class="rowpct" text-anchor="end"
                dominant-baseline="middle">{{ pct(r.cohesion) }}</text>
        </g>

        <!-- cells -->
        <rect
          v-for="cell in cells" :key="cell.key"
          :x="cell.x" :y="cell.y" :width="CELL - GAP" :height="CELL - GAP" rx="4"
          class="cell"
          :class="{ empty: cell.empty, diagonal: cell.diagonal,
                    hl: cell.i === hi.i || cell.j === hi.j }"
          :fill="cell.empty ? 'var(--line)' : 'var(--accent)'"
          :fill-opacity="cell.opacity"
          @mouseenter="over(cell.i, cell.j)" @mouseleave="out"
        >
          <title>{{ cell.title }}</title>
        </rect>

        <!-- legend -->
        <g :transform="`translate(${LGUT}, ${TGUT + n * CELL + 20})`">
          <rect x="0" y="0" width="150" height="12" rx="3" fill="url(#agr-legend)"
                stroke="var(--line)" />
          <text x="0" y="28" class="leglabel" text-anchor="start">{{ lowLabel }}</text>
          <text x="150" y="28" class="leglabel" text-anchor="end">{{ highLabel }}</text>
        </g>
      </svg>
    </div>

  </figure>
</template>

<style scoped>
.scroll { overflow-x: auto; }
.svg { width: 100%; min-width: 340px; height: auto; display: block; }
.collabel, .rowlabel { font-size: 12px; fill: var(--ink); }
.collabel.act, .rowlabel.act { font-weight: 700; fill: var(--accent); }
.rowpct { font-size: 11px; fill: var(--ink-soft); font-variant-numeric: tabular-nums; }
.dot { stroke: rgba(0,0,0,.15); stroke-width: 1; }
.cell { stroke: var(--surface); stroke-width: 1; transition: stroke .1s ease; cursor: default; }
.cell.diagonal { stroke: var(--ink-soft); stroke-width: 1.4; }
.cell.hl { stroke: var(--ink); stroke-width: 1.4; }
.cell.empty { stroke: var(--line); }
.leglabel { font-size: 10.5px; fill: var(--ink-soft); }
@media (prefers-reduced-motion: reduce) { .cell { transition: none; } }
</style>
