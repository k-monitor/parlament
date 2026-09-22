<script setup>
// Hover preview of a committee sitting's agenda (BIZ-4b) — the committee-side
// counterpart of the sitting day's word-cloud preview: what was this sitting
// about, without opening it. A committee meeting list is a column of dates and
// body names and says nothing about the subject, which is the one thing a
// reader is scanning for.
//
// Shared by the two lists that show committee sittings — the committee's own
// sheet and the sittings page's committee tab — so the behaviour (delay, cache,
// placement, scroll dismissal) is defined once and they cannot drift apart.
//
// The host list mounts one of these and drives it through the exposed
// `open(id, el)` / `close()`, since the anchor is whichever row the pointer is
// on. The popup itself is informational: it never takes the pointer, so it can
// never swallow the click on the row it describes.
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { api } from '../api.js'

const ITEMS = 5                  // agenda points shown; the rest are counted
const HOVER_DELAY = 140          // ms — ignore a quick pass over a row
const MAX_W = 380                // px — cap so the popup stays tooltip-sized
const MIN_W = 240

// { id, items, total, left, top, placement, width } — the popup on screen.
const preview = ref(null)
// meeting id → { items, total }; an empty result is cached too, so a sitting
// whose agenda we do not hold is asked for once and never again.
const cache = new Map()
let hoverTimer = 0
let hoverId = null               // the row under pointer/focus (debounce guard)

async function fetchAgenda(id) {
  if (cache.has(id)) return cache.get(id)
  try {
    const res = await api.committeeMeetingAgenda(id, ITEMS)
    const out = { items: res.items || [], total: res.total || 0 }
    cache.set(id, out)
    return out
  } catch {
    const empty = { items: [], total: 0 }
    cache.set(id, empty)
    return empty
  }
}

function position(el) {
  const r = el.getBoundingClientRect()
  // Prefer below the row; flip above when there is not enough room beneath it.
  const below = window.innerHeight - r.bottom > 220
  const width = Math.min(Math.max(r.width, MIN_W), MAX_W)
  // Anchor to the row's left edge, but keep the whole popup in the viewport.
  const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8))
  return {
    left,
    top: below ? r.bottom + 8 : r.top - 8,
    placement: below ? 'below' : 'above',
    width,
  }
}

function open(id, el) {
  if (!id || !el) return
  hoverId = id
  clearTimeout(hoverTimer)
  hoverTimer = setTimeout(async () => {
    const agenda = await fetchAgenda(id)
    // Moved on since, or there is nothing to show — a sitting whose jegyzőkönyv
    // we have not read has no agenda, and an empty popup would be worse than
    // none.
    if (hoverId !== id || !agenda.items.length) return
    preview.value = { id, ...agenda, ...position(el) }
  }, HOVER_DELAY)
}

function close() {
  hoverId = null
  clearTimeout(hoverTimer)
  preview.value = null
}

// A fixed-position popup goes stale on scroll (it does not track the row), so
// it is simply dismissed; the next hover re-opens it in the right place.
onMounted(() => window.addEventListener('scroll', close, { passive: true }))
onBeforeUnmount(() => {
  clearTimeout(hoverTimer)
  window.removeEventListener('scroll', close)
})

defineExpose({ open, close })
</script>

<template>
  <!-- Teleported to body so its fixed position is viewport-relative (no
       ancestor overflow or transform can clip it) and it stacks above the page. -->
  <Teleport to="body">
    <Transition name="agp">
      <div
        v-if="preview" class="ag-preview card pad" :class="preview.placement"
        :style="{ left: preview.left + 'px', top: preview.top + 'px',
                  width: preview.width + 'px' }"
        aria-hidden="true"
      >
        <div class="agp-label small soft">{{ $t('committees.agendaPreview') }}</div>
        <ol class="agp-items">
          <li v-for="(it, i) in preview.items" :key="i" class="agp-item">
            <span v-if="it.ordinal" class="agp-num">{{ it.ordinal }}.</span>
            <!-- An agenda title runs to a paragraph ("…szóló 2004. évi I.
                 törvény 44. § (1)…"), so it is clamped: the preview answers
                 "what was this about", the sitting itself answers the rest. -->
            <span class="agp-title">{{ it.title }}</span>
            <span v-if="it.billNumber" class="agp-bill">{{ it.billNumber }}</span>
          </li>
        </ol>
        <!-- Said rather than silently cut: a five-point preview of a fourteen
             point sitting would otherwise read as the whole agenda. -->
        <div v-if="preview.total > preview.items.length" class="agp-more small soft">
          {{ $t('committees.agendaMore', { count: preview.total - preview.items.length }) }}
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.ag-preview {
  position: fixed; z-index: 60; pointer-events: none;
  padding: .6rem .75rem;
  box-shadow: 0 4px 20px rgba(0,0,0,.14), 0 1px 4px rgba(0,0,0,.08);
}
/* Anchored above the row: its top is the row's top, so lift it by its height. */
.ag-preview.above { transform: translateY(-100%); }
.agp-label { margin-bottom: .35rem; font-weight: 600; }
.agp-items { list-style: none; margin: 0; padding: 0; display: grid; gap: .3rem; }
.agp-item {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: .1rem .35rem;
  font-size: .84rem; line-height: 1.35;
}
.agp-num { color: var(--ink-soft); font-variant-numeric: tabular-nums; }
.agp-title {
  flex: 1; min-width: 0;
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2;
  line-clamp: 2; overflow: hidden;
}
.agp-bill {
  font-size: .72rem; font-weight: 600; white-space: nowrap;
  padding: .05rem .4rem; border-radius: 999px;
  background: var(--accent-soft); color: var(--ink-soft);
}
.agp-more { margin-top: .4rem; }

.agp-enter-active, .agp-leave-active { transition: opacity .12s ease; }
.agp-enter-from, .agp-leave-to { opacity: 0; }
</style>
