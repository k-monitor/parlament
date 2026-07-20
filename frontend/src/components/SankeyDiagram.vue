<script setup>
// Dependency-free SVG Sankey diagram supporting any number of columns. Each node
// carries a `column` index (0 = leftmost); links connect nodes in adjacent
// columns. `nodes` = [{ label, color, column, side? }]; `links` =
// [{ source, target, value, color }] referencing node indices. Node heights and
// ribbon thicknesses are proportional to the flow. Each ribbon and node is
// keyboard-focusable and labelled for assistive tech (A11Y-1). For backward
// compatibility a node without `column` is placed by `side` (asker → 0,
// answerer → 1).
import { computed, ref } from 'vue'

const props = defineProps({
  nodes: { type: Array, default: () => [] },
  links: { type: Array, default: () => [] },
  caption: { type: String, default: '' },
  // When false, the caption still labels the diagram for assistive tech but is not
  // shown as a visible figcaption (the parent surfaces it elsewhere, e.g. a HelpTip).
  showCaption: { type: Boolean, default: true },
  // Column headings, indexed by column. Falls back to asker/answerer for the
  // first / last column when the array has no entry.
  columnHeadings: { type: Array, default: () => [] },
  askerHeading: { type: String, default: '' },
  answererHeading: { type: String, default: '' },
  selected: { type: Number, default: -1 },       // index of the selected link
  selectedNode: { type: Number, default: -1 },   // index of the selected node
})
const emit = defineEmits(['select', 'select-node'])

function pick(linkIndex) {
  const l = props.links[linkIndex]
  if (!l) return
  emit('select', {
    index: linkIndex, value: l.value,
    source: props.nodes[l.source], target: props.nodes[l.target],
  })
}

// Clicking a node (a column bar or its label) filters by that endpoint alone —
// every flow into/out of it — mirroring the ribbon drill-down.
function pickNode(nodeIndex) {
  const n = props.nodes[nodeIndex]
  if (!n) return
  emit('select-node', { index: nodeIndex, node: n })
}

const WIDTH = 860
const NODE_W = 13
const NODE_GAP = 7
const PAD_Y = 10
const HEAD_H = 26          // room for the column headings above the columns
const L_GUTTER = 150        // room for the first column's labels on the left
const R_GUTTER = 265        // room for (long) last-column labels on the right
const BAR_TOTAL = 540       // summed node-bar height of the busiest column → the scale

function trunc(s, n = 30) {
  s = String(s == null ? '' : s)
  return s.length > n ? s.slice(0, n - 1) + '…' : s
}

const colOf = (n) => (n.column != null ? n.column : (n.side === 'asker' ? 0 : 1))

const layout = computed(() => {
  const nodes = props.nodes
  const links = props.links
  if (!nodes.length || !links.length) return null

  const columns = [...new Set(nodes.map(colOf))].sort((a, b) => a - b)
  const lastCol = columns[columns.length - 1]
  const xLeft = L_GUTTER
  const xRight = WIDTH - R_GUTTER - NODE_W
  const colX = new Map()
  columns.forEach((c, i) => {
    colX.set(c, columns.length === 1
      ? xLeft
      : xLeft + (xRight - xLeft) * (i / (columns.length - 1)))
  })

  // A node's height is its throughput: max(incoming, outgoing) flow (the two are
  // equal for interior nodes; one side is zero at the ends).
  const inSum = nodes.map(() => 0)
  const outSum = nodes.map(() => 0)
  for (const l of links) { outSum[l.source] += l.value; inSum[l.target] += l.value }
  const totals = nodes.map((_, i) => Math.max(inSum[i], outSum[i]))

  // Scale so the busiest column fills BAR_TOTAL. Every column carries the same
  // total flow, but they can differ if a node's in/out don't balance, so take
  // the max to be safe.
  const colSum = new Map()
  nodes.forEach((n, i) => colSum.set(colOf(n), (colSum.get(colOf(n)) || 0) + totals[i]))
  const scale = BAR_TOTAL / Math.max(...colSum.values(), 1)

  const LABEL_MIN = 15   // minimum vertical gap between labels

  const box = nodes.map((n, i) => ({
    i, ...n, column: colOf(n), total: totals[i],
    h: Math.max(2, totals[i] * scale),
    x: colX.get(colOf(n)), y: 0, labelY: 0,
  }))

  // Stack each column vertically from the top of the plot area (below the
  // heading row).
  const byCol = new Map(columns.map((c) => [c, []]))
  box.forEach((b) => byCol.get(b.column).push(b.i))
  const place = (idxs) => {
    let y = PAD_Y + HEAD_H
    for (const i of idxs) { box[i].y = y; y += box[i].h + NODE_GAP }
  }
  for (const c of columns) place(byCol.get(c))

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
  for (const c of columns) declutter(byCol.get(c))
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

  const ribbons = links.map((l, k) => {
    const h = l.value * scale
    const s0 = sTop.get(l), s1 = s0 + h
    const t0 = tTop.get(l), t1 = t0 + h
    const xa = box[l.source].x + NODE_W
    const xb = box[l.target].x
    const mx = (xa + xb) / 2
    return {
      key: k,
      color: l.color || 'var(--accent)',
      value: l.value,
      source: box[l.source], target: box[l.target],
      d: `M${xa},${s0} C${mx},${s0} ${mx},${t0} ${xb},${t0}` +
         ` L${xb},${t1} C${mx},${t1} ${mx},${s1} ${xa},${s1} Z`,
    }
  })

  // Column headings, positioned to the outward edge of the first / last column
  // and centred over any interior column.
  const headings = columns.map((c, i) => {
    let text = props.columnHeadings[i] || ''
    if (!text) {
      if (i === 0) text = props.askerHeading
      else if (c === lastCol) text = props.answererHeading
    }
    const x = colX.get(c)
    if (i === 0) return { key: c, text, x: x + NODE_W, anchor: 'start' }
    if (c === lastCol) return { key: c, text, x, anchor: 'end' }
    return { key: c, text, x: x + NODE_W / 2, anchor: 'middle' }
  }).filter((h) => h.text)

  return { H, box, ribbons, headings, lastCol }
})

const active = ref(-1)   // hovered/focused node index (highlight its ribbons)
</script>

<template>
  <figure v-if="layout" class="sankey" style="margin:0;">
    <figcaption v-if="caption && showCaption" class="small soft" style="margin-bottom:.5rem;">{{ caption }}</figcaption>
    <div class="scroll">
      <svg :viewBox="`0 0 ${WIDTH} ${layout.H}`" class="svg" role="img" :aria-label="caption">
        <!-- column headings -->
        <text
          v-for="h in layout.headings" :key="'h' + h.key"
          :x="h.x" :y="12" class="colhead" :text-anchor="h.anchor"
        >{{ h.text }}</text>

        <!-- ribbons (each is a clickable flow → drill-down) -->
        <path
          v-for="r in layout.ribbons" :key="'r' + r.key" :d="r.d"
          class="ribbon" :class="{
            dim: selected !== -1
              ? r.key !== selected
              : selectedNode !== -1
                ? (r.source.i !== selectedNode && r.target.i !== selectedNode)
                : (active !== -1 && r.source.i !== active && r.target.i !== active),
            sel: r.key === selected
              || (selectedNode !== -1 && (r.source.i === selectedNode || r.target.i === selectedNode)),
          }"
          :fill="r.color" role="button" tabindex="0"
          :aria-label="`${r.source.label} → ${r.target.label}: ${r.value}`"
          @click="pick(r.key)" @keydown.enter.prevent="pick(r.key)" @keydown.space.prevent="pick(r.key)"
        >
          <title>{{ r.source.label }} → {{ r.target.label }}: {{ r.value }}</title>
        </path>

        <!-- nodes (each is a clickable endpoint → filter by that node alone) -->
        <g
          v-for="b in layout.box" :key="'n' + b.i"
          class="nodegrp" :class="{ selnode: b.i === selectedNode }"
          role="button" tabindex="0" :aria-label="`${b.label}: ${b.total}`"
          @mouseenter="active = b.i" @mouseleave="active = -1"
          @click="pickNode(b.i)"
          @keydown.enter.prevent="pickNode(b.i)" @keydown.space.prevent="pickNode(b.i)"
        >
          <rect
            :x="b.x" :y="b.y" :width="NODE_W" :height="b.h" rx="2"
            class="node" :fill="b.color || 'var(--accent)'"
          >
            <title>{{ b.label }}: {{ b.total }}</title>
          </rect>
          <text
            v-if="b.column === 0" :x="b.x - 6" :y="b.labelY"
            class="nlabel" text-anchor="end" dominant-baseline="middle"
          >{{ trunc(b.label) }} <tspan class="nval">({{ b.total }})</tspan></text>
          <text
            v-else :x="b.x + NODE_W + 6" :y="b.labelY"
            class="nlabel" :class="{ halo: b.column !== layout.lastCol }"
            text-anchor="start" dominant-baseline="middle"
          >{{ trunc(b.label) }} <tspan class="nval">({{ b.total }})</tspan></text>
        </g>
      </svg>
    </div>
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
.node { stroke: rgba(0,0,0,.12); stroke-width: .5; transition: stroke .12s ease, stroke-width .12s ease; }
.nlabel { font-size: 12px; fill: var(--ink); }
/* Interior-column labels sit over the ribbons — give them a background halo so
   they stay legible. */
.nlabel.halo { paint-order: stroke; stroke: var(--surface); stroke-width: 3px; stroke-linejoin: round; }
.nval { fill: var(--ink-soft); font-variant-numeric: tabular-nums; }
.nodegrp { cursor: pointer; }
.nodegrp:focus { outline: none; }
.nodegrp:hover .node { stroke: var(--ink-soft); stroke-width: 1; }
.nodegrp:focus-visible .node { stroke: var(--accent); stroke-width: 1.5; }
.nodegrp.selnode .node { stroke: var(--accent); stroke-width: 2; }
.nodegrp.selnode .nlabel { font-weight: 700; }
@media (prefers-reduced-motion: reduce) { .ribbon, .node { transition: none; } }
</style>
