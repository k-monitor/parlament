<script setup>
// Browsable, filterable bill list (Bills module, §7). Filter/sort state lives in
// the URL so a filtered list is shareable. Each bill links to its detail page
// and each MP sponsor links to their profile (EXT-2).
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api.js'
import { store, loadMeta } from '../../store.js'
import { formatDate } from '../../format.js'
import { createSearchClicks } from '../../lib/searchClicks.js'
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

// `sponsor` is not an interactive filter — it arrives as a deep link scoped to
// one MP. It's carried in the URL and preserved across the other filters.
const sponsor = computed(() => route.query.sponsor || '')
// The sponsor's display name, normally derived from the returned bills; when
// this MP has no törvényjavaslat in scope there is nothing to derive it from,
// so fall back to the person's own label rather than showing a raw id.
const sponsorLabel = ref('')
const sponsorName = computed(() => {
  for (const b of (data.value && data.value.bills) || []) {
    const s = (b.sponsors || []).find((x) => x.person_id === sponsor.value)
    if (s) return s.name
  }
  return sponsorLabel.value || sponsor.value
})
async function loadSponsorLabel() {
  sponsorLabel.value = ''
  if (!sponsor.value) return
  try {
    sponsorLabel.value = (await api.representative(sponsor.value)).label || ''
  } catch { sponsorLabel.value = '' }
}

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
      period: store.cycles, main_type: 'T',
    })).statuses
  } catch { statuses.value = [] }
}

// Monotonic load id: overlapping fetches (filter watcher + cycle watcher) can
// resolve out of order; only the latest may write state.
let loadSeq = 0

// Anonymous search-quality signal (SEA-12): which result the reader opens after
// a keyword search, and how far down the list it sat.
const clicks = createSearchClicks('bills')

async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false
  const offset = Number(route.query.offset) || 0
  // This page is the bills (törvényjavaslat) view; the other iromány types
  // live on the separate "Egyéb irományok" page (main_type=T scopes here).
  // `period` comes from the global cycle chooser (store.cycles; empty = all).
  const args = {
    q: route.query.q, status: route.query.status, period: store.cycles,
    sponsor: route.query.sponsor, sort: route.query.sort || 'number',
    main_type: 'T',
  }
  try {
    const res = await api.bills({ ...args, limit: PAGE, offset })
    if (seq === loadSeq) { data.value = res; clicks.arm(args, offset) }
  } catch {
    if (seq === loadSeq) error.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

onMounted(() => {
  loadMeta().catch(() => {}).finally(() => { loadFacets(); loadSponsorLabel(); load() })
})
watch(() => route.query, (q, prev) => {
  f.q = q.q || ''; f.status = q.status || ''
  f.sort = q.sort || 'number'
  if (q.sponsor !== (prev && prev.sponsor)) loadSponsorLabel()
  loadFacets(); load()
})
// Re-fetch when the global cycle changes.
watch(() => store.cycles.join(','), () => { loadFacets(); load() })
let t = null
function onSearchInput() { clearTimeout(t); t = setTimeout(apply, 300) }
// The debounce survives the component: clear it, or typing then clicking a
// bill within 300 ms yanks the user back to the list (apply() router.push).
onUnmounted(() => clearTimeout(t))
</script>

<template>
  <div class="bills-head">
    <div>
      <h1>{{ $t('bills.title') }}</h1>
      <p class="muted">{{ $t('bills.subtitle') }}</p>
    </div>
    <a
      class="figyusz-btn"
      href="https://figyusz.k-monitor.hu/"
      target="_blank" rel="noopener noreferrer"
      :title="$t('bills.figyusz')" :aria-label="$t('bills.figyusz')"
    >
      <img src="/figyusz.svg" alt="Figyusz!" />
    </a>
  </div>

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
        <li v-for="(b, i) in data.bills" :key="b.id" class="card pad billcard">
          <div class="billhead">
            <router-link :to="{ name: 'bill', params: { id: b.id } }" class="billnum" @click="clicks.hit(i)">{{ b.bill_number }}</router-link>
            <span class="badge" v-if="b.status">{{ b.status }}</span>
            <span class="muted small" v-if="b.submitted_date">{{ formatDate(b.submitted_date) }}</span>
          </div>
          <router-link :to="{ name: 'bill', params: { id: b.id } }" class="billtitle" @click="clicks.hit(i)">{{ b.title }}</router-link>
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
/* Page header: title/subtitle on the left, Figyusz! CTA pinned top-right. */
.bills-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  flex-wrap: wrap;
}
/* Figyusz! call-to-action: the official wordmark links out to figyusz.k-monitor.hu.
   The logo is transparent white text, so the button supplies the brand-purple box.
   The full sentence is shown on hover (title) and to screen readers (aria-label). */
.figyusz-btn {
  display: inline-flex;
  align-items: center;
  flex: none;
  padding: .35rem .6rem;
  background: #6000db;
  border-radius: 9px;
  transition: transform .12s ease, box-shadow .12s ease, background .12s ease;
}
.figyusz-btn img { display: block; height: 22px; width: auto; }
.figyusz-btn:hover, .figyusz-btn:focus-visible {
  background: #6d0ff0;
  transform: translateY(-1px);
  box-shadow: 0 4px 14px rgba(96, 0, 219, .35);
}
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
