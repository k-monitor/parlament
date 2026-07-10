<script setup>
// Dependency-free SVG bar chart. `items` = [{ label, value, color?, sub?, to? }];
// when an item carries `to` (a vue-router location) its label renders as a link.
import { computed } from 'vue'
const props = defineProps({
  items: { type: Array, default: () => [] },
  unit: { type: String, default: '' },
  caption: { type: String, default: '' },
  // When false the caption still labels the chart for assistive tech but isn't
  // shown as a visible figcaption (the parent provides its own heading).
  showCaption: { type: Boolean, default: true },
  valueFormat: { type: Function, default: (v) => String(v) },
})
const max = computed(() => Math.max(1, ...props.items.map((i) => i.value || 0)))
</script>

<template>
  <figure style="margin:0;">
    <figcaption v-if="caption && showCaption" class="small soft" style="margin-bottom:.4rem;">{{ caption }}</figcaption>
    <div class="bars" role="img" :aria-label="caption">
      <div v-for="(it, i) in items" :key="i" class="bar-row">
        <router-link v-if="it.to" :to="it.to" class="bar-label" :title="it.label">{{ it.label }}</router-link>
        <span v-else class="bar-label" :title="it.label">{{ it.label }}</span>
        <span class="bar-track">
          <span class="bar-fill" :style="{ width: ((it.value / max) * 100) + '%', background: it.color || 'var(--accent)' }"></span>
        </span>
        <span class="bar-val">{{ valueFormat(it.value) }}</span>
      </div>
    </div>
  </figure>
</template>

<style scoped>
.bars { display: flex; flex-direction: column; gap: .35rem; }
.bar-row { display: grid; grid-template-columns: minmax(80px, 28%) 1fr auto; gap: .6rem; align-items: center; }
.bar-label { font-size: .82rem; color: var(--ink-soft); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
a.bar-label { text-decoration: none; }
a.bar-label:hover, a.bar-label:focus-visible { color: var(--accent); text-decoration: underline; }
.bar-track { background: #eceae4; border-radius: 6px; height: 14px; overflow: hidden; }
.bar-fill { display: block; height: 100%; border-radius: 6px; min-width: 2px; }
.bar-val { font-size: .82rem; font-variant-numeric: tabular-nums; color: var(--ink); }
</style>
