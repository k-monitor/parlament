<script setup>
// The site search's scope dropdown (SEA-14) — what the box beside it searches
// in. Shared by the search page and the home page so both offer the same
// scopes, in the same order, under the same names; which of them are offered is
// the modules this deployment runs (lib/searchScopes.js).
import { computed } from 'vue'
import { availableScopes } from '../lib/searchScopes.js'

defineProps({
  modelValue: { type: String, required: true },
  id: { type: String, default: undefined },
})
const emit = defineEmits(['update:modelValue'])

const scopes = computed(() => availableScopes())
</script>

<template>
  <select
    v-if="scopes.length > 1" :id="id" class="scope-select"
    :value="modelValue" :aria-label="$t('search.scope')"
    @change="emit('update:modelValue', $event.target.value)"
  >
    <option v-for="s in scopes" :key="s.key" :value="s.key">
      {{ $t(`search.scopes.${s.key}`) }}
    </option>
  </select>
</template>

<style scoped>
/* Sized to its longest label rather than stretched like a filter select: it
   qualifies the search box, and the box is what should take the row. */
.scope-select { flex: none; width: auto; max-width: 100%; }
</style>
