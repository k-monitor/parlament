<script setup>
// Frakcióelemzés (VOTE-8): the party-voting analysis, moved out of the vote list
// into its own sub-tab of the Votes section. It reads the /votes/cohesion
// aggregate — computed house-wide over the whole cycle's roll-call set — and
// hands it to CohesionPanel, which shows the same numbers three ways (agreement
// matrix / bars / bloc map). Honours the global cycle chooser (§4A); unlike the
// old in-list panel it carries no per-page filters (it's the whole-cycle view).
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { store, loadMeta, currentCycleLabel } from '../../store.js'
import StateBlock from '../../components/StateBlock.vue'
import HelpTip from '../../components/HelpTip.vue'
import EmbedButton from '../../components/EmbedButton.vue'
import CohesionPanel, { COHESION_TABS } from './CohesionPanel.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

// The selected view lives in `?tab=` so it is deep-linkable, survives reload and
// back/forward, and can be baked into the embed snippet. The matrix is the
// default and stays out of the URL.
const tab = computed(() => {
  const v = route.query.tab
  return COHESION_TABS.includes(v) ? v : 'matrix'
})
function setTab(tb) {
  if (tb === tab.value) return
  router.replace({ query: { ...route.query, tab: tb === 'matrix' ? undefined : tb } })
}

const data = ref(null)
const loading = ref(false)
const error = ref(false)

const scopeLabel = computed(() => {
  const c = currentCycleLabel()
  return c ? t('cycle.scope', { cycle: c }) : t('cycle.scopeAll')
})

// At least two factions are needed to compare; below that the panel has nothing
// to show, so surface the empty state at page level (same guard as the API).
const ready = computed(() => !!data.value && data.value.factions.length >= 2)

// Monotonic load id: rapid cycle switches can leave fetches racing; only the
// latest may write state.
let loadSeq = 0

async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false
  try {
    const res = await api.voteCohesion({ period: store.cycles })
    if (seq === loadSeq) data.value = res
  } catch {
    if (seq === loadSeq) error.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

onMounted(() => { loadMeta().catch(() => {}).finally(load) })
watch(() => store.cycles.join(','), load)
</script>

<template>
  <div class="sechead">
    <h1>{{ $t('votes.cohesion.title') }}</h1>
    <HelpTip :label="$t('votes.cohesion.title')">
      <p>{{ $t('votes.cohesion.methodology') }}</p>
    </HelpTip>
  </div>
  <p class="muted">{{ $t('votes.cohesion.subtitle') }}</p>

  <StateBlock
    :loading="loading" :error="error"
    :empty="!!data && !ready" :empty-text="$t('votes.cohesion.empty')"
    @retry="load"
  >
    <CohesionPanel
      v-if="ready" :data="data" :scope-label="scopeLabel" hide-header
      :tab="tab" @update:tab="setTab"
    >
      <template #corner>
        <EmbedButton
          kind="faction-cohesion" :title="$t('votes.cohesion.title')"
          :params="{ tab: tab === 'matrix' ? '' : tab }"
          :height="580" :max-width="900"
        />
      </template>
    </CohesionPanel>
  </StateBlock>
</template>

<style scoped>
.sechead { display: flex; align-items: center; gap: .35rem; }
.sechead h1 { margin: 0; }
</style>
