<script setup>
// Tisztségviselők — the parliament's own all-time listing of who held which
// government or House office and when (REP-11), the same registry that backs
// parlament.hu/web/guest/tisztsegviselok.
//
// Listed one row per *term*, not per person: a career runs through several
// offices, and the term (with its real appointment and dismissal dates) is the
// thing this source records. So the same person legitimately appears more than
// once, under each office they held.
//
// Filter/sort state lives in the URL so a filtered listing is shareable.
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { store, loadMeta, currentCycleLabel } from '../../store.js'
import { formatDateLocal } from '../../format.js'
import { createSearchClicks } from '../../lib/searchClicks.js'
import StateBlock from '../../components/StateBlock.vue'
import SpeakerLink from '../../components/SpeakerLink.vue'
import Pagination from '../../components/Pagination.vue'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const PAGE = 60

const data = ref(null)
const loading = ref(false)
const error = ref(false)

// Explicit scope label. A term counts for a cycle when it **overlaps** it, so most
// of a cycle's rows can predate it — the outgoing government that governed into the
// new cycle's first days, an office holder serving straight through several cycles.
// That reads as a broken filter unless the page says it, so the label says "held
// during" rather than the generic "{cycle} data", and `startedHint` puts a number on
// the surprising part (and offers to narrow to it).
const cycleScoped = computed(() => store.cycles.length > 0)
const inCycleOnly = computed(() => f.started === 'in-cycle')
const scopeText = computed(() => {
  const c = currentCycleLabel()
  if (!c) return t('cycle.scopeAll')
  return t(inCycleOnly.value ? 'officials.scopeStarted' : 'officials.scopeHeld', { cycle: c })
})
// "…of which N began in this cycle" — null under "all cycles", where there is no
// cycle for a term to have begun in.
const inCycleCount = computed(() => (data.value ? data.value.starts.in_cycle : null))

const f = reactive({
  q: route.query.q || '',
  category: route.query.category || '',
  status: route.query.status || 'all',
  started: route.query.started || 'all',
  sort: route.query.sort || 'start',
})
const showFilters = ref(!!(route.query.category || route.query.started
  || (route.query.status && route.query.status !== 'all')))

const page = computed(() => Math.floor((Number(route.query.offset) || 0) / PAGE))
const totalPages = computed(() => (data.value ? Math.ceil(data.value.total / PAGE) : 0))
// The category facet is counted with the category filter itself lifted, so the
// options never read zero for a choice that would return rows.
const categories = computed(() => (data.value ? data.value.categories : []))

function apply() {
  const query = {}
  if (f.q) query.q = f.q
  if (f.category) query.category = f.category
  if (f.status && f.status !== 'all') query.status = f.status
  if (f.started && f.started !== 'all') query.started = f.started
  if (f.sort && f.sort !== 'start') query.sort = f.sort
  // Changing a filter resets to the first page (offset intentionally dropped).
  router.push({ name: 'officials', query })
}

function clearFilters() {
  f.category = ''
  f.status = 'all'
  f.started = 'all'
  apply()
}

// The scope line's own shortcut between the two readings of "this cycle".
function setStarted(value) {
  f.started = value
  apply()
}

function gotoPage(p) {
  router.push({ name: 'officials', query: { ...route.query, offset: p * PAGE } })
}

// "2010.06.02 – 2014.06.06", or "2010.06.02 – jelenleg" while still in office —
// an office is never shown undated, since the title alone reads as the person's
// current post even when they left it cycles ago (REP-2).
function term(o) {
  const start = formatDateLocal(o.start)
  const end = o.end ? formatDateLocal(o.end) : t('profile.present')
  return `${start || '?'} – ${end}`
}

// Monotonic load id: overlapping fetches (filter watcher + cycle watcher) can
// resolve out of order; only the latest may write state.
let loadSeq = 0

// Anonymous search-quality signal (SEA-12): which office holder the reader opens
// after a name search, and how far down the list they sat.
const clicks = createSearchClicks('officials')

async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false
  const offset = Number(route.query.offset) || 0
  const args = {
    q: route.query.q, category: route.query.category,
    status: route.query.status || 'all',
    started: route.query.started || 'all',
    sort: route.query.sort || 'start',
    period: store.cycles,
  }
  try {
    const res = await api.officials({ ...args, limit: PAGE, offset })
    if (seq === loadSeq) { data.value = res; clicks.arm(args, offset) }
  } catch {
    if (seq === loadSeq) error.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

onMounted(async () => {
  await loadMeta().catch(() => {})
  load()
})
watch(() => route.query, () => {
  const q = route.query
  f.q = q.q || ''; f.category = q.category || ''
  f.status = q.status || 'all'; f.started = q.started || 'all'
  f.sort = q.sort || 'start'
  load()
})
// Changing the cycle resets to the first page; the offset reset triggers load
// via the query watcher. Switching to "all cycles" also drops the "began in this
// cycle" filter — there is no cycle left for a term to have begun in, and leaving
// it in the URL would advertise a narrowing that no longer narrows anything.
watch(() => store.cycles.join(','), () => {
  const stale = !store.cycles.length && route.query.started
  if (route.query.offset || stale) {
    router.push({ name: 'officials',
                  query: { ...route.query, offset: undefined,
                           started: stale ? undefined : route.query.started } })
  } else load()
})
let searchTimer = null
function onSearchInput() { clearTimeout(searchTimer); searchTimer = setTimeout(apply, 300) }
// The debounce survives the component: clear it, or typing then clicking a row
// yanks the user back to the list (apply() router.push).
onUnmounted(() => clearTimeout(searchTimer))
</script>

<template>
  <h1>{{ $t('officials.title') }}</h1>
  <p class="muted small intro">{{ $t('officials.intro') }}</p>

  <form class="card pad searchform" role="search" @submit.prevent="apply">
    <div class="row" style="gap:.5rem;">
      <input
        id="o-q" type="search" v-model="f.q"
        :placeholder="$t('officials.searchPlaceholder')"
        :aria-label="$t('officials.searchPlaceholder')"
        @input="onSearchInput" style="flex:1;min-width:200px;"
      />
      <button
        class="btn secondary" type="button"
        :aria-expanded="showFilters" @click="showFilters = !showFilters"
      >
        {{ $t('search.filters') }}
      </button>
    </div>

    <fieldset v-show="showFilters" class="filters">
      <legend class="visually-hidden">{{ $t('search.filters') }}</legend>
      <div class="filter-grid">
        <div>
          <label for="o-category">{{ $t('officials.category') }}</label>
          <select id="o-category" v-model="f.category" @change="apply">
            <option value="">{{ $t('search.all') }}</option>
            <option v-for="c in categories" :key="c.key" :value="c.key">
              {{ $t('officials.categories.' + c.key) }} ({{ c.count }})
            </option>
          </select>
        </div>
        <div>
          <label for="o-status">{{ $t('officials.status') }}</label>
          <select id="o-status" v-model="f.status" @change="apply">
            <option value="all">{{ $t('search.all') }}</option>
            <option value="current">{{ $t('officials.statusCurrent') }}</option>
            <option value="past">{{ $t('officials.statusPast') }}</option>
          </select>
        </div>
        <!-- The two readings of "this cycle's office holders": every term in force
             during it (the default — a term can predate the cycle by a decade and
             still be held in it), or only the ones it started. Offered only with a
             cycle in scope; under "all cycles" there is nothing to have begun in. -->
        <div v-if="cycleScoped">
          <label for="o-started">{{ $t('officials.started') }}</label>
          <select id="o-started" v-model="f.started" @change="apply">
            <option value="all">
              {{ $t('officials.startedAll') }}<template v-if="data"> ({{ data.starts.all }})</template>
            </option>
            <option value="in-cycle">
              {{ $t('officials.startedInCycle') }}<template v-if="inCycleCount !== null"> ({{ inCycleCount }})</template>
            </option>
          </select>
        </div>
      </div>
      <button class="btn secondary small" type="button" style="margin-top:.6rem;" @click="clearFilters">
        {{ $t('search.clearFilters') }}
      </button>
    </fieldset>
  </form>

  <StateBlock
    :loading="loading" :error="error"
    :empty="!!data && data.officials.length === 0"
    :empty-text="$t('officials.noResults')"
    @retry="load"
  >
    <div v-if="data">
      <div class="results-head">
        <p class="muted small" aria-live="polite" style="margin:0;">
          {{ data.total }} {{ $t('officials.unit') }} · {{ scopeText }}
          <!-- The count that explains the page: how much of a cycle's listing
               actually began in it. Doubles as the shortcut between the two
               readings, so a reader who meant the narrower one is one click away
               rather than convinced the filter is broken. -->
          <template v-if="cycleScoped && inCycleCount !== null">
            ·
            <button v-if="!inCycleOnly" type="button" class="linkbtn" @click="setStarted('in-cycle')">
              {{ $t('officials.startedHint', { count: inCycleCount }) }}
            </button>
            <button v-else type="button" class="linkbtn" @click="setStarted('all')">
              {{ $t('officials.startedShowAll', { count: data.starts.all }) }}
            </button>
          </template>
        </p>
        <label class="sortctl small muted">
          {{ $t('search.sort') }}
          <select v-model="f.sort" @change="apply">
            <option value="start">{{ $t('officials.sortStart') }}</option>
            <option value="name">{{ $t('reps.sortName') }}</option>
            <option value="office">{{ $t('officials.sortOffice') }}</option>
          </select>
        </label>
      </div>

      <ul class="olist">
        <li v-for="(o, i) in data.officials" :key="o.id" class="card pad orow">
          <SpeakerLink
            :speaker="{ person_id: o.person_id, label: o.label, photo_uri: o.photo_uri }"
            @click="clicks.hit(i)"
          />
          <div class="ooffice">
            <span class="otitle">{{ o.title }}</span>
            <span v-if="o.category" class="badge">{{ $t('officials.categories.' + o.category) }}</span>
            <!-- An MP who also holds an office: worth saying, since most of this
                 listing is people who never held a mandate. -->
            <span v-if="o.is_mp" class="badge mp">{{ $t('officials.isMp') }}</span>
          </div>
          <div class="oterm small muted">
            <span>{{ term(o) }}</span>
            <span v-if="o.current" class="badge current">{{ $t('officials.inOffice') }}</span>
          </div>
        </li>
      </ul>

      <Pagination :page="page" :total-pages="totalPages" @goto="gotoPage" />

      <details class="methodology" style="margin-top:1rem;">
        <summary>{{ $t('factions.methodology') }}</summary>
        <p class="small muted">{{ data.methodology }}</p>
      </details>
    </div>
  </StateBlock>
</template>

<style scoped>
/* .filters, .filter-grid, .results-head, .sortctl, .badge are global (styles.css). */
.intro { margin: -.4rem 0 1rem; max-width: 68ch; }
.olist { list-style: none; padding: 0; margin: 0; display: grid; gap: .5rem; }
/* Three columns on wide screens (who · what · when), stacking on narrow ones —
   the term must never be squeezed onto a second line next to the name. */
.orow {
  display: grid; gap: .35rem .9rem; align-items: center;
  grid-template-columns: minmax(0, 15rem) minmax(0, 1fr) auto;
}
.ooffice { display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; min-width: 0; }
.otitle { font-weight: 600; }
/* The scope line's shortcut: a button, because it changes the query rather than
   navigating, but it has to read as the link it behaves like. */
.linkbtn {
  background: none; border: 0; padding: 0; font: inherit; color: var(--accent);
  cursor: pointer; text-decoration: underline;
}
.linkbtn:hover, .linkbtn:focus-visible { text-decoration: none; }
.oterm { display: flex; gap: .5rem; align-items: center; white-space: nowrap; }
/* The two states worth colouring: a mandate alongside the office, and an office
   still held (an open-ended term, which the list otherwise shows as "– jelenleg"). */
.badge.mp { background: var(--accent-soft); color: var(--accent); }
.badge.current { background: #e4efe4; color: #2c5e2e; }
@media (max-width: 720px) {
  .orow { grid-template-columns: 1fr; }
  .oterm { white-space: normal; }
}
.methodology summary { cursor: pointer; font-weight: 600; color: var(--ink-soft); font-size: .85rem; }
</style>
