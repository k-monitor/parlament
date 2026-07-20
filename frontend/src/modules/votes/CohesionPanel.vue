<script setup>
// The dynamic vote-analysis panel that sits between the votes filter and the
// list (VOTE-8: party cohesion / defection). It reads the /votes/cohesion
// aggregate — computed over the whole *filtered* roll-call set (cf. the search
// trend/breakdown, SEA-8/9) — and shows the same numbers three ways, switchable
// by a tab: an agreement matrix, cohesion/alignment bars, and a bloc map. All
// three are dependency-free SVG (or reuse BarChart), screen-reader-labelled, and
// carry a methodology note (A11Y-1 / TRUST-1).
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import BarChart from '../../components/BarChart.vue'
import AgreementMatrix from '../../components/AgreementMatrix.vue'
import FactionMap from '../../components/FactionMap.vue'
import HelpTip from '../../components/HelpTip.vue'
import { classicalMDS } from './mds.js'

const props = defineProps({
  data: { type: Object, default: null },   // { vote_count, factions, matrix }
  scopeLabel: { type: String, default: '' },
  // When the host page already renders the title + methodology (its own <h1>),
  // suppress the panel's internal header so it isn't shown twice.
  hideHeader: { type: Boolean, default: false },
})
const { t } = useI18n()

const TABS = ['matrix', 'bars', 'map']
const tab = ref('matrix')

const factions = computed(() => props.data?.factions || [])
const matrix = computed(() => props.data?.matrix || [])
const ready = computed(() => factions.value.length >= 2)

const pctVal = (v) => (v == null ? null : Math.round(v * 100))
const fmtPct = (v) => (v == null ? '—' : v + '%')

// Bars — internal cohesion, ranked most-united first.
const cohesionBars = computed(() => factions.value
  .map((f) => ({ label: f.name, value: pctVal(f.cohesion), color: f.color }))
  .filter((b) => b.value != null)
  .sort((a, b) => b.value - a.value))

// Bars — how often each other faction votes with the largest (governing) one.
const reference = computed(() => factions.value[0] || null)
const alignBars = computed(() => {
  const row = matrix.value[0] || []
  return factions.value
    .map((f, i) => ({ label: f.name, value: i === 0 ? null : pctVal(row[i]), color: f.color }))
    .filter((b) => b.value != null)
    .sort((a, b) => b.value - a.value)
})

// Map — MDS layout from distance = 1 − agreement (null ⇒ far apart).
const mapPoints = computed(() => {
  const m = matrix.value
  const dist = m.map((r, i) => r.map((v, j) => (i === j ? 0 : (v == null ? 1 : 1 - v))))
  const coords = classicalMDS(dist)
  return factions.value.map((f, i) => ({
    name: f.name, color: f.color, size: f.size, cohesion: f.cohesion,
    x: coords[i] ? coords[i][0] : 0, y: coords[i] ? coords[i][1] : 0,
  }))
})
</script>

<template>
  <section v-if="data" class="card pad cohesion">
    <div v-if="!hideHeader" class="chead">
      <h2>{{ $t('votes.cohesion.title') }}</h2>
      <HelpTip :label="$t('votes.cohesion.title')">
        <p>{{ $t('votes.cohesion.methodology') }}</p>
      </HelpTip>
    </div>
    <p class="muted small basis">
      {{ $t('votes.cohesion.basis', { n: data.vote_count }) }}
      <template v-if="scopeLabel"> · {{ scopeLabel }}</template>
    </p>

    <p v-if="!ready" class="muted small">{{ $t('votes.cohesion.empty') }}</p>

    <template v-else>
      <div class="tabs" role="tablist" :aria-label="$t('votes.cohesion.title')">
        <button
          v-for="tb in TABS" :key="tb" type="button" role="tab"
          class="tab" :class="{ active: tab === tb }" :aria-selected="tab === tb"
          @click="tab = tb"
        >{{ $t('votes.cohesion.tab_' + tb) }}</button>
      </div>

      <!-- Matrix -->
      <div v-show="tab === 'matrix'" class="pane" role="tabpanel">
        <p class="muted small hint">{{ $t('votes.cohesion.matrixCaption') }}</p>
        <AgreementMatrix
          :factions="factions" :matrix="matrix"
          :caption="$t('votes.cohesion.matrixCaption')"
          :low-label="$t('votes.cohesion.low')" :high-label="$t('votes.cohesion.high')"
        />
      </div>

      <!-- Bars -->
      <div v-show="tab === 'bars'" class="pane" role="tabpanel">
        <h3 class="barhead">{{ $t('votes.cohesion.cohesionBars') }}</h3>
        <BarChart :items="cohesionBars" :value-format="fmtPct" :show-caption="false"
                  :caption="$t('votes.cohesion.cohesionBars')" />
        <template v-if="alignBars.length && reference">
          <h3 class="barhead">{{ $t('votes.cohesion.agreeWith', { faction: reference.name }) }}</h3>
          <BarChart :items="alignBars" :value-format="fmtPct" :show-caption="false"
                    :caption="$t('votes.cohesion.agreeWith', { faction: reference.name })" />
        </template>
      </div>

      <!-- Map -->
      <div v-show="tab === 'map'" class="pane" role="tabpanel">
        <p class="muted small hint">{{ $t('votes.cohesion.mapCaption') }} {{ $t('votes.cohesion.mapHint') }}</p>
        <FactionMap
          :points="mapPoints" :caption="$t('votes.cohesion.mapCaption')"
          :size-label="$t('votes.cohesion.size')" :cohesion-label="$t('votes.cohesion.cohesion')"
        />
      </div>
    </template>

    <!-- Optional bottom-right footer control (e.g. the EmbedButton). -->
    <div v-if="$slots.corner" class="fig-foot">
      <slot name="corner" />
    </div>
  </section>
</template>

<style scoped>
.cohesion { margin: 1rem 0; }
.chead { display: flex; align-items: center; gap: .35rem; }
.chead h2 { margin: 0; font-size: 1.15rem; }
.basis { margin: .2rem 0 .6rem; }
.tabs { display: inline-flex; gap: .2rem; padding: .2rem; background: var(--bg); border: 1px solid var(--line); border-radius: 999px; margin-bottom: .3rem; flex-wrap: wrap; }
.tab { border: 0; background: none; color: var(--ink-soft); font-weight: 600; font-size: .85rem; padding: .35rem .8rem; border-radius: 999px; cursor: pointer; }
.tab:hover { color: var(--ink); }
.tab.active { background: var(--surface); color: var(--accent); box-shadow: var(--shadow); }
.pane { margin-top: .8rem; }
.hint { margin: 0 0 .6rem; }
.barhead { font-size: .92rem; margin: 1rem 0 .5rem; color: var(--ink); }
.barhead:first-child { margin-top: .2rem; }
</style>
