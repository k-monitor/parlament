<script setup>
// Global electoral-cycle chooser, sitting in the site header next to the
// language toggle. Several cycles can be in scope at once, so this is a
// checkbox dropdown rather than a native <select>: the panel lists "all cycles"
// plus every electoral period newest-first, and ticking more than one widens the
// scope of every period-aware view (store.cycles; see store.js).
//
// "All cycles" is the empty selection, not a separate value — ticking it clears
// the rest, and unticking the last cycle falls back to it, so the scope is never
// an empty result set.
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { store, periodLabel, serializeCycles, setCycles } from '../store.js'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

// Periods arrive newest-first from /meta, which is the order to offer them in:
// the current cycle is what most readers want, so it sits at the top.
const periods = computed(() => (store.meta && store.meta.periods) || [])

const open = ref(false)
const rootRef = ref(null)

// Button label: the single selected cycle, both when two are selected, and a
// count beyond that — the button must stay narrow enough for the header bar.
const label = computed(() => {
  const sel = [...store.cycles].sort((a, b) => b - a)
  if (!sel.length) return t('cycle.all')
  const labels = sel.map((n) => {
    const p = periods.value.find((x) => x.number === n)
    return p ? periodLabel(p) : String(n)
  })
  return labels.length <= 2 ? labels.join(', ') : t('cycle.count', { n: labels.length })
})

function isSelected(number) { return store.cycles.includes(number) }

// Apply a new scope: persist it and mirror it into the URL (replace, so the
// chooser doesn't pile up history entries) — the router guard keeps store ↔ URL
// in sync from there.
function apply(cycles) {
  setCycles(cycles)
  router.replace({ query: { ...route.query, cycle: serializeCycles(store.cycles) } })
}

function toggle(number) {
  apply(isSelected(number)
    ? store.cycles.filter((n) => n !== number)   // last one off → back to all cycles
    : [...store.cycles, number])
}

function selectAll() {
  apply([])
  open.value = false   // "all cycles" is a terminal choice — nothing left to tick
}

// Close on an outside click or Escape; the listeners exist only while open.
function onDocClick(e) {
  if (rootRef.value && !rootRef.value.contains(e.target)) open.value = false
}
function onKeydown(e) {
  if (e.key === 'Escape') open.value = false
}
watch(open, (isOpen) => {
  if (isOpen) {
    document.addEventListener('click', onDocClick)
    document.addEventListener('keydown', onKeydown)
  } else {
    document.removeEventListener('click', onDocClick)
    document.removeEventListener('keydown', onKeydown)
  }
})
onUnmounted(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div v-if="periods.length" ref="rootRef" class="cyclesel">
    <button
      type="button" class="cyclebtn" :class="{ open }" @click="open = !open"
      :title="$t('cycle.label')" :aria-label="$t('cycle.label') + ': ' + label"
      :aria-expanded="open ? 'true' : 'false'" aria-haspopup="true"
    >
      <span class="cyclebtn-label">{{ label }}</span>
      <svg class="cyclebtn-chevron" viewBox="0 0 24 24" width="12" height="12" fill="none"
           stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"
           aria-hidden="true" focusable="false">
        <path d="M6 9l6 6 6-6" />
      </svg>
    </button>

    <div v-if="open" class="cyclemenu" role="group" :aria-label="$t('cycle.label')">
      <button
        type="button" class="cycleopt" :class="{ selected: !store.cycles.length }"
        :aria-pressed="!store.cycles.length ? 'true' : 'false'" @click="selectAll"
      >{{ $t('cycle.all') }}</button>
      <div class="cyclemenu-sep" aria-hidden="true"></div>
      <button
        v-for="p in periods" :key="p.number"
        type="button" class="cycleopt" :class="{ selected: isSelected(p.number) }"
        :aria-pressed="isSelected(p.number) ? 'true' : 'false'" @click="toggle(p.number)"
      >{{ periodLabel(p) }}</button>
      <p class="cyclehint">{{ $t('cycle.multiHint') }}</p>
    </div>
  </div>
</template>

<style scoped>
.cyclesel { position: relative; }

/* The trigger matches the other header controls' box model (see App.vue) so the
   whole right-hand cluster lines up on one baseline. */
.cyclebtn {
  box-sizing: border-box; height: 34px; display: inline-flex; align-items: center; gap: .4rem;
  max-width: 13rem; padding: 0 .7rem; cursor: pointer;
  border: 1px solid rgba(255,255,255,.35); background: rgba(255,255,255,.15);
  border-radius: 8px; color: #fff; font-family: inherit; font-weight: 700; font-size: .8rem;
}
.cyclebtn:hover, .cyclebtn.open { background: rgba(255,255,255,.28); }
.cyclebtn-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cyclebtn-chevron { flex-shrink: 0; }
.cyclebtn.open .cyclebtn-chevron { transform: rotate(180deg); }

/* Dropdown panel: anchored to the button's right edge so it never runs off the
   viewport on narrow screens, above the sticky header's own stacking context. */
.cyclemenu {
  position: absolute; top: calc(100% + .4rem); right: 0; z-index: 40;
  min-width: 12rem; max-height: 70vh; overflow-y: auto;
  display: flex; flex-direction: column; gap: .1rem; padding: .3rem;
  background: var(--surface); color: var(--ink);
  border: 1px solid var(--line); border-radius: 10px; box-shadow: 0 10px 24px rgba(0,0,0,.18);
}
/* No tick boxes: a selected cycle is simply the highlighted row — a solid accent
   pill, the same "active scope" language the site uses elsewhere. Hover stays a
   soft tint, so it never reads as a selection. */
.cycleopt {
  display: flex; align-items: center; width: 100%;
  padding: .45rem .6rem; border: none; background: none; cursor: pointer;
  border-radius: 6px; font-family: inherit; font-size: .88rem; font-weight: 600;
  color: var(--ink); text-align: left; white-space: nowrap;
}
.cycleopt:hover { background: var(--accent-soft); }
.cycleopt.selected, .cycleopt.selected:hover {
  background: var(--accent); color: #fff; font-weight: 700;
}
.cyclemenu-sep { height: 1px; margin: .3rem .2rem; background: var(--line); }
.cyclehint {
  margin: .2rem .55rem .15rem; font-size: .72rem; color: var(--ink-soft);
  white-space: normal; max-width: 13rem;
}
</style>
