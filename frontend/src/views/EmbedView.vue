<script setup>
// Chrome-free single-chart view for <iframe> embedding (see EmbedButton). One
// route (`/embed/:kind`) dispatches on `kind` to the right chart + data fetch,
// wraps it in a compact branded frame (title · cycle scope · attribution link),
// and reads everything it needs from the URL — the electoral cycle from `?cycle=`
// and the chart-specific params (`q`, `id`, filters) from the query — so the
// embed is fully self-contained and reproduces exactly what the sharer saw.
// It deliberately ignores the visitor's saved global scope (store.cycles): an
// embed shows the cycles baked into its URL, not the reader's local preference.
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../api.js'
import { store, loadMeta, parseCycles, periodLabel, serializeCycles } from '../store.js'
import StateBlock from '../components/StateBlock.vue'
import TrendChart from '../components/TrendChart.vue'
import BarChart from '../components/BarChart.vue'
import SankeyDiagram from '../components/SankeyDiagram.vue'
import InterjectionGraph from '../components/InterjectionGraph.vue'
import PieChart from '../components/PieChart.vue'
import CohesionPanel from '../modules/votes/CohesionPanel.vue'
import TopicMixChart from '../components/TopicMixChart.vue'
import SettlementMentionMap from '../components/SettlementMentionMap.vue'
import { topicName } from '../lib/topics.js'
import { formatDate } from '../format.js'
import { queryList } from '../lib/searchScopes.js'

const props = defineProps({ kind: String })
const route = useRoute()
const { t, te, n: fmtNumber } = useI18n()

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
// A sitting day's figure is scoped by the day instead, and is named as the day
// page names itself (its share title); empty until the payload has said which.
const scopeLabel = computed(() => {
  if (props.kind === 'day-interjections') {
    const s = data.value?.session
    return s ? `${formatDate(s.date)} · ${s.sitting}. ${t('sessions.sitting').toLowerCase()}` : ''
  }
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
    case 'search-trend': {
      const trend = api.searchTrend({
        q: q.q, period: period.value, date_from: q.date_from,
        date_to: q.date_to, person_id: q.person_id, faction_id: q.faction_id,
        agenda_type: q.agenda_type, topic: queryList(q.topic),
      })
      // A query-less trend is one speaker's speeches over time (SEA-3), and the
      // chart is titled by their name — which only the id in the URL identifies,
      // so it is resolved alongside. A failed lookup costs the name, not the chart.
      if (q.q || !q.person_id) return trend
      return Promise.all([trend, api.representative(q.person_id).catch(() => null)])
        .then(([t_, rep]) => ({ ...t_, speaker: rep?.label || '' }))
    }
    case 'faction-speaking':
      return api.factions(period.value)
    case 'questions-sankey':
      return api.questionsSankey(
        period.value, route.query.types === '1', route.query.all === '1')
    case 'faction-cohesion':
      return api.voteCohesion({ period: period.value })
    case 'interjection-graph':
      return api.interjectionGraph(
        period.value, Number(route.query.top) || undefined, route.query.rank || undefined)
    // One sitting day's (§6E): the day page's own request, top included, so the
    // embed and the dialog share one cached payload — and one picture.
    case 'day-interjections':
      if (!q.session) return Promise.reject(new Error('no sitting day'))
      return api.interjectionGraph(undefined, DAY_INTERJECTION_TOP, undefined, String(q.session))
    // The settlement map (§6D). A binning this deployment cannot build — a link
    // from an instance with h3 — is drawn as points, as the page itself falls back,
    // rather than failing the whole figure over the shape it is drawn in.
    case 'settlement-map':
      return api.settlementMap(period.value).then(async (map) => {
        const seg = map.segments
        const offered = seg && seg.available ? seg.bins || [] : []
        if (!offered.includes(mapBins.value)) return { map, bins: 'points', segments: null }
        const segments = await api.settlementSegments(mapBins.value, period.value).catch(() => null)
        return { map, bins: segments ? mapBins.value : 'points', segments }
      })
    // The two agendas (TOPIC-9). Both halves are fetched, and the document one is
    // allowed to fail: a deployment can carry speech topics and no iromány ones,
    // and the figure is still the figure with one series.
    case 'topic-mix':
      return Promise.all([
        api.topicMix(period.value),
        api.billTopicMix(period.value).catch(() => null),
      ]).then(([speech, bill]) => ({ speech, bill }))
    case 'vote-participation':
      return Promise.all([
        api.representative(q.id, period.value),
        api.repStatistics(q.id, period.value),
      ]).then(([rep, stats]) => ({ rep, stats }))
    // One member's agenda (TOPIC-10). The name is fetched beside the mix for the
    // figure's title: the embed is read away from the profile, where "Egészségügy
    // 14 %" with nobody's name on it says nothing.
    case 'rep-topics':
      return Promise.all([
        api.repTopics(q.id, period.value),
        api.representative(q.id, period.value).catch(() => null),
      ]).then(([mix, rep]) => ({ mix, rep }))
    default:
      return Promise.reject(new Error('unknown embed kind'))
  }
}

onMounted(() => { loadMeta().catch(() => {}).finally(load) })

// ---- figures that fill the frame ------------------------------------------
// Most figures have a height of their own, and the snippet's iframe height is
// chosen to fit it. The interjection network has none: its frame follows the
// settled layout (InterjectionGraph's BOX_ASPECT), so in an iframe of fixed
// height a wide cloud left a band of blank under it and a tall one ran past the
// footer. It is pinned to the room instead — the body between the title and the
// footer, at whatever width the host page gave the iframe — and fills it.
// The map is the same case by another route: a fixed 520px would overflow a
// short iframe or leave a tall one half empty, so it takes the room too.
const FILL_KINDS = new Set(['interjection-graph', 'day-interjections', 'settlement-map'])
const fills = computed(() => FILL_KINDS.has(props.kind))
const bodyRef = ref(null)
const bodyW = ref(0)
const bodyH = ref(0)
let bodyObserver = null
onMounted(() => {
  if (!fills.value || typeof ResizeObserver === 'undefined' || !bodyRef.value) return
  bodyObserver = new ResizeObserver(([entry]) => {
    bodyW.value = entry.contentRect.width
    bodyH.value = entry.contentRect.height
  })
  bodyObserver.observe(bodyRef.value)
})
onBeforeUnmount(() => bodyObserver?.disconnect())
// A pixel short of the room, so rounding the frame's height can never push the
// footer down and give the iframe a scrollbar. 0 (not measured yet) = follow
// the layout, as on the page.
const fillAspect = computed(() => (bodyW.value && bodyH.value
  ? Math.max(0.3, (bodyH.value - 1) / bodyW.value) : 0))

// ---- day-interjections ----------------------------------------------------
// The day page's cap (SessionView's DAY_INTERJECTION_TOP): everyone on all but the
// rowdiest days.
const DAY_INTERJECTION_TOP = 40

// ---- settlement-map -------------------------------------------------------
// The reading and the binning, as the page's own URL carries them (`mode`, `bins`).
const mapMode = computed(() => (route.query.mode === 'blind' ? 'blind' : 'mentions'))
const mapBins = computed(() =>
  (['oevk', 'h3'].includes(route.query.bins) ? route.query.bins : 'points'))
// The caveat the page sets beside its map travels with the embed: an article is
// exactly where "mentions on the floor" gets read as "attention paid". Of the
// constituency binning's disclosures only the capital's goes with it — the area
// distortion is in the legend and a shared city is named on its own cell, but a
// Budapest missing from every cell is visible nowhere else.
const mapUnattributed = computed(() => {
  const u = data.value?.bins === 'oevk' ? data.value.segments?.unattributed : null
  return u && u.mentions
    ? t('settlements.oevkUnattributed', { names: u.names.join(', '), mentions: fmtNumber(u.mentions) })
    : ''
})

// ---- search-trend ---------------------------------------------------------
// The topics a query-less trend is drawn for, by name (the caption below).
const embedTopics = computed(() =>
  queryList(route.query.topic).map((l) => topicName(l, t, te)).join(', '))
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

// ---- rep-topics -----------------------------------------------------------
// The House's own share per topic, which the API already sends on every row — the
// baseline the member's bars are read against, exactly as on the profile.
const repTopicReference = computed(() => Object.fromEntries(
  (props.kind === 'rep-topics' ? data.value?.mix?.topics || [] : [])
    .map((topic) => [topic.label, topic.house_share])))

// ---- shared: title, empty state, on-site link -----------------------------
const title = computed(() => {
  switch (props.kind) {
    case 'search-trend':
      if (data.value?.query) return t('search.trendCaption', { q: data.value.query })
      // A query-less trend is a speaker's speeches, the speeches on a topic, or
      // both — titled as the search page titles it.
      if (embedTopics.value) {
        return data.value?.speaker
          ? t('search.trendCaptionSpeakerTopic', { name: data.value.speaker, topics: embedTopics.value })
          : t('search.trendCaptionTopic', { topics: embedTopics.value })
      }
      return data.value?.speaker
        ? t('search.trendCaptionSpeaker', { name: data.value.speaker })
        : t('nav.search')
    case 'faction-speaking': return t('factions.speakingTime')
    case 'questions-sankey': return t('questions.title')
    case 'interjection-graph': return t('interjections.title')
    case 'day-interjections': return t('interjections.dayTitle')
    case 'settlement-map':
      return mapMode.value === 'blind' ? t('settlements.embedTitleBlind') : t('settlements.embedTitle')
    case 'topic-mix': return t('topics.page.title')
    case 'faction-cohesion': return t('votes.cohesion.title')
    case 'vote-participation':
      return data.value?.rep?.label
        ? `${data.value.rep.label} — ${t('profile.voteBreakdown')}`
        : t('profile.voteBreakdown')
    case 'rep-topics':
      return data.value?.rep?.label
        ? `${data.value.rep.label} — ${t('profile.topics')}`
        : t('profile.topics')
    default: return 'Parlamonitor'
  }
})

const isEmpty = computed(() => {
  if (!data.value) return false
  switch (props.kind) {
    case 'search-trend': return !(data.value.buckets && data.value.buckets.length)
    case 'faction-speaking': return factionBars.value.length === 0
    case 'questions-sankey': return !(data.value.total > 0)
    case 'interjection-graph':
    case 'day-interjections': return !(data.value.links && data.value.links.length)
    case 'settlement-map': return !(data.value.map.points || []).length
    case 'topic-mix': return !(data.value.speech?.topics || []).length
                             && !(data.value.bill?.topics || []).length
    // Frakcióelemzés is a single-cycle analysis (analyses/registry.js): a
    // hand-built embed URL naming several cycles would pool Houses that never
    // sat together, so it shows nothing rather than a meaningless matrix.
    case 'faction-cohesion':
      return period.value.length !== 1
        || !(data.value.factions && data.value.factions.length >= 2)
    case 'vote-participation': return !(voteBreakdown.value && voteBreakdown.value.total)
    case 'rep-topics': return !(data.value.mix?.topics || []).length
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
    case 'interjection-graph':
      path = '/analyses/interjections'
      if (q.top) usp.set('top', q.top)
      if (q.rank) usp.set('rank', q.rank)
      break
    // The day page, where the figure's button is. A day is its own scope, so the
    // link carries no cycle: the page finds its day without one.
    case 'day-interjections':
      path = q.session ? `/sessions/${encodeURIComponent(q.session)}` : '/sessions'
      usp.delete('cycle')
      break
    case 'settlement-map':
      path = '/analyses/settlements'
      if (mapMode.value === 'blind') usp.set('mode', 'blind')
      if (mapBins.value !== 'points') usp.set('bins', mapBins.value)
      break
    case 'faction-cohesion':
      path = '/analyses/faction-cohesion'
      if (q.tab) usp.set('tab', q.tab)
      break
    case 'topic-mix':
      path = '/analyses/topics'
      if (q.by) usp.set('by', q.by)
      break
    case 'vote-participation': path = q.id ? `/representatives/${q.id}` : '/representatives'; break
    case 'rep-topics': path = q.id ? `/representatives/${q.id}` : '/representatives'; break
  }
  const qs = usp.toString()
  return `${location.origin}${path}${qs ? '?' + qs : ''}`
})
</script>

<template>
  <div class="embed" :class="{ 'embed-fill': fills }">
    <header class="embed-head">
      <h1 class="embed-title">{{ title }}</h1>
      <span v-if="scopeLabel" class="embed-scope">{{ scopeLabel }}</span>
    </header>

    <div ref="bodyRef" class="embed-body" :class="{ 'embed-stack': kind === 'settlement-map' }">
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

          <!-- Interjection network (§6E/INT-5), for the cycle scope or for one
               sitting day. Not clickable in an embed: the drill-down list is the
               page's (or the day dialog's), and a click inside an iframe cannot
               go anywhere useful. -->
          <InterjectionGraph
            v-else-if="kind === 'interjection-graph' || kind === 'day-interjections'"
            :nodes="data.nodes" :links="data.links" :metric="data.rank"
            :aspect="fillAspect"
            :caption="$t('interjections.chartCaption')" :show-caption="false"
            :labels="{ zoomIn: $t('interjections.zoomIn'),
                       zoomOut: $t('interjections.zoomOut'),
                       reset: $t('interjections.resetView'),
                       fullscreen: $t('interjections.fullscreen'),
                       exitFullscreen: $t('interjections.exitFullscreen'),
                       made: $t('interjections.made'),
                       received: $t('interjections.received') }"
          />

          <!-- Settlement mentions (§6D TEL-6/TEL-7), in the reading and binning
               the sharer had open. A point opens nothing here — the settlement's
               page is the site's — but a cell still zooms into itself. -->
          <template v-else-if="kind === 'settlement-map'">
            <SettlementMentionMap
              :points="data.map.points" :map="data.map.map" :max="data.map.max"
              :mode="mapMode" :bins="data.bins" :segments="data.segments" fill
            />
            <p class="embed-caveat">{{ $t('settlements.mapCaveat') }}</p>
            <p v-if="mapUnattributed" class="embed-caveat">{{ mapUnattributed }}</p>
          </template>

          <!-- The two agendas (TOPIC-9). Rows are not clickable here: what a
               click opens is the page's detail panel, which an iframe has
               nowhere to put. -->
          <TopicMixChart
            v-else-if="kind === 'topic-mix'"
            :speech="data.speech" :bill="data.bill"
            :sort-by="route.query.by === 'bills' ? 'bill' : 'speech'"
            :interactive="false"
            :caption="$t('topics.page.chartCaption')"
          />

          <!-- Faction vote analysis (VOTE-8) -->
          <CohesionPanel
            v-else-if="kind === 'faction-cohesion'"
            :data="data" :scope-label="scopeLabel" hide-header :tab="cohesionTab"
          />

          <!-- One member's topic mix (TOPIC-10), with the House's own share as
               the tick each bar is read against. Every topic is shown: an embed
               has no "show all" to open. -->
          <TopicMixChart
            v-else-if="kind === 'rep-topics'"
            :speech="data.mix"
            :reference="repTopicReference"
            :reference-label="$t('profile.topicsHouse')"
            :series-label="$t('profile.topicsOwn')"
            :interactive="false"
            :caption="$t('profile.topicsCaption')"
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
/* Exactly the iframe's height, and a body that takes what is left of it rather
   than what its content asks for — the figure is then sized from the body, and
   the body never from the figure. */
.embed-fill { height: 100vh; }
.embed-fill .embed-body { min-height: 0; }
/* A figure with notes under it (the map): the figure flexes, the notes keep the
   lines they need. */
.embed-stack { display: flex; flex-direction: column; gap: .45rem; }
.embed-caveat { margin: 0; font-size: .74rem; line-height: 1.4; color: var(--ink-soft); }

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
