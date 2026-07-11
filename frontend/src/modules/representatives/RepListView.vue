<script setup>
// Browsable, filterable representative list (REP-1). Filter/sort state is in the
// URL so a filtered list is shareable.
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { store, loadMeta, currentCycleLabel } from '../../store.js'
import { formatSpeakingTime } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import SpeakerLink from '../../components/SpeakerLink.vue'
import Pagination from '../../components/Pagination.vue'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const PAGE = 60

// Explicit scope label so it's clear the list (and its stats) cover only the
// globally selected cycle, not all cycles.
const scopeText = computed(() => {
  const c = currentCycleLabel()
  return c ? t('cycle.scope', { cycle: c }) : t('cycle.scopeAll')
})

const data = ref(null)
const loading = ref(false)
const error = ref(false)
const factions = ref([])

const page = computed(() => Math.floor((Number(route.query.offset) || 0) / PAGE))
const totalPages = computed(() => (data.value ? Math.ceil(data.value.total / PAGE) : 0))

const f = reactive({
  q: route.query.q || '',
  faction_id: route.query.faction_id || '',
  sort: route.query.sort || 'speaking_time',
})
// Open the filter panel on load when a filter is already active (e.g. a shared
// or deep-linked list), so its filters are visible rather than hidden.
const showFilters = ref(!!route.query.faction_id)

function clearFilters() {
  f.faction_id = ''
  apply()
}

function apply() {
  const query = {}
  if (f.q) query.q = f.q
  if (f.faction_id) query.faction_id = f.faction_id
  if (f.sort) query.sort = f.sort
  // Changing a filter resets to the first page (offset is intentionally dropped).
  router.push({ name: 'representatives', query })
}

function gotoPage(p) {
  router.push({ name: 'representatives', query: { ...route.query, offset: p * PAGE } })
}

// Monotonic load id: overlapping fetches (filter watcher + cycle watcher) can
// resolve out of order; only the latest may write state.
let loadSeq = 0

async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false
  try {
    // `period` comes from the global cycle chooser (store.cycle; null = all) —
    // it scopes the list to MPs serving in that cycle.
    const res = await api.representatives({
      q: route.query.q, faction_id: route.query.faction_id, period: store.cycle,
      sort: route.query.sort || 'speaking_time', limit: PAGE, offset: route.query.offset || 0,
    })
    if (seq === loadSeq) data.value = res
  } catch {
    if (seq === loadSeq) error.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

onMounted(async () => {
  await loadMeta().catch(() => {})
  try { factions.value = (await api.factions()).factions } catch {}
  load()
})
watch(() => route.query, (q) => {
  f.q = q.q || ''; f.faction_id = q.faction_id || ''; f.sort = q.sort || 'speaking_time'
  load()
})
// Changing the cycle resets to the first page; the offset reset triggers load via the query watcher.
watch(() => store.cycle, () => {
  if (route.query.offset) router.push({ name: 'representatives', query: { ...route.query, offset: undefined } })
  else load()
})
let searchTimer = null
function onSearchInput() { clearTimeout(searchTimer); searchTimer = setTimeout(apply, 300) }
// The debounce survives the component: clear it, or typing then clicking an
// MP within 300 ms yanks the user back to the list (apply() router.push).
onUnmounted(() => clearTimeout(searchTimer))
</script>

<template>
  <h1>{{ $t('reps.title') }}</h1>

  <form class="card pad searchform" role="search" @submit.prevent="apply">
    <div class="row" style="gap:.5rem;">
      <input
        id="r-q" type="search" v-model="f.q" :placeholder="$t('reps.searchPlaceholder')"
        :aria-label="$t('reps.searchPlaceholder')" @input="onSearchInput" style="flex:1;min-width:200px;"
      />
      <button class="btn secondary" type="button" :aria-expanded="showFilters" @click="showFilters = !showFilters">
        {{ $t('search.filters') }}
      </button>
    </div>

    <fieldset v-show="showFilters" class="filters">
      <legend class="visually-hidden">{{ $t('search.filters') }}</legend>
      <div class="filter-grid">
        <div>
          <label for="r-faction">{{ $t('reps.faction') }}</label>
          <select id="r-faction" v-model="f.faction_id" @change="apply">
            <option value="">{{ $t('search.all') }}</option>
            <option v-for="x in factions" :key="x.id" :value="x.id">{{ x.label }}</option>
          </select>
        </div>
      </div>
      <button class="btn secondary small" type="button" style="margin-top:.6rem;" @click="clearFilters">
        {{ $t('search.clearFilters') }}
      </button>
    </fieldset>
  </form>

  <StateBlock
    :loading="loading" :error="error"
    :empty="!!data && data.representatives.length === 0" :empty-text="$t('reps.noResults')"
    @retry="load"
  >
    <div v-if="data">
      <div class="results-head">
        <p class="muted small" aria-live="polite" style="margin:0;">{{ data.total }} {{ $t('home.stats.representatives') }} · {{ scopeText }}</p>
        <label class="sortctl small muted">
          {{ $t('search.sort') }}
          <select v-model="f.sort" @change="apply">
            <option value="name">{{ $t('reps.sortName') }}</option>
            <option value="speeches">{{ $t('reps.sortSpeeches') }}</option>
            <option value="speaking_time">{{ $t('reps.sortSpeakingTime') }}</option>
          </select>
        </label>
      </div>
      <ul class="replist grid">
        <li v-for="r in data.representatives" :key="r.person_id" class="card pad repcard">
          <SpeakerLink :speaker="{ person_id: r.person_id, label: r.label, photo_uri: r.photo_uri }" />
          <div class="repmeta">
            <FactionBadge :faction="r.faction" link />
            <span v-if="r.constituency" class="muted small">📍 {{ r.constituency }}</span>
          </div>
          <div class="repstats small muted">
            <span>{{ r.speech_count }} {{ $t('reps.speeches') }}</span>
            <span>·</span>
            <span>{{ formatSpeakingTime(r.speaking_seconds) }}</span>
          </div>
        </li>
      </ul>
      <Pagination :page="page" :total-pages="totalPages" @goto="gotoPage" />
    </div>
  </StateBlock>
</template>

<style scoped>
/* .filters, .filter-grid, .results-head, .sortctl are global (styles.css). */
.replist { list-style: none; padding: 0; margin: 0; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); }
.repcard { display: flex; flex-direction: column; gap: .5rem; }
.repmeta { display: flex; gap: .6rem; flex-wrap: wrap; align-items: center; }
.repstats { display: flex; gap: .4rem; }
</style>
