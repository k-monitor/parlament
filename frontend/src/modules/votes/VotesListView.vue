<script setup>
// Browsable, filterable vote list (Votes module, §7). Filter/sort state lives in
// the URL so a filtered list is shareable. Each vote links to its detail page
// (the per-MP roll call) and each decided bill links to the Bills module (EXT-2).
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api.js'
import { store, loadMeta } from '../../store.js'
import { formatDateTime } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import Pagination from '../../components/Pagination.vue'

const route = useRoute()
const router = useRouter()

const PAGE = 50

const data = ref(null)
const loading = ref(false)
const error = ref(false)
const results = ref([])

const page = computed(() => Math.floor((Number(route.query.offset) || 0) / PAGE))
const totalPages = computed(() => (data.value ? Math.ceil(data.value.total / PAGE) : 0))

const f = reactive({
  q: route.query.q || '',
  result: route.query.result || '',
})

// `bill` is not an interactive filter — it arrives via a link from a bill page.
const bill = computed(() => route.query.bill || '')

function apply() {
  const query = {}
  if (f.q) query.q = f.q
  if (f.result) query.result = f.result
  if (bill.value) query.bill = bill.value
  // Changing a filter resets to the first page (offset is intentionally dropped).
  router.push({ name: 'votes', query })
}

function gotoPage(p) {
  router.push({ name: 'votes', query: { ...route.query, offset: p * PAGE } })
}

function voteParts(v) {
  const yes = v.yes || 0, no = v.no || 0, abstain = v.abstain || 0
  const total = yes + no + abstain || 1
  return { yes: (100 * yes) / total, no: (100 * no) / total, abstain: (100 * abstain) / total }
}

async function loadFacets() {
  try {
    results.value = (await api.voteFacets({ period: store.cycle })).results
  } catch { results.value = [] }
}

// Monotonic load id: overlapping fetches (filter watcher + cycle watcher) can
// resolve out of order; only the latest may write state.
let loadSeq = 0

async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false
  try {
    // `period` comes from the global cycle chooser (store.cycle; null = all).
    const res = await api.votes({
      q: route.query.q, result: route.query.result, period: store.cycle,
      bill: route.query.bill, limit: PAGE, offset: route.query.offset || 0,
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
  f.q = q.q || ''; f.result = q.result || ''
  loadFacets(); load()
})
// Re-fetch when the global cycle changes.
watch(() => store.cycle, () => { loadFacets(); load() })
let t = null
function onSearchInput() { clearTimeout(t); t = setTimeout(apply, 300) }
// The debounce survives the component: clear it, or typing then clicking a
// vote within 300 ms yanks the user back to the list (apply() router.push).
onUnmounted(() => clearTimeout(t))
</script>

<template>
  <h1>{{ $t('votes.title') }}</h1>
  <p class="muted">{{ $t('votes.subtitle') }}</p>

  <form class="card pad toolbar" role="search" @submit.prevent="apply">
    <div>
      <label for="v-q">{{ $t('votes.searchPlaceholder') }}</label>
      <input id="v-q" type="search" v-model="f.q" :placeholder="$t('votes.searchPlaceholder')" @input="onSearchInput" />
    </div>
    <div>
      <label for="v-result">{{ $t('votes.result') }}</label>
      <select id="v-result" v-model="f.result" @change="apply">
        <option value="">{{ $t('votes.all') }}</option>
        <option v-for="r in results" :key="r" :value="r">{{ r }}</option>
      </select>
    </div>
  </form>

  <StateBlock
    :loading="loading" :error="error"
    :empty="!!data && data.votes.length === 0" :empty-text="$t('votes.noResults')"
    @retry="load"
  >
    <div v-if="data">
      <p class="muted small">{{ data.total }} {{ $t('votes.count') }}</p>
      <ul class="votelist">
        <li v-for="v in data.votes" :key="v.id" class="card pad votecard">
          <div class="vhead">
            <router-link :to="{ name: 'vote', params: { id: v.id } }" class="vdate">{{ formatDateTime(v.vote_datetime) }}</router-link>
            <span class="badge" :class="{ ok: v.result === $t('votes.accepted') }">{{ v.result }}</span>
            <span v-if="v.has_per_mp" class="badge rollcall">{{ $t('votes.rollCall') }}</span>
          </div>
          <router-link :to="{ name: 'vote', params: { id: v.id } }" class="vsubject">{{ v.subject }}</router-link>
          <div v-if="v.subjects.length" class="vbills small">
            <span class="muted">{{ $t('votes.decidedBills') }}:</span>
            <span v-for="(s, i) in v.subjects" :key="i" class="vbill">
              <router-link v-if="s.bill_id" :to="{ name: 'bill', params: { id: s.bill_id } }">
                <strong>{{ s.bill_number }}</strong><template v-if="s.title"> — {{ s.title }}</template>
              </router-link>
              <span v-else><strong>{{ s.bill_number }}</strong><template v-if="s.title"> — {{ s.title }}</template></span>
            </span>
          </div>
          <div class="vbar" role="img"
               :aria-label="`${$t('votes.yes')} ${v.yes ?? 0}, ${$t('votes.no')} ${v.no ?? 0}, ${$t('votes.abstain')} ${v.abstain ?? 0}`">
            <span class="seg yes" :style="{ width: voteParts(v).yes + '%' }"></span>
            <span class="seg no" :style="{ width: voteParts(v).no + '%' }"></span>
            <span class="seg abstain" :style="{ width: voteParts(v).abstain + '%' }"></span>
          </div>
          <div class="vcounts small">
            <span class="c yes"><b>{{ v.yes ?? 0 }}</b> {{ $t('votes.yes') }}</span>
            <span class="c no"><b>{{ v.no ?? 0 }}</b> {{ $t('votes.no') }}</span>
            <span class="c abstain"><b>{{ v.abstain ?? 0 }}</b> {{ $t('votes.abstain') }}</span>
          </div>
        </li>
      </ul>
      <Pagination :page="page" :total-pages="totalPages" @goto="gotoPage" />
    </div>
  </StateBlock>
</template>

<style scoped>
.toolbar { display: grid; grid-template-columns: 2fr 1.4fr; gap: .8rem; align-items: end; margin: 1rem 0; }
.votelist { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .6rem; }
.votecard { display: flex; flex-direction: column; gap: .45rem; }
.vhead { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; }
.vdate { font-weight: 800; color: var(--accent); font-variant-numeric: tabular-nums; }
.vsubject { color: var(--ink); font-weight: 600; }
.vsubject:hover { color: var(--accent); }
.badge.ok { background: #eef6ee; color: #2e7d32; }
.badge.rollcall { background: var(--accent-soft); color: var(--accent); }
.vbills { display: flex; flex-direction: column; gap: .2rem; }
.vbills .vbill strong { font-variant-numeric: tabular-nums; }
.vbar { display: flex; height: .7rem; border-radius: 999px; overflow: hidden; background: var(--line); box-shadow: inset 0 0 0 1px rgba(0,0,0,.04); }
.vbar .seg.yes { background: #2e7d32; }
.vbar .seg.no { background: #c62828; }
.vbar .seg.abstain { background: #b9b6ad; }
.vcounts { display: flex; gap: 1.2rem; }
.vcounts .c b { font-variant-numeric: tabular-nums; }
.vcounts .c.yes { color: #2e7d32; }
.vcounts .c.no { color: #c62828; }
.vcounts .c.abstain { color: var(--ink-faint); }
@media (max-width: 700px) { .toolbar { grid-template-columns: 1fr; } }
</style>
