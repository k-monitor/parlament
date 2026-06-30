<script setup>
// Dependency-free word cloud (WCLOUD-1/3): the classic packed "tag cloud" look —
// the most distinctive word anchored at the centre and the rest spiralled around
// it (some rotated upright), sized by weight. Same built-in accessible table
// equivalent as the other charts (REP-6 / A11Y-1), so sizing/placement is never
// the only carrier of meaning.
// `words` = [{ text, count, weight, kind }] ordered by weight desc. `weight` is
// the sizing metric (a TF·IDF "distinctiveness" score); `count` is the raw
// occurrences shown to the user. Words are HuSpaCy lemmas; `kind === 'entity'`
// marks a recognized named entity (person/place/organisation), styled distinctly.
// Each word emits `pick` so the parent can deep-link it into search scoped to the
// sitting day (WCLOUD-4).
import { ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
const props = defineProps({
  words: { type: Array, default: () => [] },
  caption: { type: String, default: '' },
})
defineEmits(['pick'])

const cloudEl = ref(null)
const placed = ref([])        // laid-out words: { ...word, left, top, size, rot, fw, op }
const cloudHeight = ref(0)
let ro = null
let lastWidth = 0

// Font range (rem). A wider range than a plain list so the dominant word reads as
// the headline of the cloud. Sizing is decorative — the count is in the table.
const MIN_REM = 0.62
const MAX_REM = 2.52
const GAP = 3                 // px breathing room between word boxes
const wt = (w) => (w.weight != null ? w.weight : w.count)

// Deterministic per-word hash → stable rotation / spiral phase, so a relayout on
// resize doesn't reshuffle the cloud.
function hash(str) {
  let h = 2166136261
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619) }
  return h >>> 0
}

function sizeRem(v, min, max) {
  if (max === min) return (MIN_REM + MAX_REM) / 2
  const t = (Math.sqrt(v) - Math.sqrt(min)) / (Math.sqrt(max) - Math.sqrt(min))
  return MIN_REM + t * (MAX_REM - MIN_REM)
}
function fontWeight(rem) { return 400 + Math.round((rem - MIN_REM) / (MAX_REM - MIN_REM) * 3) * 100 }
function opacity(rem) { return 0.5 + 0.5 * (rem - MIN_REM) / (MAX_REM - MIN_REM) }

function layout() {
  const el = cloudEl.value
  if (!el) return
  const W = el.clientWidth
  if (!W) return
  lastWidth = W
  const list = props.words
  if (!list.length) { placed.value = []; cloudHeight.value = 0; return }

  const rootPx = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
  const family = getComputedStyle(el).fontFamily || 'sans-serif'
  const ctx = (layout._cvs || (layout._cvs = document.createElement('canvas'))).getContext('2d')

  const vals = list.map(wt)
  const max = Math.max(1e-6, ...vals)
  const min = Math.min(...vals, max)
  const order = list.map((_, i) => i).sort((a, b) => wt(list[b]) - wt(list[a]))

  // Place each word (largest first) on an Archimedean spiral from the centre,
  // walking outward until its box clears every already-placed box. The spiral is
  // flattened (aspect < 1) so the cloud spreads into a landscape shape.
  const ASPECT = 0.62
  const boxes = []              // { x, y, hw, hh } centre + half extents (incl. gap)
  const items = []
  for (const i of order) {
    const w = list[i]
    const rem = sizeRem(wt(w), min, max)
    const px = rem * rootPx
    const fw = fontWeight(rem)
    ctx.font = `${fw} ${px}px ${family}`
    const m = ctx.measureText(w.text)
    const tw = m.width
    const th = (m.actualBoundingBoxAscent != null && m.actualBoundingBoxDescent != null)
      ? m.actualBoundingBoxAscent + m.actualBoundingBoxDescent : px * 0.92
    const h = hash(w.text)
    const rot = ((h % 100) < 26 && w.text.length <= 13) ? 90 : 0   // ~1/4 upright
    const bw = (rot ? th : tw) + GAP * 2
    const bh = (rot ? tw : th) + GAP * 2

    let x = 0, y = 0
    const phase = (h % 628) / 100               // 0..2π starting angle
    for (let t = 0; t < 4000; t++) {
      const angle = phase + t * 0.22
      const r = t * 0.28
      const cx = Math.cos(angle) * r
      const cy = Math.sin(angle) * r * ASPECT
      let hit = false
      for (const b of boxes) {
        if (Math.abs(b.x - cx) < b.hw + bw / 2 && Math.abs(b.y - cy) < b.hh + bh / 2) { hit = true; break }
      }
      if (!hit) { x = cx; y = cy; break }
    }
    boxes.push({ x, y, hw: bw / 2, hh: bh / 2 })
    items.push({ w, x, y, hw: bw / 2, hh: bh / 2, rem, fw, op: opacity(rem), rot })
  }

  // Bounding box → uniform-scale to fit the container width (keeps non-overlap),
  // then centre horizontally.
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
  for (const it of items) {
    minX = Math.min(minX, it.x - it.hw); maxX = Math.max(maxX, it.x + it.hw)
    minY = Math.min(minY, it.y - it.hh); maxY = Math.max(maxY, it.y + it.hh)
  }
  const bw = maxX - minX || 1
  const bh = maxY - minY || 1
  const scale = Math.min(1, W / bw)
  const offX = (W - bw * scale) / 2
  cloudHeight.value = Math.ceil(bh * scale)

  placed.value = items.map((it) => ({
    text: it.w.text, count: it.w.count, kind: it.w.kind,
    left: offX + (it.x - minX) * scale,
    top: (it.y - minY) * scale,
    size: it.rem * scale,
    fw: it.fw, op: it.op, rot: it.rot,
  }))
}

function relayout() { nextTick(layout) }

onMounted(() => {
  relayout()
  ro = new ResizeObserver(() => {
    const w = cloudEl.value ? cloudEl.value.clientWidth : 0
    if (w && w !== lastWidth) layout()       // width change only (avoid height feedback)
  })
  if (cloudEl.value) ro.observe(cloudEl.value)
})
onBeforeUnmount(() => { if (ro) ro.disconnect() })
watch(() => props.words, relayout)
</script>

<template>
  <figure v-if="words.length" class="wc" style="margin:0;">
    <figcaption v-if="caption" class="small soft" style="margin-bottom:.5rem;">{{ caption }}</figcaption>
    <div ref="cloudEl" class="cloud" :style="{ height: cloudHeight + 'px' }" role="img" :aria-label="caption">
      <button
        v-for="p in placed" :key="p.text" type="button" class="word"
        :class="{ 'is-entity': p.kind === 'entity' }"
        :style="{
          left: p.left + 'px', top: p.top + 'px',
          fontSize: p.size + 'rem', fontWeight: p.fw, opacity: p.op,
          transform: `translate(-50%, -50%) rotate(${p.rot}deg)`,
        }"
        :title="p.kind === 'entity' ? `${p.text}: ${p.count} · ${$t('sessions.wordcloudEntity')}` : `${p.text}: ${p.count}`"
        @click="$emit('pick', p.text)"
      >{{ p.text }}</button>
    </div>
  </figure>
</template>

<style scoped>
.cloud { position: relative; width: 100%; line-height: 1; overflow: hidden; }
.word {
  position: absolute; white-space: nowrap;
  background: none; border: 0; padding: 0; cursor: pointer; color: var(--accent);
  font-family: inherit; line-height: 1; border-radius: 4px;
  transition: color .12s, background .12s;
}
.word:hover, .word:focus-visible { background: var(--accent-soft); color: var(--ink); outline: none; }
/* Named entities (people/places/organisations) read as a distinct, "tagged"
   token — italic with a dotted underline — so they stand out from plain lemmas. */
.word.is-entity { font-style: italic; text-decoration: underline dotted; text-underline-offset: 3px; }
</style>
