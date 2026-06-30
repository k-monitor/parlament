<script setup>
// Full-text sentence search (§5.1). Search state lives entirely in the URL query
// (SEA-6: deep-linkable / citable). Editing filters or paging updates the URL,
// and a watcher re-runs the query — so the back button and a pasted link both
// reproduce the exact result set.
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api.js'
import { store, loadMeta, setCycle, currentCycleLabel } from '../../store.js'
import { agendaLabel, formatDate } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import SpeakerLink from '../../components/SpeakerLink.vue'
import TimingBadge from '../../components/TimingBadge.vue'
import TrendChart from '../../components/TrendChart.vue'
import BarChart from '../../components/BarChart.vue'

const route = useRoute()
const router = useRouter()

const PAGE = 20
const AGENDA_TYPES = ['opening', 'procedural', 'regular', 'oath', 'voting',
  'rules_of_procedure', 'questioning_of_the_government', 'qa', 'condolence']

const input = ref(route.query.q || '')
// The electoral cycle is a *global* scope set from the header (store.cycle), not
// a per-search filter — so it is no longer part of the URL query here.
const filters = reactive({
  date_from: route.query.date_from || '',
  date_to: route.query.date_to || '',
  faction_id: route.query.faction_id || '',
  agenda_type: route.query.agenda_type || '',
})
const showFilters = ref(false)

const data = ref(null)
const trend = ref(null)
const breakdown = ref(null)
const loading = ref(false)
const error = ref(false)

const factions = ref([])
const page = computed(() => Math.floor((Number(route.query.offset) || 0) / PAGE))
const totalPages = computed(() =>
  data.value ? Math.ceil(Math.min(data.value.total, 1000) / PAGE) : 0)

onMounted(async () => {
  // Ensure the global cycle is initialised before the first search so results
  // are scoped to the selected cycle (not briefly to "all").
  await loadMeta().catch(() => {})
  try { factions.value = (await api.factions()).factions } catch {}
  if (route.query.q) runFromRoute()
})

// Re-run the active search when the global cycle changes.
watch(() => store.cycle, () => { if (route.query.q) runFromRoute() })

// Push the current form state into the URL (this triggers the watcher → fetch).
function submit(resetPage = true) {
  const query = { q: input.value.trim() }
  for (const k of ['date_from', 'date_to', 'faction_id', 'agenda_type']) {
    if (filters[k]) query[k] = filters[k]
  }
  if (!resetPage && route.query.offset) query.offset = route.query.offset
  router.push({ name: 'search', query })
}

function clearFilters() {
  filters.date_from = filters.date_to = ''
  filters.faction_id = filters.agenda_type = ''
  submit()
}

function gotoPage(p) {
  router.push({ name: 'search', query: { ...route.query, offset: p * PAGE } })
}

async function runFromRoute() {
  if (!route.query.q) { data.value = null; trend.value = null; breakdown.value = null; return }
  loading.value = true; error.value = false
  const filterArgs = {
    q: route.query.q,
    period: store.cycle, // global cycle scope (null = all)
    date_from: route.query.date_from,
    date_to: route.query.date_to,
    faction_id: route.query.faction_id,
    agenda_type: route.query.agenda_type,
  }
  // The over-time popularity chart (SEA-8) and the who-said-it breakdown (SEA-9)
  // are independent aggregates over the whole result set (not just this page),
  // so they run alongside and their failure must never break the results list.
  api.searchTrend(filterArgs).then((t) => { trend.value = t }).catch(() => { trend.value = null })
  api.searchBreakdown(filterArgs).then((b) => { breakdown.value = b }).catch(() => { breakdown.value = null })
  try {
    data.value = await api.search({
      ...filterArgs,
      limit: PAGE,
      offset: route.query.offset || 0,
    })
  } catch (e) { error.value = true } finally { loading.value = false }
}

// Keep the form synced when navigating via back/forward, and re-run on any change.
watch(() => route.query, (q) => {
  input.value = q.q || ''
  filters.date_from = q.date_from || ''
  filters.date_to = q.date_to || ''
  filters.faction_id = q.faction_id || ''
  filters.agenda_type = q.agenda_type || ''
  runFromRoute()
})

function viewerLink(r) {
  return { name: 'viewer', params: { uid: r.speech_uid }, query: { s: r.sentence_ord } }
}

// SEA-9 breakdown → BarChart rows. Factions keep their colour; each
// representative links to their profile (REP-1).
const factionItems = computed(() =>
  (breakdown.value?.factions || []).map((f) => ({
    label: f.label, value: f.hits, color: f.color,
  })))
const speakerItems = computed(() =>
  (breakdown.value?.speakers || []).map((s) => ({
    label: s.label, value: s.hits,
    to: { name: 'profile', params: { id: s.person_id } },
  })))
const hasBreakdown = computed(() =>
  factionItems.value.length > 0 || speakerItems.value.length > 0)

// When a specific cycle is selected globally, the search is scoped to it; offer a
// one-click switch back to "all cycles" (null) which re-runs the active search.
const cycleScopeLabel = computed(() => currentCycleLabel())
</script>

<template>
  <h1>{{ $t('search.title') }}</h1>

  <form role="search" class="card pad searchform" @submit.prevent="submit()">
    <div class="row" style="gap:.5rem;">
      <input
        v-model="input" type="search" :placeholder="$t('search.placeholder')"
        :aria-label="$t('search.title')" style="flex:1;min-width:200px;"
      />
      <button class="btn" type="submit">{{ $t('search.button') }}</button>
      <button class="btn secondary" type="button" :aria-expanded="showFilters" @click="showFilters = !showFilters">
        {{ $t('search.filters') }}
      </button>
    </div>
    <p class="small muted" style="margin:.4rem 0 0;">{{ $t('search.hint') }}</p>

    <fieldset v-show="showFilters" class="filters">
      <legend class="visually-hidden">{{ $t('search.filters') }}</legend>
      <div class="filter-grid">
        <div>
          <label for="f-faction">{{ $t('search.faction') }}</label>
          <select id="f-faction" v-model="filters.faction_id" @change="submit()">
            <option value="">{{ $t('search.all') }}</option>
            <option v-for="f in factions" :key="f.id" :value="f.id">{{ f.label }}</option>
          </select>
        </div>
        <div>
          <label for="f-agenda">{{ $t('search.agendaType') }}</label>
          <select id="f-agenda" v-model="filters.agenda_type" @change="submit()">
            <option value="">{{ $t('search.all') }}</option>
            <option v-for="t in AGENDA_TYPES" :key="t" :value="t">{{ agendaLabel(t) }}</option>
          </select>
        </div>
        <div>
          <label for="f-from">{{ $t('search.dateFrom') }}</label>
          <input id="f-from" type="date" v-model="filters.date_from" @change="submit()" />
        </div>
        <div>
          <label for="f-to">{{ $t('search.dateTo') }}</label>
          <input id="f-to" type="date" v-model="filters.date_to" @change="submit()" />
        </div>
      </div>
      <button class="btn secondary small" type="button" style="margin-top:.6rem;" @click="clearFilters">
        {{ $t('search.clearFilters') }}
      </button>
    </fieldset>
  </form>

  <p v-if="cycleScopeLabel" class="muted small cyclenotice">
    {{ $t('search.cycleScope', { cycle: cycleScopeLabel }) }}
    <a href="#" @click.prevent="setCycle(null)">{{ $t('search.cycleScopeAll') }}</a>
  </p>

  <StateBlock
    :loading="loading" :error="error"
    :empty="!!data && data.results.length === 0"
    :empty-text="$t('search.noResults')"
    @retry="runFromRoute"
  >
    <div v-if="data">
      <p class="muted small" aria-live="polite" style="margin:1rem 0 .5rem;">
        <strong>{{ data.total_is_capped ? $t('search.resultsCapped', { n: data.total }) : data.total.toLocaleString('hu-HU') }}</strong>
        {{ $t('search.results') }}
      </p>

      <section v-if="trend && trend.buckets.length > 1" class="card pad trendcard">
        <TrendChart
          :buckets="trend.buckets" :granularity="trend.granularity"
          :caption="$t('search.trendCaption', { q: data.query })"
          :unit="$t('search.results')"
        />
      </section>

      <section v-if="hasBreakdown" class="card pad breakdowncard">
        <div class="breakdown-grid">
          <BarChart
            v-if="factionItems.length"
            :items="factionItems"
            :caption="$t('search.breakdownFactions', { q: data.query })"
            :unit="$t('search.results')"
            :value-format="(v) => v.toLocaleString('hu-HU')"
          />
          <BarChart
            v-if="speakerItems.length"
            :items="speakerItems"
            :caption="$t('search.breakdownSpeakers', { q: data.query })"
            :unit="$t('search.results')"
            :value-format="(v) => v.toLocaleString('hu-HU')"
          />
        </div>
      </section>

      <ol class="results">
        <li v-for="r in data.results" :key="r.sentence_id" class="card pad result">
          <router-link :to="viewerLink(r)" class="result-sentence">
            <span v-html="r.highlighted"></span>
          </router-link>
          <div class="result-meta row">
            <SpeakerLink v-if="r.speaker" :speaker="r.speaker" />
            <FactionBadge :faction="r.faction" />
            <span class="muted small">{{ formatDate(r.date) }}</span>
            <span class="muted small" v-if="r.agenda_title">{{ $t('search.on') }} {{ r.agenda_title }}</span>
            <TimingBadge :timing="r.timing" />
            <router-link :to="viewerLink(r)" class="btn secondary small watch">▶ {{ $t('search.watch') }}</router-link>
          </div>
        </li>
      </ol>

      <nav v-if="totalPages > 1" class="pager" :aria-label="$t('search.results')">
        <button class="btn secondary" :disabled="page === 0" @click="gotoPage(page - 1)">‹</button>
        <span class="muted small">{{ page + 1 }} / {{ totalPages }}</span>
        <button class="btn secondary" :disabled="page + 1 >= totalPages" @click="gotoPage(page + 1)">›</button>
      </nav>
    </div>
  </StateBlock>
</template>

<style scoped>
.filters { border: none; border-top: 1px solid var(--line); margin-top: .8rem; padding: .8rem 0 0; }
.filter-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: .7rem; }
.cyclenotice { margin: .6rem 0 0; }
.trendcard { margin: 0 0 .9rem; }
.breakdowncard { margin: 0 0 .9rem; }
.breakdown-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1.2rem; }
.results { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .7rem; }
.result-sentence { display: block; font-size: 1.12rem; color: var(--ink); line-height: 1.5; }
.result-sentence:hover { text-decoration: none; color: var(--accent); }
.result-meta { margin-top: .6rem; gap: .8rem; row-gap: .4rem; }
.watch { margin-left: auto; }
.pager { display: flex; align-items: center; justify-content: center; gap: 1rem; margin: 1.5rem 0; }
</style>
