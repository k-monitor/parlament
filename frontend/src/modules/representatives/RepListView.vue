<script setup>
// Browsable, filterable representative list (REP-1). Filter/sort state is in the
// URL so a filtered list is shareable.
import {
  defineAsyncComponent, ref, reactive, computed, watch, onMounted, onUnmounted,
} from 'vue'
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
import SpeakerSearchModes from './SpeakerSearchModes.vue'

// "Ki a képviselőm?" (REP-10) is this page's other search mode: the same question
// — which of these people is mine — keyed by place instead of by name. It answers
// with one MP rather than a list, so it takes over the card and everything below
// it instead of filtering the rows. Loaded on demand: it brings a map (and, in
// turn, Leaflet) that no reader browsing the list should pay for.
const ConstituencyLookupPanel = defineAsyncComponent(
  () => import('./ConstituencyLookupPanel.vue'))

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

// Everyone who takes the floor lives on this one page — Felszólalók — shown a
// category at a time through the chip row under the search box: the MPs (the
// default, so the list still means "representatives" unless you ask otherwise),
// the nationality advocates (szószólók, REP-9), the other speakers (REP-12 —
// those who spoke holding neither mandate: non-MP ministers and state
// secretaries, the President of the Republic, invited guests), and all of them
// at once. They differ only in the mandate they cover, which is exactly what the
// list endpoint's `role` selects.
//
// The selected category is carried by the **route**, not a query param: each
// chip keeps the URL it has always had, so existing links, the sitemap and the
// per-page share cards all survive the merge, and a category stays citable.
const ROLE_TABS = [
  { role: 'mp', name: 'representatives' },
  { role: 'advocate', name: 'advocates' },
  { role: 'other', name: 'speakers' },
  { role: 'all', name: 'allSpeakers' },
]
const ROLE_OF_ROUTE = Object.fromEntries(ROLE_TABS.map((x) => [x.name, x.role]))
const role = computed(() => ROLE_OF_ROUTE[route.name] || 'mp')

// The place mode (the `lookup` route): the panel replaces the search field, the
// category chips, the filters and the list, so none of the list machinery below
// runs while it is open — no rows to fetch, and the settlement in `?q=` is not a
// person's name to search for.
const place = computed(() => route.name === 'lookup')

// Wording that follows the selected category, so the page never reports "N
// képviselő" for people who are not representatives, and each category explains
// itself. `all` speaks of felszólalók: the one word true of every row in it.
function byRole(map) {
  return computed(() => (map[role.value] === undefined ? '' : t(map[role.value])))
}
const countUnit = byRole({
  mp: 'home.stats.representatives', advocate: 'reps.advocatesUnit',
  other: 'reps.othersUnit', all: 'reps.othersUnit',
})
const searchPlaceholder = byRole({
  mp: 'reps.searchPlaceholder', advocate: 'reps.searchAdvocatePlaceholder',
  other: 'reps.searchOtherPlaceholder', all: 'reps.searchOtherPlaceholder',
})
const emptyText = byRole({
  mp: 'reps.noResults', advocate: 'reps.noAdvocateResults',
  other: 'reps.noOtherResults', all: 'reps.noOtherResults',
})
const note = byRole({
  advocate: 'reps.advocateNote', other: 'reps.otherNote', all: 'reps.allNote',
})

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

// Faction and mandate are MP-only notions — neither an advocate nor a non-MP
// speaker has either — so the filter panel is offered on the Képviselők chip
// alone. On "all" it would silently reduce the list to MPs, which is the one
// thing that chip exists not to do.
const hasFilters = computed(() => role.value === 'mp')

function apply() {
  const query = {}
  if (f.q) query.q = f.q
  if (f.faction_id) query.faction_id = f.faction_id
  if (f.mandate) query.mandate = f.mandate
  if (f.sort) query.sort = f.sort
  // Changing a filter resets to the first page (offset is intentionally dropped).
  // Stays on the current category, since the route is what selects the chip.
  router.push({ name: route.name, query })
}

// Switching category keeps what still applies — the typed name and a chosen sort
// order — and drops the rest: the offset (the new list is a different one, so
// page 4 of it is not where the reader was), and the MP-only filters whenever the
// target chip has no filter panel to show them in.
function chipTo(tab) {
  const query = {}
  if (f.q) query.q = f.q
  if (f.sort && f.sort !== 'speaking_time') query.sort = f.sort
  if (tab.role === 'mp') {
    if (f.faction_id) query.faction_id = f.faction_id
    if (f.mandate) query.mandate = f.mandate
  }
  return { name: tab.name, query }
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

// The faction filter's options. Fetched when a list is actually shown — the place
// mode has no filter panel to put them in — and only once per visit.
async function ensureFactions() {
  if (factions.value.length) return
  try { factions.value = (await api.factions()).factions } catch {}
}

onMounted(async () => {
  await loadMeta().catch(() => {})
  if (place.value) return
  ensureFactions()
  load()
})
// Watches the route *name* as well as the query: every category chip — and the
// place mode — shares this component, so switching chip reuses the instance
// instead of remounting it. With only the query watched, the list would keep
// showing the previous category's mandate.
watch(() => [route.name, route.query], () => {
  if (place.value) return
  const q = route.query
  f.q = q.q || ''; f.faction_id = q.faction_id || ''
  f.mandate = q.mandate || ''
  f.sort = q.sort || 'speaking_time'
  // Coming back from the place mode the options may still be missing: nothing
  // fetched them on mount, since there was no filter panel to show them in.
  ensureFactions()
  load()
})
// Changing the cycle resets to the first page; the offset reset triggers load via
// the query watcher. The lookup answers for the cycle its constituency boundaries
// belong to, so the header's scope is not its business (see the panel).
watch(() => store.cycles.join(','), () => {
  if (place.value) return
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
  <h1>{{ $t('reps.title') }}</h1>

  <!-- Place mode (REP-10): the same question keyed by a settlement instead of a
       name, so it takes the search card's place — and, since it answers with one
       MP rather than a list, everything below it too. Its own route, hence its own
       share card and address (see SpeakerSearchModes). -->
  <ConstituencyLookupPanel v-if="place" />

  <form v-else class="card pad searchform" role="search" @submit.prevent="apply">
    <SpeakerSearchModes mode="name" />
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

    <!-- Which of the House's speakers the list covers. Links rather than
         buttons, because each category is a page in its own right: middle-click
         opens one in a tab, Back returns to the previous one, and the address is
         worth sharing. The selected one is marked by `aria-current`, not by its
         fill alone (A11Y-1). -->
    <nav class="rolechips" :aria-label="$t('reps.category')">
      <router-link
        v-for="tab in ROLE_TABS" :key="tab.role" :to="chipTo(tab)"
        class="rolechip" :class="{ active: role === tab.role }"
        :aria-current="role === tab.role ? 'page' : undefined"
      >{{ $t('reps.role.' + tab.role) }}</router-link>
    </nav>

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

  <!-- What the selected category is, since a szószóló and a non-MP minister are
       both easily mistaken for representatives: they speak in the House but hold
       no mandate (REP-9 / REP-12 / TRUST-1). The Képviselők chip needs no such
       note, so it has none. -->
  <p v-if="note" class="muted small rolenote">{{ note }}</p>

  <StateBlock
    v-if="!place"
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

/* Category chips. Pills rather than another tab strip: the section already has a
   tab bar one row above, and a second one would read as more navigation instead
   of a filter of the list below it. */
.rolechips { display: flex; flex-wrap: wrap; gap: .4rem; margin-top: .7rem; }
.rolechip {
  display: inline-flex; align-items: center; padding: .32rem .8rem;
  border: 1px solid var(--line); border-radius: 999px; background: var(--surface);
  font-size: .85rem; font-weight: 600; line-height: 1.45; color: var(--ink-soft);
}
.rolechip:hover {
  color: var(--accent); border-color: var(--accent);
  background: var(--accent-soft); text-decoration: none;
}
.rolechip.active, .rolechip.active:hover {
  background: var(--accent); border-color: var(--accent); color: var(--accent-ink);
}
.rolenote { margin: .8rem 0 -.2rem; max-width: 62ch; }
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
