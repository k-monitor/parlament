<script setup>
// Dependency-free word cloud (WCLOUD-1/3): words sized by frequency, with the
// same built-in accessible table equivalent as the other charts (REP-6 / A11Y-1).
// `words` = [{ text, count, weight }] ordered by weight desc. `weight` is the
// sizing metric (a TF·IDF "distinctiveness" score); `count` is the raw
// occurrences shown to the user. Each word emits `pick` so the parent can
// deep-link it into search scoped to the sitting day (WCLOUD-4).
import { ref, computed } from 'vue'
const props = defineProps({
  words: { type: Array, default: () => [] },
  caption: { type: String, default: '' },
})
defineEmits(['pick'])
const showTable = ref(false)

// Size each word between a min/max font on a sqrt scale of its weight, so a very
// dominant word does not dwarf the rest. Sizing is decorative — the count is
// always available in the title and the table (A11Y: not the only signal).
const MIN_REM = 0.8
const MAX_REM = 2.6
const wt = (w) => (w.weight != null ? w.weight : w.count)
const max = computed(() => Math.max(1e-6, ...props.words.map(wt)))
const min = computed(() => Math.min(...props.words.map(wt), max.value))

function sizeRem(w) {
  const v = wt(w)
  if (max.value === min.value) return (MIN_REM + MAX_REM) / 2
  const t = (Math.sqrt(v) - Math.sqrt(min.value)) /
            (Math.sqrt(max.value) - Math.sqrt(min.value))
  return MIN_REM + t * (MAX_REM - MIN_REM)
}
function fontWeight(w) {
  return 400 + Math.round(sizeRem(w) / MAX_REM * 3) * 100 // 400..700
}
// More distinctive words a touch darker so the cloud reads as a heat map too,
// not by size alone.
function opacity(w) {
  return 0.55 + 0.45 * (sizeRem(w) - MIN_REM) / (MAX_REM - MIN_REM)
}
</script>

<template>
  <figure v-if="words.length" class="wc" style="margin:0;">
    <figcaption v-if="caption" class="small soft" style="margin-bottom:.5rem;">{{ caption }}</figcaption>
    <div class="cloud" role="img" :aria-label="caption">
      <button
        v-for="w in words" :key="w.text" type="button" class="word"
        :style="{ fontSize: sizeRem(w) + 'rem', fontWeight: fontWeight(w), opacity: opacity(w) }"
        :title="`${w.text}: ${w.count}`"
        @click="$emit('pick', w.text)"
      >{{ w.text }}</button>
    </div>
    <button class="btn secondary small" style="margin-top:.6rem;padding:.3rem .7rem;" @click="showTable = !showTable">
      {{ showTable ? $t('profile.hideTable') : $t('profile.showTable') }}
    </button>
    <table v-if="showTable" class="data" style="margin-top:.6rem;">
      <caption class="visually-hidden">{{ caption }}</caption>
      <thead><tr><th scope="col">#</th><th scope="col">{{ $t('sessions.wordcloudCount') }}</th></tr></thead>
      <tbody>
        <tr v-for="w in words" :key="w.text"><th scope="row">{{ w.text }}</th><td>{{ w.count }}</td></tr>
      </tbody>
    </table>
  </figure>
</template>

<style scoped>
.cloud { display: flex; flex-wrap: wrap; gap: .15rem .6rem; align-items: baseline; line-height: 1.5; }
.word {
  background: none; border: 0; padding: 0 .1rem; cursor: pointer; color: var(--accent);
  font-family: inherit; line-height: 1; border-radius: 4px;
}
.word:hover, .word:focus-visible { background: var(--accent-soft); color: var(--ink); outline: none; }
</style>
