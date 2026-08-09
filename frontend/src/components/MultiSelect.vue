<script setup>
// A filter control for a field that can hold several values at once — the
// checkbox-dropdown counterpart of a plain <select>. The trigger looks and sits
// exactly like a <select> in a filter grid and says what is in scope ("Mind",
// the single chosen value, or "n kiválasztva"); the panel below lists every
// option with a tick box.
//
// Used by the irományok list's type and status filters, where a reader routinely
// wants two or three categories together (kérdés *and* interpelláció) and a
// single-choice <select> made that impossible.
//
// As in CycleSelect, the empty selection *is* "all": "Mind" clears the ticks
// rather than being a value of its own, so the scope is never an empty set.
import { computed, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  // Either plain strings, or { value, label } objects when the two differ.
  options: { type: Array, default: () => [] },
  // The field's name ("Típus"), for the panel's accessible name.
  label: { type: String, default: '' },
  // What the trigger reads when nothing is ticked; defaults to the shared "Mind".
  allLabel: { type: String, default: '' },
  id: { type: String, default: undefined },
})
const emit = defineEmits(['update:modelValue', 'change'])
const { t } = useI18n()

const open = ref(false)
const rootRef = ref(null)

const normalized = computed(() => props.options
  .filter((o) => o !== null && o !== undefined && o !== '')
  .map((o) => (typeof o === 'object' ? o : { value: o, label: String(o) })))

// A selected value the option list doesn't offer still gets a row: the options
// are facets scoped to the current cycle, so switching cycle can strip one out
// from under an active filter — without a row of its own that filter would stay
// on with no way to untick it.
const allOptions = computed(() => {
  const known = new Set(normalized.value.map((o) => o.value))
  return [
    ...normalized.value,
    ...props.modelValue.filter((v) => !known.has(v)).map((v) => ({ value: v, label: String(v) })),
  ]
})

const allText = computed(() => props.allLabel || t('search.all'))

// Trigger text: the value itself when exactly one is ticked, a count beyond that
// — spelling out three iromány types would blow the filter grid's column width.
const triggerText = computed(() => {
  const sel = props.modelValue
  if (!sel.length) return allText.value
  if (sel.length === 1) {
    const o = allOptions.value.find((x) => x.value === sel[0])
    return o ? o.label : String(sel[0])
  }
  return t('search.selectedCount', { n: sel.length })
})

function isSelected(v) { return props.modelValue.includes(v) }

// `update:modelValue` first, then `change`: they fire synchronously, so v-model
// has already written the new array by the time the parent's change handler runs
// and reads it back to build the URL.
function emitValue(next) {
  emit('update:modelValue', next)
  emit('change', next)
}
function toggle(v) {
  emitValue(isSelected(v) ? props.modelValue.filter((x) => x !== v) : [...props.modelValue, v])
}
function selectAll() {
  emitValue([])
  open.value = false   // "all" is a terminal choice — nothing left to tick
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
  <div ref="rootRef" class="msel">
    <button
      :id="id" type="button" class="mstrigger" :class="{ open }"
      :aria-expanded="open ? 'true' : 'false'" aria-haspopup="true"
      @click="open = !open"
    >
      <span class="mstext">{{ triggerText }}</span>
      <svg class="mschevron" viewBox="0 0 24 24" width="12" height="12" fill="none"
           stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"
           aria-hidden="true" focusable="false">
        <path d="M6 9l6 6 6-6" />
      </svg>
    </button>

    <div v-if="open" class="msmenu" role="group" :aria-label="label || undefined">
      <button
        type="button" class="msall" :class="{ selected: !modelValue.length }"
        :aria-pressed="!modelValue.length ? 'true' : 'false'" @click="selectAll"
      >{{ allText }}</button>
      <div class="msmenu-sep" aria-hidden="true"></div>
      <label v-for="o in allOptions" :key="o.value" class="msopt">
        <input type="checkbox" :checked="isSelected(o.value)" @change="toggle(o.value)" />
        <span>{{ o.label }}</span>
      </label>
      <p v-if="allOptions.length" class="mshint">{{ $t('search.multiHint') }}</p>
    </div>
  </div>
</template>

<style scoped>
.msel { position: relative; }

/* The trigger mirrors the global <select> box model (styles.css) so a
   multi-value filter is indistinguishable from its neighbours in a filter grid. */
.mstrigger {
  display: flex; align-items: center; justify-content: space-between; gap: .4rem;
  width: 100%; font: inherit; text-align: left; cursor: pointer;
  padding: .5rem .6rem; border: 1px solid #c7c4bc; border-radius: 8px;
  background: var(--surface); color: var(--ink);
}
.mstrigger:hover, .mstrigger.open { border-color: var(--ink-soft); }
.mstext { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mschevron { flex-shrink: 0; color: var(--ink-soft); }
.mstrigger.open .mschevron { transform: rotate(180deg); }

/* Panel: anchored under the trigger, at least as wide as it, and free to grow
   wider — iromány type names are long — but never past the viewport. */
.msmenu {
  position: absolute; top: calc(100% + .3rem); left: 0; z-index: 30;
  min-width: 100%; max-width: min(22rem, 90vw); max-height: 60vh; overflow-y: auto;
  display: flex; flex-direction: column; gap: .1rem; padding: .3rem;
  background: var(--surface); color: var(--ink);
  border: 1px solid var(--line); border-radius: 10px; box-shadow: 0 10px 24px rgba(0, 0, 0, .18);
}
.msall {
  width: 100%; padding: .4rem .55rem; border: none; background: none; cursor: pointer;
  border-radius: 6px; font: inherit; font-size: .88rem; font-weight: 600;
  color: var(--ink); text-align: left;
}
.msall:hover { background: var(--accent-soft); }
.msall.selected, .msall.selected:hover { background: var(--accent); color: #fff; font-weight: 700; }
.msmenu-sep { height: 1px; margin: .3rem .2rem; background: var(--line); }
/* Overrides the global `label` rule (block, muted, bottom margin), which is
   meant for a field's caption rather than a row inside a menu. */
.msopt {
  display: flex; align-items: center; gap: .5rem; margin: 0;
  padding: .35rem .55rem; border-radius: 6px; cursor: pointer;
  font-size: .88rem; font-weight: 500; color: var(--ink);
}
.msopt:hover { background: var(--accent-soft); }
.msopt input { flex: none; accent-color: var(--accent); }
.mshint {
  margin: .3rem .55rem .15rem; font-size: .72rem; color: var(--ink-soft);
}
</style>
