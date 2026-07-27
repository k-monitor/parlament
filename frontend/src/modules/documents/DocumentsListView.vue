<script setup>
// Browsable, filterable list of the cycle's *other* irományok — every document
// type except törvényjavaslat (bills), which has its own page. Shares the Bills
// module's data layer (`/api/v1/bills` with `main_type_not=T`) and detail view;
// only the browse page is distinct. Filter/sort state lives in the URL so a
// filtered list is shareable, and each MP submitter links to their profile
// (EXT-2).
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api.js'
import { store, loadMeta } from '../../store.js'
import { formatDate } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import Pagination from '../../components/Pagination.vue'

const route = useRoute()
const router = useRouter()

const PAGE = 50

const data = ref(null)
const loading = ref(false)
const error = ref(false)
const types = ref([])      // distinct iromány categories (excl. törvényjavaslat)
const statuses = ref([])

const page = computed(() => Math.floor((Number(route.query.offset) || 0) / PAGE))
const totalPages = computed(() => (data.value ? Math.ceil(data.value.total / PAGE) : 0))

const f = reactive({
  q: route.query.q || '',
  type: route.query.type || '',
  status: route.query.status || '',
  sort: route.query.sort || 'number',
})
// Open the filter panel on load when a filter is already active (e.g. a shared
// or deep-linked list), so its filters are visible rather than hidden.
const showFilters = ref(!!(route.query.type || route.query.status))

function clearFilters() {
  f.type = ''
  f.status = ''
  apply()
}

function apply() {
  const query = {}
  if (f.q) query.q = f.q
  if (f.type) query.type = f.type
  if (f.status) query.status = f.status
  if (f.sort && f.sort !== 'number') query.sort = f.sort
  // Changing a filter resets to the first page (offset is intentionally dropped).
  router.push({ name: 'documents', query })
}

function gotoPage(p) {
  router.push({ name: 'documents', query: { ...route.query, offset: p * PAGE } })
}

async function loadFacets() {
  try {
    const r = await api.billFacets({ period: store.cycles, main_type_not: 'T' })
    types.value = r.types.map((t) => t.type)
    statuses.value = r.statuses
  } catch { types.value = []; statuses.value = [] }
}

// Monotonic load id: overlapping fetches (filter watcher + cycle watcher) can
// resolve out of order; only the latest may write state.
let loadSeq = 0

async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false
  try {
    // `period` comes from the global cycle chooser (store.cycles; empty = all).
    const res = await api.bills({
      q: route.query.q, type: route.query.type, status: route.query.status,
      period: store.cycles, sort: route.query.sort || 'number',
      main_type_not: 'T', limit: PAGE, offset: route.query.offset || 0,
    })
    if (seq === loadSeq) data.value = res
  } catch {
    if (seq === loadSeq) error.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

onMounted(() => { loadMeta().catch(() => {}).finally(() => { loadFacets(); load() }) })
watch(() => route.query, (q) => {
  f.q = q.q || ''; f.type = q.type || ''; f.status = q.status || ''
  f.sort = q.sort || 'number'
  loadFacets(); load()
})
// Re-fetch when the global cycle changes.
watch(() => store.cycles.join(','), () => { loadFacets(); load() })
let t = null
function onSearchInput() { clearTimeout(t); t = setTimeout(apply, 300) }
// The debounce survives the component: clear it, or typing then clicking a
// document within 300 ms yanks the user back to the list (apply() router.push).
onUnmounted(() => clearTimeout(t))
</script>

<template>
  <h1>{{ $t('documents.title') }}</h1>
  <p class="muted">{{ $t('documents.subtitle') }}</p>

  <form class="card pad searchform" role="search" @submit.prevent="apply">
    <div class="row" style="gap:.5rem;">
      <input
        id="d-q" type="search" v-model="f.q" :placeholder="$t('documents.searchPlaceholder')"
        :aria-label="$t('documents.searchPlaceholder')" @input="onSearchInput" style="flex:1;min-width:200px;"
      />
      <button class="btn secondary" type="button" :aria-expanded="showFilters" @click="showFilters = !showFilters">
        {{ $t('search.filters') }}
      </button>
    </div>

    <fieldset v-show="showFilters" class="filters">
      <legend class="visually-hidden">{{ $t('search.filters') }}</legend>
      <div class="filter-grid">
        <div>
          <label for="d-type">{{ $t('documents.type') }}</label>
          <select id="d-type" v-model="f.type" @change="apply">
            <option value="">{{ $t('search.all') }}</option>
            <option v-for="ty in types" :key="ty" :value="ty">{{ ty }}</option>
          </select>
        </div>
        <div>
          <label for="d-status">{{ $t('documents.status') }}</label>
          <select id="d-status" v-model="f.status" @change="apply">
            <option value="">{{ $t('search.all') }}</option>
            <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
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
    :empty="!!data && data.bills.length === 0" :empty-text="$t('documents.noResults')"
    @retry="load"
  >
    <div v-if="data">
      <div class="results-head">
        <p class="muted small" aria-live="polite" style="margin:0;">{{ data.total }} {{ $t('documents.count') }}</p>
        <label class="sortctl small muted">
          {{ $t('search.sort') }}
          <select v-model="f.sort" @change="apply">
            <option value="number">{{ $t('documents.sortNumber') }}</option>
            <option value="date">{{ $t('documents.sortDate') }}</option>
          </select>
        </label>
      </div>
      <ul class="billlist">
        <li v-for="b in data.bills" :key="b.id" class="card pad billcard">
          <div class="billhead">
            <router-link :to="{ name: 'document', params: { id: b.id } }" class="billnum">{{ b.bill_number }}</router-link>
            <span class="badge" v-if="b.type">{{ b.type }}</span>
            <span class="badge status" v-if="b.status">{{ b.status }}</span>
            <span class="muted small" v-if="b.submitted_date">{{ formatDate(b.submitted_date) }}</span>
          </div>
          <router-link :to="{ name: 'document', params: { id: b.id } }" class="billtitle">{{ b.title }}</router-link>
          <div class="sponsors small" v-if="b.sponsors.length || b.responder">
            <template v-for="(s, i) in b.sponsors" :key="i">
              <router-link v-if="s.person_id" :to="{ name: 'profile', params: { id: s.person_id } }">{{ s.name }}</router-link>
              <span v-else>{{ s.name }}</span>
              <FactionBadge v-if="s.faction" :faction="s.faction" />
              <span v-if="i < b.sponsors.length - 1" aria-hidden="true">·</span>
            </template>
            <!-- questions: submitter → whoever answered it in plenary -->
            <template v-if="b.responder">
              <span class="arrow" aria-hidden="true">→</span>
              <span class="visually-hidden">{{ $t('documents.answeredBy') }}:</span>
              <router-link
                v-if="b.responder.person_id" :title="b.responder.office"
                :to="{ name: 'profile', params: { id: b.responder.person_id } }"
              >{{ b.responder.name }}</router-link>
              <span v-else :title="b.responder.office">{{ b.responder.name }}</span>
              <FactionBadge v-if="b.responder.faction" :faction="b.responder.faction" />
            </template>
          </div>
        </li>
      </ul>
      <Pagination :page="page" :total-pages="totalPages" @goto="gotoPage" />
    </div>
  </StateBlock>
</template>

<style scoped>
/* .filters, .filter-grid, .results-head, .sortctl are global (styles.css). */
.billlist { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .6rem; }
.billcard { display: flex; flex-direction: column; gap: .4rem; }
.billhead { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; }
.billnum { font-weight: 800; color: var(--accent); }
.billtitle { color: var(--ink); font-weight: 600; }
.billtitle:hover { color: var(--accent); }
.sponsors { display: flex; gap: .4rem; flex-wrap: wrap; align-items: center; }
.sponsors .arrow { color: var(--ink-soft); }
.badge.status { background: var(--accent-soft); }
</style>
