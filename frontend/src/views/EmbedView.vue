<script setup>
// Chrome-free single-chart view for <iframe> embedding (see EmbedButton). One
// route (`/embed/:kind`) dispatches on `kind` to the right chart + data fetch,
// wraps it in a compact branded frame (title · cycle scope · attribution link),
// and reads everything it needs from the URL — the electoral cycle from `?cycle=`
// and the chart-specific params (`q`, `id`, filters) from the query — so the
// embed is fully self-contained and reproduces exactly what the sharer saw.
// It deliberately ignores the visitor's saved global scope (store.cycles): an
// embed shows the cycles baked into its URL, not the reader's local preference.
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../api.js'
import { store, loadMeta, parseCycles, periodLabel, serializeCycles } from '../store.js'
import StateBlock from '../components/StateBlock.vue'
import TrendChart from '../components/TrendChart.vue'
import BarChart from '../components/BarChart.vue'
import SankeyDiagram from '../components/SankeyDiagram.vue'
import PieChart from '../components/PieChart.vue'
import CohesionPanel from '../modules/votes/CohesionPanel.vue'

const props = defineProps({ kind: String })
const route = useRoute()
const { t } = useI18n()

// The cycle scope carried in the URL: `all` (or absent) → [] (all cycles), else
// the comma-separated electoral-period numbers. This — not store.cycles — scopes
// every embed fetch. Validated against /meta once it has loaded; before that the
// raw numbers are used, so the first fetch isn't delayed by the manifest.
const period = computed(() => {
  const raw = route.query.cycle
  if (store.meta) return parseCycles(raw, store.meta.periods) || []
  // /meta hasn't landed (or failed): there is nothing to validate against, so
  // take the numbers at face value rather than silently widening to all cycles.
  return [...new Set(String(raw ?? '').split(',').map(Number).filter(Number.isFinite))]
    .sort((a, b) => a - b)
})

const data = ref(null)
const loading = ref(false)
const error = ref(false)

// Newest cycle first, matching how the site itself names a multi-cycle scope
// (store.currentCycleLabel) — the scope is `period`, which is sorted ascending.
const scopeLabel = computed(() => {
  if (!period.value.length) return t('cycle.all')
  return [...period.value].sort((a, b) => b - a).map((n) => {
    const p = (store.meta?.periods || []).find((x) => x.number === n)
    return p ? periodLabel(p) : String(n)
  }).join(', ')
})

// ---- per-kind data fetch --------------------------------------------------
let loadSeq = 0
async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false
  try {
    const res = await fetchForKind()
    if (seq === loadSeq) data.value = res
  } catch {
    if (seq === loadSeq) error.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function fetchForKind() {
  const q = route.query
  switch (props.kind) {
    case 'search-trend':
      return api.searchTrend({
        q: q.q, period: period.value, date_from: q.date_from,
        date_to: q.date_to, faction_id: q.faction_id, agenda_type: q.agenda_type,
      })
    case 'faction-speaking':
      return api.factions(period.value)
    case 'questions-sankey':
      return api.questionsSankey(
        period.value, route.query.types === '1', route.query.all === '1')
    case 'faction-cohesion':
      return api.voteCohesion({ period: period.value })
    case 'vote-participation':
      return Promise.all([
        api.representative(q.id, period.value),
        api.repStatistics(q.id, period.value),
      ]).then(([rep, stats]) => ({ rep, stats }))
    default:
      return Promise.reject(new Error('unknown embed kind'))
  }
}

onMounted(() => { loadMeta().catch(() => {}).finally(load) })

// ---- search-trend ---------------------------------------------------------
// Whenever the axis spans more than one cycle, mark each cycle's start/end
// (mirrors the search page). Empty for a single cycle — the axis is then just
// that cycle.
const cycleMarkers = computed(() => {
  if (period.value.length === 1 || !store.meta) return []
  const inScope = (p) => !period.value.length || period.value.includes(p.number)
  const out = []
  for (const p of (store.meta.periods || []).filter(inScope)) {
    const label = periodLabel(p)
    if (p.date_start) out.push({ date: p.date_start, label })
    if (p.date_end) out.push({ date: p.date_end, label })
  }
  return out
})

// ---- faction-speaking -----------------------------------------------------
const factionBars = computed(() =>
  (props.kind === 'faction-speaking' && data.value ? data.value.factions : [])
    .filter((f) => f.speaking_seconds > 0)
    .map((f) => ({ label: f.label, value: Math.round(f.speaking_seconds / 60), color: f.color })))

// ---- questions-sankey -----------------------------------------------------
function nodeLabel(n) {
  if (n.label) return n.label
  if (n.kind === 'type') return t('questions.type.' + n.main_type)
  return t('questions.node.' + n.kind)
}
const sankeyNodes = computed(() => (data.value?.nodes || []).map((n) => ({
  side: n.side,
  column: n.column,
  label: nodeLabel(n),
  color: n.color || (n.side === 'asker' ? 'var(--accent)' : '#9c9188'),
})))
// The type column is shown only when the embed URL carries types=1 (hidden by
// default, matching the main view).
const sankeyHeadings = computed(() => route.query.types === '1'
  ? [t('questions.typeHeading'), t('questions.askerHeading'), t('questions.answererHeading')]
  : [t('questions.askerHeading'), t('questions.answererHeading')])

// ---- vote-participation ---------------------------------------------------
// Same segment palette + ordering as the representative profile pie.
const VB_SEGMENTS = [
  { key: 'voted', color: '#2e7d32' },
  { key: 'novote', color: '#c79a2e' },
  { key: 'absent', color: '#7c8288' },
  { key: 'not_present', color: '#3f434a' },
]
const VB_LABEL = {
  voted: 'profile.vbVoted', novote: 'profile.vbNovote',
  absent: 'profile.vbAbsent', not_present: 'profile.vbNotPresent',
}
const voteBreakdown = computed(() => data.value?.stats?.totals?.vote_breakdown || null)
const pieSegments = computed(() => {
  const b = voteBreakdown.value
  if (!b) return []
  // No per-segment `to` links here — an embed must not try to navigate the host
  // page inside the iframe (the footer link opens the full profile instead).
  return VB_SEGMENTS.map((s) => ({ ...s, label: t(VB_LABEL[s.key]), value: b[s.key] || 0 }))
})
const pieExtra = computed(() => {
  const n = voteBreakdown.value?.not_mp || 0
  return n ? [{ key: 'not_mp', label: t('profile.vbNotMp'), value: n, color: '#c3c7cc' }] : []
})

// ---- faction-cohesion -----------------------------------------------------
// Which of the three views the sharer had open, straight from the URL. The
// panel validates it and keeps its tabs clickable inside the iframe.
const cohesionTab = computed(() => (typeof route.query.tab === 'string' ? route.query.tab : ''))

// ---- shared: title, empty state, on-site link -----------------------------
const title = computed(() => {
  switch (props.kind) {
    case 'search-trend':
      return data.value?.query
        ? t('search.trendCaption', { q: data.value.query })
        : t('nav.search')
    case 'faction-speaking': return t('factions.speakingTime')
    case 'questions-sankey': return t('questions.title')
    case 'faction-cohesion': return t('votes.cohesion.title')
    case 'vote-participation':
      return data.value?.rep?.label
        ? `${data.value.rep.label} — ${t('profile.voteBreakdown')}`
        : t('profile.voteBreakdown')
    default: return 'Parlamonitor'
  }
})

const isEmpty = computed(() => {
  if (!data.value) return false
  switch (props.kind) {
    case 'search-trend': return !(data.value.buckets && data.value.buckets.length)
    case 'faction-speaking': return factionBars.value.length === 0
    case 'questions-sankey': return !(data.value.total > 0)
    case 'faction-cohesion': return !(data.value.factions && data.value.factions.length >= 2)
    case 'vote-participation': return !(voteBreakdown.value && voteBreakdown.value.total)
    default: return true
  }
})

// The full interactive page this figure comes from, as an absolute URL carrying
// the same cycle + params. Opened in a new tab (target=_blank) since a link
// inside an iframe would otherwise be trapped in the embed.
const siteHref = computed(() => {
  const q = route.query
  const usp = new URLSearchParams({ cycle: serializeCycles(period.value) })
  let path = '/'
  switch (props.kind) {
    case 'search-trend':
      path = '/search'
      for (const k of ['q', 'date_from', 'date_to', 'faction_id', 'agenda_type']) {
        if (q[k]) usp.set(k, q[k])
      }
      break
    case 'faction-speaking': path = '/representatives/factions'; break
    case 'questions-sankey': path = '/analyses/questions'; break
    case 'faction-cohesion':
      path = '/analyses/faction-cohesion'
      if (q.tab) usp.set('tab', q.tab)
      break
    case 'vote-participation': path = q.id ? `/representatives/${q.id}` : '/representatives'; break
  }
  return `${location.origin}${path}?${usp.toString()}`
})
</script>

<template>
  <div class="embed">
    <header class="embed-head">
      <h1 class="embed-title">{{ title }}</h1>
      <span class="embed-scope">{{ scopeLabel }}</span>
    </header>

    <div class="embed-body">
      <StateBlock
        :loading="loading" :error="error"
        :empty="isEmpty" :empty-text="$t('embed.noData')"
        @retry="load"
      >
        <template v-if="data">
          <!-- Search popularity histogram (SEA-8) -->
          <TrendChart
            v-if="kind === 'search-trend'"
            :buckets="data.buckets" :granularity="data.granularity"
            :start="data.start" :end="data.end" :markers="cycleMarkers"
            :caption="''" :unit="$t('search.results')"
          />

          <!-- Faction speaking time (REP-4) -->
          <BarChart
            v-else-if="kind === 'faction-speaking'"
            :items="factionBars"
            :caption="$t('factions.speakingTime')" :show-caption="false"
            unit="perc" :value-format="(v) => v + ' p'"
          />

          <!-- Questions Sankey (BILL-11) -->
          <SankeyDiagram
            v-else-if="kind === 'questions-sankey'"
            :nodes="sankeyNodes" :links="data.links"
            :caption="$t('questions.chartCaption')" :show-caption="false"
            :column-headings="sankeyHeadings"
          />

          <!-- Faction vote analysis (VOTE-8) -->
          <CohesionPanel
            v-else-if="kind === 'faction-cohesion'"
            :data="data" :scope-label="scopeLabel" hide-header :tab="cohesionTab"
          />

          <!-- Representative vote participation (REP-3) -->
          <PieChart
            v-else-if="kind === 'vote-participation'"
            :segments="pieSegments" :extra="pieExtra"
            :extra-note="pieExtra.length ? $t('profile.vbNotMpNote') : ''"
            :caption="''" :total-label="$t('profile.vbUnit')"
          />
        </template>
      </StateBlock>
    </div>

    <footer class="embed-foot">
      <a :href="siteHref" target="_blank" rel="noopener" class="embed-brand">
        <img class="embed-mark" src="/parlamonitor.png" alt="" aria-hidden="true" />
        <span>Parlamonitor</span>
      </a>
      <a :href="siteHref" target="_blank" rel="noopener" class="embed-open">{{ $t('embed.openInteractive') }}</a>
    </footer>
  </div>
</template>

<style scoped>
/* Fills the whole iframe viewport, background included, so the figure reads as a
   self-contained card wherever it is embedded. Footer pinned to the bottom. */
.embed {
  box-sizing: border-box; min-height: 100vh;
  display: flex; flex-direction: column; gap: .7rem;
  padding: .9rem 1.1rem; background: var(--surface); color: var(--ink);
}
.embed-head { display: flex; align-items: baseline; justify-content: space-between; gap: .8rem; flex-wrap: wrap; }
.embed-title { margin: 0; font-size: 1.02rem; line-height: 1.3; }
.embed-scope {
  flex: none; font-size: .74rem; font-weight: 700; color: var(--accent);
  background: var(--accent-soft); border-radius: 999px; padding: .18rem .6rem;
}
.embed-body { flex: 1; min-width: 0; }

.embed-foot {
  display: flex; align-items: center; justify-content: space-between; gap: .8rem;
  padding-top: .55rem; margin-top: .2rem; border-top: 1px solid var(--line);
  font-size: .78rem;
}
.embed-brand { display: inline-flex; align-items: center; gap: .4rem; font-weight: 800; color: var(--ink); }
.embed-brand:hover { text-decoration: none; color: var(--accent); }
.embed-mark { width: 1.15rem; height: 1.15rem; object-fit: contain; border-radius: 4px; display: block; }
.embed-open { color: var(--ink-soft); font-weight: 600; }
.embed-open:hover { color: var(--accent); }
</style>
