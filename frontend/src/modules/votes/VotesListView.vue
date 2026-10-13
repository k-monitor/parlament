<script setup>
// Browsable, filterable vote list (Votes module, §7). Filter/sort state lives in
// the URL so a filtered list is shareable. Each vote links to its detail page
// (the per-MP roll call) and each decided bill links to the Bills module (EXT-2).
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api.js'
import { store, loadMeta } from '../../store.js'
import { createSearchClicks } from '../../lib/searchClicks.js'
import StateBlock from '../../components/StateBlock.vue'
import Pagination from '../../components/Pagination.vue'
import VoteCard from '../../components/VoteCard.vue'
import { ANALYSIS_BY_ROUTE, analysisAvailable } from '../analyses/registry.js'

const route = useRoute()
const router = useRouter()

const PAGE = 50
const SORTS = ['date_desc', 'date_asc', 'attendance_desc', 'attendance_asc',
               'crossvoting_desc', 'crossvoting_asc']

const data = ref(null)
const loading = ref(false)
const error = ref(false)
const results = ref([])
const votingModes = ref([])
// The MP's name for the person-scope header; kept as its own ref so the header
// survives an empty result set (when StateBlock replaces the list) and a load.
const personName = ref('')

const page = computed(() => Math.floor((Number(route.query.offset) || 0) / PAGE))
const totalPages = computed(() => (data.value ? Math.ceil(data.value.total / PAGE) : 0))

const f = reactive({
  q: route.query.q || '',
  result: route.query.result || '',
  voting_mode: route.query.voting_mode || '',
  date_from: route.query.date_from || '',
  date_to: route.query.date_to || '',
})
const sort = ref(SORTS.includes(route.query.sort) ? route.query.sort : 'date_desc')
// Open the filter panel on load when a filter is already active (e.g. a shared
// or deep-linked list), so its filters are visible rather than hidden.
const showFilters = ref(!!(route.query.result || route.query.voting_mode || route.query.date_from || route.query.date_to))

// `bill` is not an interactive filter — it arrives via a link from a bill page.
const bill = computed(() => route.query.bill || '')

// `person` + `value` scope the list to one MP's roll-call participation; they
// arrive via a link from the profile's statistics (like `bill`), not the toolbar.
const person = computed(() => route.query.person || '')
const scopeValue = computed(() => route.query.value || '')
// Localised label for the participation segment being shown (voted/novote/…).
const PART_LABELS = {
  voted: 'profile.vbVoted', novote: 'profile.vbNovote',
  absent: 'profile.vbAbsent', not_present: 'profile.vbNotPresent',
  missed: 'profile.votesAbsent',
}
const scopeValueLabel = computed(() => PART_LABELS[scopeValue.value] || '')

// Any interactive filter set? Drives the clear-filters affordance (the `bill`
// scope arrives via a link, so it isn't counted as a user-cleared filter).
const hasFilters = computed(() =>
  !!(f.q || f.result || f.voting_mode || f.date_from || f.date_to))

function apply() {
  const query = {}
  if (f.q) query.q = f.q
  if (f.result) query.result = f.result
  if (f.voting_mode) query.voting_mode = f.voting_mode
  if (f.date_from) query.date_from = f.date_from
  if (f.date_to) query.date_to = f.date_to
  if (bill.value) query.bill = bill.value
  // Keep the person scope while refining with the toolbar filters.
  if (person.value) query.person = person.value
  if (scopeValue.value) query.value = scopeValue.value
  if (sort.value !== 'date_desc') query.sort = sort.value
  // Changing a filter resets to the first page (offset is intentionally dropped).
  router.push({ name: 'votes', query })
}

// Leave the per-MP scope (and its value segment) back to the full vote list,
// keeping any interactive filters the user set within it.
function clearPersonScope() {
  const query = { ...route.query }
  delete query.person; delete query.value; delete query.offset
  router.push({ name: 'votes', query })
}

// Re-order the list: a new ordering always returns to the first page.
function changeSort() {
  const query = { ...route.query }
  delete query.offset
  if (sort.value === 'date_desc') delete query.sort
  else query.sort = sort.value
  router.push({ name: 'votes', query })
}

function clearFilters() {
  f.q = f.result = f.voting_mode = f.date_from = f.date_to = ''
  apply()
}

function gotoPage(p) {
  router.push({ name: 'votes', query: { ...route.query, offset: p * PAGE } })
}

async function loadFacets() {
  try {
    const facets = await api.voteFacets({ period: store.cycles })
    results.value = facets.results
    votingModes.value = facets.voting_modes || []
  } catch { results.value = []; votingModes.value = [] }
}

// Monotonic load id: overlapping fetches (filter watcher + cycle watcher) can
// resolve out of order; only the latest may write state.
let loadSeq = 0

// Anonymous search-quality signal (SEA-12): which vote the reader opens after a
// keyword search, and how far down the list it sat.
const clicks = createSearchClicks('votes')

async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false
  const offset = Number(route.query.offset) || 0
  // `period` comes from the global cycle chooser (store.cycles; empty = all).
  const args = {
    q: route.query.q, result: route.query.result, period: store.cycles,
    voting_mode: route.query.voting_mode,
    date_from: route.query.date_from, date_to: route.query.date_to,
    sort: route.query.sort,
    bill: route.query.bill,
    person: route.query.person, value: route.query.value,
  }
  try {
    const res = await api.votes({ ...args, limit: PAGE, offset })
    if (seq === loadSeq) {
      data.value = res
      personName.value = res.person ? res.person.label : ''
      clicks.arm(args, offset)
    }
  } catch {
    if (seq === loadSeq) error.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

// Frakcióelemzés (VOTE-8) is what this list adds up to — who votes with whom
// across the whole cycle rather than one division at a time. Linked only while
// that page can actually be opened, for which the Elemzések registry is the one
// authority: it is cycle-scoped, so the link comes and goes with the cycle
// chooser (an "all cycles" scope would pool terms that never sat together).
const cohesionAvailable = computed(() => {
  // Guarded on the entry existing at all, not just on its module: if the
  // analysis is ever dropped from the registry this link should quietly go with
  // it rather than take the whole list page down with a missing `module`.
  const cohesion = ANALYSIS_BY_ROUTE.cohesion
  return !!cohesion && analysisAvailable(cohesion)
})

onMounted(() => { loadMeta().catch(() => {}).finally(() => { loadFacets(); load() }) })
watch(() => route.query, (q) => {
  f.q = q.q || ''; f.result = q.result || ''
  f.voting_mode = q.voting_mode || ''
  f.date_from = q.date_from || ''; f.date_to = q.date_to || ''
  sort.value = SORTS.includes(q.sort) ? q.sort : 'date_desc'
  loadFacets(); load()
})
// Re-fetch when the global cycle changes.
watch(() => store.cycles.join(','), () => { loadFacets(); load() })
let searchTimer = null
function onSearchInput() { clearTimeout(searchTimer); searchTimer = setTimeout(apply, 300) }
// The debounce survives the component: clear it, or typing then clicking a
// vote within 300 ms yanks the user back to the list (apply() router.push).
onUnmounted(() => clearTimeout(searchTimer))
</script>

<template>
  <h1>{{ $t('votes.title') }}</h1>
  <p class="muted">{{ $t('votes.subtitle') }}</p>

  <form class="card pad searchform" role="search" @submit.prevent="apply">
    <div class="row" style="gap:.5rem;">
      <input
        id="v-q" type="search" v-model="f.q" :placeholder="$t('votes.searchPlaceholder')"
        :aria-label="$t('votes.searchPlaceholder')" @input="onSearchInput" style="flex:1;min-width:200px;"
      />
      <button class="btn secondary" type="button" :aria-expanded="showFilters" @click="showFilters = !showFilters">
        {{ $t('search.filters') }}
      </button>
    </div>

    <fieldset v-show="showFilters" class="filters">
      <legend class="visually-hidden">{{ $t('search.filters') }}</legend>
      <div class="filter-grid">
        <div>
          <label for="v-result">{{ $t('votes.result') }}</label>
          <select id="v-result" v-model="f.result" @change="apply">
            <option value="">{{ $t('votes.all') }}</option>
            <option v-for="r in results" :key="r" :value="r">{{ r }}</option>
          </select>
        </div>
        <div>
          <label for="v-mode">{{ $t('votes.votingMode') }}</label>
          <select id="v-mode" v-model="f.voting_mode" @change="apply">
            <option value="">{{ $t('votes.all') }}</option>
            <option v-for="m in votingModes" :key="m" :value="m">{{ m }}</option>
          </select>
        </div>
        <div>
          <label for="v-from">{{ $t('votes.dateFrom') }}</label>
          <input id="v-from" type="date" v-model="f.date_from" @change="apply" />
        </div>
        <div>
          <label for="v-to">{{ $t('votes.dateTo') }}</label>
          <input id="v-to" type="date" v-model="f.date_to" @change="apply" />
        </div>
      </div>
      <button class="btn secondary small" type="button" style="margin-top:.6rem;" :disabled="!hasFilters" @click="clearFilters">
        {{ $t('votes.clearFilters') }}
      </button>
    </fieldset>
  </form>

  <!-- The next question this list raises: who keeps voting with whom? -->
  <p v-if="cohesionAvailable" class="card pad analysislink">
    <span class="muted small">{{ $t('votes.cohesionLead') }}</span>
    <!-- Plain named link: the cycle scope is re-attached by the router guard,
         the same way every other in-site link leaves it to. -->
    <router-link class="small" :to="{ name: 'cohesion' }">
      {{ $t('votes.cohesionLink') }} →
    </router-link>
  </p>

  <!-- Person scope banner (arrives via a link from a profile's statistics):
       names the MP + which participation segment is shown, with a way back to
       the full list. Lives outside StateBlock so it stays put on an empty set. -->
  <div v-if="person" class="card pad personscope">
    <p class="ps-title">
      <router-link :to="{ name: 'profile', params: { id: person } }">{{ personName || person }}</router-link>
      {{ $t('votes.personScopeSuffix') }}<template v-if="scopeValueLabel"> · <span class="ps-seg">{{ $t(scopeValueLabel) }}</span></template>
    </p>
    <button type="button" class="btn secondary small" @click="clearPersonScope">{{ $t('votes.clearPersonScope') }}</button>
  </div>

  <StateBlock
    :loading="loading" :error="error"
    :empty="!!data && data.votes.length === 0" :empty-text="$t('votes.noResults')"
    @retry="load"
  >
    <div v-if="data">
      <div class="results-head">
        <p class="muted small" aria-live="polite" style="margin:0;">{{ data.total }} {{ $t('votes.count') }}</p>
        <label class="sortctl small muted">
          {{ $t('votes.sort') }}
          <select v-model="sort" @change="changeSort">
            <option value="date_desc">{{ $t('votes.sortNewest') }}</option>
            <option value="date_asc">{{ $t('votes.sortOldest') }}</option>
            <option value="attendance_desc">{{ $t('votes.sortAttendanceDesc') }}</option>
            <option value="attendance_asc">{{ $t('votes.sortAttendanceAsc') }}</option>
            <option value="crossvoting_desc">{{ $t('votes.sortCrossDesc') }}</option>
            <option value="crossvoting_asc">{{ $t('votes.sortCrossAsc') }}</option>
          </select>
        </label>
      </div>
      <ul class="votelist">
        <VoteCard v-for="(v, i) in data.votes" :key="v.id" :vote="v" :person-scoped="!!person" @open="clicks.hit(i)" />
      </ul>
      <Pagination :page="page" :total-pages="totalPages" @goto="gotoPage" />
    </div>
  </StateBlock>
</template>

<style scoped>
/* .filters, .filter-grid, .results-head, .sortctl are global (styles.css). */
.analysislink { display: flex; gap: .75rem; align-items: baseline; flex-wrap: wrap; margin-bottom: 1rem; }
.personscope { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; margin: 1rem 0 .2rem; }
.personscope .ps-title { margin: 0; font-weight: 600; }
.personscope .ps-seg { color: var(--accent); }
.votelist { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .6rem; }
</style>
