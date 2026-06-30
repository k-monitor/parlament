<script setup>
// Small "?" icon that reveals a description in a popover on hover/focus/click,
// so a section's explanatory text can be tucked away to keep the page compact.
// The description goes in the default slot. Reuses the activity board's icon look.
import { ref } from 'vue'
const open = ref(false)
defineProps({ label: { type: String, default: '' } })
</script>

<template>
  <span class="helptip" @mouseenter="open = true" @mouseleave="open = false">
    <button type="button" class="help" :aria-label="label || 'Részletek'"
            :aria-expanded="open" @click="open = !open" @blur="open = false"
            @keydown.esc="open = false">
      <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
        <circle cx="8" cy="8" r="7.2" fill="none" stroke="currentColor" stroke-width="1.3" />
        <text x="8" y="11.7" text-anchor="middle" font-size="10" font-weight="700" fill="currentColor">?</text>
      </svg>
    </button>
    <span v-if="open" class="bubble small" role="tooltip"><slot /></span>
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
  position: absolute; top: calc(100% + 6px); left: 0; z-index: 5;
  width: max-content; max-width: 320px;
  background: var(--surface); color: var(--ink-soft);
  border: 1px solid var(--line); border-radius: 8px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, .14);
  padding: .55rem .7rem; text-align: left; font-weight: 400; line-height: 1.45;
}
.bubble :deep(p) { margin: 0; }
.bubble :deep(p + p) { margin-top: .4rem; }
</style>
