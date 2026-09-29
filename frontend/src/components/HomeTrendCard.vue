<script setup>
// One example search at a time, beside the home page's search box (§SEA-8).
//
// The search's signature chart is its popularity-over-time histogram, and this is
// the invitation to it: a curated topic, how often the House has said it, and when.
// The card cycles through the topics on its own (lib/rotation.js), and the whole
// card opens that search. It replaced a grid of one card per topic — the same
// idea, at a tenth of the page.
//
// Histograms are fetched one at a time, for the topic on screen and the one after
// it, rather than all up front: most visitors will see two or three of them.
import { computed, reactive, watch } from 'vue'
import { api } from '../api.js'
import { store, periodLabel } from '../store.js'
import { useRotation } from '../lib/rotation.js'
import TrendChart from './TrendChart.vue'
import RotationControls from './RotationControls.vue'

// Literal Hungarian transcript queries, not UI text, so they are not translated.
const TERMS = ['költségvetés', 'korrupció', 'oktatás', 'infláció', 'Ukrajna', 'lakhatás',
  'demokrácia', 'egészségügy', 'migráció', 'Paks', 'adózás', 'kormányváltás']

const rot = useRotation(TERMS.length, { interval: 7000 })
const term = computed(() => TERMS[rot.index.value])

// scope|term → { trend, total } once loaded, `null` while in flight.
const cache = reactive({})
const scopeKey = computed(() => store.cycles.join(','))
const keyOf = (t) => `${scopeKey.value}|${t}`
const entry = computed(() => cache[keyOf(term.value)])

async function fetchTerm(t) {
  const key = keyOf(t)
  if (key in cache) return
  cache[key] = null
  try {
    const trend = await api.searchTrend({ q: t, period: store.cycles })
    cache[key] = { trend, total: (trend.buckets || []).reduce((s, b) => s + b.hits, 0) }
  } catch {
    cache[key] = { trend: null, total: 0 }
  }
}

watch([term, scopeKey], () => {
  fetchTerm(term.value)
  fetchTerm(TERMS[(rot.index.value + 1) % TERMS.length])
}, { immediate: true })

// Over more than one cycle, mark each cycle's start and end so the eras stay
// legible (as the search page does); a single cycle is the whole axis already.
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

const fmt = (n) => (n ?? 0).toLocaleString('hu-HU')
</script>

<template>
  <section
    class="card trendcard" :aria-label="$t('home.examplesTitle')"
    aria-roledescription="carousel" v-bind="rot.holdHandlers"
  >
    <router-link
      :to="{ name: 'search', query: { q: term } }" class="trendcard__link"
      :aria-live="rot.running.value ? 'off' : 'polite'"
    >
      <span class="trendcard__head">
        <span class="trendcard__term">{{ term }}</span>
        <span v-if="entry && entry.total" class="trendcard__count">
          {{ fmt(entry.total) }} {{ $t('search.results') }}
        </span>
      </span>
      <TrendChart
        v-if="entry && entry.trend && entry.trend.buckets.length"
        :key="term"
        :buckets="entry.trend.buckets" :granularity="entry.trend.granularity"
        :start="entry.trend.start" :end="entry.trend.end" :markers="cycleMarkers"
        :height="64" :unit="$t('search.results')"
      />
      <span v-else-if="entry" class="trendcard__empty soft small">{{ $t('home.examplesEmpty') }}</span>
      <span v-else class="trendcard__skeleton" aria-hidden="true"></span>
    </router-link>
    <RotationControls
      class="trendcard__foot"
      :count="TERMS.length" :index="rot.index.value" :running="rot.running.value"
      :stopped="rot.stopped.value" :turn="rot.turn.value" :interval="rot.interval"
      :names="TERMS" :label="$t('home.examplesTitle')" compact
      @go="rot.go" @toggle="rot.toggle"
    />
  </section>
</template>

<style scoped>
.trendcard { display: flex; flex-direction: column; padding: .8rem 1rem .5rem; min-width: 0; }
.trendcard__link { display: block; color: var(--ink); flex: 1; }
.trendcard__link:hover { text-decoration: none; }
.trendcard__link:hover .trendcard__term { color: var(--accent); }
.trendcard__head { display: flex; align-items: baseline; justify-content: space-between; gap: .6rem; margin-bottom: .35rem; }
.trendcard__term { font-weight: 700; font-size: 1.02rem; }
.trendcard__term::before { content: '„'; color: var(--ink-faint); }
.trendcard__term::after { content: '”'; color: var(--ink-faint); }
.trendcard__count { color: var(--ink-faint); font-size: .78rem; white-space: nowrap; }
.trendcard__empty { display: block; padding: 1.2rem 0; }
/* Holds the histogram's height while it loads, so the card never jumps. */
.trendcard__skeleton { display: block; height: calc(64px + 1.45rem); }
.trendcard__foot { margin-top: .35rem; }
</style>
