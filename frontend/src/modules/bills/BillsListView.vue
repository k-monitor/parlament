<script setup>
// Browsable, filterable bill list (Bills module, §7). Filter/sort state lives in
// the URL so a filtered list is shareable. Each bill links to its detail page
// and each MP sponsor links to their profile (EXT-2).
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api.js'
import { store } from '../../store.js'
import { formatDate } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import FactionBadge from '../../components/FactionBadge.vue'

const route = useRoute()
const router = useRouter()

const data = ref(null)
const loading = ref(false)
const error = ref(false)
const statuses = ref([])

const f = reactive({
  q: route.query.q || '',
  status: route.query.status || '',
  period: route.query.period || '',
  sort: route.query.sort || 'number',
})

const periods = computed(() => (store.meta && store.meta.periods) || [])

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
  if (f.period) query.period = f.period
  if (f.sort && f.sort !== 'number') query.sort = f.sort
  if (sponsor.value) query.sponsor = sponsor.value
  router.push({ name: 'bills', query })
}

async function loadFacets() {
  try {
    statuses.value = (await api.billFacets({
      period: route.query.period, main_type: 'T',
    })).statuses
  } catch { statuses.value = [] }
}

async function load() {
  loading.value = true; error.value = false
  try {
    // This page is the bills (törvényjavaslat) view; the other iromány types
    // live on the separate "Egyéb irományok" page (main_type=T scopes here).
    data.value = await api.bills({
      q: route.query.q, status: route.query.status, period: route.query.period,
      sponsor: route.query.sponsor, sort: route.query.sort || 'number',
      main_type: 'T', limit: 200,
    })
  } catch { error.value = true } finally { loading.value = false }
}

onMounted(() => { loadFacets(); load() })
watch(() => route.query, (q) => {
  f.q = q.q || ''; f.status = q.status || ''; f.period = q.period || ''
  f.sort = q.sort || 'number'
  loadFacets(); load()
})
let t = null
function onSearchInput() { clearTimeout(t); t = setTimeout(apply, 300) }
</script>

<template>
  <h1>{{ $t('bills.title') }}</h1>
  <p class="muted">{{ $t('bills.subtitle') }}</p>

  <form class="card pad toolbar" role="search" @submit.prevent="apply">
    <div>
      <label for="b-q">{{ $t('bills.searchPlaceholder') }}</label>
      <input id="b-q" type="search" v-model="f.q" :placeholder="$t('bills.searchPlaceholder')" @input="onSearchInput" />
    </div>
    <div v-if="periods.length">
      <label for="b-period">{{ $t('bills.period') }}</label>
      <select id="b-period" v-model="f.period" @change="apply">
        <option value="">{{ $t('search.all') }}</option>
        <option v-for="p in periods" :key="p.number" :value="p.number">{{ p.label || p.number }}</option>
      </select>
    </div>
    <div>
      <label for="b-status">{{ $t('bills.status') }}</label>
      <select id="b-status" v-model="f.status" @change="apply">
        <option value="">{{ $t('search.all') }}</option>
        <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
      </select>
    </div>
    <div>
      <label for="b-sort">↕</label>
      <select id="b-sort" v-model="f.sort" @change="apply">
        <option value="number">{{ $t('bills.sortNumber') }}</option>
        <option value="date">{{ $t('bills.sortDate') }}</option>
      </select>
    </div>
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
      <p class="muted small">{{ data.total }} {{ $t('bills.count') }}</p>
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
    </div>
  </StateBlock>
</template>

<style scoped>
.toolbar { display: grid; grid-template-columns: 2fr 1fr 1.4fr 1fr; gap: .8rem; align-items: end; margin: 1rem 0; }
.sponsorfilter { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; margin-bottom: 1rem; background: var(--accent-soft); }
.sponsorfilter .clear { margin-left: auto; }
.billlist { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .6rem; }
.billcard { display: flex; flex-direction: column; gap: .4rem; }
.billhead { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; }
.billnum { font-weight: 800; color: var(--accent); }
.billtitle { color: var(--ink); font-weight: 600; }
.billtitle:hover { color: var(--accent); }
.sponsors { display: flex; gap: .4rem; flex-wrap: wrap; align-items: center; }
@media (max-width: 700px) { .toolbar { grid-template-columns: 1fr; } }
</style>
