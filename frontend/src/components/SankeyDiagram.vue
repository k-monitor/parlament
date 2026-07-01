<script setup>
// Dependency-free SVG Sankey diagram (two columns: askers on the left, answerers
// on the right). `nodes` = [{ label, color, side: 'asker' | 'answerer' }];
// `links` = [{ source, target, value, color }] referencing node indices. Node
// heights and ribbon thicknesses are proportional to the flow. Colour is
// decorative — an accessible table below carries the same numbers (A11Y-1).
import { computed, ref } from 'vue'

const props = defineProps({
  nodes: { type: Array, default: () => [] },
  links: { type: Array, default: () => [] },
  caption: { type: String, default: '' },
  askerHeading: { type: String, default: '' },
  answererHeading: { type: String, default: '' },
  valueLabel: { type: String, default: '' },   // unit for the a11y table
  selected: { type: Number, default: -1 },       // index of the selected link
})
const emit = defineEmits(['select'])

function pick(linkIndex) {
  const l = props.links[linkIndex]
  if (!l) return
  emit('select', {
    index: linkIndex, value: l.value,
    source: props.nodes[l.source], target: props.nodes[l.target],
  })
}

const WIDTH = 860
const NODE_W = 13
const NODE_GAP = 7
const PAD_Y = 10
const HEAD_H = 26          // room for the column headings above the columns
const L_GUTTER = 150        // room for asker labels on the left
const R_GUTTER = 265        // room for (long) ministry labels on the right
const BAR_TOTAL = 540       // summed node-bar height on one side → sets the scale

function trunc(s, n = 30) {
  s = String(s == null ? '' : s)
  return s.length > n ? s.slice(0, n - 1) + '…' : s
}

const layout = computed(() => {
  const nodes = props.nodes
  const links = props.links
  if (!nodes.length || !links.length) return null

  const totals = nodes.map(() => 0)
  for (const l of links) { totals[l.source] += l.value; totals[l.target] += l.value }
  const sumVal = links.reduce((s, l) => s + l.value, 0) || 1
  const scale = BAR_TOTAL / sumVal

  const askerIdx = nodes.map((_, i) => i).filter((i) => nodes[i].side === 'asker')
  const answerIdx = nodes.map((_, i) => i).filter((i) => nodes[i].side === 'answerer')
  const maxCount = Math.max(askerIdx.length, answerIdx.length)
  const LABEL_MIN = 15   // minimum vertical gap between labels

  const xLeft = L_GUTTER
  const xRight = WIDTH - R_GUTTER - NODE_W

  const box = nodes.map((n, i) => ({
    i, ...n, total: totals[i],
    h: Math.max(2, totals[i] * scale),
    x: n.side === 'asker' ? xLeft : xRight,
    y: 0, labelY: 0,
  }))

  // Stack each column vertically from the top of the plot area (below the
  // heading row).
  const place = (idxs) => {
    let y = PAD_Y + HEAD_H
    for (const i of idxs) { box[i].y = y; y += box[i].h + NODE_GAP }
  }
  place(askerIdx)
  place(answerIdx)

  // Declutter labels: a tiny node's bar is thinner than its label, so labels
  // near a cluster of small nodes would collide. Anchor each label at its bar's
  // centre, then push labels down just enough to keep LABEL_MIN between them.
  let bottom = 0
  const declutter = (idxs) => {
    let prev = -Infinity
    for (const i of idxs) {
      let ly = box[i].y + box[i].h / 2
      if (ly < prev + LABEL_MIN) ly = prev + LABEL_MIN
      box[i].labelY = ly
      prev = ly
      bottom = Math.max(bottom, ly)
    }
  }
  declutter(askerIdx)
  declutter(answerIdx)
  const H = Math.round(Math.max(bottom + PAD_Y, PAD_Y * 2 + 40))

  // Allocate ribbon endpoints: within a source node, order outgoing ribbons by
  // the target's vertical position (and vice-versa) to reduce crossings.
  const sy = box.map((b) => b.y)
  const ty = box.map((b) => b.y)
  const bySource = [...links].sort((a, b) =>
    (box[a.source].y - box[b.source].y) || (box[a.target].y - box[b.target].y))
  const sTop = new Map()
  for (const l of bySource) { sTop.set(l, sy[l.source]); sy[l.source] += l.value * scale }
  const byTarget = [...links].sort((a, b) =>
    (box[a.target].y - box[b.target].y) || (box[a.source].y - box[b.source].y))
  const tTop = new Map()
  for (const l of byTarget) { tTop.set(l, ty[l.target]); ty[l.target] += l.value * scale }

  const xa = xLeft + NODE_W
  const xb = xRight
  const mx = (xa + xb) / 2
  const ribbons = links.map((l, k) => {
    const h = l.value * scale
    const s0 = sTop.get(l), s1 = s0 + h
    const t0 = tTop.get(l), t1 = t0 + h
    return {
      key: k,
      color: l.color || 'var(--accent)',
      value: l.value,
      source: box[l.source], target: box[l.target],
      d: `M${xa},${s0} C${mx},${s0} ${mx},${t0} ${xb},${t0}` +
         ` L${xb},${t1} C${mx},${t1} ${mx},${s1} ${xa},${s1} Z`,
    }
  })

  return { H, box, ribbons, xLeft, xRight }
})

// Rows for the accessible table fallback: asker → answerer → value. Each row
// carries its link index so it drills down exactly like the ribbon.
const tableRows = computed(() =>
  props.links
    .map((l, index) => ({
      index,
      asker: props.nodes[l.source]?.label || '—',
      answerer: props.nodes[l.target]?.label || '—',
      value: l.value,
    }))
    .sort((a, b) => b.value - a.value))

const showTable = ref(false)
const active = ref(-1)   // hovered/focused source node index (highlight its ribbons)
</script>

<template>
  <figure v-if="layout" class="sankey" style="margin:0;">
    <figcaption v-if="caption" class="small soft" style="margin-bottom:.5rem;">{{ caption }}</figcaption>
    <div class="scroll">
      <svg :viewBox="`0 0 ${WIDTH} ${layout.H}`" class="svg" role="img" :aria-label="caption">
        <!-- column headings -->
        <text v-if="askerHeading" :x="layout.xLeft + NODE_W" :y="12" class="colhead" text-anchor="start">{{ askerHeading }}</text>
        <text v-if="answererHeading" :x="layout.xRight" :y="12" class="colhead" text-anchor="end">{{ answererHeading }}</text>

        <!-- ribbons (each is a clickable flow → drill-down) -->
        <path
          v-for="r in layout.ribbons" :key="'r' + r.key" :d="r.d"
          class="ribbon" :class="{
            dim: selected !== -1 ? r.key !== selected : (active !== -1 && r.source.i !== active),
            sel: r.key === selected,
          }"
          :fill="r.color" role="button" tabindex="0"
          :aria-label="`${r.source.label} → ${r.target.label}: ${r.value}`"
          @click="pick(r.key)" @keydown.enter.prevent="pick(r.key)" @keydown.space.prevent="pick(r.key)"
        >
          <title>{{ r.source.label }} → {{ r.target.label }}: {{ r.value }}</title>
        </path>

        <!-- nodes -->
        <g
          v-for="b in layout.box" :key="'n' + b.i"
          @mouseenter="b.side === 'asker' && (active = b.i)" @mouseleave="active = -1"
        >
          <rect
            :x="b.x" :y="b.y" :width="NODE_W" :height="b.h" rx="2"
            class="node" :fill="b.color || (b.side === 'asker' ? 'var(--accent)' : '#9c9188')"
          >
            <title>{{ b.label }}: {{ b.total }}</title>
          </rect>
          <text
            v-if="b.side === 'asker'" :x="b.x - 6" :y="b.labelY"
            class="nlabel" text-anchor="end" dominant-baseline="middle"
          >{{ trunc(b.label) }} <tspan class="nval">({{ b.total }})</tspan></text>
          <text
            v-else :x="b.x + NODE_W + 6" :y="b.labelY"
            class="nlabel" text-anchor="start" dominant-baseline="middle"
          >{{ trunc(b.label) }} <tspan class="nval">({{ b.total }})</tspan></text>
        </g>
      </svg>
    </div>

    <button type="button" class="tabletoggle small" @click="showTable = !showTable">
      {{ showTable ? '▾ ' + $t('a11y.hideTable') : '▸ ' + $t('a11y.showTable') }}
    </button>
    <table v-if="showTable" class="a11y-table small">
      <thead>
        <tr>
          <th>{{ askerHeading }}</th>
          <th>{{ answererHeading }}</th>
          <th class="num">{{ valueLabel }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in tableRows" :key="row.index"
            class="rowlink" :class="{ sel: row.index === selected }"
            tabindex="0" @click="pick(row.index)"
            @keydown.enter.prevent="pick(row.index)" @keydown.space.prevent="pick(row.index)">
          <td>{{ row.asker }}</td>
          <td>{{ row.answerer }}</td>
          <td class="num">{{ row.value }}</td>
        </tr>
      </tbody>
    </table>
  </figure>
</template>

<style scoped>
.scroll { overflow-x: auto; }
.svg { width: 100%; min-width: 640px; height: auto; display: block; }
.colhead { font-size: 11px; font-weight: 700; fill: var(--ink-soft); text-transform: uppercase; letter-spacing: .03em; }
.ribbon { opacity: .38; transition: opacity .12s ease; cursor: pointer; }
.ribbon:hover { opacity: .72; }
.ribbon:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.ribbon.dim { opacity: .08; }
.ribbon.sel { opacity: .82; }
.node { stroke: rgba(0,0,0,.12); stroke-width: .5; }
.nlabel { font-size: 12px; fill: var(--ink); }
.nval { fill: var(--ink-soft); font-variant-numeric: tabular-nums; }
.tabletoggle { margin-top: .6rem; background: none; border: 0; color: var(--accent); cursor: pointer; padding: .2rem 0; font-weight: 600; }
.a11y-table { width: 100%; border-collapse: collapse; margin-top: .5rem; }
.a11y-table th, .a11y-table td { text-align: left; padding: .3rem .5rem; border-bottom: 1px solid var(--line); }
.a11y-table .num { text-align: right; font-variant-numeric: tabular-nums; }
.rowlink { cursor: pointer; }
.rowlink:hover { background: var(--accent-soft); }
.rowlink.sel { background: var(--accent-soft); font-weight: 600; }
@media (prefers-reduced-motion: reduce) { .ribbon { transition: none; } }
</style>
