<script setup>
// The site's figures, one at a time, on the home page: the settlement map, the
// interjection network, the topic mix and the speaking-time ranking, taking turns
// (lib/rotation.js).
//
// They are the real, interactive components, not pictures of them: a reader can
// hover a town, drag a member, open a topic — and every such click lands on the
// full page for exactly that thing, which is what the card is for. Touching the
// figure stops the rotation for good (`stop()`): nobody wants the map they are
// panning to turn into a different figure under their hand.
//
// One height for every figure (`--fig-h`), so that a turn never moves the page:
// the map is sized to it, the network is pinned to its aspect, and the topic mix
// and the speaker bars show as many rows as fit. Each figure is gated on the module behind it, and
// one with nothing to draw in this scope is left out of the turn.
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../api.js'
import { store, currentCycleLabel } from '../store.js'
import { useRotation } from '../lib/rotation.js'
import RotationControls from './RotationControls.vue'
import BarChart from './BarChart.vue'
import { formatSpeakingTime } from '../format.js'

// Loaded with the figure, not with the home page: Leaflet and d3 are the two
// heaviest libraries on the site, and each is needed only while its figure shows.
const SettlementMentionMap = defineAsyncComponent(() => import('./SettlementMentionMap.vue'))
const InterjectionGraph = defineAsyncComponent(() => import('./InterjectionGraph.vue'))
const TopicMixChart = defineAsyncComponent(() => import('./TopicMixChart.vue'))

const router = useRouter()
const { t } = useI18n()

// How many members the network draws, topics the mix lists, and speakers the
// ranking bars, in a card.
const GRAPH_TOP = 10
const TOPIC_ROWS = 9
const SPEAKER_ROWS = 10

const FIGURES = [
  {
    id: 'map',
    on: () => store.moduleEnabled('settlements'),
    load: (period) => api.settlementMap(period),
    usable: (d) => !!(d.points && d.points.length && d.max),
    link: { name: 'settlements' },
  },
  {
    id: 'interjections',
    on: () => store.moduleEnabled('interjections'),
    load: (period) => api.interjectionGraph(period, GRAPH_TOP),
    usable: (d) => !!(d.links && d.links.length),
    link: { name: 'interjections' },
  },
  {
    id: 'topics',
    on: () => store.moduleEnabled('proceedings') && store.featureEnabled('speech_topics'),
    load: (period) => api.topicMix(period),
    usable: (d) => !!(d.topics && d.topics.length),
    link: { name: 'topics' },
  },
  {
    // The same ranking the speaking-time fact tops and the Felszólalók list opens
    // on (its default sort), so the bars here are that list's first rows.
    id: 'speakers',
    on: () => store.moduleEnabled('representatives'),
    load: (period) => api.representatives({ period, sort: 'speaking_time', limit: SPEAKER_ROWS }),
    usable: (d) => (d.representatives || []).some((r) => r.speaking_seconds > 0),
    link: { name: 'representatives' },
  },
]

const figures = ref([])     // [{ id, data, link }] — the ones with something to draw
const loading = ref(true)
let seq = 0
async function load() {
  const mine = ++seq
  loading.value = true
  const period = store.cycles
  const enabled = FIGURES.filter((f) => f.on())
  const results = await Promise.allSettled(enabled.map((f) => f.load(period)))
  if (mine !== seq) return
  figures.value = results
    .map((r, i) => (r.status === 'fulfilled' && r.value && enabled[i].usable(r.value)
      ? { id: enabled[i].id, data: r.value, link: enabled[i].link } : null))
    .filter(Boolean)
  loading.value = false
}
watch(() => [store.loaded, store.cycles.join(',')], () => { if (store.loaded) load() },
      { immediate: true })

const rot = useRotation(computed(() => figures.value.length), { interval: 14000 })
const figure = computed(() => figures.value[rot.index.value] || null)
const names = computed(() => figures.value.map((f) => t(`home.figures.${f.id}.title`)))

const scopeLabel = computed(() => {
  const c = currentCycleLabel()
  return c ? t('cycle.scope', { cycle: c }) : t('cycle.scopeAll')
})
const num = (n) => (n ?? 0).toLocaleString('hu-HU')

// ---- the stage: one height for every figure ---------------------------------
const stageRef = ref(null)
const stageW = ref(0)
const stageH = ref(0)
let observer = null
onMounted(() => {
  if (typeof ResizeObserver === 'undefined' || !stageRef.value) return
  observer = new ResizeObserver(([entry]) => {
    stageW.value = entry.contentRect.width
    // The stage's height is --fig-h (min-height), read back rather than repeated here.
    stageH.value = parseFloat(getComputedStyle(entry.target).minHeight) || 0
  })
  observer.observe(stageRef.value)
})
onBeforeUnmount(() => observer?.disconnect())
// The network is pinned to the stage's shape, less its zoom-button row.
const graphAspect = computed(() => (stageW.value && stageH.value
  ? Math.max(0.3, (stageH.value - 8) / stageW.value) : 0.55))

// ---- map --------------------------------------------------------------------
const topPlaces = computed(() => {
  const d = figure.value && figure.value.id === 'map' ? figure.value.data : null
  if (!d) return []
  const cols = d.columns || []
  const iName = Math.max(cols.indexOf('name'), 0)
  const iMentions = cols.indexOf('mentions') >= 0 ? cols.indexOf('mentions') : 5
  return [...d.points]
    .sort((a, b) => (b[iMentions] || 0) - (a[iMentions] || 0))
    .slice(0, 4)
    .filter((p) => p[iMentions] > 0)
    .map((p) => ({ id: p[0], name: p[iName], mentions: p[iMentions] }))
})
function openSettlement(id) {
  const [maz, taz] = String(id).split('/')
  router.push({ name: 'settlement', params: { maz, taz } })
}

// ---- interjections -------------------------------------------------------------
const graphLabels = computed(() => ({
  zoomIn: t('interjections.zoomIn'), zoomOut: t('interjections.zoomOut'),
  reset: t('interjections.resetView'), fullscreen: t('interjections.fullscreen'),
  exitFullscreen: t('interjections.exitFullscreen'),
  made: t('interjections.made'), received: t('interjections.received'),
}))
// The same two ways in as on the page: an arrow opens that pair, a member opens
// the bigger side of their cross-talk (InterjectionsView `defaultSide`).
function openPair(sel) {
  router.push({ name: 'interjections',
                query: { from: sel.source.person_id, to: sel.target.person_id } })
}
function openMember(sel) {
  const n = sel.node
  router.push({ name: 'interjections',
                query: n.in > n.out ? { to: n.person_id } : { from: n.person_id } })
}

// ---- topics -----------------------------------------------------------------------
const topicHead = computed(() => {
  const d = figure.value && figure.value.id === 'topics' ? figure.value.data : null
  return d ? { ...d, topics: d.topics.slice(0, TOPIC_ROWS) } : null
})
function openTopic(label) {
  router.push({ name: 'topics', query: { topic: label } })
}

// ---- speakers -----------------------------------------------------------------------
// A bar wears its member's faction colour, so the factions drawn get a legend:
// colour is never the only thing saying who sits where.
const NO_FACTION = 'var(--ink-faint)'
const speakerRows = computed(() => {
  const d = figure.value && figure.value.id === 'speakers' ? figure.value.data : null
  return d ? d.representatives.filter((r) => r.speaking_seconds > 0) : []
})
const speakerBars = computed(() => speakerRows.value.map((r) => ({
  label: r.label, value: r.speaking_seconds,
  color: (r.faction && r.faction.color) || NO_FACTION,
  to: { name: 'profile', params: { id: r.person_id } },
})))
const speakerFactions = computed(() => {
  const seen = new Map()
  for (const r of speakerRows.value) {
    if (r.faction && !seen.has(r.faction.id)) seen.set(r.faction.id, r.faction)
  }
  return [...seen.values()]
})
</script>

<template>
  <section
    class="card pad figcard" :aria-label="$t('home.figures.title')"
    aria-roledescription="carousel" v-bind="rot.holdHandlers"
  >
    <div class="figcard__head">
      <h2 class="figcard__title">
        {{ figure ? $t(`home.figures.${figure.id}.title`) : $t('home.figures.title') }}
      </h2>
      <span class="figcard__scope">{{ scopeLabel }}</span>
    </div>

    <div ref="stageRef" class="figcard__stage" @pointerdown="rot.stop()">
      <div v-if="loading && !figures.length" class="figcard__skeleton" aria-hidden="true"></div>
      <p v-else-if="!figures.length" class="soft figcard__empty">{{ $t('home.figures.empty') }}</p>
      <Transition v-else name="fig-fade" mode="out-in">
        <div v-if="figure" :key="figure.id" class="figcard__figure">
          <SettlementMentionMap
            v-if="figure.id === 'map'"
            :points="figure.data.points" :map="figure.data.map" :max="figure.data.max"
            height="calc(var(--fig-h) - 2.1rem)" compact
            @select="openSettlement"
          />
          <InterjectionGraph
            v-else-if="figure.id === 'interjections'"
            :nodes="figure.data.nodes" :links="figure.data.links" :metric="figure.data.rank"
            :aspect="graphAspect" :wheel-zoom="false"
            :caption="$t('interjections.chartCaption')" :show-caption="false"
            :labels="graphLabels"
            @select="openPair" @select-node="openMember"
          />
          <TopicMixChart
            v-else-if="figure.id === 'topics'"
            :speech="topicHead" @select="openTopic"
          />
          <BarChart
            v-else-if="figure.id === 'speakers'"
            :items="speakerBars" :value-format="formatSpeakingTime"
            :caption="$t('home.figures.speakers.title')" :show-caption="false"
          />
        </div>
      </Transition>
    </div>

    <div v-if="figure" class="figcard__summary">
      <p v-if="figure.id === 'map'" class="figcard__places">
        <span v-for="p in topPlaces" :key="p.id">
          <strong>{{ p.name }}</strong> {{ num(p.mentions) }}
        </span>
      </p>
      <p v-else-if="figure.id === 'interjections'" class="figcard__note soft">
        {{ $t('home.figures.interjections.summary',
              { n: figure.data.nodes.length, total: num(figure.data.total) }) }}
      </p>
      <p v-else-if="figure.id === 'topics'" class="figcard__note soft">
        {{ $t('home.figures.topics.summary',
              { n: topicHead.topics.length, total: figure.data.topics.length }) }}
      </p>
      <p v-else-if="figure.id === 'speakers'" class="figcard__places"
         :aria-label="$t('home.figures.speakers.legend')">
        <span v-for="f in speakerFactions" :key="f.id" class="figcard__legend">
          <span class="figcard__dot" :style="{ background: f.color || NO_FACTION }"
                aria-hidden="true"></span>{{ f.label }}
        </span>
      </p>
      <router-link :to="figure.link" class="figcard__more">
        {{ $t(`home.figures.${figure.id}.link`) }} <span aria-hidden="true">→</span>
      </router-link>
    </div>

    <RotationControls
      class="figcard__foot"
      :count="figures.length" :index="rot.index.value" :running="rot.running.value"
      :stopped="rot.stopped.value" :turn="rot.turn.value" :interval="rot.interval"
      :names="names" :label="$t('home.figures.title')"
      @go="rot.go" @toggle="rot.toggle"
    />
  </section>
</template>

<style scoped>
.figcard { --fig-h: 300px; display: flex; flex-direction: column; min-width: 0; }
@media (max-width: 560px) { .figcard { --fig-h: 260px; } }
.figcard__head { display: flex; align-items: baseline; justify-content: space-between; gap: .6rem; margin-bottom: .6rem; }
.figcard__title { margin: 0; font-size: 1.1rem; }
.figcard__scope { font-size: .78rem; color: var(--ink-faint); text-align: right; flex: none; }
.figcard__stage { position: relative; min-height: var(--fig-h); }
.figcard__figure { min-width: 0; }
/* The topic mix is a list, not a picture: it keeps to the stage, top-aligned. */
.figcard__figure :deep(.mix) { margin: 0; }
/* Speaker names are the point of that figure: a wider name column than BarChart's. */
.figcard__figure :deep(.bar-row) { grid-template-columns: minmax(90px, 36%) 1fr auto; }
.figcard__skeleton { height: var(--fig-h); border-radius: var(--radius); background: #f0eee8; }
.figcard__empty { margin: 0; padding-top: 3rem; text-align: center; }

.figcard__summary {
  display: flex; align-items: baseline; justify-content: space-between; gap: .4rem 1rem;
  flex-wrap: wrap; margin-top: .55rem;
}
.figcard__places { display: flex; flex-wrap: wrap; gap: .2rem .9rem; margin: 0; font-size: .85rem; color: var(--ink-soft); }
.figcard__places strong { color: var(--ink); }
/* Baseline-aligned, with the dot centred out of it, so the legend sits on the
   same line as the link — a centred row would make this summary 1px taller. */
.figcard__legend { display: inline-flex; align-items: baseline; gap: .3rem; }
.figcard__dot { align-self: center; width: .65rem; height: .65rem; border-radius: 50%; flex: none; border: 1px solid rgba(0,0,0,.15); }
.figcard__note { margin: 0; font-size: .82rem; }
.figcard__more { font-weight: 700; font-size: .9rem; white-space: nowrap; margin-left: auto; }
.figcard__foot { margin-top: .6rem; }

.fig-fade-enter-active, .fig-fade-leave-active { transition: opacity .25s ease; }
.fig-fade-enter-from, .fig-fade-leave-to { opacity: 0; }
@media (prefers-reduced-motion: reduce) {
  .fig-fade-enter-active, .fig-fade-leave-active { transition: none; }
}
</style>
