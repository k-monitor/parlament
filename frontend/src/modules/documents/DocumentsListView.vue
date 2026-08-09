<script setup>
// Browsable, filterable list of the cycle's *other* irományok — every document
// type except törvényjavaslat (bills), which has its own page. Shares the Bills
// module's data layer (`/api/v1/bills` with `main_type_not=T`) and detail view;
// only the browse page is distinct. Filter/sort state lives in the URL so a
// filtered list is shareable, and each MP submitter links to their profile
// (EXT-2).
//
// One exception to "every type except T": with a `sponsor` in the URL the page
// is one MP's complete iromány list (linked from the "Benyújtott önálló
// indítványok" profile stat, which counts every type), so törvényjavaslatok are
// included — the type exclusion is what splits the *browse* pages, not what a
// per-submitter list should hide.
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api.js'
import { store, loadMeta } from '../../store.js'
import { formatDate } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import MultiSelect from '../../components/MultiSelect.vue'
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

// Type and status hold several values at once (a reader after "questions" wants
// kérdés *and* interpelláció), so they travel as repeated query params — which
// vue-router hands back as a string when there is one and an array beyond that.
function toArray(v) {
  if (v === undefined || v === null || v === '') return []
  return (Array.isArray(v) ? v : [v]).filter((x) => x !== null && x !== '')
}

const f = reactive({
  q: route.query.q || '',
  type: toArray(route.query.type),
  status: toArray(route.query.status),
  // Did the MP who asked accept the answer they got? (interpellációk only)
  verdict: route.query.verdict || '',
  sort: route.query.sort || 'number',
})
// Open the filter panel on load when a filter is already active (e.g. a shared
// or deep-linked list), so its filters are visible rather than hidden.
const showFilters = ref(!!(f.type.length || f.status.length || route.query.verdict))

function clearFilters() {
  f.type = []
  f.status = []
  f.verdict = ''
  apply()
}

// `sponsor` is not an interactive filter — it arrives as a deep link scoped to
// one MP (from their profile). It's carried in the URL and preserved across the
// other filters.
const sponsor = computed(() => route.query.sponsor || '')
// The submitter's display name, normally derived from the returned irományok;
// when the list comes back empty there is nothing to derive it from, so fall
// back to the person's own label rather than showing a raw person id.
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
  if (f.type.length) query.type = f.type
  if (f.status.length) query.status = f.status
  if (f.verdict) query.verdict = f.verdict
  if (f.sort && f.sort !== 'number') query.sort = f.sort
  if (sponsor.value) query.sponsor = sponsor.value
  // Changing a filter resets to the first page (offset is intentionally dropped).
  router.push({ name: 'documents', query })
}

function gotoPage(p) {
  router.push({ name: 'documents', query: { ...route.query, offset: p * PAGE } })
}

async function loadFacets() {
  try {
    const r = await api.billFacets({
      period: store.cycles, main_type_not: mainTypeNot.value,
      sponsor: sponsor.value || undefined,
    })
    // One type name can appear under two fotipusok, so the same string comes
    // back twice — dedupe, or the picker lists it twice.
    types.value = [...new Set(r.types.map((t) => t.type).filter(Boolean))]
    statuses.value = r.statuses
  } catch { types.value = []; statuses.value = [] }
}

// Bills are excluded on the browse page (they have their own), but not from a
// single submitter's list — see the note at the top of the file.
const mainTypeNot = computed(() => (sponsor.value ? undefined : 'T'))

// Monotonic load id: overlapping fetches (filter watcher + cycle watcher) can
// resolve out of order; only the latest may write state.
let loadSeq = 0

async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false
  try {
    // `period` comes from the global cycle chooser (store.cycles; empty = all).
    const res = await api.bills({
      q: route.query.q,
      type: toArray(route.query.type), status: toArray(route.query.status),
      period: store.cycles, sort: route.query.sort || 'number',
      sponsor: route.query.sponsor, main_type_not: mainTypeNot.value,
      answer_verdict: route.query.verdict || undefined,
      limit: PAGE, offset: route.query.offset || 0,
    })
    if (seq === loadSeq) data.value = res
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
  f.q = q.q || ''; f.type = toArray(q.type); f.status = toArray(q.status)
  f.verdict = q.verdict || ''; f.sort = q.sort || 'number'
  if (q.sponsor !== (prev && prev.sponsor)) loadSponsorLabel()
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
  <h1>{{ sponsor ? $t('documents.sponsorTitle') : $t('documents.title') }}</h1>
  <p class="muted">{{ sponsor ? $t('documents.sponsorSubtitle') : $t('documents.subtitle') }}</p>

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
        <!-- Type and status take several values at once — see `f` above. -->
        <div>
          <label for="d-type">{{ $t('documents.type') }}</label>
          <MultiSelect
            id="d-type" v-model="f.type" :options="types"
            :label="$t('documents.type')" @change="apply"
          />
        </div>
        <div>
          <label for="d-status">{{ $t('documents.status') }}</label>
          <MultiSelect
            id="d-status" v-model="f.status" :options="statuses"
            :label="$t('documents.status')" @change="apply"
          />
        </div>
        <!-- Only question-type irományok record the asking MP's verdict on the
             answer, so this narrows the list to interpellációk by itself. -->
        <div>
          <label for="d-verdict">{{ $t('documents.verdict') }}</label>
          <select id="d-verdict" v-model="f.verdict" @change="apply">
            <option value="">{{ $t('search.all') }}</option>
            <option value="accepted">{{ $t('documents.verdictAccepted') }}</option>
            <option value="rejected">{{ $t('documents.verdictRejected') }}</option>
          </select>
          <p class="muted small hint">{{ $t('documents.verdictHint') }}</p>
        </div>
      </div>
      <button class="btn secondary small" type="button" style="margin-top:.6rem;" @click="clearFilters">
        {{ $t('search.clearFilters') }}
      </button>
    </fieldset>
  </form>

  <p v-if="sponsor" class="card pad sponsorfilter">
    <span>{{ $t('documents.bySponsor') }}: <strong>{{ sponsorName }}</strong></span>
    <router-link :to="{ name: 'profile', params: { id: sponsor } }" class="small">{{ $t('documents.viewProfile') }}</router-link>
    <router-link :to="{ name: 'documents' }" class="small clear">✕ {{ $t('documents.clearSponsor') }}</router-link>
  </p>

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
.hint { margin: .25rem 0 0; }
.sponsors { display: flex; gap: .4rem; flex-wrap: wrap; align-items: center; }
.sponsors .arrow { color: var(--ink-soft); }
/* Submitter-scoped list: the active filter, its profile link and a way out. */
.sponsorfilter { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; margin-bottom: 1rem; background: var(--accent-soft); }
.sponsorfilter .clear { margin-left: auto; }
.badge.status { background: var(--accent-soft); }
</style>
