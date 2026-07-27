<script setup>
// Faction-level aggregate statistics (REP-4) with consistent colours and an
// accessible chart + table.
import { ref, computed, onMounted, watch } from 'vue'
import { api } from '../../api.js'
import { store, loadMeta } from '../../store.js'
import { formatSpeakingTime } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import BarChart from '../../components/BarChart.vue'
import EmbedButton from '../../components/EmbedButton.vue'

const data = ref(null)
const loading = ref(false)
const error = ref(false)

// Monotonic load id: rapid cycle switches can leave fetches racing; only the
// latest may write state.
let loadSeq = 0

async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false
  // Scoped to the global cycle chooser (store.cycle; null = all cycles).
  try {
    const res = await api.factions(store.cycle)
    if (seq === loadSeq) data.value = res
  } catch {
    if (seq === loadSeq) error.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}
onMounted(() => { loadMeta().catch(() => {}).finally(load) })
// Re-fetch when the global cycle changes.
watch(() => store.cycle, load)

const chartItems = computed(() =>
  (data.value ? data.value.factions : [])
    .filter((f) => f.speaking_seconds > 0)
    .map((f) => ({ label: f.label, value: Math.round(f.speaking_seconds / 60), color: f.color })))
</script>

<template>
  <h1>{{ $t('factions.title') }}</h1>
  <p class="muted">{{ $t('factions.subtitle') }}</p>

  <StateBlock :loading="loading" :error="error" @retry="load">
    <div v-if="data">
      <section class="card pad" style="margin-bottom:1.2rem;">
        <h2 class="barhead">{{ $t('factions.speakingTime') + ' (perc)' }}</h2>
        <BarChart
          :items="chartItems"
          :caption="$t('factions.speakingTime') + ' (perc)'"
          :show-caption="false"
          unit="perc"
          :value-format="(v) => v + ' p'"
        />
        <div class="fig-foot">
          <EmbedButton
            kind="faction-speaking" :title="$t('factions.speakingTime')"
            :height="360"
          />
        </div>
      </section>

      <div class="grid fgrid">
        <article v-for="f in data.factions.filter(x => x.mp_count > 0)" :key="f.id" class="card pad fcard">
          <div class="fbar" :style="{ background: f.color }" aria-hidden="true"></div>
          <h2>{{ f.label }}</h2>
          <dl class="fstats">
            <div><dt>{{ $t('factions.members') }}</dt><dd>{{ f.mp_count }}</dd></div>
            <div><dt>{{ $t('factions.speeches') }}</dt><dd>{{ f.speech_count }}</dd></div>
            <div><dt>{{ $t('factions.speakingTime') }}</dt><dd>{{ formatSpeakingTime(f.speaking_seconds) }}</dd></div>
            <div><dt>{{ $t('factions.avgPerMp') }}</dt><dd>{{ formatSpeakingTime(f.avg_speaking_seconds) }}</dd></div>
          </dl>
          <router-link :to="{ name: 'representatives', query: { faction_id: f.id } }" class="btn secondary small">
            {{ $t('factions.membersLink') }} →
          </router-link>
        </article>
      </div>

      <details class="methodology" style="margin-top:1rem;">
        <summary>{{ $t('factions.methodology') }}</summary>
        <p class="small muted">{{ data.methodology }}</p>
      </details>
    </div>
  </StateBlock>
</template>

<style scoped>
.barhead { font-size: .92rem; margin: 0 0 .5rem; color: var(--ink); }
.fgrid { grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); }
.fcard { position: relative; overflow: hidden; }
.fbar { position: absolute; top: 0; left: 0; right: 0; height: 5px; }
.fcard h2 { margin-top: .4rem; }
.fstats { display: grid; grid-template-columns: 1fr 1fr; gap: .5rem; margin: .6rem 0 1rem; }
.fstats div { margin: 0; }
.fstats dt { font-size: .75rem; color: var(--ink-faint); }
.fstats dd { margin: 0; font-weight: 700; font-size: 1.05rem; }
.methodology summary { cursor: pointer; font-weight: 600; color: var(--ink-soft); font-size: .85rem; }
</style>
