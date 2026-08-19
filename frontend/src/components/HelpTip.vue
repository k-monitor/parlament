<script setup>
// Small "?" icon that reveals a description in a popover on hover/focus/click,
// so a section's explanatory text can be tucked away to keep the page compact.
// The description goes in the default slot. Reuses the activity board's icon look.
//
// The bubble is **teleported to body and positioned fixed** rather than absolutely
// inside the icon's own box, because an inline popover only survives in plain flow:
// the icon sits in a sticky table header (the comparison table's pinned label
// column), inside a horizontal scroller, inside cards — each of which clips the
// bubble or paints over it. A sticky cell creates its own stacking context, so any
// z-index the bubble carries is capped by the cell's, and every *later* sticky cell
// with the same z-index (i.e. every row below) draws its opaque background straight
// over the tip. Anchoring to the viewport escapes all of it at once.
import { nextTick, onBeforeUnmount, ref } from 'vue'

const open = ref(false)
const btn = ref(null)
const bubble = ref(null)
// { left, top, placement } — viewport coordinates, recomputed on every open.
const pos = ref({ left: 0, top: 0, placement: 'below' })
let closeTimer = 0

defineProps({ label: { type: String, default: '' } })

const MARGIN = 8                 // px kept between the bubble and the viewport edge
const GAP = 6                    // px between icon and bubble
const ROOM_BELOW = 140           // px of space under the icon needed to open downwards

// Anchor to the icon's left edge, opening below it when there is room and above it
// otherwise. The bubble is content-sized, so its width is only knowable once it is
// rendered: place it optimistically, then pull it back inside the viewport.
async function place() {
  const el = btn.value
  if (!el) return
  const r = el.getBoundingClientRect()
  const below = window.innerHeight - r.bottom > ROOM_BELOW
  pos.value = {
    left: r.left,
    top: below ? r.bottom + GAP : r.top - GAP,
    placement: below ? 'below' : 'above',
  }
  await nextTick()
  const b = bubble.value
  if (!b) return
  const w = b.getBoundingClientRect().width
  const left = Math.max(MARGIN, Math.min(r.left, window.innerWidth - w - MARGIN))
  if (left !== pos.value.left) pos.value = { ...pos.value, left }
}

// A fixed bubble does not track its icon, so any scroll (including the table
// scroller's own — hence the capture listener, scroll events don't bubble) or resize
// dismisses it; the next hover re-opens it in the right place. Only bound while a tip
// is actually open: a page can carry dozens of these icons, and none of them needs to
// hear about scrolling while shut.
let watching = false

function watchViewport(on) {
  if (on === watching) return
  watching = on
  const bind = on ? window.addEventListener : window.removeEventListener
  bind.call(window, 'scroll', hide, { passive: true, capture: true })
  bind.call(window, 'resize', hide, { passive: true })
}

function show() {
  clearTimeout(closeTimer)
  open.value = true
  watchViewport(true)
  place()
}

function hide() {
  clearTimeout(closeTimer)
  open.value = false
  watchViewport(false)
}

// The bubble is no longer a child of the hover target, so the pointer crossing the
// gap between icon and bubble would dismiss it mid-sentence: leaving either one only
// schedules the close, and entering the other cancels it.
function hideSoon() {
  clearTimeout(closeTimer)
  closeTimer = setTimeout(hide, 120)
}

function toggle() {
  if (open.value) hide()
  else show()
}

onBeforeUnmount(() => {
  clearTimeout(closeTimer)
  watchViewport(false)
})
</script>

<template>
  <span class="helptip" @mouseenter="show" @mouseleave="hideSoon">
    <button ref="btn" type="button" class="help" :aria-label="label || 'Részletek'"
            :aria-expanded="open" @click="toggle" @blur="hide"
            @keydown.esc="hide">
      <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
        <circle cx="8" cy="8" r="7.2" fill="none" stroke="currentColor" stroke-width="1.3" />
        <text x="8" y="11.7" text-anchor="middle" font-size="10" font-weight="700" fill="currentColor">?</text>
      </svg>
    </button>
    <!-- Teleported to body: scoped styles still apply (the vnode belongs to this
         component), but no ancestor can clip it or paint over it. -->
    <Teleport to="body">
      <span v-if="open" ref="bubble" class="bubble small" :class="pos.placement" role="tooltip"
            :style="{ left: pos.left + 'px', top: pos.top + 'px' }"
            @mouseenter="show" @mouseleave="hideSoon"><slot /></span>
    </Teleport>
  </span>
</template>

<style scoped>
.helptip { position: relative; display: inline-flex; vertical-align: middle; }
.help {
  display: inline-flex; padding: 0; border: 0; background: none; cursor: help;
  color: var(--ink-faint); line-height: 0;
}
.help:hover, .help[aria-expanded="true"] { color: var(--accent); }
.bubble {
  /* Above the page's own layers (sticky columns, dropdowns, the sitting-day
     preview at 60), below the modal surfaces at 100+ — a stray tip should never
     float over a dialog. */
  position: fixed; z-index: 90;
  width: max-content; max-width: min(320px, calc(100vw - 16px));
  background: var(--surface); color: var(--ink-soft);
  border: 1px solid var(--line); border-radius: 8px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, .14);
  padding: .55rem .7rem; text-align: left; font-weight: 400; line-height: 1.45;
}
/* Opened upwards, `top` is the icon's top edge: lift the bubble by its own height. */
.bubble.above { transform: translateY(-100%); }
.bubble :deep(p) { margin: 0; }
.bubble :deep(p + p) { margin-top: .4rem; }
</style>
