<script setup>
// Browsable, filterable vote list (Votes module, §7). Filter/sort state lives in
// the URL so a filtered list is shareable. Each vote links to its detail page
// (the per-MP roll call) and each decided bill links to the Bills module (EXT-2).
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { store, loadMeta, currentCycleLabel } from '../../store.js'
import { formatDateTime } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import Pagination from '../../components/Pagination.vue'
import CohesionPanel from './CohesionPanel.vue'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const PAGE = 50
const SORTS = ['date_desc', 'date_asc']

const data = ref(null)
const loading = ref(false)
const error = ref(false)
const results = ref([])
const votingModes = ref([])
// The MP's name for the person-scope header; kept as its own ref so the header
// survives an empty result set (when StateBlock replaces the list) and a load.
const personName = ref('')

// The dynamic cohesion/agreement panel (VOTE-8): a house-wide aggregate over the
// filtered set, shown between the filter and the list — never in the per-MP scope.
const cohesion = ref(null)
const scopeLabel = computed(() => {
  const c = currentCycleLabel()
  return c ? t('cycle.scope', { cycle: c }) : t('cycle.scopeAll')
})

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

// Chip colour for an MP's own cast value (matches the roll-call palette); a
// "nem volt jelen" vote has no record, so it falls through to the neutral class.
const VOTE_CLASS = { yes: 'yes', no: 'no', abstain: 'abstain', novote: 'novote', absent: 'absent' }

function voteParts(v) {
  const yes = v.yes || 0, no = v.no || 0, abstain = v.abstain || 0
  const total = yes + no + abstain || 1
  return { yes: (100 * yes) / total, no: (100 * no) / total, abstain: (100 * abstain) / total }
}

async function loadFacets() {
  try {
    const facets = await api.voteFacets({ period: store.cycle })
    results.value = facets.results
    votingModes.value = facets.voting_modes || []
  } catch { results.value = []; votingModes.value = [] }
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
      voting_mode: route.query.voting_mode,
      date_from: route.query.date_from, date_to: route.query.date_to,
      sort: route.query.sort,
      bill: route.query.bill,
      person: route.query.person, value: route.query.value,
      limit: PAGE, offset: route.query.offset || 0,
    })
    if (seq === loadSeq) {
      data.value = res
      personName.value = res.person ? res.person.label : ''
    }
  } catch {
    if (seq === loadSeq) error.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

// The cohesion panel is house-wide, so it ignores the per-MP scope (and isn't
// fetched there). It honours the same shared filters as the list and is computed
// over the whole filtered set by the backend (not one page), like the list's
// companion aggregates elsewhere.
let cohesionSeq = 0
async function loadCohesion() {
  if (person.value) { cohesion.value = null; return }
  const seq = ++cohesionSeq
  try {
    const res = await api.voteCohesion({
      q: route.query.q, result: route.query.result, period: store.cycle,
      voting_mode: route.query.voting_mode,
      date_from: route.query.date_from, date_to: route.query.date_to,
      bill: route.query.bill,
    })
    if (seq === cohesionSeq) cohesion.value = res
  } catch {
    if (seq === cohesionSeq) cohesion.value = null
  }
}

onMounted(() => { loadMeta().catch(() => {}).finally(() => { loadFacets(); load(); loadCohesion() }) })
watch(() => route.query, (q) => {
  f.q = q.q || ''; f.result = q.result || ''
  f.voting_mode = q.voting_mode || ''
  f.date_from = q.date_from || ''; f.date_to = q.date_to || ''
  sort.value = SORTS.includes(q.sort) ? q.sort : 'date_desc'
  loadFacets(); load(); loadCohesion()
})
// Re-fetch when the global cycle changes.
watch(() => store.cycle, () => { loadFacets(); load(); loadCohesion() })
let searchTimer = null
function onSearchInput() { clearTimeout(searchTimer); searchTimer = setTimeout(apply, 300) }
// The debounce survives the component: clear it, or typing then clicking a
// vote within 300 ms yanks the user back to the list (apply() router.push).
onUnmounted(() => clearTimeout(searchTimer))
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
    <button class="btn secondary small clearbtn" type="button" :disabled="!hasFilters" @click="clearFilters">
      {{ $t('votes.clearFilters') }}
    </button>
  </form>

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

  <!-- Dynamic vote-analysis (VOTE-8): faction cohesion & inter-faction agreement
       over the current filtered set. House-wide, so hidden in the per-MP scope
       and when there aren't at least two factions to compare. -->
  <CohesionPanel
    v-if="!person && cohesion && cohesion.factions.length >= 2"
    :data="cohesion" :scope-label="scopeLabel"
  />

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
          </select>
        </label>
      </div>
      <ul class="votelist">
        <li v-for="v in data.votes" :key="v.id" class="card pad votecard">
          <div class="vhead">
            <router-link :to="{ name: 'vote', params: { id: v.id } }" class="vdate">{{ formatDateTime(v.vote_datetime) }}</router-link>
            <span class="badge" :class="{ ok: v.result === $t('votes.accepted') }">{{ v.result }}</span>
            <span v-if="v.has_per_mp" class="badge rollcall">{{ $t('votes.rollCall') }}</span>
            <!-- How this MP voted (only in the person-scoped list). -->
            <span v-if="person" class="mpvote" :class="VOTE_CLASS[v.person_value_code] || 'notpresent'">
              {{ v.person_value_code ? $t('votes.' + v.person_value_code) : $t('profile.vbNotPresent') }}
            </span>
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
.toolbar { display: grid; grid-template-columns: 2fr 1.3fr 1.3fr 1fr 1fr; gap: .8rem; align-items: end; margin: 1rem 0; }
.clearbtn { justify-self: start; align-self: end; }
.personscope { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; margin: 1rem 0 .2rem; }
.personscope .ps-title { margin: 0; font-weight: 600; }
.personscope .ps-seg { color: var(--accent); }
/* the MP's own cast value on each card, in the roll-call palette */
.mpvote { font-size: .72rem; font-weight: 700; line-height: 1.6; padding: 0 .5rem; border-radius: 999px; color: #fff; white-space: nowrap; }
.mpvote.yes { background: #2e7d32; } .mpvote.no { background: #c62828; }
.mpvote.abstain { background: #8a8780; } .mpvote.novote { background: #c79a2e; }
.mpvote.absent { background: #7c8288; } .mpvote.notpresent { background: #3f434a; }
.results-head { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: .5rem; margin: .2rem 0 .6rem; }
.sortctl { display: inline-flex; align-items: center; gap: .4rem; }
.sortctl select { width: auto; }
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
@media (max-width: 900px) { .toolbar { grid-template-columns: 1fr 1fr; } }
@media (max-width: 560px) { .toolbar { grid-template-columns: 1fr; } }
</style>
