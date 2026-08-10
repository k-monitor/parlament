<script setup>
// Browsable, filterable vote list (Votes module, §7). Filter/sort state lives in
// the URL so a filtered list is shareable. Each vote links to its detail page
// (the per-MP roll call) and each decided bill links to the Bills module (EXT-2).
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api.js'
import { store, loadMeta } from '../../store.js'
import { formatDateTime } from '../../format.js'
import { createSearchClicks } from '../../lib/searchClicks.js'
import StateBlock from '../../components/StateBlock.vue'
import Pagination from '../../components/Pagination.vue'

const route = useRoute()
const router = useRouter()

const PAGE = 50
const SORTS = ['date_desc', 'date_asc', 'attendance_desc', 'attendance_asc',
               'crossvoting_desc', 'crossvoting_asc']
// How many defecting factions a card names before collapsing the rest into "+N".
const CROSS_FACTIONS_SHOWN = 4

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

// Chip colour for an MP's own cast value (matches the roll-call palette); a
// "nem volt jelen" vote has no record, so it falls through to the neutral class.
const VOTE_CLASS = { yes: 'yes', no: 'no', abstain: 'abstain', novote: 'novote', absent: 'absent' }

// Attendance (votes cast / seats held) as served by the API — shown on every card
// so the attendance orderings are readable, and null on a vote with no
// per-faction breakdown (a list or show-of-hands vote).
function attendancePct(v) {
  return v.attendance == null ? '' : Math.round(v.attendance * 100) + '%'
}

// Cross-voting: the MPs who broke their own faction's line, as served by the API.
// Null on a vote with no per-faction breakdown and on a presence check (where the
// figure would not mean dissent), so the chip simply doesn't appear there.
function crossPct(v) {
  if (v.defector_share == null) return ''
  return (100 * v.defector_share).toLocaleString('hu-HU',
    { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + '%'
}
function crossFactions(v) {
  return (v.defector_factions || []).slice(0, CROSS_FACTIONS_SHOWN)
}
function crossFactionsMore(v) {
  return Math.max(0, (v.defector_factions || []).length - CROSS_FACTIONS_SHOWN)
}

function voteParts(v) {
  const yes = v.yes || 0, no = v.no || 0, abstain = v.abstain || 0
  const total = yes + no + abstain || 1
  return { yes: (100 * yes) / total, no: (100 * no) / total, abstain: (100 * abstain) / total }
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
        <li v-for="(v, i) in data.votes" :key="v.id" class="card pad votecard">
          <div class="vhead">
            <router-link :to="{ name: 'vote', params: { id: v.id } }" class="vdate" @click="clicks.hit(i)">{{ formatDateTime(v.vote_datetime) }}</router-link>
            <span class="badge" :class="{ ok: v.result === $t('votes.accepted') }">{{ v.result }}</span>
            <span v-if="v.has_per_mp" class="badge rollcall">{{ $t('votes.rollCall') }}</span>
            <!-- How this MP voted (only in the person-scoped list). -->
            <span v-if="person" class="mpvote" :class="VOTE_CLASS[v.person_value_code] || 'notpresent'">
              {{ v.person_value_code ? $t('votes.' + v.person_value_code) : $t('profile.vbNotPresent') }}
            </span>
          </div>
          <router-link :to="{ name: 'vote', params: { id: v.id } }" class="vsubject" @click="clicks.hit(i)">{{ v.subject }}</router-link>
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
            <span v-if="v.attendance != null" class="c att"
                  :title="$t('votes.attendanceTitle', { present: v.present, seats: v.seats })">
              {{ $t('votes.attendance') }} <b>{{ attendancePct(v) }}</b>
            </span>
            <span v-if="v.defectors" class="c cross"
                  :title="$t('votes.crossVotingTitle', { n: v.defectors, pct: crossPct(v) })">
              <b>{{ v.defectors }}</b> {{ $t('votes.crossVoting') }} ({{ crossPct(v) }})
            </span>
          </div>
          <!-- Which factions actually split — the cross-voting number broken down,
               so the ranking is readable without opening the vote. -->
          <div v-if="v.defectors" class="vcross small muted">
            <span v-for="fx in crossFactions(v)" :key="fx.name" class="xf">
              <span class="dot" :style="{ background: fx.color || '#bbb' }" aria-hidden="true"></span>
              {{ fx.name }} <b>{{ fx.defectors }}</b>
            </span>
            <span v-if="crossFactionsMore(v)" class="xf">+{{ crossFactionsMore(v) }}</span>
          </div>
        </li>
      </ul>
      <Pagination :page="page" :total-pages="totalPages" @goto="gotoPage" />
    </div>
  </StateBlock>
</template>

<style scoped>
/* .filters, .filter-grid, .results-head, .sortctl are global (styles.css). */
.personscope { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; margin: 1rem 0 .2rem; }
.personscope .ps-title { margin: 0; font-weight: 600; }
.personscope .ps-seg { color: var(--accent); }
/* the MP's own cast value on each card, in the roll-call palette */
.mpvote { font-size: .72rem; font-weight: 700; line-height: 1.6; padding: 0 .5rem; border-radius: 999px; color: #fff; white-space: nowrap; }
.mpvote.yes { background: #2e7d32; } .mpvote.no { background: #c62828; }
.mpvote.abstain { background: #8a8780; } .mpvote.novote { background: #c79a2e; }
.mpvote.absent { background: #7c8288; } .mpvote.notpresent { background: #3f434a; }
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
/* attendance and cross-voting sit apart from the yes/no/abstain triplet — they are
   derived measures, not tallies */
.vcounts .c.att { color: var(--ink-faint); margin-left: auto; cursor: help; }
/* cross-voting always follows attendance (a vote only has one if it has the
   other), so it rides that item's margin-left:auto to the right edge */
.vcounts .c.cross { color: var(--ink-faint); cursor: help; }
/* the factions behind the cross-voting number — right-aligned so they sit under
   it rather than under the yes/no/abstain tallies */
.vcross { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: .15rem .8rem; }
.vcross .xf { display: inline-flex; align-items: center; gap: .3rem; white-space: nowrap; }
.vcross .dot { width: .5rem; height: .5rem; border-radius: 50%; flex: none; display: inline-block; }
</style>
