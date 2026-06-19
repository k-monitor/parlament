<script setup>
// Dependency-free SVG-free column chart for a value over time (SEA-8), with the
// same built-in accessible table equivalent as BarChart (REP-6 / A11Y-1).
// `buckets` = [{ period, hits }] (period = 'YYYY' or 'YYYY-MM'); gaps between the
// first and last period are filled with zeros so the timeline is continuous.
import { ref, computed } from 'vue'
const props = defineProps({
  buckets: { type: Array, default: () => [] },
  granularity: { type: String, default: 'month' }, // 'month' | 'year'
  caption: { type: String, default: '' },
  unit: { type: String, default: '' },
})
const showTable = ref(false)

const MONTHS = ['', 'jan', 'feb', 'márc', 'ápr', 'máj', 'jún',
  'júl', 'aug', 'szept', 'okt', 'nov', 'dec']

// Fill the zero-gap periods so a quiet month reads as zero, not as missing.
const filled = computed(() => {
  const src = props.buckets
  if (!src.length) return []
  const byPeriod = new Map(src.map((b) => [b.period, b.hits]))
  const out = []
  if (props.granularity === 'year') {
    const lo = +src[0].period, hi = +src[src.length - 1].period
    for (let y = lo; y <= hi; y++) {
      out.push({ period: String(y), label: String(y), hits: byPeriod.get(String(y)) || 0 })
    }
  } else {
    const [ly, lm] = src[0].period.split('-').map(Number)
    const [hy, hm] = src[src.length - 1].period.split('-').map(Number)
    for (let y = ly, m = lm; y < hy || (y === hy && m <= hm);) {
      const key = `${y}-${String(m).padStart(2, '0')}`
      out.push({ period: key, label: `${MONTHS[m]} ${y}`, hits: byPeriod.get(key) || 0 })
      if (++m > 12) { m = 1; y++ }
    }
  }
  return out
})

const max = computed(() => Math.max(1, ...filled.value.map((b) => b.hits)))
const total = computed(() => filled.value.reduce((s, b) => s + b.hits, 0))
// Keep axis ticks sparse so many monthly columns stay legible.
const tickEvery = computed(() => Math.ceil(filled.value.length / 12) || 1)
</script>

<template>
  <figure v-if="filled.length" class="trend" style="margin:0;">
    <figcaption v-if="caption" class="small soft" style="margin-bottom:.5rem;">{{ caption }}</figcaption>
    <div class="cols" role="img" :aria-label="caption">
      <div v-for="(b, i) in filled" :key="b.period" class="col"
           :title="`${b.label}: ${b.hits} ${unit}`">
        <span class="col-track">
          <span class="col-fill" :style="{ height: Math.max(2, (b.hits / max) * 100) + '%' }"></span>
        </span>
        <span class="col-tick" :class="{ on: i % tickEvery === 0 }">{{ b.label }}</span>
      </div>
    </div>
    <button class="btn secondary small" style="margin-top:.6rem;padding:.3rem .7rem;" @click="showTable = !showTable">
      {{ showTable ? $t('profile.hideTable') : $t('profile.showTable') }}
    </button>
    <table v-if="showTable" class="data" style="margin-top:.6rem;">
      <caption class="visually-hidden">{{ caption }}</caption>
      <thead><tr><th scope="col">#</th><th scope="col">{{ unit }}</th></tr></thead>
      <tbody>
        <tr v-for="b in filled" :key="b.period"><th scope="row">{{ b.label }}</th><td>{{ b.hits }}</td></tr>
      </tbody>
    </table>
  </figure>
</template>

<style scoped>
.cols { display: flex; align-items: flex-end; gap: 3px; height: 120px; overflow-x: auto; padding-bottom: 1.3rem; }
.col { flex: 1 1 0; min-width: 6px; display: flex; flex-direction: column; align-items: center; height: 100%; }
.col-track { flex: 1; width: 100%; display: flex; align-items: flex-end; }
.col-fill { display: block; width: 100%; background: var(--accent); border-radius: 3px 3px 0 0; transition: height .2s; }
.col:hover .col-fill { background: var(--ink); }
.col-tick { position: relative; top: .25rem; font-size: .62rem; color: var(--ink-soft);
  white-space: nowrap; visibility: hidden; transform: rotate(0deg); }
.col-tick.on { visibility: visible; }
</style>
