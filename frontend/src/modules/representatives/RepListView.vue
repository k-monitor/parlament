<script setup>
// Browsable, filterable representative list (REP-1). Filter/sort state is in the
// URL so a filtered list is shareable.
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { store, loadMeta, currentCycleLabel } from '../../store.js'
import { formatDateLocal, formatSpeakingTime } from '../../format.js'
import { createSearchClicks } from '../../lib/searchClicks.js'
import StateBlock from '../../components/StateBlock.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import SpeakerLink from '../../components/SpeakerLink.vue'
import Pagination from '../../components/Pagination.vue'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const PAGE = 60

// Explicit scope label so it's clear the list (and its stats) cover only the
// globally selected cycle, not all cycles.
const scopeText = computed(() => {
  const c = currentCycleLabel()
  return c ? t('cycle.scope', { cycle: c }) : t('cycle.scopeAll')
})

const data = ref(null)
const loading = ref(false)
const error = ref(false)
const factions = ref([])

// This one component serves three sibling pages of the Representatives section
// (its sub-nav tabs): the MP list, the nationality advocates (szószólók, REP-9)
// and the other speakers (REP-12) — everyone who spoke in the House holding
// neither mandate: non-MP ministers and state secretaries, the President of the
// Republic, invited guests. Which one is decided by the route, so each is its own
// URL — no query state. They differ only in the mandate they cover, which is
// exactly what the list endpoint's `role` selects.
const isAdvocates = computed(() => route.name === 'advocates')
const isOthers = computed(() => route.name === 'speakers')
const role = computed(() =>
  (isAdvocates.value ? 'advocate' : isOthers.value ? 'other' : 'mp'))

// The unit the result count is counted in, so neither tab reports "N képviselő"
// for people who are not representatives.
const countUnit = computed(() => (
  isAdvocates.value ? t('reps.advocatesUnit')
  : isOthers.value ? t('reps.othersUnit')
  : t('home.stats.representatives')))

// Per-page wording, so each tab says what it is about rather than sharing one
// generic phrasing.
const pageTitle = computed(() => (
  isAdvocates.value ? t('nav.advocates')
  : isOthers.value ? t('nav.speakers')
  : t('reps.title')))
const searchPlaceholder = computed(() => (
  isAdvocates.value ? t('reps.searchAdvocatePlaceholder')
  : isOthers.value ? t('reps.searchOtherPlaceholder')
  : t('reps.searchPlaceholder')))
const emptyText = computed(() => (
  isAdvocates.value ? t('reps.noAdvocateResults')
  : isOthers.value ? t('reps.noOtherResults')
  : t('reps.noResults')))
const note = computed(() => (
  isAdvocates.value ? t('reps.advocateNote')
  : isOthers.value ? t('reps.otherNote')
  : ''))

const page = computed(() => Math.floor((Number(route.query.offset) || 0) / PAGE))
const totalPages = computed(() => (data.value ? Math.ceil(data.value.total / PAGE) : 0))

const f = reactive({
  q: route.query.q || '',
  faction_id: route.query.faction_id || '',
  // Mandate state (REP-14): '' = everyone who held a mandate in the cycle, which is
  // the default because that IS the cycle's membership — a roster is a history, not
  // a snapshot. 'active'/'terminated' narrow it to either side.
  mandate: route.query.mandate || '',
  sort: route.query.sort || 'speaking_time',
})
// Open the filter panel on load when a filter is already active (e.g. a shared
// or deep-linked list), so its filters are visible rather than hidden.
const showFilters = ref(!!route.query.faction_id || !!route.query.mandate)

function clearFilters() {
  f.faction_id = ''
  f.mandate = ''
  apply()
}

// "megszűnt mandátum · 2024-09-30" — a card must say that a seat was given up,
// or a former MP reads exactly like a sitting one (REP-14).
function mandateEndedLabel(m) {
  const end = formatDateLocal(m && m.end)
  return end ? `${t('reps.mandateEnded')} · ${end}` : t('reps.mandateEnded')
}

// Faction is the only filter here, and neither an advocate nor a non-MP speaker
// has one — so the panel is offered on the MP tab alone.
const hasFilters = computed(() => !isAdvocates.value && !isOthers.value)

function apply() {
  const query = {}
  if (f.q) query.q = f.q
  if (f.faction_id) query.faction_id = f.faction_id
  if (f.mandate) query.mandate = f.mandate
  if (f.sort) query.sort = f.sort
  // Changing a filter resets to the first page (offset is intentionally dropped).
  // Stays on the current page (MP list or advocates), since the route is the tab.
  router.push({ name: route.name, query })
}

function gotoPage(p) {
  router.push({ name: route.name, query: { ...route.query, offset: p * PAGE } })
}

// Monotonic load id: overlapping fetches (filter watcher + cycle watcher) can
// resolve out of order; only the latest may write state.
let loadSeq = 0

// Anonymous search-quality signal (SEA-12): which representative the reader
// opens after a name search, and how far down the list they sat.
const clicks = createSearchClicks('representatives')

async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false
  const offset = Number(route.query.offset) || 0
  // `period` comes from the global cycle chooser (store.cycles; empty = all) —
  // it scopes the list to MPs serving in that cycle.
  const args = {
    q: route.query.q, faction_id: route.query.faction_id, period: store.cycles,
    role: role.value, mandate: route.query.mandate || undefined,
    sort: route.query.sort || 'speaking_time',
  }
  try {
    const res = await api.representatives({ ...args, limit: PAGE, offset })
    if (seq === loadSeq) { data.value = res; clicks.arm(args, offset) }
  } catch {
    if (seq === loadSeq) error.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

onMounted(async () => {
  await loadMeta().catch(() => {})
  try { factions.value = (await api.factions()).factions } catch {}
  load()
})
// Watches the route *name* as well as the query: the MP list and the advocates
// page share this component, so switching sub-tab may reuse the instance — with
// only the query watched, the list would keep showing the other mandate.
watch(() => [route.name, route.query], () => {
  const q = route.query
  f.q = q.q || ''; f.faction_id = q.faction_id || ''
  f.mandate = q.mandate || ''
  f.sort = q.sort || 'speaking_time'
  load()
})
// Changing the cycle resets to the first page; the offset reset triggers load via the query watcher.
watch(() => store.cycles.join(','), () => {
  if (route.query.offset) router.push({ name: route.name, query: { ...route.query, offset: undefined } })
  else load()
})
let searchTimer = null
function onSearchInput() { clearTimeout(searchTimer); searchTimer = setTimeout(apply, 300) }
// The debounce survives the component: clear it, or typing then clicking an
// MP within 300 ms yanks the user back to the list (apply() router.push).
onUnmounted(() => clearTimeout(searchTimer))
</script>

<template>
  <h1>{{ pageTitle }}</h1>

  <!-- What a szószóló / a non-MP speaker is, since both are easily mistaken for
       representatives: they speak in the House but hold no mandate
       (REP-9 / REP-12 / TRUST-1). -->
  <p v-if="note" class="muted small advocate-note">{{ note }}</p>

  <form class="card pad searchform" role="search" @submit.prevent="apply">
    <div class="row" style="gap:.5rem;">
      <input
        id="r-q" type="search" v-model="f.q"
        :placeholder="searchPlaceholder" :aria-label="searchPlaceholder"
        @input="onSearchInput" style="flex:1;min-width:200px;"
      />
      <button
        v-if="hasFilters" class="btn secondary" type="button"
        :aria-expanded="showFilters" @click="showFilters = !showFilters"
      >
        {{ $t('search.filters') }}
      </button>
    </div>

    <fieldset v-show="showFilters && hasFilters" class="filters">
      <legend class="visually-hidden">{{ $t('search.filters') }}</legend>
      <div class="filter-grid">
        <div>
          <label for="r-faction">{{ $t('reps.faction') }}</label>
          <select id="r-faction" v-model="f.faction_id" @change="apply">
            <option value="">{{ $t('search.all') }}</option>
            <option v-for="x in factions" :key="x.id" :value="x.id">{{ x.label }}</option>
          </select>
        </div>
        <!-- Mandate state (REP-14): the list covers everyone who held a mandate in
             the cycle, so this is what narrows it to those who still held it. What
             "active" means depends on whether the cycle is over, hence the note. -->
        <div>
          <label for="r-mandate">{{ $t('reps.mandateFilter') }}</label>
          <select id="r-mandate" v-model="f.mandate" @change="apply"
                  aria-describedby="r-mandate-note">
            <option value="">{{ $t('reps.mandateAll') }}</option>
            <option value="active">{{ $t('reps.mandateActive') }}</option>
            <option value="terminated">{{ $t('reps.mandateTerminated') }}</option>
          </select>
          <p id="r-mandate-note" class="muted small hint">{{ $t('reps.mandateFilterNote') }}</p>
        </div>
      </div>
      <button class="btn secondary small" type="button" style="margin-top:.6rem;" @click="clearFilters">
        {{ $t('search.clearFilters') }}
      </button>
    </fieldset>
  </form>

  <StateBlock
    :loading="loading" :error="error"
    :empty="!!data && data.representatives.length === 0"
    :empty-text="emptyText"
    @retry="load"
  >
    <div v-if="data">
      <div class="results-head">
        <p class="muted small" aria-live="polite" style="margin:0;">{{ data.total }} {{ countUnit }} · {{ scopeText }}</p>
        <label class="sortctl small muted">
          {{ $t('search.sort') }}
          <select v-model="f.sort" @change="apply">
            <option value="name">{{ $t('reps.sortName') }}</option>
            <option value="speeches">{{ $t('reps.sortSpeeches') }}</option>
            <option value="speaking_time">{{ $t('reps.sortSpeakingTime') }}</option>
          </select>
        </label>
      </div>
      <ul class="replist grid">
        <li v-for="(r, i) in data.representatives" :key="r.person_id" class="card pad repcard">
          <SpeakerLink
            :speaker="{ person_id: r.person_id, label: r.label, photo_uri: r.photo_uri }"
            @click="clicks.hit(i)"
          />
          <div class="repmeta">
            <FactionBadge :faction="r.faction" link />
            <!-- An advocate has no faction/constituency; the nationality they
                 speak for takes that slot (REP-9). The chip shows the bare
                 nationality (the tab already says these are advocates), but
                 carries the full phrase for assistive tech. -->
            <span
              v-if="r.is_advocate && r.nationality" class="chip"
              :aria-label="$t('reps.advocateFor', { nationality: r.nationality })"
            >{{ r.nationality }}</span>
            <!-- A non-MP speaker has neither faction nor constituency either: the
                 office they spoke in is what identifies them (REP-2/REP-12), so it
                 takes the same slot. Scoped to the selected cycle by the API. -->
            <span v-if="r.office" class="chip office">{{ r.office }}</span>
            <span v-if="r.constituency" class="muted small">📍 {{ r.constituency }}</span>
            <!-- A mandate given up before the term ended (REP-14). Marked on the
                 card, with the date, so a former MP is never listed as if they
                 were still sitting. Text, not colour, carries it (A11Y-1). -->
            <span v-if="r.mandate && r.mandate.terminated" class="chip ended"
                  :title="r.mandate.end_reason || ''">{{ mandateEndedLabel(r.mandate) }}</span>
          </div>
          <div class="repstats small muted">
            <span>{{ r.speech_count }} {{ $t('reps.speeches') }}</span>
            <span>·</span>
            <span>{{ formatSpeakingTime(r.speaking_seconds) }}</span>
          </div>
        </li>
      </ul>
      <Pagination :page="page" :total-pages="totalPages" @goto="gotoPage" />
    </div>
  </StateBlock>
</template>

<style scoped>
/* .filters, .filter-grid, .results-head, .sortctl are global (styles.css). */
.advocate-note { margin: -.4rem 0 1rem; max-width: 62ch; }
.replist { list-style: none; padding: 0; margin: 0; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); }
.repcard { display: flex; flex-direction: column; gap: .5rem; }
.repmeta { display: flex; gap: .6rem; flex-wrap: wrap; align-items: center; }
/* Office titles are long ("Külgazdasági és Külügyminisztérium államtitkára") and
   the cards are narrow, so this chip wraps instead of stretching the grid. */
.chip.office { font-weight: 600; line-height: 1.25; }
/* A terminated mandate reads as a quiet annotation, not an alarm — it is a fact
   about the seat, not a judgement about the person. */
.chip.ended { font-size: .8rem; opacity: .85; }
.hint { margin: .35rem 0 0; max-width: 46ch; }
.repstats { display: flex; gap: .4rem; }
</style>
