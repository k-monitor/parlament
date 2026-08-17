<script setup>
// Települések — the local dimension of the record (§6D / TEL-6, TEL-7).
//
// The page answers two questions with one dataset, which is why the map and the
// list sit side by side rather than on separate pages: *which places does the House
// talk about* (the map, shaded by mention count) and *which does it never mention*
// (the same map with the silence as the figure, and the list filtered to it).
//
// The blind-spot half is the reason the page exists, so it is a mode of the main
// view and not a footnote: 1 300-odd settlements have never been named on the floor
// of the House, and that number is only meaningful next to the ones that have.
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { currentCycleLabel, loadMeta, store } from '../../store.js'
import { createSearchClicks } from '../../lib/searchClicks.js'
import Pagination from '../../components/Pagination.vue'
import SettlementMentionMap from '../../components/SettlementMentionMap.vue'
import StateBlock from '../../components/StateBlock.vue'

const route = useRoute()
const router = useRouter()
const { t, n: fmtNumber } = useI18n()

const PER_PAGE = 50

const list = ref(null)
const summary = ref(null)
const mapData = ref(null)
const loading = ref(false)
const error = ref(false)
const mapLoading = ref(false)
// A segmented binning (hexagons or constituencies), fetched only when a reader
// actually asks for it (TEL-15/TEL-16) — the point map is the default and must not pay
// for a view nobody opened.
const segments = ref(null)
const segmentsError = ref(false)

// The binnings the page can offer, in the order the server lists them, with 'points'
// always first: it is the default and it is the one that needs no extra data.
const BINS = ['points', 'oevk', 'h3']

// `mode` is the page's own URL state (CYC-5), so a shared link reproduces whether
// the reader was looking at what is discussed or at what is not.
const f = reactive({
  q: route.query.q || '',
  // 'mentions' | 'blind' — the same two values the map component takes, so the two
  // can never disagree about which reading is on screen.
  mode: route.query.mode === 'blind' ? 'blind' : 'mentions',
  county: route.query.county || '',
  sort: route.query.sort || 'mentions',
  // 'points' | 'oevk' | 'h3' (TEL-15/TEL-16) — per-page URL state (CYC-5), so a
  // shared link reproduces the exact reading. The hexagons' cell size is **not** a
  // reader-facing choice: the server serves one size and says which (TEL-15).
  bins: BINS.includes(route.query.bins) ? route.query.bins : 'points',
})
const page = computed(() => Math.max(0, Number(route.query.page || 0)))

const scopeText = computed(() => {
  const c = currentCycleLabel()
  return c ? t('settlements.scope', { cycle: c }) : t('cycle.scopeAll')
})
const totalPages = computed(() =>
  list.value ? Math.ceil(list.value.total / PER_PAGE) : 0)

// The headline: how much of the country gets named at all. Stated as a share as
// well as a count, because "1 182" means nothing without "of 3 178" (TEL-7).
const coverage = computed(() => {
  if (!summary.value || !summary.value.settlements) return null
  const { named, blind, settlements, mentions } = summary.value
  return { named, blind, settlements, mentions,
           share: Math.round((named / settlements) * 100) }
})

// Counties with the most never-named settlements — where to look next, which is
// what turns the total into something actionable (TEL-7).
const blindCounties = computed(() => {
  if (!summary.value) return []
  return [...summary.value.counties]
    .filter((c) => c.county)
    .sort((a, b) => b.blind - a.blind)
    .slice(0, 8)
})

// Which binnings the server can actually answer for. A binning it cannot build is not
// offered at all rather than offered and then failing (EXT-6), so the switch appears
// with only the options that work — and not at all if none do.
const offeredBins = computed(() => {
  const seg = mapData.value && mapData.value.segments
  if (!seg || !seg.available) return []
  return ['points', ...BINS.filter((b) => b !== 'points' && (seg.bins || []).includes(b))]
})

// The two figures the constituency binning owes the reader, straight off its payload:
// the cells overlap where a city sits in several of them, and the capital as a whole
// sits in none (TEL-16). Both are stated under the map, not buried in the methodology,
// because without them the cells look like a partition of the country's mentions.
const oevkNotes = computed(() => {
  if (f.bins !== 'oevk' || !segments.value) return null
  const { overlap, unattributed } = segments.value
  return {
    overlap: overlap && overlap.settlements ? overlap : null,
    unattributed: unattributed && unattributed.mentions ? unattributed : null,
  }
})

const clicks = createSearchClicks('settlements')

// `replace` is for corrections rather than choices — a shared link naming a binning
// this deployment cannot build gets rewritten, and pushing that would leave the back
// button bouncing off the URL it just corrected.
function apply(extra = {}, replace = false) {
  const query = {}
  if (f.q) query.q = f.q
  if (f.mode === 'blind') query.mode = 'blind'
  if (f.bins !== 'points') query.bins = f.bins
  if (f.county) query.county = f.county
  if (f.sort !== 'mentions') query.sort = f.sort
  Object.assign(query, extra)
  router[replace ? 'replace' : 'push']({ name: 'settlements', query })
}
function goto(p) { apply({ page: p || undefined }) }
function openSettlement(id) {
  const [maz, taz] = String(id).split('/')
  router.push({ name: 'settlement', params: { maz, taz } })
}

let listSeq = 0
async function loadList() {
  const seq = ++listSeq
  loading.value = true; error.value = false
  const args = {
    q: route.query.q, county: route.query.county,
    mentioned: route.query.mode === 'blind' ? 'no' : undefined,
    sort: route.query.sort || 'mentions',
    period: store.cycles, limit: PER_PAGE, offset: page.value * PER_PAGE,
  }
  try {
    const res = await api.settlements(args)
    if (seq === listSeq) { list.value = res; clicks.arm(args) }
  } catch {
    if (seq === listSeq) error.value = true
  } finally {
    if (seq === listSeq) loading.value = false
  }
}

// The map and the summary are separate requests from the list, so paging or typing
// in the search box never redraws (or waits on) 3 178 points (TEL-11 / WCLOUD-5).
let mapSeq = 0
async function loadMap() {
  const seq = ++mapSeq
  mapLoading.value = true
  try {
    const [m, s] = await Promise.all([
      api.settlementMap(store.cycles), api.settlementSummary(store.cycles),
    ])
    if (seq === mapSeq) { mapData.value = m; summary.value = s }
  } catch {
    if (seq === mapSeq) mapData.value = null
  } finally {
    if (seq === mapSeq) mapLoading.value = false
  }
}

// Fetched per (binning, cycle scope) and kept until one of them changes: switching the
// reading between mentions and blind spots re-styles the same cells, so it costs
// nothing (the counts for both readings ride on every feature).
let segSeq = 0
async function loadSegments() {
  if (f.bins === 'points') return
  // A link can name a binning this deployment cannot build — a shared URL from an
  // instance with h3, or with reachable geometry. Fall back to the points rather than
  // waiting on a request that will never be made; the switch then shows where the
  // reader actually is, instead of a spinner that never resolves.
  if (mapData.value && !offeredBins.value.includes(f.bins)) {
    f.bins = 'points'
    apply({}, true)
    return
  }
  const seq = ++segSeq
  segmentsError.value = false
  try {
    const res = await api.settlementSegments(f.bins, store.cycles)
    if (seq === segSeq) segments.value = res
  } catch {
    // A 503 here means the deployment cannot build this binning (EXT-6); the page then
    // says so rather than leaving the reader with a blank map.
    if (seq === segSeq) { segments.value = null; segmentsError.value = true }
  }
}

// Only writes the URL: the route watcher is what fetches, so the state on screen is
// always the state the URL describes (CYC-5) — and a shared link and a click take the
// identical path.
function setBins(bins) {
  if (bins === f.bins) return
  // Dropped rather than kept: the cells of one binning are not the cells of another,
  // and leaving the old ones on screen would draw hexagons under a constituency legend
  // for as long as the request takes.
  segments.value = null
  f.bins = bins
  apply()
}

onMounted(async () => {
  await loadMeta().catch(() => {})
  loadList()
  await loadMap()
  loadSegments()
})
watch(() => route.query, () => {
  f.q = route.query.q || ''
  f.mode = route.query.mode === 'blind' ? 'blind' : 'mentions'
  f.county = route.query.county || ''
  f.sort = route.query.sort || 'mentions'
  f.bins = BINS.includes(route.query.bins) ? route.query.bins : 'points'
  loadList()
  loadSegments()
})
watch(() => store.cycles.join(','), async () => {
  loadList()
  await loadMap()
  // The segments are cycle-scoped like every other count, so a changed scope
  // invalidates them (CYC-2).
  segments.value = null
  loadSegments()
})

let searchTimer = null
function onSearchInput() { clearTimeout(searchTimer); searchTimer = setTimeout(() => apply(), 300) }
onUnmounted(() => clearTimeout(searchTimer))
</script>

<template>
  <h1>{{ $t('settlements.title') }}</h1>
  <p class="muted small intro">{{ $t('settlements.intro') }}</p>

  <!-- The two headline numbers, before anything else: what share of the country the
       House names at all, and how many places it never has (TEL-7). -->
  <section v-if="coverage" class="card pad heads" aria-live="polite">
    <div class="head">
      <strong class="big">{{ fmtNumber(coverage.named) }}</strong>
      <span class="small muted">{{ $t('settlements.namedOf', { total: fmtNumber(coverage.settlements) }) }}</span>
    </div>
    <div class="head">
      <strong class="big">{{ fmtNumber(coverage.blind) }}</strong>
      <span class="small muted">{{ $t('settlements.neverNamed') }}</span>
    </div>
    <div class="head">
      <strong class="big">{{ fmtNumber(coverage.mentions) }}</strong>
      <span class="small muted">{{ $t('settlements.mentionsTotal') }}</span>
    </div>
    <p class="small muted scope">{{ scopeText }}</p>
  </section>

  <!-- Mode switch. Two radio-style buttons rather than a checkbox: they are two
       readings of the same map, and neither is a modifier of the other. -->
  <div class="modes" role="group" :aria-label="$t('settlements.modeLabel')">
    <button
      class="btn" :class="f.mode === 'mentions' ? '' : 'secondary'"
      :aria-pressed="f.mode === 'mentions' ? 'true' : 'false'"
      @click="f.mode = 'mentions'; apply()"
    >{{ $t('settlements.modeAll') }}</button>
    <button
      class="btn" :class="f.mode === 'blind' ? '' : 'secondary'"
      :aria-pressed="f.mode === 'blind' ? 'true' : 'false'"
      @click="f.mode = 'blind'; apply()"
    >{{ $t('settlements.modeBlind') }}</button>
  </div>

  <!-- The binning: which shape the map is drawn in (TEL-15/TEL-16). A separate control
       from the reading above it, because the two compose — every binning can show
       either reading. Only the ones the server can build are listed. -->
  <div v-if="offeredBins.length > 1" class="modes bins" role="group"
       :aria-label="$t('settlements.binsLabel')">
    <button
      v-for="b in offeredBins" :key="b"
      class="btn small" :class="f.bins === b ? '' : 'secondary'"
      :aria-pressed="f.bins === b ? 'true' : 'false'"
      @click="setBins(b)"
    >{{ $t('settlements.bins.' + b) }}</button>
  </div>

  <p v-if="mapLoading && !mapData" class="state">{{ $t('app.loading') }}</p>
  <p v-else-if="segmentsError" class="state" role="alert">
    {{ $t('settlements.segmentsUnavailable') }}
  </p>
  <p v-else-if="f.bins !== 'points' && !segments" class="state">{{ $t('app.loading') }}</p>
  <SettlementMentionMap
    v-else-if="mapData && mapData.points.length"
    :points="mapData.points" :map="mapData.map" :max="mapData.max" :mode="f.mode"
    :bins="f.bins" :segments="segments"
    @select="openSettlement"
  />
  <p v-else-if="mapData" class="state">{{ $t('settlements.noGeometry') }}</p>

  <!-- The map's own caveat, next to the map: it measures plenary mentions and
       nothing wider, and it answers for the selected cycles only (TEL-7/TEL-12). -->
  <p class="muted small caveat">
    {{ $t('settlements.mapCaveat') }}
    <!-- A share over one or two settlements is coarse, and the finer the resolution
         the more cells hold exactly one — so the caveat travels with the view that
         has the problem (TEL-15). -->
    <template v-if="f.bins === 'h3'"> {{ $t('settlements.segmentCaveat') }}</template>
    <!-- A constituency's own caveat is a different one: its cells are near-equal in
         voters but not in area, so a rural one covers far more of the map than an
         urban one at the same number (TEL-16). -->
    <template v-if="f.bins === 'oevk'"> {{ $t('settlements.oevkCaveat') }}</template>
  </p>

  <!-- The constituency binning's two disclosures, from its own payload, one paragraph
       each: without them a reader would take the cells for a partition of the country's
       mentions, and the quiet capital for a finding (TEL-16). -->
  <template v-if="oevkNotes">
    <p v-if="oevkNotes.unattributed" class="muted small caveat">
      {{ $t('settlements.oevkUnattributed', {
        names: oevkNotes.unattributed.names.join(', '),
        mentions: fmtNumber(oevkNotes.unattributed.mentions),
      }) }}
    </p>
    <p v-if="oevkNotes.overlap" class="muted small caveat">
      {{ $t('settlements.oevkOverlap', {
        settlements: oevkNotes.overlap.settlements,
        mentions: fmtNumber(oevkNotes.overlap.mentions),
      }) }}
    </p>
  </template>

  <form class="card pad searchform" role="search" @submit.prevent="apply()">
    <input
      type="search" v-model="f.q"
      :placeholder="$t('settlements.searchPlaceholder')"
      :aria-label="$t('settlements.searchPlaceholder')"
      @input="onSearchInput"
    />
    <label class="sortlabel small muted">
      {{ $t('settlements.sortBy') }}
      <select v-model="f.sort" @change="apply()">
        <option value="mentions">{{ $t('settlements.sortMentions') }}</option>
        <option value="name">{{ $t('settlements.sortName') }}</option>
        <option value="electorate">{{ $t('settlements.sortElectorate') }}</option>
      </select>
    </label>
    <button v-if="f.county" type="button" class="btn secondary small"
            @click="f.county = ''; apply()">
      {{ $t('settlements.clearCounty', { county: f.county }) }}
    </button>
  </form>

  <StateBlock
    :loading="loading" :error="error"
    :empty="!!list && list.settlements.length === 0"
    :empty-text="$t('settlements.noResults')"
    @retry="loadList"
  >
    <div v-if="list">
      <p class="muted small" aria-live="polite" style="margin:.2rem 0 .6rem;">
        {{ fmtNumber(list.total) }} {{ $t('settlements.unit') }}
      </p>
      <ul class="slist">
        <li v-for="(s, i) in list.settlements" :key="s.id" class="card pad srow">
          <div class="sname">
            <RouterLink
              :to="{ name: 'settlement',
                     params: { maz: s.id.split('/')[0], taz: s.id.split('/')[1] } }"
              @click="clicks.hit(page * PER_PAGE + i)"
            >{{ s.name }}</RouterLink>
            <span v-if="s.county" class="small muted county">
              <button type="button" class="linkish"
                      @click="f.county = s.county; apply()">{{ s.county }}</button>
            </span>
          </div>
          <div class="scount small">
            <!-- A zero here is a finding, not missing data, so it is worded rather
                 than left as a bare "0" (TEL-7). -->
            <span v-if="s.mentions" class="stat">
              <strong>{{ fmtNumber(s.mentions) }}</strong> {{ $t('settlements.mentionsShort') }}
            </span>
            <span v-else class="stat none">{{ $t('settlements.neverShort') }}</span>
          </div>
        </li>
      </ul>
      <Pagination :page="page" :total-pages="totalPages" @goto="goto" />
    </div>
  </StateBlock>

  <section v-if="blindCounties.length" class="card pad counties">
    <h2 class="chead">{{ $t('settlements.blindByCounty') }}</h2>
    <ul class="clist small">
      <li v-for="c in blindCounties" :key="c.county">
        <button type="button" class="linkish" @click="f.county = c.county; f.mode = 'blind'; apply()">
          {{ c.county }}
        </button>
        <span class="muted">
          {{ $t('settlements.blindOfTotal', { blind: c.blind, total: c.settlements }) }}
        </span>
      </li>
    </ul>
  </section>

  <details class="methodology">
    <summary>{{ $t('factions.methodology') }}</summary>
    <p class="small muted">{{ $t('settlements.methodology') }}</p>
    <p v-if="summary && summary.source" class="small muted">
      {{ $t('settlements.sourceNote', { source: summary.source.geography }) }}
    </p>
  </details>
</template>

<style scoped>
.intro { margin: -.4rem 0 1rem; max-width: 68ch; }
.heads { display: flex; flex-wrap: wrap; gap: .4rem 2rem; align-items: baseline; }
.head { display: flex; flex-direction: column; gap: .1rem; }
.big { font-size: 1.5rem; font-variant-numeric: tabular-nums; }
.scope { flex: 1 1 100%; margin: .2rem 0 0; }
.modes { display: flex; gap: .4rem; margin: 1rem 0 0; flex-wrap: wrap; }
/* The binning sits under the reading switch and reads as secondary to it: the
   reading is what the map is about, the binning only how it is drawn. */
.bins { margin-top: .45rem; align-items: center; }
.caveat { margin: .5rem 0 1rem; max-width: 72ch; }
/* Consecutive caveats (the constituency binning's two disclosures) read as a stack of
   related notes rather than as separate sections. */
.caveat + .caveat { margin-top: -.5rem; }
.searchform { display: flex; flex-wrap: wrap; gap: .6rem; align-items: center; }
.searchform input[type=search] { flex: 1 1 16rem; }
.sortlabel { display: inline-flex; align-items: center; gap: .35rem; }
.slist { list-style: none; padding: 0; margin: .3rem 0 0; display: grid; gap: .4rem; }
.srow {
  display: grid; gap: .2rem .9rem; align-items: center;
  grid-template-columns: minmax(0, 1fr) auto;
}
.sname { display: flex; flex-direction: column; gap: .05rem; min-width: 0; }
.sname > a { font-weight: 600; }
.scount .stat strong { font-variant-numeric: tabular-nums; }
/* "Never mentioned" is a result, so it is stated in words and kept quiet rather
   than dressed as an error. */
.scount .none { color: var(--ink-faint); font-style: italic; }
.linkish {
  background: none; border: 0; padding: 0; cursor: pointer;
  color: var(--ink-soft); text-decoration: underline; font: inherit;
}
.linkish:hover { color: var(--accent); }
.counties { margin-top: 1rem; }
.chead { font-size: 1rem; margin: 0 0 .4rem; color: var(--ink-soft); }
.clist { list-style: none; padding: 0; margin: 0; display: grid; gap: .2rem; }
.clist li { display: flex; gap: .4rem; flex-wrap: wrap; }
.methodology { margin-top: 1rem; }
.methodology summary { cursor: pointer; font-weight: 600; color: var(--ink-soft); font-size: .85rem; }
@media (max-width: 720px) { .srow { grid-template-columns: 1fr; } }
</style>
