<script setup>
// Dependency-free SVG "faction map": each faction is a bubble, positioned so that
// distance ≈ how rarely the two factions vote together (from a classical-MDS
// layout the parent computes), so voting blocs cluster spatially. Bubble radius
// encodes the faction's size (members); fill opacity encodes internal cohesion
// (a fuller bubble votes more as one). The axes carry no meaning — only the
// distances do — so there are none. Colour/position are never the sole carriers:
// each bubble is text-labelled and carries its size/cohesion in a hover title,
// and the map has a screen-reader summary (A11Y-1).
import { computed, ref } from 'vue'

const props = defineProps({
  // [{ name, color, size, cohesion, x, y }] — x/y are raw MDS coordinates.
  points: { type: Array, default: () => [] },
  caption: { type: String, default: '' },
  sizeLabel: { type: String, default: '' },
  cohesionLabel: { type: String, default: '' },
})

const W = 540
const H = 380
const MX = 66           // horizontal margin (room for edge bubbles + labels)
const MY = 52
const RMIN = 12
const RMAX = 34

function trunc(s, k = 14) {
  s = String(s == null ? '' : s)
  return s.length > k ? s.slice(0, k - 1) + '…' : s
}
function pct(v) { return v == null ? '—' : Math.round(v * 100) + '%' }

const laid = computed(() => {
  const pts = props.points
  if (!pts.length) return []
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y)
  const minX = Math.min(...xs), maxX = Math.max(...xs)
  const minY = Math.min(...ys), maxY = Math.max(...ys)
  const spanX = (maxX - minX) || 1, spanY = (maxY - minY) || 1
  const s = Math.min((W - 2 * MX) / spanX, (H - 2 * MY) / spanY)
  const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2
  const maxSize = Math.max(1, ...pts.map((p) => p.size || 0))
  const out = pts.map((p, i) => ({
    i, name: p.name, color: p.color || 'var(--ink-soft)',
    size: p.size, cohesion: p.cohesion,
    cxp: W / 2 + (p.x - cx) * s,
    cyp: H / 2 - (p.y - cy) * s,     // flip: SVG y grows downward
    r: RMIN + (RMAX - RMIN) * Math.sqrt((p.size || 0) / maxSize),
    fillOpacity: 0.2 + 0.6 * (p.cohesion == null ? 0 : p.cohesion),
    title: `${p.name} · ${sizeLabelText(p)}`,
  }))

  // Gentle de-overlap: nudge apart any two bubbles whose discs intersect, so a
  // coalition that votes almost identically (near-zero MDS distance) still shows
  // as two readable bubbles rather than one stack — without meaningfully moving
  // the layout. Coincident points get a deterministic spread direction.
  const PAD = 7
  for (let iter = 0; iter < 80; iter++) {
    let moved = false
    for (let a = 0; a < out.length; a++) {
      for (let b = a + 1; b < out.length; b++) {
        const A = out[a], B = out[b]
        let dx = B.cxp - A.cxp, dy = B.cyp - A.cyp
        let d = Math.hypot(dx, dy)
        if (d < 0.5) { dx = Math.cos(a * 1.7); dy = Math.sin(a * 1.7); d = 1 }
        const min = A.r + B.r + PAD
        if (d < min) {
          const push = (min - d) / 2
          dx /= d; dy /= d
          A.cxp -= dx * push; A.cyp -= dy * push
          B.cxp += dx * push; B.cyp += dy * push
          moved = true
        }
      }
    }
    if (!moved) break
  }
  // Keep every bubble inside the canvas after nudging.
  for (const o of out) {
    o.cxp = Math.max(o.r + 2, Math.min(W - o.r - 2, o.cxp))
    o.cyp = Math.max(o.r + 2, Math.min(H - o.r - 14, o.cyp))
  }
  return out
})
function sizeLabelText(p) {
  return `${props.sizeLabel} ${p.size} · ${props.cohesionLabel} ${pct(p.cohesion)}`
}

const hi = ref(-1)
</script>

<template>
  <figure class="fmap" style="margin:0;">
    <div class="scroll">
      <svg :viewBox="`0 0 ${W} ${H}`" class="svg" role="img" :aria-label="caption">
        <g v-for="p in laid" :key="p.i"
           @mouseenter="hi = p.i" @mouseleave="hi = -1" class="bub"
           :class="{ dim: hi !== -1 && hi !== p.i }">
          <circle :cx="p.cxp" :cy="p.cyp" :r="p.r"
                  :fill="p.color" :fill-opacity="p.fillOpacity"
                  :stroke="p.color" stroke-width="2" class="disc">
            <title>{{ p.title }}</title>
          </circle>
          <text :x="p.cxp" :y="p.cyp + p.r + 13" text-anchor="middle" class="lbl">{{ trunc(p.name) }}</text>
        </g>
      </svg>
    </div>

  </figure>
</template>

<style scoped>
.scroll { overflow-x: auto; }
.svg { width: 100%; min-width: 320px; max-width: 560px; height: auto; display: block; }
.bub { cursor: default; transition: opacity .12s ease; }
.bub.dim { opacity: .3; }
.disc { transition: fill-opacity .12s ease; }
.bub:hover .disc { fill-opacity: .85; }
.lbl {
  font-size: 12px; font-weight: 600; fill: var(--ink);
  paint-order: stroke; stroke: var(--surface); stroke-width: 3px; stroke-linejoin: round;
}
@media (prefers-reduced-motion: reduce) { .bub, .disc { transition: none; } }
</style>
