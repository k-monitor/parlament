<script setup>
// Dependency-free SVG bar chart with a built-in accessible table equivalent
// (REP-6 / A11Y-1). `items` = [{ label, value, color?, sub? }].
import { ref, computed } from 'vue'
const props = defineProps({
  items: { type: Array, default: () => [] },
  unit: { type: String, default: '' },
  caption: { type: String, default: '' },
  valueFormat: { type: Function, default: (v) => String(v) },
})
const showTable = ref(false)
const max = computed(() => Math.max(1, ...props.items.map((i) => i.value || 0)))
</script>

<template>
  <figure style="margin:0;">
    <figcaption v-if="caption" class="small soft" style="margin-bottom:.4rem;">{{ caption }}</figcaption>
    <div class="bars" role="img" :aria-label="caption">
      <div v-for="(it, i) in items" :key="i" class="bar-row">
        <span class="bar-label" :title="it.label">{{ it.label }}</span>
        <span class="bar-track">
          <span class="bar-fill" :style="{ width: ((it.value / max) * 100) + '%', background: it.color || 'var(--accent)' }"></span>
        </span>
        <span class="bar-val">{{ valueFormat(it.value) }}</span>
      </div>
    </div>
    <button class="btn secondary small" style="margin-top:.6rem;padding:.3rem .7rem;" @click="showTable = !showTable">
      {{ showTable ? $t('profile.hideTable') : $t('profile.showTable') }}
    </button>
    <table v-if="showTable" class="data" style="margin-top:.6rem;">
      <caption class="visually-hidden">{{ caption }}</caption>
      <thead><tr><th scope="col">#</th><th scope="col">{{ unit }}</th></tr></thead>
      <tbody>
        <tr v-for="(it, i) in items" :key="i"><th scope="row">{{ it.label }}</th><td>{{ valueFormat(it.value) }}</td></tr>
      </tbody>
    </table>
  </figure>
</template>

<style scoped>
.bars { display: flex; flex-direction: column; gap: .35rem; }
.bar-row { display: grid; grid-template-columns: minmax(80px, 28%) 1fr auto; gap: .6rem; align-items: center; }
.bar-label { font-size: .82rem; color: var(--ink-soft); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bar-track { background: #eceae4; border-radius: 6px; height: 14px; overflow: hidden; }
.bar-fill { display: block; height: 100%; border-radius: 6px; min-width: 2px; }
.bar-val { font-size: .82rem; font-variant-numeric: tabular-nums; color: var(--ink); }
</style>
