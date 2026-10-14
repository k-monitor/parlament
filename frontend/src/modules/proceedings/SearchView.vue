<script setup>
// The site search (§5.1). Search state lives entirely in the URL query (SEA-6:
// deep-linkable / citable). Editing filters or paging updates the URL, and a
// watcher re-runs the query — so the back button and a pasted link both
// reproduce the exact result set.
//
// It searches in one or several scopes at once (SEA-14): the plenary transcript,
// the committee minutes, the irományok, the votes and the people. One scope is a
// full, paged result list; several are a section each, holding that scope's top
// hits and a way to open all of them. Each scope is answered by its own
// endpoint, translated from this page's filters in lib/searchScopes.js.
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { store, loadMeta, setCycles, currentCycleLabel, periodLabel } from '../../store.js'
import { agendaLabel } from '../../format.js'
import { createSearchClicks } from '../../lib/searchClicks.js'
import {
  FILTERS, SEARCH_SCOPES, SCOPE_BY_KEY, scopesFromQuery, scopeTarget, scopesHonour,
  honours, scopeState, ignoredFilters, sortOptions, listPageTarget, queryList,
} from '../../lib/searchScopes.js'
import { topicGlyph, topicName } from '../../lib/topics.js'
import { useSpeakerSuggest } from '../../lib/speakerSuggest.js'
import { onekoForQuery, dismissOneko } from '../../lib/oneko.js'
import StateBlock from '../../components/StateBlock.vue'
import SpeakerFilter from '../../components/SpeakerFilter.vue'
import SpeakerSuggestList from '../../components/SpeakerSuggestList.vue'
import TrendChart from '../../components/TrendChart.vue'
import BarChart from '../../components/BarChart.vue'
import EmbedButton from '../../components/EmbedButton.vue'
import DonateCard from '../../components/DonateCard.vue'
import SearchScopeSelect from '../../components/SearchScopeSelect.vue'
import MultiSelect from '../../components/MultiSelect.vue'
import SearchResults from './SearchResults.vue'

const route = useRoute()
const router = useRouter()
const { t, te, locale } = useI18n()

// A single scope's page size, and how many hits each section of a search across
// several scopes shows before its "all results" link — people are small grid
// tiles, so two rows of them.
const PAGE = 20
const SECTION_LIMIT = 5
const SECTION_LIMIT_PEOPLE = 6
const AGENDA_TYPES = ['opening', 'procedural', 'regular', 'oath', 'voting',
  'rules_of_procedure', 'questioning_of_the_government', 'qa', 'condolence']

const input = ref(route.query.q || '')
// What the box searches in (SEA-14) — one scope or several.
const scopes = ref(scopesFromQuery(route.query))
const single = computed(() => (scopes.value.length === 1 ? scopes.value[0] : null))
// Which filters to offer: those at least one picked scope can apply. A filter
// set while another scope was picked, and that no picked scope honours, is
// dropped from the URL by submit() rather than kept on silently.
const offers = computed(() => Object.fromEntries(Object.keys(FILTERS)
  .map((group) => [group, scopesHonour(scopes.value, group)])))
// The electoral cycle is a *global* scope set from the header (store.cycles),
// not a per-search filter — so it is no longer part of the URL query here.
const filters = reactive({
  date_from: route.query.date_from || '',
  date_to: route.query.date_to || '',
  faction_id: route.query.faction_id || '',
  agenda_type: route.query.agenda_type || '',
  topic: queryList(route.query.topic),
})
// The speaker filter (SEA-3) is URL state like the others, but it travels as an
// opaque `person_id` while the panel has to show a *name* — so the page keeps the
// resolved person next to the id, rather than the id alone in `filters`.
const speaker = ref(null)
// Open the filter panel on load when a filter is already active (e.g. a shared
// or deep-linked query), so its filters are visible rather than hidden.
const showFilters = ref(!!(filters.date_from || filters.date_to || filters.faction_id
  || filters.agenda_type || filters.topic.length || route.query.person_id))

// The CAP policy topics (TOPIC-2) as picker rows, with the glyph and name their
// chip shows, alphabetical in the reader's language. Every policy topic is
// offered rather than a facet of the current results: here the topic narrows a
// search, as the faction does, and counting each one's hits for every term
// would cost a breakdown-sized aggregate per keystroke. "Other" is never a
// subject (TOPIC-4), so it cannot be filtered on.
const topicOptions = computed(() => {
  const labels = store.meta?.speech_topics?.labels || store.meta?.bill_topics?.labels || []
  return labels.filter((l) => l !== 'Other')
    .map((l) => ({ value: l, name: topicName(l, t, te) }))
    .sort((a, b) => a.name.localeCompare(b.name, locale.value))
    .map((o) => ({ value: o.value, label: `${topicGlyph(o.value)} ${o.name}` }))
})
// The picked topics by name, for the captions that say what a list without a
// keyword is a list of.
const topicNames = computed(() =>
  queryList(route.query.topic).map((l) => topicName(l, t, te)).join(', '))
// Result ordering (SEA-10): relevance (default) | date_desc | date_asc. Kept in
// the URL like the other per-page filters so a sorted view is deep-linkable (CYC-5).
const SORTS = ['relevance', 'date_desc', 'date_asc']
const sort = ref(SORTS.includes(route.query.sort) ? route.query.sort : 'relevance')
// What the sort control offers for the picked scopes, and what it shows: a
// search with nothing to rank by (no term, or only scopes that cannot rank) is
// in date order whatever the URL asks for, and the control says so — without
// writing that back, so picking a scope that does rank still ranks.
const sorts = computed(() => sortOptions(scopes.value, route.query.q))
const shownSort = computed(() => (sorts.value.includes(sort.value) ? sort.value : 'date_desc'))
const SORT_LABELS = {
  relevance: 'search.sortRelevance', date_desc: 'search.sortNewest', date_asc: 'search.sortOldest',
}

// One entry per picked scope, in the picker's order, filled in as each scope's
// endpoint answers — a slow scope never holds the others back.
const sections = ref([])
const trend = ref(null)
const breakdown = ref(null)
const breakdownLoading = ref(false)

const factions = ref([])
const page = computed(() => Math.floor((Number(route.query.offset) || 0) / PAGE))
// `total` arrives already capped by the backend (max_search_total), so page
// straight from it — a second, lower cap here would strand reachable results.
const totalPages = computed(() =>
  (single.value && sections.value[0] ? Math.ceil(sections.value[0].total / PAGE) : 0))

onMounted(async () => {
  document.addEventListener('pointerdown', onDocPointerDown)
  // A deep-linked speaker filter resolves alongside the search, not before it:
  // the results must not wait on a name that is only shown in the panel.
  syncSpeaker(route.query.person_id || '')
  // Ensure the global cycle is initialised before the first search so results
  // are scoped to the selected cycle (not briefly to "all") — and the module
  // list, so a scope this deployment does not run is dropped before it is asked.
  await loadMeta().catch(() => {})
  try { factions.value = (await api.factions()).factions } catch {}
  runFromRoute()
})

// What the page can run: a term, or — with no term — a speaker, whose speeches,
// irományok and votes are then listed, or a topic, whose speeches and irományok
// are (SEA-3). Any other filter needs a term to narrow.
const searchable = computed(() =>
  !!(route.query.q || route.query.person_id || queryList(route.query.topic).length))

// Re-run the active search when the global cycle changes.
watch(() => store.cycles.join(','), () => { if (searchable.value) runFromRoute() })

// Push the current form state into the URL (this triggers the watcher → fetch).
function submit(resetPage = true) {
  clearSuggest()
  // A filter can be set before a term is (picking a speaker from the box below
  // is exactly that), and the backend has no query-less search — so an empty `q`
  // is left out of the URL rather than sent (scopeTarget): the filters stay, and
  // the page waits for the word to search for.
  const query = {}
  for (const [group, params] of Object.entries(FILTERS)) {
    if (group === 'speaker' || !offers.value[group]) continue
    for (const k of params) {
      const v = filters[k]
      if (Array.isArray(v) ? v.length : v) query[k] = v
    }
  }
  if (speaker.value && offers.value.speaker) query.person_id = speaker.value.person_id
  if (sort.value !== 'relevance') query.sort = sort.value
  if (!resetPage && route.query.offset) query.offset = route.query.offset
  router.push(scopeTarget(scopes.value, input.value, query))
}

// Picking scopes searches there at once, as picking a filter does. With nothing
// to search for yet the choice just waits for the term.
function onScopes(keys) {
  scopes.value = keys
  const runnable = input.value.trim() || (speaker.value && scopesHonour(keys, 'speaker'))
    || (filters.topic.length && scopesHonour(keys, 'topic'))
  if (runnable) submit()
}

// Re-order the results: a new ordering always returns to the first page.
function changeSort(value) {
  sort.value = value
  const query = { ...route.query }
  delete query.offset
  if (value === 'relevance') delete query.sort
  else query.sort = value
  router.push({ name: 'search', query })
}

function clearFilters() {
  filters.date_from = filters.date_to = ''
  filters.faction_id = filters.agenda_type = ''
  filters.topic = []
  speaker.value = null
  submit()
}

function gotoPage(p) {
  router.push({ name: 'search', query: { ...route.query, offset: p * PAGE } })
}

// The same search narrowed to one of its scopes — a section's way to all of
// its results. The filters that scope cannot apply are left behind, as a scope
// change on the page leaves them (submit), so the list it opens is not
// qualified by a filter its panel no longer shows.
function onlyScope(key) {
  const query = { ...route.query }
  delete query.in; delete query.offset
  for (const [group, params] of Object.entries(FILTERS)) {
    if (!honours(key, group)) for (const k of params) delete query[k]
  }
  return scopeTarget([key], query.q, query)
}

// Bring `speaker` in line with the `person_id` the URL carries. A filter picked
// on this page arrives with its name already known; one that arrives from a
// shared link, a reload or the back button is a bare id, so it is shown as one
// at once — an active filter must never be invisible — and the name is swapped
// in when the profile answers. A failed lookup costs the name, not the filter.
let speakerSeq = 0
async function syncSpeaker(id) {
  if (!id) { speakerSeq++; speaker.value = null; return }
  if (speaker.value?.person_id === id) return          // already resolved
  speaker.value = { person_id: id, label: id }
  const seq = ++speakerSeq
  try {
    const p = await api.representative(id)
    if (seq === speakerSeq) {
      speaker.value = { person_id: id, label: p.label, photo_uri: p.photo_uri }
    }
  } catch { /* the id still filters correctly; only its name is missing */ }
}

// Search-as-you-type speaker suggestions in the main box (SEA-7). A name typed
// there is almost never a useful search *term* — the transcript carries a
// speaker in its label, not in the sentences the index holds — so what the list
// offers is the speaker FILTER, and picking one moves the name out of the query
// box and into the filter it was meant to be. Not offered when no picked scope
// takes a speaker: in the people scope alone a typed name IS the search.
// No loading state is taken from it: under the main box a spinner would fire on
// every fourth character of an ordinary search term, so the list simply appears
// when there is somebody to show.
const { speakers: suggested, ask: askSuggest, clear: clearSuggest } = useSpeakerSuggest()
const qInput = ref(null)
const qBox = ref(null)
function onInput() {
  if (offers.value.speaker) askSuggest(input.value)
  else clearSuggest()
}

// Picked in the filter panel, or dropped from it — either way the search re-runs
// against the new filter.
function onSpeakerFilter(s) {
  speaker.value = s
  submit()
}

function pickSuggested(s) {
  speaker.value = { person_id: s.person_id, label: s.label, photo_uri: s.photo_uri }
  clearSuggest()
  input.value = ''          // the name is the filter now, not the query
  showFilters.value = true  // …and the filter it became has to be visible
  submit()
  qInput.value?.focus()     // ready for the word they actually came to search
}

// A dropdown is dismissed by clicking away from it, not only by picking from it.
function onDocPointerDown(e) {
  if (qBox.value && !qBox.value.contains(e.target)) clearSuggest()
}

// Monotonic request id: every trigger (query watcher, cycle watcher, retry)
// may overlap in flight, and HTTP responses can complete out of order — only
// the latest request may write state, or a slow older search would overwrite
// a newer one's results (and the trend/breakdown could belong to a different
// query than the list).
let reqSeq = 0
// The filters the current plenary result set was fetched with — reused to lazily
// fetch the breakdown, and the reqSeq that breakdown was last fetched for.
let currentArgs = null
let breakdownLoadedSeq = -1
// Anonymous search-quality signal (SEA-12): which result the reader opens, and
// how far down its list it sat. One tracker per scope, each counted under that
// scope's own source — a section's hits are ranked within the section.
const clicks = Object.fromEntries(SEARCH_SCOPES.map((s) => [s.key, createSearchClicks(s.source)]))

// The page's search as the scope adapters take it (lib/searchScopes.js).
function searchState() {
  return {
    q: route.query.q,
    period: store.cycles, // global cycle scope (empty = all cycles)
    date_from: route.query.date_from,
    date_to: route.query.date_to,
    person_id: route.query.person_id,
    faction_id: route.query.faction_id,
    agenda_type: route.query.agenda_type,
    topic: queryList(route.query.topic),
    sort: route.query.sort,
  }
}

function emptySection(key) {
  return {
    key, loading: true, error: false, tooSlow: false, unavailable: false,
    skipped: false, total: 0, capped: false, results: [], query: '',
  }
}

function sectionLimit(key) {
  if (single.value) return PAGE
  return key === 'people' ? SECTION_LIMIT_PEOPLE : SECTION_LIMIT
}

async function runFromRoute() {
  // The site's one easter egg (SEA-13): a search for cats fetches a cat, and
  // any other search sends it home again. Called before the early return so an
  // emptied query dismisses it too.
  onekoForQuery(route.query.q)
  scopes.value = scopesFromQuery(route.query)
  trend.value = null; breakdown.value = null
  currentArgs = null; breakdownLoadedSeq = -1
  if (!searchable.value) {
    reqSeq++ // orphan any in-flight responses
    sections.value = []
    return
  }
  const seq = ++reqSeq
  const state = searchState()
  const offset = single.value ? Number(route.query.offset) || 0 : 0
  sections.value = scopes.value.map(emptySection)
  // The plenary's two aggregates belong to a plenary search on its own: the
  // over-time popularity chart (SEA-8) is shown by default, so it runs alongside
  // the results (its failure must never break the list). The who-said-it
  // breakdown (SEA-9) is collapsed by default AND is the backend's most expensive
  // query (a top-N over the entire match set), so it is deferred until the user
  // actually opens the panel — most searches never pay for it. If the panel is
  // already open (a re-search with it expanded), refresh it for the new query.
  const plenaryState = scopeState('plenary', state)
  if (single.value === 'plenary' && SCOPE_BY_KEY.plenary.runnable(plenaryState)) {
    const { sort: _ordering, ...filterArgs } = SCOPE_BY_KEY.plenary.args(plenaryState)
    currentArgs = filterArgs
    api.searchTrend(filterArgs)
      .then((tr) => { if (seq === reqSeq) trend.value = tr })
      .catch(() => { if (seq === reqSeq) trend.value = null })
    if (showBreakdown.value) loadBreakdown()
  }
  await Promise.all(sections.value.map((sec) => runSection(sec, seq, state, offset)))
}

// Fetch one scope's hits into its section. A scope with nothing it can search
// by (people without a name) is skipped and says why, rather than listing
// everyone.
async function runSection(sec, seq, state, offset) {
  const scope = SCOPE_BY_KEY[sec.key]
  const own = scopeState(sec.key, state)
  if (!scope.runnable(own)) { sec.skipped = true; sec.loading = false; return }
  const args = scope.args(own)
  sec.loading = true; sec.error = false; sec.tooSlow = false
  try {
    const res = await scope.fetch({ ...args, limit: sectionLimit(sec.key), offset })
    if (seq !== reqSeq) return
    Object.assign(sec, scope.read(res))
    // Armed with the same args the search ran with, so a click is counted onto
    // this search's own aggregate row.
    clicks[sec.key].arm(args, offset)
  } catch (e) {
    // A search the origin refused as too expensive (503) is not a transient
    // fault: re-running it costs the same and fails the same, so the reader is
    // told what to change instead of being offered a retry.
    if (seq === reqSeq) { sec.error = true; sec.tooSlow = e.status === 503 }
  } finally {
    if (seq === reqSeq) sec.loading = false
  }
}

function retry(sec) {
  runSection(sec, reqSeq, searchState(), single.value ? Number(route.query.offset) || 0 : 0)
}

// Fetch the breakdown for the active search, once, on first expand of its panel
// (SEA-9). Guarded by reqSeq so a breakdown from a superseded query never lands.
async function loadBreakdown() {
  if (!currentArgs) return
  const seq = reqSeq
  if (breakdownLoadedSeq === seq) return // already fetched for this search
  breakdownLoadedSeq = seq
  breakdownLoading.value = true
  try {
    const b = await api.searchBreakdown(currentArgs)
    if (seq === reqSeq) breakdown.value = b
  } catch {
    if (seq === reqSeq) { breakdown.value = null; breakdownLoadedSeq = -1 }
  } finally {
    if (seq === reqSeq) breakdownLoading.value = false
  }
}

function toggleBreakdown() {
  showBreakdown.value = !showBreakdown.value
  if (showBreakdown.value) loadBreakdown()
}

// Keep the form synced when navigating via back/forward, and re-run on any change.
watch(() => route.query, (q) => {
  input.value = q.q || ''
  filters.date_from = q.date_from || ''
  filters.date_to = q.date_to || ''
  filters.faction_id = q.faction_id || ''
  filters.agenda_type = q.agenda_type || ''
  filters.topic = queryList(q.topic)
  syncSpeaker(q.person_id || '')
  sort.value = SORTS.includes(q.sort) ? q.sort : 'relevance'
  runFromRoute()
})

// The cat is appended to <body> and the suggestion debounce runs on a timer, so
// both outlive this component unless they are told otherwise — leaving the
// search page has to take the cat with us (SEA-13) and drop the listener.
onUnmounted(() => {
  dismissOneko()
  clearSuggest()
  document.removeEventListener('pointerdown', onDocPointerDown)
})

// How many hits a section found, as a sentence fragment and as a bare figure.
function countText(sec) {
  return sec.capped
    ? t('search.resultsCapped', { n: sec.total.toLocaleString('hu-HU') })
    : `${sec.total.toLocaleString('hu-HU')} ${t('search.results')}`
}
function countFigure(sec) {
  return sec.total.toLocaleString('hu-HU') + (sec.capped ? '+' : '')
}
// Whether a section has an answer to count — not still loading, failed, or
// unable to search at all.
function found(sec) {
  return !sec.loading && !sec.error && !sec.skipped && !sec.unavailable
}

// What a section's results must be read with: the filters it could not apply,
// and what the speaker filter means where it is not a speaker.
function scopeNotes(key) {
  const out = []
  const ignored = ignoredFilters(key, route.query)
  if (ignored.length) {
    out.push(t('search.notApplied', {
      filters: ignored.map((g) => t(`search.filterNames.${g}`)).join(', '),
    }))
  }
  const name = speaker.value?.label
  if (route.query.person_id && name) {
    if (key === 'documents') out.push(t('search.asSponsor', { name }))
    if (key === 'votes') out.push(t('search.asVoter', { name }))
  }
  return out
}

function emptyText(sec) {
  if (sec.skipped) return t(sec.key === 'people' ? 'search.peopleNeedTerm' : 'search.needsTerm')
  if (sec.unavailable) return t('search.committeeUnavailable')
  return t('search.noResults')
}

// A record scope's own list page, for the filters and orderings the search
// page does not carry (shown under a single scope's full list).
const listPage = computed(() => (single.value ? listPageTarget(single.value, searchState()) : null))

// From the summary line to a section: scroll there and move focus to its
// heading, so a keyboard reader lands where a sighted one looks.
function jumpTo(key) {
  const el = document.getElementById(`srh-${key}`)
  if (!el) return
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  el.focus({ preventScroll: true })
  el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
}

// The page names what is searched: "the speeches" while every picked scope is
// a transcript, plain "search" once records are among them — and so does the
// cycle notice.
const speechesOnly = computed(() =>
  scopes.value.every((k) => k === 'plenary' || k === 'committee'))
const title = computed(() => t(speechesOnly.value ? 'search.title' : 'search.titleAny'))

// SEA-9 breakdown → BarChart rows. Factions keep their colour; each
// representative links to their profile (REP-1).
const factionItems = computed(() =>
  (breakdown.value?.factions || []).map((f) => ({
    label: f.label, value: f.hits, color: f.color,
  })))
const speakerItems = computed(() =>
  (breakdown.value?.speakers || []).map((s) => ({
    label: s.label, value: s.hits,
    to: { name: 'profile', params: { id: s.person_id } },
  })))
const hasBreakdown = computed(() =>
  factionItems.value.length > 0 || speakerItems.value.length > 0)
// The breakdown is a secondary aid, not the primary result — keep it collapsed by
// default so it doesn't push the result list down the page.
const showBreakdown = ref(false)

// When cycles are selected globally, the search is scoped to them; offer a
// one-click switch back to "all cycles" which re-runs the active search.
const cycleScopeLabel = computed(() => currentCycleLabel())

// Whenever the trend spans more than one cycle, mark each electoral cycle's
// start/end with a reference line to keep the eras legible — the whole corpus
// under "all cycles", just the selected ones under a multi-cycle scope. Empty for
// a single-cycle scope (the axis is then just that one cycle). Mirrors the home page.
const cycleMarkers = computed(() => {
  if (store.cycles.length === 1 || !store.meta) return []
  const inScope = (p) => !store.cycles.length || store.cycles.includes(p.number)
  const out = []
  for (const p of (store.meta.periods || []).filter(inScope)) {
    const label = periodLabel(p)
    if (p.date_start) out.push({ date: p.date_start, label })
    if (p.date_end) out.push({ date: p.date_end, label })
  }
  return out
})

// The plenary section of a plenary-only search — what the two aggregates are
// drawn for.
const plenary = computed(() => (single.value === 'plenary' ? sections.value[0] : null))

// What the result set is *called* — the keyword in quotes, or, for a query-less
// search, the speaker whose speeches are listed. The captions read the same way
// round either way, so they take one name.
const trendCaption = computed(() => {
  if (plenary.value?.query) return t('search.trendCaption', { q: plenary.value.query })
  const name = route.query.person_id ? speaker.value?.label || '' : ''
  if (topicNames.value) {
    return name
      ? t('search.trendCaptionSpeakerTopic', { name, topics: topicNames.value })
      : t('search.trendCaptionTopic', { topics: topicNames.value })
  }
  return t('search.trendCaptionSpeaker', { name })
})

// Params carried into the embeddable histogram (EmbedView `search-trend`): the
// executed query plus the active filters, so the embed shows the same result set.
// The cycle is added by EmbedButton from the global scope.
const trendEmbedParams = computed(() => ({
  q: plenary.value?.query,
  date_from: route.query.date_from,
  date_to: route.query.date_to,
  person_id: route.query.person_id,
  faction_id: route.query.faction_id,
  agenda_type: route.query.agenda_type,
  topic: queryList(route.query.topic),
}))

// Drill the search into a clicked histogram bucket's timeframe by applying its
// date range as the date_from/date_to filters. The trend then re-anchors to that
// span (a finer granularity), so a click zooms in; clearing the filters zooms out.
function onTrendSelect({ from, to }) {
  filters.date_from = from
  filters.date_to = to
  showFilters.value = true // reveal the now-active date range
  submit()
}
</script>

<template>
  <h1>{{ title }}</h1>

  <form role="search" class="card pad searchform" @submit.prevent="submit()">
    <div class="row" style="gap:.5rem;">
      <SearchScopeSelect :model-value="scopes" @change="onScopes" />
      <div ref="qBox" class="qbox">
        <input
          ref="qInput" v-model="input" type="search" autocomplete="off"
          :placeholder="$t('search.placeholder')" :aria-label="title"
          @input="onInput" @keydown.esc="clearSuggest()"
        />
        <!-- Who the typed text names (SEA-7). Offered as the speaker filter,
             and silent for the ordinary search terms that match nobody. -->
        <SpeakerSuggestList
          floating :speakers="suggested"
          :heading="$t('search.speakerSuggest')" @pick="pickSuggested"
        />
      </div>
      <button class="btn" type="submit">{{ $t('search.button') }}</button>
      <button class="btn secondary" type="button" :aria-expanded="showFilters" @click="showFilters = !showFilters">
        {{ $t('search.filters') }}
      </button>
    </div>
    <p class="small muted" style="margin:.4rem 0 0;">{{ $t('search.hint') }}</p>

    <fieldset v-show="showFilters" class="filters">
      <legend class="visually-hidden">{{ $t('search.filters') }}</legend>
      <div class="filter-grid">
        <!-- Full width: its suggestions need the room, and it is the filter most
             often reached for after the query itself. -->
        <div v-if="offers.speaker" class="wide">
          <SpeakerFilter
            :model-value="speaker" input-id="f-speaker"
            @update:model-value="onSpeakerFilter"
          />
        </div>
        <div v-if="offers.faction">
          <label for="f-faction">{{ $t('search.faction') }}</label>
          <select id="f-faction" v-model="filters.faction_id" @change="submit()">
            <option value="">{{ $t('search.all') }}</option>
            <option v-for="f in factions" :key="f.id" :value="f.id">{{ f.label }}</option>
          </select>
        </div>
        <div v-if="offers.agenda">
          <label for="f-agenda">{{ $t('search.agendaType') }}</label>
          <select id="f-agenda" v-model="filters.agenda_type" @change="submit()">
            <option value="">{{ $t('search.all') }}</option>
            <option v-for="a in AGENDA_TYPES" :key="a" :value="a">{{ agendaLabel(a) }}</option>
          </select>
        </div>
        <!-- The machine-read policy topic (TOPIC-1/8): of the speech in the
             plenary, of the iromány among the documents. It selects exactly what
             carries that chip — the dominant topic, at the same threshold — and
             is offered only where a picked scope was classified at all. -->
        <div v-if="offers.topic && topicOptions.length" class="topic-cell">
          <label for="f-topic">{{ $t('search.topic') }}</label>
          <MultiSelect
            id="f-topic" v-model="filters.topic" :options="topicOptions"
            :label="$t('search.topic')" @change="submit()"
          />
        </div>
        <template v-if="offers.dates">
          <div>
            <label for="f-from">{{ $t('search.dateFrom') }}</label>
            <input id="f-from" type="date" v-model="filters.date_from" @change="submit()" />
          </div>
          <div>
            <label for="f-to">{{ $t('search.dateTo') }}</label>
            <input id="f-to" type="date" v-model="filters.date_to" @change="submit()" />
          </div>
        </template>
      </div>
      <button class="btn secondary small" type="button" style="margin-top:.6rem;" @click="clearFilters">
        {{ $t('search.clearFilters') }}
      </button>
    </fieldset>
  </form>

  <p v-if="cycleScopeLabel" class="muted small cyclenotice">
    {{ $t(speechesOnly ? 'search.cycleScope' : 'search.cycleScopeAny', { cycle: cycleScopeLabel }) }}
    <a href="#" @click.prevent="setCycles([])">{{ $t('search.cycleScopeAll') }}</a>
  </p>

  <!-- One scope: its full result list, paged, with the plenary's aggregates. -->
  <template v-if="single && sections.length">
    <StateBlock
      v-for="sec in sections" :key="sec.key"
      :loading="sec.loading" :error="sec.error"
      :error-text="sec.tooSlow ? $t('search.tooSlow') : ''"
      :retryable="!sec.tooSlow"
      :empty="sec.skipped || sec.results.length === 0"
      :empty-text="emptyText(sec)"
      @retry="retry(sec)"
    >
      <div>
        <div class="results-head">
          <p class="muted small" aria-live="polite" style="margin:0;">
            <strong>{{ sec.capped ? $t('search.resultsCapped', { n: sec.total }) : sec.total.toLocaleString('hu-HU') }}</strong>{{ sec.capped ? '' : ` ${$t('search.results')}` }}
          </p>
          <label v-if="sorts.length" class="sortctl small muted">
            {{ $t('search.sort') }}
            <select :value="shownSort" @change="changeSort($event.target.value)">
              <option v-for="o in sorts" :key="o" :value="o">{{ $t(SORT_LABELS[o]) }}</option>
            </select>
          </label>
        </div>
        <p v-for="(n, j) in scopeNotes(sec.key)" :key="j" class="muted small scope-note">{{ n }}</p>

        <section v-if="plenary && trend && trend.buckets.length > 1" class="card pad trendcard">
          <TrendChart
            :buckets="trend.buckets" :granularity="trend.granularity"
            :start="trend.start" :end="trend.end" :markers="cycleMarkers"
            :caption="trendCaption"
            :unit="$t('search.results')"
            selectable @select="onTrendSelect"
          />
          <div class="fig-foot">
            <p class="small muted trendhint">{{ $t('search.trendHint') }}</p>
            <EmbedButton
              kind="search-trend" :params="trendEmbedParams"
              :title="trendCaption" :height="300"
            />
          </div>
        </section>

        <!-- Only for a keyword search: a query-less list is one speaker's own
             speeches, so both groupings would be a single bar of their own. -->
        <section v-if="plenary && plenary.query" class="card pad breakdowncard">
          <button
            class="breakdown-toggle" type="button"
            :aria-expanded="showBreakdown" @click="toggleBreakdown"
          >
            <span class="chev" :class="{ open: showBreakdown }" aria-hidden="true">▸</span>
            {{ $t('search.breakdown') }}
          </button>
          <div v-show="showBreakdown">
            <p v-if="breakdownLoading" class="muted small bd-status">{{ $t('app.loading') }}</p>
            <div v-else-if="hasBreakdown" class="breakdown-grid">
              <BarChart
                v-if="factionItems.length"
                :items="factionItems"
                :caption="$t('search.breakdownFactions', { q: plenary.query })"
                :unit="$t('search.results')"
                :value-format="(v) => v.toLocaleString('hu-HU')"
              />
              <BarChart
                v-if="speakerItems.length"
                :items="speakerItems"
                :caption="$t('search.breakdownSpeakers', { q: plenary.query })"
                :unit="$t('search.results')"
                :value-format="(v) => v.toLocaleString('hu-HU')"
              />
            </div>
          </div>
        </section>

        <SearchResults
          :scope="sec.key" :results="sec.results"
          :person-scoped="!!route.query.person_id" @hit="clicks[sec.key].hit($event)"
        />

        <nav v-if="totalPages > 1" class="pager" :aria-label="$t('search.results')">
          <button class="btn secondary" :disabled="page === 0" @click="gotoPage(page - 1)">‹</button>
          <span class="muted small">{{ page + 1 }} / {{ totalPages }}</span>
          <button class="btn secondary" :disabled="page + 1 >= totalPages" @click="gotoPage(page + 1)">›</button>
        </nav>

        <p v-if="listPage" class="small list-handoff">
          <router-link :to="listPage">{{ $t('search.moreFilters', { page: $t(`search.listPages.${sec.key}`) }) }}</router-link>
        </p>

        <DonateCard class="search-donate" dismissible storage-key="parlamonitor.donate.search" />
      </div>
    </StateBlock>
  </template>

  <!-- Several scopes: a section each, its top hits and a way to all of them. -->
  <template v-else-if="sections.length">
    <div class="results-head">
      <nav class="scope-jump small" :aria-label="$t('search.byScope')">
        <template v-for="sec in sections" :key="sec.key">
          <button v-if="!found(sec) || sec.results.length" type="button" class="jump" @click="jumpTo(sec.key)">
            {{ $t(`search.scopes.${sec.key}`) }}
            <b>{{ sec.loading ? '…' : found(sec) ? countFigure(sec) : '–' }}</b>
          </button>
          <!-- Nothing there to jump to. -->
          <span v-else class="jump-none">{{ $t(`search.scopes.${sec.key}`) }} <b>0</b></span>
        </template>
      </nav>
      <label v-if="sorts.length" class="sortctl small muted">
        {{ $t('search.sort') }}
        <select :value="shownSort" @change="changeSort($event.target.value)">
          <option v-for="o in sorts" :key="o" :value="o">{{ $t(SORT_LABELS[o]) }}</option>
        </select>
      </label>
    </div>

    <section
      v-for="sec in sections" :key="sec.key" class="scope-section"
      :class="{ nohits: found(sec) && !sec.results.length }"
      :aria-labelledby="`srh-${sec.key}`"
    >
      <div class="scope-head">
        <h2 :id="`srh-${sec.key}`" tabindex="-1">{{ $t(`search.scopes.${sec.key}`) }}</h2>
        <span v-if="found(sec)" class="muted small">{{ countText(sec) }}</span>
      </div>
      <p v-for="(n, j) in scopeNotes(sec.key)" :key="j" class="muted small scope-note">{{ n }}</p>
      <!-- A section that found nothing says so in its heading's count; a second
           "no results" line under it would only repeat that. -->
      <StateBlock
        v-if="!found(sec) || sec.results.length"
        :loading="sec.loading" :error="sec.error"
        :error-text="sec.tooSlow ? $t('search.tooSlow') : ''"
        :retryable="!sec.tooSlow"
        :empty="sec.skipped || sec.results.length === 0"
        :empty-text="emptyText(sec)"
        @retry="retry(sec)"
      >
        <SearchResults
          :scope="sec.key" :results="sec.results"
          :person-scoped="!!route.query.person_id" @hit="clicks[sec.key].hit($event)"
        />
        <!-- vue-router matches an active link on its path alone, so every
             link back into this page would claim to be the current page. -->
        <p v-if="sec.total > sec.results.length" class="scope-more">
          <router-link :to="onlyScope(sec.key)" :aria-current-value="null" class="btn secondary small">
            {{ $t('search.showAll', { n: countFigure(sec) }) }}
          </router-link>
        </p>
      </StateBlock>
    </section>

    <DonateCard class="search-donate" dismissible storage-key="parlamonitor.donate.search" />
  </template>
</template>

<style scoped>
/* .filters, .filter-grid, .results-head, .sortctl are global (styles.css). */
.cyclenotice { margin: .6rem 0 0; }
/* The positioning context the floating suggestion list hangs from. */
.qbox { position: relative; flex: 1; min-width: 200px; }
/* A filter whose control carries a dropdown takes the whole grid row. */
.filter-grid .wide { grid-column: 1 / -1; }
/* Topic names run long ("Belkereskedelem és bankügy"), and the picker's panel is
   as wide as its trigger: in one 150px track the trigger truncates the name and
   every row of the panel wraps. Two tracks wherever the grid has two — never on
   a phone, whose one-track grid would sprout an implicit second column. */
@media (min-width: 480px) {
  .filter-grid .topic-cell { grid-column: span 2; }
}
.search-donate { margin-top: 1.2rem; }
.trendcard { margin: 0 0 .9rem; }
/* In the figure footer row the hint sits left, pushing the embed button right. */
.trendhint { margin: 0; margin-right: auto; }
.breakdowncard { margin: 0 0 .9rem; }
.breakdown-toggle {
  display: flex; align-items: center; gap: .45rem; width: 100%;
  padding: 0; background: none; border: 0; cursor: pointer;
  font: inherit; font-weight: 600; color: inherit; text-align: left;
}
.breakdown-toggle .chev { font-size: .8em; opacity: .6; transition: transform .15s ease; }
.breakdown-toggle .chev.open { transform: rotate(90deg); }
.breakdown-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1.2rem; margin-top: 1rem; }
.bd-status { margin-top: 1rem; }
.pager { display: flex; align-items: center; justify-content: center; gap: 1rem; margin: 1.5rem 0; }
/* A scope's caveats: the filters it could not apply, and what the speaker
   filter reads as there. Quiet, but above the results they qualify. */
.scope-note { margin: -.2rem 0 .6rem; }
.list-handoff { margin: 1rem 0 0; text-align: center; }

/* The summary line of a search across several scopes: each scope with its hit
   count, a jump to its section. */
.scope-jump { display: flex; flex-wrap: wrap; gap: .3rem 1.1rem; }
.jump {
  padding: 0; border: 0; background: none; cursor: pointer;
  font: inherit; color: var(--accent); text-align: left;
}
.jump:hover { text-decoration: underline; }
.jump b, .jump-none b { color: var(--ink); font-variant-numeric: tabular-nums; }
.jump-none { color: var(--ink-faint); }
.scope-section { margin-top: 1.6rem; }
/* A scope that found nothing stays in its place — the sections never reorder
   as they answer — but recedes. */
.scope-section.nohits { margin-top: 1.1rem; }
.scope-section.nohits h2 { color: var(--ink-faint); font-size: 1rem; }
.scope-head { display: flex; align-items: baseline; gap: .8rem; flex-wrap: wrap; margin-bottom: .6rem; }
/* Clear of the sticky site header when jumped to. */
.scope-head h2 { margin: 0; font-size: 1.15rem; scroll-margin-top: 5.5rem; }
.scope-head h2:focus { outline: none; }
.scope-head h2:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
/* A section's own loading / empty / error state is a line, not the page-sized
   block a whole list gets. */
.scope-section :deep(.state) { padding: 1rem; }
.scope-more { margin: .7rem 0 0; display: flex; justify-content: center; }
/* On a phone the scope picker takes a row of its own, as on the home page,
   rather than half of one with the box squeezed beneath it. */
@media (max-width: 560px) {
  .searchform .scope-select { flex-basis: 100%; width: 100%; }
}
</style>
