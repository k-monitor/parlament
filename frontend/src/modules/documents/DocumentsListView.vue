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
    const r = await api.billFacets({ period: store.cycle, main_type_not: 'T' })
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
    // `period` comes from the global cycle chooser (store.cycle; null = all).
    const res = await api.bills({
      q: route.query.q, type: route.query.type, status: route.query.status,
      period: store.cycle, sort: route.query.sort || 'number',
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
watch(() => store.cycle, () => { loadFacets(); load() })
let t = null
function onSearchInput() { clearTimeout(t); t = setTimeout(apply, 300) }
// The debounce survives the component: clear it, or typing then clicking a
// document within 300 ms yanks the user back to the list (apply() router.push).
onUnmounted(() => clearTimeout(t))
</script>

<template>
  <h1>{{ $t('documents.title') }}</h1>
  <p class="muted">{{ $t('documents.subtitle') }}</p>

  <form class="card pad toolbar" role="search" @submit.prevent="apply">
    <div>
      <label for="d-q">{{ $t('documents.searchPlaceholder') }}</label>
      <input id="d-q" type="search" v-model="f.q" :placeholder="$t('documents.searchPlaceholder')" @input="onSearchInput" />
    </div>
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
    <div>
      <label for="d-sort">↕</label>
      <select id="d-sort" v-model="f.sort" @change="apply">
        <option value="number">{{ $t('documents.sortNumber') }}</option>
        <option value="date">{{ $t('documents.sortDate') }}</option>
      </select>
    </div>
  </form>

  <StateBlock
    :loading="loading" :error="error"
    :empty="!!data && data.bills.length === 0" :empty-text="$t('documents.noResults')"
    @retry="load"
  >
    <div v-if="data">
      <p class="muted small">{{ data.total }} {{ $t('documents.count') }}</p>
      <ul class="billlist">
        <li v-for="b in data.bills" :key="b.id" class="card pad billcard">
          <div class="billhead">
            <router-link :to="{ name: 'document', params: { id: b.id } }" class="billnum">{{ b.bill_number }}</router-link>
            <span class="badge" v-if="b.type">{{ b.type }}</span>
            <span class="badge status" v-if="b.status">{{ b.status }}</span>
            <span class="muted small" v-if="b.submitted_date">{{ formatDate(b.submitted_date) }}</span>
          </div>
          <router-link :to="{ name: 'document', params: { id: b.id } }" class="billtitle">{{ b.title }}</router-link>
          <div class="sponsors small" v-if="b.sponsors.length">
            <span class="muted">{{ $t('documents.submitters') }}:</span>
            <template v-for="(s, i) in b.sponsors" :key="i">
              <router-link v-if="s.person_id" :to="{ name: 'profile', params: { id: s.person_id } }">{{ s.name }}</router-link>
              <span v-else>{{ s.name }}</span>
              <FactionBadge v-if="s.faction" :faction="s.faction" />
              <span v-if="i < b.sponsors.length - 1" aria-hidden="true">·</span>
            </template>
          </div>
        </li>
      </ul>
      <Pagination :page="page" :total-pages="totalPages" @goto="gotoPage" />
    </div>
  </StateBlock>
</template>

<style scoped>
.toolbar { display: grid; grid-template-columns: 2fr 1.4fr 1.4fr 1fr; gap: .8rem; align-items: end; margin: 1rem 0; }
.billlist { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .6rem; }
.billcard { display: flex; flex-direction: column; gap: .4rem; }
.billhead { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; }
.billnum { font-weight: 800; color: var(--accent); }
.billtitle { color: var(--ink); font-weight: 600; }
.billtitle:hover { color: var(--accent); }
.sponsors { display: flex; gap: .4rem; flex-wrap: wrap; align-items: center; }
.badge.status { background: var(--accent-soft); }
@media (max-width: 700px) { .toolbar { grid-template-columns: 1fr; } }
</style>
