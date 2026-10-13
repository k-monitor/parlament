<script setup>
// The site search's scope picker (SEA-14) — what the box beside it searches
// in. Shared by the search page and the home page so both offer the same
// scopes, in the same order, under the same names; which of them are offered is
// the modules this deployment runs (lib/searchScopes.js).
//
// Several scopes can be ticked at once. It is the filters' checkbox dropdown
// (MultiSelect), whose empty selection means "all" — here "Mindenhol", every
// scope — so the model, which always names its scopes, is translated at the
// edge: every scope ticked reads as the "all" choice, and that choice comes back
// as every scope.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { availableScopes } from '../lib/searchScopes.js'
import MultiSelect from './MultiSelect.vue'

const props = defineProps({
  modelValue: { type: Array, required: true },
  id: { type: String, default: undefined },
})
const emit = defineEmits(['update:modelValue', 'change'])
const { t } = useI18n()

const scopes = computed(() => availableScopes())
const options = computed(() =>
  scopes.value.map((s) => ({ value: s.key, label: t(`search.scopes.${s.key}`) })))
const everywhere = computed(() =>
  scopes.value.every((s) => props.modelValue.includes(s.key)))

function pick(keys) {
  const next = keys.length ? keys : scopes.value.map((s) => s.key)
  emit('update:modelValue', next)
  emit('change', next)
}
</script>

<template>
  <MultiSelect
    v-if="scopes.length > 1" :id="id" class="scope-select"
    :model-value="everywhere ? [] : modelValue" :options="options"
    :label="$t('search.scope')" :all-label="$t('search.scopeAll')"
    :trigger-label="$t('search.scope')"
    @change="pick"
  />
</template>

<style scoped>
/* A fixed width rather than the filter grid's stretch: it qualifies the search
   box, which is what should take the row, and its label changes as scopes are
   ticked — sized to the longest one, the box beside it never jumps. */
.scope-select { flex: none; width: 11.5rem; max-width: 100%; }
</style>
