<script setup>
// Közbeszólások (§6E/INT-5..8): a directed graph of who shouts over whose speech.
// Each arrow is one ordered pair, thick with how many interjections it carries;
// clicking one lists the actual words below, each linking to the moment in the
// video they were shouted at. A slider narrows the picture to the N people most
// involved, because the whole relation is thousands of people and only the top of
// it is a diagram at all.
//
// Part of the Interjections module: it reads `/api/v1/interjections/*` and honours
// the global cycle chooser (§4A). The slider position, the ranking mode and the
// opened arrow all live in the URL — `?top=`, `?rank=`, `?from=`+`?to=` for an
// arrow, `?who=` for a person — so a reader can send someone the
// exact thing they are looking at, back/forward walks the arrows they opened, and
// a claim about two members is citable (§4D/TRUST-1). All of them are absent at
// their defaults, keeping the page's canonical address parameter-free (§SEO-2).
//
// `rank` picks what the top-N cut is ranked by — the whole exchange, or only the
// interjections a member made or took. The cut itself is the same shape in every
// mode (exactly N people, arrows between them), so switching modes changes *who*
// the figure is about, never how much of it there is.
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { store, loadMeta, currentCycleLabel } from '../../store.js'
import { formatLongDate } from '../../format.js'
import { keepScrollOnce } from '../../lib/scrollMemory.js'
import StateBlock from '../../components/StateBlock.vue'
import InterjectionGraph from '../../components/InterjectionGraph.vue'
import EmbedButton from '../../components/EmbedButton.vue'
import HelpTip from '../../components/HelpTip.vue'
import Pagination from '../../components/Pagination.vue'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()

const PAGE = 25
// The figure keeps a fixed aspect whatever N is (the settled layout is scaled to
// fit a fixed box), so the iframe height the snippet bakes in is that aspect plus
// the embed frame's own title and footer rows.
const EMBED_WIDTH = 900
const EMBED_HEIGHT = 730
const MIN_TOP = 2
const MAX_TOP = 40
const DEFAULT_TOP = 12
const RANKS = ['total', 'made', 'received']
const DEFAULT_RANK = 'total'

// How many people the graph draws. Read from the URL so the view is shareable and
// survives a reload; the default stays *out* of the address bar, keeping the page's
// canonical URL parameter-free (§SEO-2).
function topFromRoute() {
  const n = Number(route.query.top)
  return Number.isFinite(n) ? Math.min(MAX_TOP, Math.max(MIN_TOP, Math.round(n))) : DEFAULT_TOP
}

// What the top-N cut is ranked by — same URL treatment as `top` (§SEO-2).
function rankFromRoute() {
  const r = route.query.rank
  return RANKS.includes(r) ? r : DEFAULT_RANK
}

const top = ref(topFromRoute())
const rank = ref(rankFromRoute())
const data = ref(null)
const loading = ref(false)
const error = ref(false)

// The slider moves under the finger, but the URL and the request follow it only
// once it settles: a drag across the whole range would otherwise fire forty
// requests and thirty-nine history entries.
let commitTimer = null
function onSlide() {
  clearTimeout(commitTimer)
  commitTimer = setTimeout(commitTop, 220)
}

function commitTop() {
  clearTimeout(commitTimer)
  const wanted = top.value === DEFAULT_TOP ? undefined : String(top.value)
  if ((route.query.top ?? undefined) !== wanted) {
    // Every navigation this page makes only rewrites its own query, so it must
    // claim its scroll position — otherwise the router's "a new address starts at
    // the top" rule yanks the reader away from the chart they are dragging.
    keepScrollOnce(route.path)
    router.replace({ query: { ...route.query, top: wanted } })
  } else {
    load()
  }
}

// Switching the ranking mode is a click, not a drag, so it commits straight away —
// no debounce needed. A stale drill-down panel (an arrow or person from the mode
// just left) is closed rather than reloaded under the new mode, since the pair it
// named may no longer be an edge in the picture at all.
function setRank(r) {
  if (r === rank.value) return
  keepScrollOnce(route.path)
  router.replace({
    query: { ...route.query, rank: r === DEFAULT_RANK ? undefined : r,
              from: undefined, to: undefined, who: undefined },
  })
}

const scopeLabel = computed(() => {
  const c = currentCycleLabel()
  return c ? t('cycle.scope', { cycle: c }) : t('cycle.scopeAll')
})

// What the drawn picture leaves out, in the page's own words (TRUST-1): the top N
// hold this much of the cross-talk, and this many people have some of it. Worded
// per ranking mode, since "the N most involved" is only who `total` draws.
const shownShare = computed(() => {
  if (!data.value || !data.value.total) return 0
  return Math.round((100 * data.value.shown) / data.value.total)
})
const shownShareKey = computed(() => ({
  total: 'interjections.shownShareTotal',
  made: 'interjections.shownShareMade',
  received: 'interjections.shownShareReceived',
}[rank.value]))

const unattributed = computed(() => {
  if (!data.value) return 0
  const c = data.value.coverage
  return c ? c.extracted - c.attributed : 0
})

// Monotonic load id: rapid slider/cycle changes can leave fetches racing; only
// the latest may write state.
let loadSeq = 0

async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false
  try {
    const res = await api.interjectionGraph(store.cycles, top.value, rank.value)
    if (seq === loadSeq) data.value = res
  } catch {
    if (seq === loadSeq) error.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

// --- drill-down: the interjections behind one clicked arrow or person --------
// The opened pair is read from the URL, never held beside it, so the address bar
// and the panel cannot disagree — and the browser's own back button walks the
// arrows a reader opened.
//
// A person panel (`?who=`) can additionally be narrowed to **one counterpart**
// (INT-11), and the side it is narrowed on is the direction: `?who=P&from=X` is
// "X shouting at P", `?who=P&to=Y` is "P shouting at Y". The two are mutually
// exclusive because a single interjection has two ends and the subject holds one
// of them. Either way the pair is complete, so the rest of the panel — the list
// query, the rows, the heading — needs no notion of a filter: it reads the
// `speaker`/`target` this resolves to, exactly as it does for a clicked arrow.
const pair = computed(() => {
  const who = typeof route.query.who === 'string' ? route.query.who : null
  const speaker = typeof route.query.from === 'string' ? route.query.from : null
  const target = typeof route.query.to === 'string' ? route.query.to : null
  if (who) {
    if (speaker) return { person: who, speaker, target: who, side: 'from', other: speaker }
    if (target) return { person: who, speaker: who, target, side: 'to', other: target }
    return { person: who, speaker: null, target: null, side: null, other: null }
  }
  return speaker && target
    ? { person: null, speaker, target, side: null, other: null } : null
})

const pairData = ref(null)
const pairLoading = ref(false)
const pairError = ref(false)
const pairOffset = ref(0)
const panelRef = ref(null)

// A name for one end of the panel heading. Taken from the drawn graph where it has
// it (so the colour matches the arrow), and from the loaded list where it does not
// — a pair pasted in from a link may name somebody the current cut leaves out, and
// the panel should still say who they are.
function endLabel(person_id) {
  if (!person_id) return null
  const node = (data.value?.nodes || []).find((n) => n.person_id === person_id)
  if (node) return { label: node.label, color: node.faction?.color || null }
  const end = (pairData.value?.interjections || [])
    .flatMap((i) => [i.speaker, i.target]).find((e) => e.person_id === person_id)
  return { label: end ? end.label : person_id, color: null }
}

const pairSegs = computed(() => {
  if (!pair.value) return []
  const ends = pair.value.person
    ? [pair.value.person] : [pair.value.speaker, pair.value.target]
  return ends.map(endLabel).filter(Boolean)
})

// A row names only the end the heading has *not* already fixed. With one arrow
// open the heading says both, so repeating "A → B" on twenty-five rows below it
// adds nothing; with a person open, both ends vary from row to row and the row is
// the only place the direction of that particular shout is written down.
const showSpeaker = computed(() => !(pair.value && pair.value.speaker))
const showTarget = computed(() => !(pair.value && pair.value.target))

const pairPage = computed(() => Math.floor(pairOffset.value / PAGE))
const pairTotalPages = computed(() =>
  pairData.value ? Math.ceil(pairData.value.total / PAGE) : 0)

// Which arrow / node the graph should draw as selected, derived from the chosen
// pair rather than stored beside it — so a reload of the graph (a new cycle, a
// new slider position) cannot leave a stale index highlighted.
const selectedLink = computed(() => {
  if (!pair.value || !data.value || !pair.value.speaker) return -1
  const nodes = data.value.nodes
  return data.value.links.findIndex(
    (l) => nodes[l.source]?.person_id === pair.value.speaker
        && nodes[l.target]?.person_id === pair.value.target)
})

const selectedNode = computed(() => {
  if (!pair.value || !data.value || !pair.value.person) return -1
  return data.value.nodes.findIndex((n) => n.person_id === pair.value.person)
})

function clearPair() {
  keepScrollOnce(route.path)
  router.push({
    query: { ...route.query, from: undefined, to: undefined, who: undefined },
  })
}

function scrollToPanel() {
  nextTick(() => panelRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
}

function onSelect(sel) {
  choose({ from: sel.source.person_id, to: sel.target.person_id })
}

// Clicking a person shows every interjection they were part of, in either
// direction — which is exactly what the graph highlights when a node is picked,
// and what the number beside their name on it counts. Each row then names both
// ends, so a mixed list is never ambiguous about which way a shout went.
function onSelectNode(sel) {
  choose({ who: sel.node.person_id })
}

// Pushed, not replaced: opening an arrow is somewhere the reader went, so `back`
// should return them to the picture they came from.
function choose(next) {
  const q = { from: undefined, to: undefined, who: undefined, ...next }
  const same = ['from', 'to', 'who']
    .every((k) => (route.query[k] ?? undefined) === q[k])
  if (same) {
    scrollToPanel()
    return
  }
  keepScrollOnce(route.path)
  router.push({ query: { ...route.query, ...q } })
}

let pairSeq = 0

async function loadPair() {
  if (!pair.value) return
  const seq = ++pairSeq
  pairLoading.value = true; pairError.value = false
  try {
    const res = await api.interjectionList({
      period: store.cycles,
      speaker: pair.value.speaker || undefined,
      target: pair.value.target || undefined,
      // Either a whole pair (a clicked arrow, or a person narrowed to one
      // counterpart) or a person's own row of interjections, never both.
      person: pair.value.speaker || pair.value.target
        ? undefined : pair.value.person,
      limit: PAGE, offset: pairOffset.value,
    })
    if (seq === pairSeq) pairData.value = res
  } catch {
    if (seq === pairSeq) pairError.value = true
  } finally {
    if (seq === pairSeq) pairLoading.value = false
  }
}

function gotoPairPage(p) {
  pairOffset.value = p * PAGE
  loadPair()
  scrollToPanel()
}

// --- the two counterpart choosers (INT-11) --------------------------------
// Keyed by the subject, not by the whole pair: narrowing to one counterpart and
// back must not refetch the lists the choosers are made of. They are fetched
// rather than read off the graph payload because a busy member's counterparts run
// into the dozens, most of them outside whatever top-N cut is drawn.
const partners = ref(null)
let partnersFor = null

async function loadPartners() {
  const subject = pair.value?.person
  if (!subject) { partners.value = null; partnersFor = null; return }
  const key = `${subject}|${store.cycles.join(',')}`
  if (partnersFor === key) return
  partnersFor = key
  try {
    const res = await api.interjectionPartners(store.cycles, subject)
    if (partnersFor === key) partners.value = res
  } catch {
    // A chooser that cannot be built is simply not offered: the panel below it
    // is the point of the page and works without it.
    if (partnersFor === key) { partners.value = null; partnersFor = null }
  }
}

// Narrow the open person panel to one counterpart, or (empty value) widen it
// back to everything they were part of. Setting one side clears the other.
function setPartner(side, id) {
  const who = pair.value?.person
  if (!who) return
  choose(id ? { who, [side]: id } : { who })
}

// What each chooser currently reads. A native `<select>` is as wide as its
// *widest* option, which in a heading leaves the chosen word stranded a long way
// from its own chevron — so the visible text is this label and the select itself
// is laid over it, transparent (see `.partnerpick`).
function partnerLabel(side) {
  if (pair.value?.side !== side) return t('interjections.anyone')
  const list = side === 'from'
    ? partners.value?.received_from : partners.value?.made_to
  const hit = (list || []).find((p) => p.person_id === pair.value.other)
  return hit ? hit.label : (endLabel(pair.value.other)?.label || pair.value.other)
}

onMounted(() => {
  loadMeta().catch(() => {}).finally(() => { load(); loadPair(); loadPartners() })
})
watch(() => store.cycles.join(','), () => { load(); loadPair(); loadPartners() })
watch(() => route.query.top, () => {
  const wanted = topFromRoute()
  if (wanted !== top.value) top.value = wanted
  load()
})
watch(() => route.query.rank, () => {
  const wanted = rankFromRoute()
  if (wanted !== rank.value) rank.value = wanted
  load()
})
// The opened arrow: reload its words, and bring the panel into view — including
// when the reader arrived by pasting a link, where the panel is below the fold.
watch(() => [route.query.from, route.query.to, route.query.who].join('|'), () => {
  pairOffset.value = 0
  pairData.value = null
  loadPair()
  loadPartners()
  if (pair.value) scrollToPanel()
})
</script>

<template>
  <div class="sechead">
    <h1>{{ $t('interjections.title') }}</h1>
    <HelpTip :label="$t('interjections.title')">
      <p>{{ $t('interjections.chartCaption') }}</p>
      <p>{{ $t('interjections.methodology') }}</p>
      <p>{{ $t('interjections.methodologyExclusions') }}</p>
    </HelpTip>
  </div>
  <p class="muted">{{ $t('interjections.subtitle') }}</p>

  <StateBlock
    :loading="loading" :error="error"
    :empty="!!data && data.total === 0" :empty-text="$t('interjections.noResults')"
    @retry="load"
  >
    <div v-if="data && data.total">
      <div class="rankgroup" role="group" :aria-label="$t('interjections.rankLabel')">
        <button
          v-for="r in RANKS" :key="r" type="button"
          class="rankbtn" :class="{ active: rank === r }"
          :aria-pressed="rank === r"
          @click="setRank(r)"
        >{{ $t('interjections.rank.' + r) }}</button>
      </div>

      <div class="i-controls">
        <p class="muted small">
          {{ $t('interjections.count', { n: data.total, people: data.people_total }) }}
          · {{ scopeLabel }}
        </p>
        <label class="topslider">
          <span class="small">{{ $t('interjections.topLabel', { n: top }) }}</span>
          <input
            type="range" :min="MIN_TOP" :max="MAX_TOP" step="1"
            v-model.number="top"
            :aria-label="$t('interjections.topLabel', { n: top })"
            @input="onSlide" @change="commitTop"
          />
        </label>
      </div>

      <div class="card pad">
        <InterjectionGraph
          :nodes="data.nodes" :links="data.links" :metric="rank"
          :selected="selectedLink" :selected-node="selectedNode"
          :caption="$t('interjections.chartCaption')" :show-caption="false"
          :labels="{ zoomIn: $t('interjections.zoomIn'),
                     zoomOut: $t('interjections.zoomOut'),
                     reset: $t('interjections.resetView'),
                     made: $t('interjections.made'),
                     received: $t('interjections.received') }"
          @select="onSelect" @select-node="onSelectNode"
        />
        <div class="fig-foot">
          <p class="muted small hint">
            {{ $t('interjections.clickHint') }} {{ $t('interjections.dragHint') }}
          </p>
          <EmbedButton
            kind="interjection-graph" :title="$t('interjections.title')"
            :params="{ top: top === DEFAULT_TOP ? undefined : String(top),
                       rank: rank === DEFAULT_RANK ? undefined : rank }"
            :height="EMBED_HEIGHT" :max-width="EMBED_WIDTH"
          />
        </div>
      </div>

      <p class="small soft share">
        {{ $t(shownShareKey, { share: shownShare, n: top }) }}
        <template v-if="unattributed">
          {{ $t('interjections.unattributed', { n: unattributed }) }}
        </template>
      </p>

      <!-- drill-down: the interjections making up the clicked arrow -->
      <section v-if="pair" ref="panelRef" class="flowpanel">
        <div class="flowhead">
          <h2>
            <!-- One member's own panel: the two choosers flank their name, on the
                 side their arrows run — who shouted at them to the left, whom they
                 shouted at to the right, so the heading reads as the direction it
                 selects (INT-11). -->
            <template v-if="pair.person && partners">
              <span class="partnerpick" :class="{ set: pair.side === 'from' }">
                <span class="partnerlabel" aria-hidden="true">{{ partnerLabel('from') }}</span>
                <select
                  class="partner" :value="pair.side === 'from' ? pair.other : ''"
                  :aria-label="$t('interjections.filterFrom')"
                  @change="setPartner('from', $event.target.value)"
                >
                  <option value="">{{ $t('interjections.anyone') }}</option>
                  <option v-for="p in partners.received_from" :key="p.person_id"
                          :value="p.person_id">{{ p.label }} ({{ p.count }})</option>
                </select>
              </span>
              <span class="arrow" aria-hidden="true">→</span>
            </template>
            <template v-for="(seg, i) in pairSegs" :key="i">
              <span v-if="i > 0" class="arrow" aria-hidden="true">→</span>
              <span :style="seg.color ? { color: seg.color } : undefined">{{ seg.label }}</span>
            </template>
            <template v-if="pair.person && partners">
              <span class="arrow" aria-hidden="true">→</span>
              <span class="partnerpick" :class="{ set: pair.side === 'to' }">
                <span class="partnerlabel" aria-hidden="true">{{ partnerLabel('to') }}</span>
                <select
                  class="partner" :value="pair.side === 'to' ? pair.other : ''"
                  :aria-label="$t('interjections.filterTo')"
                  @change="setPartner('to', $event.target.value)"
                >
                  <option value="">{{ $t('interjections.anyone') }}</option>
                  <option v-for="p in partners.made_to" :key="p.person_id"
                          :value="p.person_id">{{ p.label }} ({{ p.count }})</option>
                </select>
              </span>
            </template>
            <!-- Only where the choosers could not be built: with them, "Bárki →
                 name → Bárki" already says both ways, in the words that change it. -->
            <span v-if="pair.person && !pair.side && !partners" class="muted outward">
              {{ $t('interjections.bothWays') }}
            </span>
          </h2>
          <button type="button" class="btn secondary small" @click="clearPair">
            ✕ {{ $t('interjections.close') }}
          </button>
        </div>

        <StateBlock
          :loading="pairLoading" :error="pairError"
          :empty="!!pairData && pairData.interjections.length === 0"
          :empty-text="$t('interjections.noResults')"
          @retry="loadPair"
        >
          <div v-if="pairData">
            <p class="muted small">{{ $t('interjections.listCount', { n: pairData.total }) }}</p>
            <ul class="ijlist">
              <li v-for="i in pairData.interjections" :key="i.id" class="card pad ijcard">
                <blockquote class="ijtext">{{ i.text }}</blockquote>
                <p class="small muted ijmeta">
                  <template v-if="showSpeaker">
                    <RouterLink v-if="i.speaker.person_id"
                                :to="{ name: 'profile', params: { id: i.speaker.person_id } }">
                      {{ i.speaker.label }}
                    </RouterLink>
                    <span v-else>{{ i.speaker.label }}</span>
                  </template>
                  <template v-if="showSpeaker || showTarget">
                    <span class="arrow" aria-hidden="true">→</span>
                    <span class="visually-hidden">{{ $t('interjections.during') }}</span>
                  </template>
                  <template v-if="showTarget">
                    <RouterLink v-if="i.target.person_id"
                                :to="{ name: 'profile', params: { id: i.target.person_id } }">
                      {{ i.target.label }}
                    </RouterLink>
                    <span v-else>{{ i.target.label }}</span>
                  </template>
                  <span v-if="showSpeaker || showTarget" aria-hidden="true"> · </span>
                  <RouterLink :to="{ name: 'session', params: { id: i.session_id } }">
                    {{ formatLongDate(i.date, locale) }}
                  </RouterLink>
                  <template v-if="i.agenda_title">
                    <span aria-hidden="true"> · </span>
                    <span class="agenda" :title="i.agenda_title">{{ i.agenda_title }}</span>
                  </template>
                  <span aria-hidden="true"> · </span>
                  <!-- Straight to the moment it was shouted (VIE-3/VIE-5). -->
                  <RouterLink :to="{ name: 'viewer', params: { uid: i.speech_uid },
                                     hash: '#s=' + i.sentence_id }">
                    {{ $t('interjections.watch') }}
                  </RouterLink>
                </p>
              </li>
            </ul>
            <Pagination :page="pairPage" :total-pages="pairTotalPages" @goto="gotoPairPage" />
          </div>
        </StateBlock>
      </section>

      <details class="methodology">
        <summary>{{ $t('factions.methodology') }}</summary>
        <p>{{ $t('interjections.methodology') }}</p>
        <p>{{ $t('interjections.methodologyExclusions') }}</p>
        <p v-if="data.coverage">
          {{ $t('interjections.coverage', {
            extracted: data.coverage.extracted,
            attributed: data.coverage.attributed,
            procedural: data.coverage.procedural,
          }) }}
        </p>
      </details>
    </div>
  </StateBlock>
</template>

<style scoped>
.sechead { display: flex; align-items: center; gap: .35rem; }
.sechead h1 { margin: 0; }
/* Same segmented-control language as the representatives search-mode switch
   (SpeakerSearchModes.vue): a bordered track of filled/unfilled buttons, which
   reads as "either/or" rather than as one more filter pill. */
.rankgroup {
  display: flex; flex-wrap: wrap; gap: .2rem; width: fit-content; max-width: 100%;
  margin: 0 0 .6rem; padding: .2rem; border: 1px solid var(--line);
  border-radius: var(--radius); background: var(--bg);
}
.rankbtn {
  padding: .38rem .9rem; border-radius: calc(var(--radius) - 3px);
  font-size: .9rem; font-weight: 600; line-height: 1.4; color: var(--ink-soft);
  background: transparent; border: none; cursor: pointer;
}
.rankbtn:hover { color: var(--accent); background: var(--accent-soft); }
.rankbtn.active, .rankbtn.active:hover {
  background: var(--accent); color: var(--accent-ink); box-shadow: var(--shadow);
}
/* Count on the left, the top-N slider on the right, above the chart. */
.i-controls { display: flex; align-items: center; justify-content: space-between; gap: .8rem; flex-wrap: wrap; margin-bottom: .75rem; }
.i-controls p { margin: 0; }
.topslider { display: flex; align-items: center; gap: .5rem; }
.topslider input { width: 190px; accent-color: var(--accent); }
.hint { margin: 0; margin-right: auto; }
.share { max-width: 70ch; margin-top: .6rem; }
.flowpanel { margin-top: 1.5rem; scroll-margin-top: 5rem; }
.flowhead { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; margin-bottom: .5rem; }
.flowhead h2 { margin: 0; font-size: 1.15rem; display: flex; align-items: center; gap: .3rem; flex-wrap: wrap; }
/* The choosers sit *in* the heading, so they carry its weight and stay quiet
   until used — a full form control either side of the name would read as a
   toolbar and bury the name it is about. The `<select>` is the real control but
   is laid over the label transparently, because a native one is as wide as its
   widest option: in a heading that strands the chosen word a long way from its
   own chevron. The wrapper is therefore what is styled, and what shows focus. */
.partnerpick {
  position: relative; display: inline-flex; align-items: center;
  padding: .1rem 1.15rem .1rem .35rem; border-radius: 6px;
  border: 1px solid transparent; color: var(--ink-soft); font-size: .95rem;
}
.partnerpick.set { color: var(--ink); }
.partnerpick::after {
  content: ''; position: absolute; right: .45rem; top: 50%;
  width: .3rem; height: .3rem; margin-top: -.22rem;
  border-right: 1.5px solid currentColor; border-bottom: 1.5px solid currentColor;
  transform: rotate(45deg); opacity: .6;
}
.partnerpick:hover, .partnerpick:focus-within {
  color: var(--accent); border-color: var(--line); background: var(--surface);
}
/* An outline on the select itself is drawn at the select's own opacity — that is,
   invisibly — so the site's focus ring (A11Y-1) has to be borrowed by the wrapper.
   `:has(:focus-visible)` rather than `:focus-within`, so it stays a keyboard
   indicator and a click does not flash a ring. */
.partnerpick:has(.partner:focus-visible) {
  outline: 3px solid var(--focus); outline-offset: 2px;
}
.partnerlabel { max-width: 13rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* Transparent, not hidden: it stays the focusable, keyboard-operable control
   (its own ring is invisible, so the wrapper draws one via :focus-within). */
.partner {
  position: absolute; inset: 0; width: 100%; height: 100%;
  opacity: 0; cursor: pointer; font: inherit;
}
.outward { font-weight: 400; font-size: .85rem; margin-left: .4rem; }
.arrow { color: var(--ink-soft); }
.ijlist { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .6rem; }
.ijcard { display: flex; flex-direction: column; gap: .4rem; }
/* The words themselves are the point of the panel, so they lead each row. */
.ijtext { margin: 0; font-size: 1.02rem; line-height: 1.45; border-left: 3px solid var(--line); padding-left: .7rem; }
.ijmeta { display: flex; gap: .35rem; flex-wrap: wrap; align-items: center; }
.agenda { max-width: 34ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.methodology { margin-top: 1.6rem; max-width: 70ch; }
</style>
