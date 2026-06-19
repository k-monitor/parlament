<script setup>
// Full-text sentence search (§5.1). Search state lives entirely in the URL query
// (SEA-6: deep-linkable / citable). Editing filters or paging updates the URL,
// and a watcher re-runs the query — so the back button and a pasted link both
// reproduce the exact result set.
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api.js'
import { store } from '../../store.js'
import { agendaLabel, formatDate } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import SpeakerLink from '../../components/SpeakerLink.vue'
import TimingBadge from '../../components/TimingBadge.vue'
import TrendChart from '../../components/TrendChart.vue'

const route = useRoute()
const router = useRouter()

const PAGE = 20
const AGENDA_TYPES = ['opening', 'procedural', 'regular', 'oath', 'voting',
  'rules_of_procedure', 'questioning_of_the_government', 'qa', 'condolence']

const input = ref(route.query.q || '')
const filters = reactive({
  period: route.query.period || '',
  date_from: route.query.date_from || '',
  date_to: route.query.date_to || '',
  faction_id: route.query.faction_id || '',
  agenda_type: route.query.agenda_type || '',
})
const showFilters = ref(false)

const data = ref(null)
const trend = ref(null)
const loading = ref(false)
const error = ref(false)

const periods = computed(() => (store.meta && store.meta.periods) || [])
const factions = ref([])
const page = computed(() => Math.floor((Number(route.query.offset) || 0) / PAGE))
const totalPages = computed(() =>
  data.value ? Math.ceil(Math.min(data.value.total, 1000) / PAGE) : 0)

onMounted(async () => {
  try { factions.value = (await api.factions()).factions } catch {}
  if (route.query.q) runFromRoute()
})

// Push the current form state into the URL (this triggers the watcher → fetch).
function submit(resetPage = true) {
  const query = { q: input.value.trim() }
  for (const k of ['period', 'date_from', 'date_to', 'faction_id', 'agenda_type']) {
    if (filters[k]) query[k] = filters[k]
  }
  if (!resetPage && route.query.offset) query.offset = route.query.offset
  router.push({ name: 'search', query })
}

function clearFilters() {
  filters.period = filters.date_from = filters.date_to = ''
  filters.faction_id = filters.agenda_type = ''
  submit()
}

function gotoPage(p) {
  router.push({ name: 'search', query: { ...route.query, offset: p * PAGE } })
}

async function runFromRoute() {
  if (!route.query.q) { data.value = null; trend.value = null; return }
  loading.value = true; error.value = false
  const filterArgs = {
    q: route.query.q,
    period: route.query.period,
    date_from: route.query.date_from,
    date_to: route.query.date_to,
    faction_id: route.query.faction_id,
    agenda_type: route.query.agenda_type,
  }
  // The over-time popularity chart (SEA-8) is an independent aggregate over the
  // whole result set (not just this page), so it runs alongside and its failure
  // must never break the results list.
  api.searchTrend(filterArgs).then((t) => { trend.value = t }).catch(() => { trend.value = null })
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
  filters.period = q.period || ''
  filters.date_from = q.date_from || ''
  filters.date_to = q.date_to || ''
  filters.faction_id = q.faction_id || ''
  filters.agenda_type = q.agenda_type || ''
  runFromRoute()
})

function viewerLink(r) {
  return { name: 'viewer', params: { uid: r.speech_uid }, query: { s: r.sentence_ord } }
}
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
          <label for="f-period">{{ $t('search.period') }}</label>
          <select id="f-period" v-model="filters.period" @change="submit()">
            <option value="">{{ $t('search.all') }}</option>
            <option v-for="p in periods" :key="p.number" :value="p.number">{{ p.label || p.number }}</option>
          </select>
        </div>
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
.trendcard { margin: 0 0 .9rem; }
.results { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .7rem; }
.result-sentence { display: block; font-size: 1.12rem; color: var(--ink); line-height: 1.5; }
.result-sentence:hover { text-decoration: none; color: var(--accent); }
.result-meta { margin-top: .6rem; gap: .8rem; row-gap: .4rem; }
.watch { margin-left: auto; }
.pager { display: flex; align-items: center; justify-content: center; gap: 1rem; margin: 1.5rem 0; }
</style>
