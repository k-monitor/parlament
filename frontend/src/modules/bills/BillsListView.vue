<script setup>
// Browsable, filterable bill list (Bills module, §7). Filter/sort state lives in
// the URL so a filtered list is shareable. Each bill links to its detail page
// and each MP sponsor links to their profile (EXT-2).
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
const statuses = ref([])

const page = computed(() => Math.floor((Number(route.query.offset) || 0) / PAGE))
const totalPages = computed(() => (data.value ? Math.ceil(data.value.total / PAGE) : 0))

const f = reactive({
  q: route.query.q || '',
  status: route.query.status || '',
  sort: route.query.sort || 'number',
})
// Open the filter panel on load when a filter is already active (e.g. a shared
// or deep-linked list), so its filters are visible rather than hidden.
const showFilters = ref(!!route.query.status)

function clearFilters() {
  f.status = ''
  apply()
}

// `sponsor` is not an interactive filter — it arrives via a link from an MP
// (the headline stat / profile bills section). It's carried in the URL and
// preserved across the other filters.
const sponsor = computed(() => route.query.sponsor || '')
// The sponsor's display name, derived from the returned bills (avoids a second
// request to the representatives module just for a label).
const sponsorName = computed(() => {
  if (!sponsor.value || !data.value) return sponsor.value
  for (const b of data.value.bills) {
    const s = (b.sponsors || []).find((x) => x.person_id === sponsor.value)
    if (s) return s.name
  }
  return sponsor.value
})

function apply() {
  const query = {}
  if (f.q) query.q = f.q
  if (f.status) query.status = f.status
  if (f.sort && f.sort !== 'number') query.sort = f.sort
  if (sponsor.value) query.sponsor = sponsor.value
  // Changing a filter resets to the first page (offset is intentionally dropped).
  router.push({ name: 'bills', query })
}

function gotoPage(p) {
  router.push({ name: 'bills', query: { ...route.query, offset: p * PAGE } })
}

async function loadFacets() {
  try {
    statuses.value = (await api.billFacets({
      period: store.cycle, main_type: 'T',
    })).statuses
  } catch { statuses.value = [] }
}

// Monotonic load id: overlapping fetches (filter watcher + cycle watcher) can
// resolve out of order; only the latest may write state.
let loadSeq = 0

async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false
  try {
    // This page is the bills (törvényjavaslat) view; the other iromány types
    // live on the separate "Egyéb irományok" page (main_type=T scopes here).
    // `period` comes from the global cycle chooser (store.cycle; null = all).
    const res = await api.bills({
      q: route.query.q, status: route.query.status, period: store.cycle,
      sponsor: route.query.sponsor, sort: route.query.sort || 'number',
      main_type: 'T', limit: PAGE, offset: route.query.offset || 0,
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
  f.q = q.q || ''; f.status = q.status || ''
  f.sort = q.sort || 'number'
  loadFacets(); load()
})
// Re-fetch when the global cycle changes.
watch(() => store.cycle, () => { loadFacets(); load() })
let t = null
function onSearchInput() { clearTimeout(t); t = setTimeout(apply, 300) }
// The debounce survives the component: clear it, or typing then clicking a
// bill within 300 ms yanks the user back to the list (apply() router.push).
onUnmounted(() => clearTimeout(t))
</script>

<template>
  <h1>{{ $t('bills.title') }}</h1>
  <p class="muted">{{ $t('bills.subtitle') }}</p>

  <form class="card pad searchform" role="search" @submit.prevent="apply">
    <div class="row" style="gap:.5rem;">
      <input
        id="b-q" type="search" v-model="f.q" :placeholder="$t('bills.searchPlaceholder')"
        :aria-label="$t('bills.searchPlaceholder')" @input="onSearchInput" style="flex:1;min-width:200px;"
      />
      <button class="btn secondary" type="button" :aria-expanded="showFilters" @click="showFilters = !showFilters">
        {{ $t('search.filters') }}
      </button>
    </div>

    <fieldset v-show="showFilters" class="filters">
      <legend class="visually-hidden">{{ $t('search.filters') }}</legend>
      <div class="filter-grid">
        <div>
          <label for="b-status">{{ $t('bills.status') }}</label>
          <select id="b-status" v-model="f.status" @change="apply">
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

  <p v-if="sponsor" class="card pad sponsorfilter">
    <span>{{ $t('bills.bySponsor') }}: <strong>{{ sponsorName }}</strong></span>
    <router-link :to="{ name: 'profile', params: { id: sponsor } }" class="small">{{ $t('bills.viewProfile') }}</router-link>
    <router-link :to="{ name: 'bills' }" class="small clear">✕ {{ $t('bills.clearSponsor') }}</router-link>
  </p>

  <StateBlock
    :loading="loading" :error="error"
    :empty="!!data && data.bills.length === 0" :empty-text="$t('bills.noResults')"
    @retry="load"
  >
    <div v-if="data">
      <div class="results-head">
        <p class="muted small" aria-live="polite" style="margin:0;">{{ data.total }} {{ $t('bills.count') }}</p>
        <label class="sortctl small muted">
          {{ $t('search.sort') }}
          <select v-model="f.sort" @change="apply">
            <option value="number">{{ $t('bills.sortNumber') }}</option>
            <option value="date">{{ $t('bills.sortDate') }}</option>
          </select>
        </label>
      </div>
      <ul class="billlist">
        <li v-for="b in data.bills" :key="b.id" class="card pad billcard">
          <div class="billhead">
            <router-link :to="{ name: 'bill', params: { id: b.id } }" class="billnum">{{ b.bill_number }}</router-link>
            <span class="badge" v-if="b.status">{{ b.status }}</span>
            <span class="muted small" v-if="b.submitted_date">{{ formatDate(b.submitted_date) }}</span>
          </div>
          <router-link :to="{ name: 'bill', params: { id: b.id } }" class="billtitle">{{ b.title }}</router-link>
          <div class="sponsors small" v-if="b.sponsors.length">
            <span class="muted">{{ $t('bills.submitters') }}:</span>
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
/* .filters, .filter-grid, .results-head, .sortctl are global (styles.css). */
.sponsorfilter { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; margin-bottom: 1rem; background: var(--accent-soft); }
.sponsorfilter .clear { margin-left: auto; }
.billlist { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .6rem; }
.billcard { display: flex; flex-direction: column; gap: .4rem; }
.billhead { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; }
.billnum { font-weight: 800; color: var(--accent); }
.billtitle { color: var(--ink); font-weight: 600; }
.billtitle:hover { color: var(--accent); }
.sponsors { display: flex; gap: .4rem; flex-wrap: wrap; align-items: center; }
</style>
