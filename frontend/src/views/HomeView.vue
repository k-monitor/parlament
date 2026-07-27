<script setup>
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'
import { store, loadMeta, periodLabel } from '../store.js'
import TrendChart from '../components/TrendChart.vue'
import DonateCard from '../components/DonateCard.vue'

const router = useRouter()
const q = ref('')
const counts = computed(() => (store.meta && store.meta.counts) || {})
function fmt(n) { return (n ?? 0).toLocaleString('hu-HU') }

// Earliest year the corpus reaches back to, taken from the electoral periods so
// the framing line above the stats reflects whatever data is actually loaded
// (e.g. "1990" once the full archive is in, "2014" on a partial dev DB) rather
// than a hard-coded date. null when no dated period is known.
const startYear = computed(() => {
  const years = ((store.meta && store.meta.periods) || [])
    .map((p) => (p.date_start ? Number(String(p.date_start).slice(0, 4)) : null))
    .filter((y) => Number.isFinite(y))
  return years.length ? Math.min(...years) : null
})
function go() {
  if (q.value.trim()) router.push({ name: 'search', query: { q: q.value.trim() } })
}
const showProceedings = computed(() => store.moduleEnabled('proceedings'))
const showReps = computed(() => store.moduleEnabled('representatives'))

// Curated example searches (§SEA-8): a handful of evergreen topics, each shown
// with its popularity-over-time histogram so the home page invites exploration
// and previews the search's signature chart. Clicking one opens the full search.
// These are literal Hungarian transcript queries, not UI text, so they are not
// translated.
const EXAMPLE_QUERIES = ['költségvetés', 'korrupció', 'oktatás', 'infláció', 'Ukrajna', 'demokrácia', 'egészségügy', 'migráció', 'kormányváltás', 'adózás']
const examples = reactive(Object.fromEntries(
  EXAMPLE_QUERIES.map((term) => [term, { trend: null, total: 0, ready: false }])))

// Only a first batch is shown up front (two full rows at the widest grid); the
// rest stay hidden behind a "more topics" toggle so the home page stays compact
// and only fetches the visible histograms until the reader asks for more.
const INITIAL_EXAMPLES = 3
const showAllExamples = ref(false)
const visibleQueries = computed(() =>
  showAllExamples.value ? EXAMPLE_QUERIES : EXAMPLE_QUERIES.slice(0, INITIAL_EXAMPLES))

// On the all-cycles view the histograms span the whole corpus, so mark each
// electoral cycle's start and end with a line to keep the eras legible. Empty
// for a single-cycle scope (the axis is then just that cycle).
const cycleMarkers = computed(() => {
  if (store.cycle !== null || !store.meta) return []
  const out = []
  for (const p of store.meta.periods || []) {
    const label = periodLabel(p)
    if (p.date_start) out.push({ date: p.date_start, label })
    if (p.date_end) out.push({ date: p.date_end, label })
  }
  return out
})

// The histograms honour the global cycle scope (§4A) like every other view.
// Monotonic seq guards against out-of-order responses when the cycle switches;
// loadedCycle dedupes the onMounted + watcher double-trigger for the same cycle.
let exSeq = 0
let loadedCycle
const loadedTerms = new Set()
async function loadExamples() {
  // A cycle switch invalidates every histogram, so reset and refetch from
  // scratch; otherwise (e.g. the "more topics" toggle) fetch only the newly
  // visible terms we haven't loaded yet for the current cycle.
  if (loadedCycle !== store.cycle) {
    loadedCycle = store.cycle
    loadedTerms.clear()
    for (const term of EXAMPLE_QUERIES) examples[term].ready = false
    exSeq++ // invalidate any in-flight fetch from the previous cycle
  }
  // seq is per-cycle, not per-call: batches within one cycle (initial + "more")
  // are all valid, so only a cycle switch drops still-pending responses.
  const seq = exSeq
  const terms = visibleQueries.value.filter((term) => !loadedTerms.has(term))
  if (!terms.length) return
  for (const term of terms) loadedTerms.add(term)
  await Promise.all(terms.map(async (term) => {
    try {
      const t = await api.searchTrend({ q: term, period: store.cycle })
      if (seq !== exSeq) return
      examples[term].trend = t
      examples[term].total = (t.buckets || []).reduce((s, b) => s + b.hits, 0)
    } catch {
      if (seq !== exSeq) return
      examples[term].trend = null
      examples[term].total = 0
    } finally {
      if (seq === exSeq) examples[term].ready = true
    }
  }))
}

onMounted(async () => {
  await loadMeta().catch(() => {})
  if (showProceedings.value) loadExamples()
})
watch(() => store.cycle, () => { if (showProceedings.value) loadExamples() })
// Revealing the rest of the topics fetches their histograms on demand.
watch(showAllExamples, () => { if (showProceedings.value) loadExamples() })
</script>

<template>
  <section class="hero card pad">
    <h1>{{ $t('app.title') }}</h1>
    <p class="hero-lead">{{ $t('app.tagline') }}</p>
    <p class="soft hero-sub">{{ $t('app.taglineSub') }}</p>

    <form v-if="showProceedings" class="searchbar" role="search" @submit.prevent="go">
      <input
        v-model="q" type="search" :placeholder="$t('home.searchPlaceholder')"
        :aria-label="$t('home.searchPlaceholder')" autofocus
      />
      <button class="btn" type="submit">{{ $t('home.searchButton') }}</button>
    </form>
  </section>

  <section class="grid explore-grid">
    <router-link v-if="showProceedings" :to="{ name: 'search' }" class="card feature">
      <span class="feature__icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
        </svg>
      </span>
      <h2>{{ $t('home.exploreSearch') }}</h2>
      <p class="soft">{{ $t('home.exploreSearchDesc') }}</p>
      <span class="feature__cta" aria-hidden="true">→</span>
    </router-link>

    <router-link v-if="showReps" :to="{ name: 'representatives' }" class="card feature">
      <span class="feature__icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M16 20v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1" /><circle cx="9.5" cy="7" r="3.5" />
          <path d="M21 20v-1a4 4 0 0 0-3-3.87M16.5 3.6a3.5 3.5 0 0 1 0 6.8" />
        </svg>
      </span>
      <h2>{{ $t('home.exploreReps') }}</h2>
      <p class="soft">{{ $t('home.exploreRepsDesc') }}</p>
      <span class="feature__cta" aria-hidden="true">→</span>
    </router-link>

    <router-link v-if="showProceedings" :to="{ name: 'sessions' }" class="card feature">
      <span class="feature__icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="4.5" width="18" height="16" rx="2" /><path d="M3 9h18M8 2.5v4M16 2.5v4" />
          <path d="M7.5 13h4M7.5 16.5h9" />
        </svg>
      </span>
      <h2>{{ $t('home.exploreSessions') }}</h2>
      <p class="soft">{{ $t('home.exploreSessionsDesc') }}</p>
      <span class="feature__cta" aria-hidden="true">→</span>
    </router-link>
  </section>

  <section v-if="showProceedings" class="examples" aria-labelledby="examples-title">
    <div class="examples__head">
      <h2 id="examples-title">{{ $t('home.examplesTitle') }}</h2>
      <p class="soft">{{ $t('home.examplesLead') }}</p>
    </div>
    <div class="grid examples-grid">
      <router-link
        v-for="term in visibleQueries" :key="term"
        :to="{ name: 'search', query: { q: term } }" class="card pad example"
      >
        <div class="example__head">
          <span class="example__term">{{ term }}</span>
          <span v-if="examples[term].ready && examples[term].total" class="example__count">
            {{ fmt(examples[term].total) }} {{ $t('search.results') }}
          </span>
        </div>
        <TrendChart
          v-if="examples[term].trend && examples[term].trend.buckets.length"
          :buckets="examples[term].trend.buckets"
          :granularity="examples[term].trend.granularity"
          :start="examples[term].trend.start"
          :end="examples[term].trend.end"
          :markers="cycleMarkers"
          :height="72" :unit="$t('search.results')"
        />
        <p v-else-if="examples[term].ready" class="soft small example__empty">
          {{ $t('home.examplesEmpty') }}
        </p>
        <div v-else class="example__skeleton" aria-hidden="true"></div>
      </router-link>
    </div>
    <div v-if="!showAllExamples && EXAMPLE_QUERIES.length > INITIAL_EXAMPLES" class="examples__more">
      <button class="btn secondary" type="button" @click="showAllExamples = true">
        {{ $t('home.examplesMore') }}
      </button>
    </div>
  </section>

  <section v-if="store.meta" class="stats-section card pad">
    <p class="stats-lead">
      {{ startYear ? $t('home.statsLead', { year: startYear }) : $t('home.statsLeadNoYear') }}
    </p>
    <dl class="stats" aria-label="corpus statistics">
      <div><dt>{{ fmt(counts.sentences) }}</dt><dd>{{ $t('home.stats.sentences') }}</dd></div>
      <div><dt>{{ fmt(counts.speeches) }}</dt><dd>{{ $t('home.stats.speeches') }}</dd></div>
      <div><dt>{{ fmt(counts.sessions) }}</dt><dd>{{ $t('home.stats.sessions') }}</dd></div>
      <div><dt>{{ fmt(counts.representatives) }}</dt><dd>{{ $t('home.stats.representatives') }}</dd></div>
    </dl>
  </section>

  <section class="grid explore-grid home-donate-row">
    <DonateCard class="home-donate" />
  </section>
</template>

<style scoped>
.hero { margin-bottom: 1.5rem; }
.hero-lead { margin: .4rem 0 .4rem; font-size: 1.5rem; font-weight: 700; color: var(--ink); max-width: 30ch; }
.hero-sub { font-size: 1.1rem; max-width: 60ch; }
.searchbar { display: flex; gap: .5rem; margin: 1.2rem 0; max-width: 640px; }
.searchbar input { flex: 1; font-size: 1.05rem; padding: .7rem .8rem; }
.stats-section { margin-top: 1.5rem; }
.stats-lead { margin: 0 0 .6rem; color: var(--ink); font-size: 1.05rem; }
/* Even four-column grid so the numbers sit on a regular rhythm and the
   dividers land at consistent intervals regardless of each number's width. */
.stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0; max-width: 680px; }
.stats div { margin: 0; padding: 0 1.6rem; border-left: 1px solid var(--line); }
.stats div:first-child { padding-left: 0; border-left: 0; }
.stats dt { font-size: 1.6rem; font-weight: 800; color: var(--accent); }
.stats dd { margin: 0; color: var(--ink-faint); font-size: .9rem; }
/* On narrow screens fold to a 2×2 grid, keeping the divider only mid-row. */
@media (max-width: 620px) {
  .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); row-gap: 1.1rem; max-width: 360px; }
  .stats div { padding-left: 1.4rem; }
  .stats div:nth-child(odd) { padding-left: 0; border-left: 0; }
}
/* Shared column tracks for the donate row + the three feature cards, so the
   donate card's edges line up exactly with the cards below it. */
.explore-grid { grid-template-columns: 1fr; }
@media (min-width: 520px) { .explore-grid { grid-template-columns: repeat(2, 1fr); } }
@media (min-width: 780px) { .explore-grid { grid-template-columns: repeat(3, 1fr); } }

.home-donate-row { margin-top: 1.5rem; }
/* Spans all three tracks so it lines up with the full width of the cards below. */
.home-donate { grid-column: 1 / -1; }

/* Example searches: a curated set of topics, each with its popularity histogram
   (§SEA-8) as a teaser. The card is a link into the full search for that term. */
.examples { margin-top: 1.5rem; margin-bottom: 1.5rem; }
.examples__head { margin-bottom: 1rem; }
.examples__head h2 { margin: 0 0 .3rem; font-size: 1.25rem; }
.examples__head p { margin: 0; max-width: 70ch; }
.examples-grid { grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }
.examples__more { display: flex; justify-content: center; margin-top: 1.2rem; }
.example {
  display: flex;
  flex-direction: column;
  color: var(--ink);
  transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease;
}
.example:hover {
  text-decoration: none;
  border-color: var(--accent);
  transform: translateY(-3px);
  box-shadow: 0 2px 6px rgba(0,0,0,.07), 0 12px 28px rgba(0,0,0,.09);
}
.example__head { display: flex; align-items: baseline; justify-content: space-between; gap: .6rem; margin-bottom: .5rem; }
.example__term { font-weight: 700; font-size: 1.05rem; }
.example__term::before { content: '„'; color: var(--ink-faint); }
.example__term::after { content: '”'; color: var(--ink-faint); }
.example__count { color: var(--ink-faint); font-size: .8rem; white-space: nowrap; }
.example__empty { margin: .3rem 0 0; }
/* Reserve the histogram's height while its data loads so the grid doesn't jump. */
.example__skeleton { height: calc(72px + 1.45rem); }
@media (prefers-reduced-motion: reduce) {
  .example { transition: none; }
  .example:hover { transform: none; }
}

.feature {
  position: relative;
  display: flex;
  flex-direction: column;
  padding: 1.4rem 1.4rem 1.6rem;
  color: var(--ink);
  transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease;
}
.feature:hover {
  text-decoration: none;
  border-color: var(--accent);
  transform: translateY(-3px);
  box-shadow: 0 2px 6px rgba(0,0,0,.07), 0 12px 28px rgba(0,0,0,.09);
}
.feature__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 12px;
  background: var(--accent-soft);
  color: var(--accent);
  margin-bottom: 1rem;
  transition: background .15s ease, color .15s ease;
}
.feature__icon svg { width: 26px; height: 26px; }
.feature:hover .feature__icon { background: var(--accent); color: var(--accent-ink); }
.feature h2 { margin: 0 0 .4rem; font-size: 1.15rem; }
.feature p { margin: 0; flex: 1; }
.feature__cta {
  color: var(--accent);
  font-size: 1.25rem;
  font-weight: 700;
  line-height: 1;
  margin-top: 1rem;
  transition: transform .15s ease;
}
.feature:hover .feature__cta { transform: translateX(4px); }
@media (prefers-reduced-motion: reduce) {
  .feature, .feature__icon, .feature__cta { transition: none; }
  .feature:hover { transform: none; }
  .feature:hover .feature__cta { transform: none; }
}
</style>
