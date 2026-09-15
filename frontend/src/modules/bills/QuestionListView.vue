<script setup>
// Browsable, filterable list of the question-type irományok — kérdés, írásbeli
// kérdés, interpelláció and azonnali kérdés (Felicitas `main_type` A/I/K)
// — the middle tab of the Törvényjavaslatok section (BILL-13).
//
// It exists because these are ~90% of everything that is not a
// törvényjavaslat: on the all-irományok list (BILL-9) they bury every other
// type, and the filters that matter for a question — was it answered, from the
// floor or on paper, by which tárca, and did the asker accept the reply — have
// nowhere to live there.
//
// It adds no data layer of its own: `/api/v1/bills` scoped with
// `main_type_in=A,I,K`, the same way the bills page scopes itself with
// `main_type=T`, and the same detail view. An opened question is a
// `/documents/:id` — the canonical detail address of every
// non-törvényjavaslat (og.py) — so the URL a reader shares from here is the one
// the sitemap and the share card already name.
//
// Filter/sort state lives in the URL, so a filtered list is shareable, and each
// MP asker and each responder links to their profile (EXT-2).
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { store, loadMeta } from '../../store.js'
import { formatDate } from '../../format.js'
import { createSearchClicks } from '../../lib/searchClicks.js'
import StateBlock from '../../components/StateBlock.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import TopicBadge from '../../components/TopicBadge.vue'
import MultiSelect from '../../components/MultiSelect.vue'
import Pagination from '../../components/Pagination.vue'
import { topicGlyph, topicName } from '../../lib/topics.js'
import { ANALYSIS_BY_ROUTE, analysisAvailable } from '../analyses/registry.js'

const route = useRoute()
const router = useRouter()
// `t` below is the search-debounce timer, so the i18n helpers are named.
const { t: tr, te: hasTr } = useI18n()

const PAGE = 50

// What makes an iromány a question. The same three fotipusok the Kérdések
// Sankey classifies (`_QUESTION_TYPES` in the bills router), sent as the
// endpoint's `main_type_in` — this list and that analysis must always be
// looking at the same set of documents.
const QUESTION_TYPES = 'A,I,K'

const data = ref(null)
const loading = ref(false)
const error = ref(false)
const types = ref([])       // the four question categories present in this slice
const statuses = ref([])
// CAP policy topics present in this slice, with their counts (TOPIC-8) —
// facet-driven, so a cycle that used nine of the model's 21 offers nine.
const topics = ref([])
// The tárcák that have answered a question (§6C), for the responder filter.
const portfolios = ref([])

const page = computed(() => Math.floor((Number(route.query.offset) || 0) / PAGE))
const totalPages = computed(() => (data.value ? Math.ceil(data.value.total / PAGE) : 0))

// Type, status and topic hold several values at once, so they travel as repeated
// query params — which vue-router hands back as a string when there is one and
// an array beyond that.
function toArray(v) {
  if (v === undefined || v === null || v === '') return []
  return (Array.isArray(v) ? v : [v]).filter((x) => x !== null && x !== '')
}

const f = reactive({
  q: route.query.q || '',
  type: toArray(route.query.type),
  status: toArray(route.query.status),
  topic: toArray(route.query.topic),
  // Whether and how the question was answered — the one thing a reader wants to
  // know about a question that the iromány's own status does not say.
  answer: route.query.answer || '',
  // Which tárca answered it (MIN-7). Resolved through the derived portfolio link
  // table, so this filter and a tárca's own page can never disagree.
  responder: route.query.responder || '',
  // Did the MP who asked accept the answer they got? (interpellációk only)
  verdict: route.query.verdict || '',
  sort: route.query.sort || 'number',
})
// Open the filter panel on load when a filter is already active (e.g. a shared
// or deep-linked list), so its filters are visible rather than hidden.
const showFilters = ref(!!(f.type.length || f.status.length || f.topic.length
  || f.answer || f.responder || f.verdict))

function clearFilters() {
  f.type = []
  f.status = []
  f.topic = []
  f.answer = ''
  f.responder = ''
  f.verdict = ''
  apply()
}

function apply() {
  const query = {}
  if (f.q) query.q = f.q
  if (f.type.length) query.type = f.type
  if (f.status.length) query.status = f.status
  if (f.topic.length) query.topic = f.topic
  if (f.answer) query.answer = f.answer
  if (f.responder) query.responder = f.responder
  if (f.verdict) query.verdict = f.verdict
  if (f.sort && f.sort !== 'number') query.sort = f.sort
  // Changing a filter resets to the first page (offset is intentionally dropped).
  router.push({ name: 'questionList', query })
}

function gotoPage(p) {
  router.push({ name: 'questionList', query: { ...route.query, offset: p * PAGE } })
}

// Facet topics as picker rows: the glyph and Hungarian name the chip uses, plus
// the hit count, which is what tells a reader which topics are worth opening.
// Empty — so the control does not render at all — on a DB with no topics.
const topicOptions = computed(() => topics.value.map((x) => ({
  value: x.label,
  label: `${topicGlyph(x.label)} ${topicName(x.label, tr, hasTr)} (${x.count})`,
})))

// Only tárcák that actually answered something in scope: a responder filter
// listing a ministry with no answers to its name is a dead end dressed as a
// choice, the same rule the topic facet follows.
const responderOptions = computed(() =>
  portfolios.value.filter((p) => p.answered > 0)
    .map((p) => ({ value: p.slug, label: `${p.name} (${p.answered})` })))

async function loadFacets() {
  try {
    const r = await api.billFacets({
      period: store.cycles, main_type_in: QUESTION_TYPES,
    })
    // One type name can appear under two fotipusok, so the same string can come
    // back twice — dedupe, or the picker lists it twice.
    types.value = [...new Set(r.types.map((t) => t.type).filter(Boolean))]
    statuses.value = r.statuses
    topics.value = r.topics || []
  } catch { types.value = []; statuses.value = []; topics.value = [] }
}

// The responder filter needs the §6C tárca table, which is its own module and
// can be switched off (EXT-6) or absent from an older DB (`built: false`) — in
// which case the list stays empty and the control does not render, rather than
// offering a filter that would match nothing.
async function loadPortfolios() {
  if (!store.moduleEnabled('portfolios')) { portfolios.value = []; return }
  try {
    const r = await api.portfolios({ period: store.cycles })
    portfolios.value = r.built ? (r.portfolios || []) : []
  } catch { portfolios.value = [] }
}

// Monotonic load id: overlapping fetches (filter watcher + cycle watcher) can
// resolve out of order; only the latest may write state.
let loadSeq = 0

// Anonymous search-quality signal (SEA-12): which result the reader opens after
// a keyword search, and how far down the list it sat. The backend counts this
// page under the same `bills` source as the other two iromány lists — the three
// are one endpoint, told apart by their fotipus filters.
const clicks = createSearchClicks('bills')

async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false
  const offset = Number(route.query.offset) || 0
  // `period` comes from the global cycle chooser (store.cycles; empty = all).
  const args = {
    q: route.query.q,
    type: toArray(route.query.type), status: toArray(route.query.status),
    topic: toArray(route.query.topic),
    period: store.cycles, sort: route.query.sort || 'number',
    main_type_in: QUESTION_TYPES,
    answer_state: route.query.answer || undefined,
    portfolio: route.query.responder || undefined,
    // The tárca link, scoped to the answering role: "which questions did this
    // ministry reply to", not the ones the government submitted through it. Left
    // unscoped by `portfolio_period` on purpose — the cycle a reader chose scopes
    // the *questions* here (b.period_number), not the tárca's own activity.
    portfolio_role: route.query.responder ? 'answered' : undefined,
    answer_verdict: route.query.verdict || undefined,
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

// The Kérdések *Sankey* (BILL-11) answers the question this list raises next —
// where do these flow, from whose faction to which tárca. Linked only while that
// page can actually be opened (its module mounted), for which the Elemzések
// registry is the one authority.
const sankeyAvailable = computed(() => {
  // Guarded on the entry existing at all, not just on its module: if the Sankey
  // is ever dropped from the registry this link should quietly go with it rather
  // than take the whole list page down with a missing `module`.
  const sankey = ANALYSIS_BY_ROUTE.questions
  return !!sankey && analysisAvailable(sankey)
})

onMounted(() => {
  loadMeta().catch(() => {}).finally(() => { loadFacets(); loadPortfolios(); load() })
})
watch(() => route.query, () => {
  f.q = route.query.q || ''
  f.type = toArray(route.query.type); f.status = toArray(route.query.status)
  f.topic = toArray(route.query.topic)
  f.answer = route.query.answer || ''
  f.responder = route.query.responder || ''
  f.verdict = route.query.verdict || ''
  f.sort = route.query.sort || 'number'
  loadFacets(); load()
})
// Re-fetch when the global cycle changes.
watch(() => store.cycles.join(','), () => { loadFacets(); loadPortfolios(); load() })
let t = null
function onSearchInput() { clearTimeout(t); t = setTimeout(apply, 300) }
// The debounce survives the component: clear it, or typing then clicking a
// question within 300 ms yanks the user back to the list (apply() router.push).
onUnmounted(() => clearTimeout(t))
</script>

<template>
  <h1>{{ $t('questionList.title') }}</h1>
  <p class="muted">{{ $t('questionList.subtitle') }}</p>

  <form class="card pad searchform" role="search" @submit.prevent="apply">
    <div class="row" style="gap:.5rem;">
      <input
        id="ql-q" type="search" v-model="f.q" :placeholder="$t('questionList.searchPlaceholder')"
        :aria-label="$t('questionList.searchPlaceholder')" @input="onSearchInput"
        style="flex:1;min-width:200px;"
      />
      <button class="btn secondary" type="button" :aria-expanded="showFilters" @click="showFilters = !showFilters">
        {{ $t('search.filters') }}
      </button>
    </div>

    <fieldset v-show="showFilters" class="filters">
      <legend class="visually-hidden">{{ $t('search.filters') }}</legend>
      <div class="filter-grid">
        <!-- The four question categories — see `f` above for why they are
             multi-valued. -->
        <div>
          <label for="ql-type">{{ $t('questionList.type') }}</label>
          <MultiSelect
            id="ql-type" v-model="f.type" :options="types"
            :label="$t('questionList.type')" @change="apply"
          />
        </div>
        <!-- Whether and how it was answered. Derived from the answer events, so
             "megválaszolatlan" means no answer of either kind is on record —
             which for a recent question may simply mean not yet. -->
        <div>
          <label for="ql-answer">{{ $t('questionList.answer') }}</label>
          <select id="ql-answer" v-model="f.answer" @change="apply">
            <option value="">{{ $t('search.all') }}</option>
            <option value="answered">{{ $t('questionList.answerAnswered') }}</option>
            <option value="oral">{{ $t('questionList.answerOral') }}</option>
            <option value="written">{{ $t('questionList.answerWritten') }}</option>
            <option value="unanswered">{{ $t('questionList.answerUnanswered') }}</option>
          </select>
          <p class="muted small hint">{{ $t('questionList.answerHint') }}</p>
        </div>
        <!-- Which tárca replied (MIN-7). Absent where the §6C table is not in
             this deployment — see `loadPortfolios`. -->
        <div v-if="responderOptions.length">
          <label for="ql-responder">{{ $t('questionList.responder') }}</label>
          <select id="ql-responder" v-model="f.responder" @change="apply">
            <option value="">{{ $t('search.all') }}</option>
            <option v-for="o in responderOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
        </div>
        <!-- Only question-type irományok record the asking MP's verdict on the
             answer, and in practice only interpellációk carry it. -->
        <div>
          <label for="ql-verdict">{{ $t('questionList.verdict') }}</label>
          <select id="ql-verdict" v-model="f.verdict" @change="apply">
            <option value="">{{ $t('search.all') }}</option>
            <option value="accepted">{{ $t('questionList.verdictAccepted') }}</option>
            <option value="rejected">{{ $t('questionList.verdictRejected') }}</option>
          </select>
          <p class="muted small hint">{{ $t('questionList.verdictHint') }}</p>
        </div>
        <div>
          <label for="ql-status">{{ $t('questionList.status') }}</label>
          <MultiSelect
            id="ql-status" v-model="f.status" :options="statuses"
            :label="$t('questionList.status')" @change="apply"
          />
        </div>
        <!-- The machine-read policy topic (TOPIC-8). Selecting one returns
             exactly the questions whose chip shows it — the filter resolves the
             dominant topic by the same rule, at the same confidence threshold. -->
        <div v-if="topicOptions.length">
          <label for="ql-topic">{{ $t('questionList.topic') }}</label>
          <MultiSelect
            id="ql-topic" v-model="f.topic" :options="topicOptions"
            :label="$t('questionList.topic')" @change="apply"
          />
        </div>
      </div>
      <button class="btn secondary small" type="button" style="margin-top:.6rem;" @click="clearFilters">
        {{ $t('search.clearFilters') }}
      </button>
    </fieldset>
  </form>

  <!-- The next question this list raises: where do these flow? -->
  <p v-if="sankeyAvailable" class="card pad sankeylink">
    <span class="muted small">{{ $t('questionList.sankeyLead') }}</span>
    <!-- Plain named link: the cycle scope is re-attached by the router guard,
         the same way every other in-site link leaves it to. -->
    <router-link class="small" :to="{ name: 'questions' }">
      {{ $t('questionList.sankeyLink') }} →
    </router-link>
  </p>

  <StateBlock
    :loading="loading" :error="error"
    :empty="!!data && data.bills.length === 0" :empty-text="$t('questionList.noResults')"
    @retry="load"
  >
    <div v-if="data">
      <div class="results-head">
        <p class="muted small" aria-live="polite" style="margin:0;">{{ data.total }} {{ $t('questionList.count') }}</p>
        <label class="sortctl small muted">
          {{ $t('search.sort') }}
          <select v-model="f.sort" @change="apply">
            <option value="number">{{ $t('questionList.sortNumber') }}</option>
            <option value="date">{{ $t('questionList.sortDate') }}</option>
          </select>
        </label>
      </div>
      <ul class="billlist">
        <li v-for="(b, i) in data.bills" :key="b.id" class="card pad billcard">
          <div class="billhead">
            <router-link :to="{ name: 'document', params: { id: b.id } }" class="billnum" @click="clicks.hit(i)">{{ b.bill_number }}</router-link>
            <span class="badge" v-if="b.type">{{ b.type }}</span>
            <span class="badge status" v-if="b.status">{{ b.status }}</span>
            <span class="muted small" v-if="b.submitted_date">{{ formatDate(b.submitted_date) }}</span>
            <TopicBadge :topic="b.topic" kind="bill" compact />
          </div>
          <router-link :to="{ name: 'document', params: { id: b.id } }" class="billtitle" @click="clicks.hit(i)">{{ b.title }}</router-link>
          <div class="sponsors small" v-if="b.sponsors.length || b.responder">
            <template v-for="(s, si) in b.sponsors" :key="si">
              <router-link v-if="s.person_id" :to="{ name: 'profile', params: { id: s.person_id } }">{{ s.name }}</router-link>
              <span v-else>{{ s.name }}</span>
              <FactionBadge v-if="s.faction" :faction="s.faction" />
              <span v-if="si < b.sponsors.length - 1" aria-hidden="true">·</span>
            </template>
            <!-- asker → whoever answered it from the floor. A question answered
                 only in writing names a responding tárca upstream but no person,
                 so it has no responder here — the tárca filter above is how that
                 side of the record is reached. -->
            <template v-if="b.responder">
              <span class="arrow" aria-hidden="true">→</span>
              <span class="visually-hidden">{{ $t('questionList.answeredBy') }}:</span>
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
/* .filters, .filter-grid, .results-head, .sortctl are global (styles.css); the
   row styles below are the iromány card's, shared in shape with the other two
   browse pages so the three tabs read as one list in three scopes. */
.billlist { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .6rem; }
.billcard { display: flex; flex-direction: column; gap: .4rem; }
.billhead { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; }
.billnum { font-weight: 800; color: var(--accent); }
.billtitle { color: var(--ink); font-weight: 600; }
.billtitle:hover { color: var(--accent); }
.hint { margin: .25rem 0 0; }
.sponsors { display: flex; gap: .4rem; flex-wrap: wrap; align-items: center; }
.sponsors .arrow { color: var(--ink-soft); }
.badge.status { background: var(--accent-soft); }
/* The pointer to the Sankey: a quiet aside above the list, not a second title. */
.sankeylink { display: flex; gap: .75rem; align-items: baseline; flex-wrap: wrap; margin-bottom: 1rem; }
</style>
