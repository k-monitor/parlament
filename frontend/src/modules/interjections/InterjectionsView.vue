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
// opened arrow all live in the URL — `?top=`, `?rank=`, and `?from=`/`?to=` for the
// panel, either end of which may be absent, meaning "anyone" — so a reader can send
// someone the exact thing they are looking at, back/forward walks the arrows they
// opened, and a claim about two members is citable (§4D/TRUST-1). All of them are
// absent at their defaults, keeping the page's canonical address parameter-free
// (§SEO-2).
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

// The slider's number follows the finger; the figure is refetched only when the
// reader lets go. A debounce mid-drag was not enough: every commit re-ran the
// request *and* the force layout under a moving pointer, and since the controls
// then lived inside the block a reload replaces, it tore the slider out of the
// hand dragging it. `change` is exactly "let go" for a pointer or a touch — fired
// once, whatever the travel — so that is where the commit belongs. A *held* arrow
// key repeats `change` instead of ending with it, so a keyboard run is the one
// case still worth coalescing.
const KEY_SETTLE = 250
let commitTimer = null
let byKeyboard = false

function onKeyNav() { byKeyboard = true }
function onPointerNav() { byKeyboard = false }

function onSlideEnd() {
  clearTimeout(commitTimer)
  if (byKeyboard) commitTimer = setTimeout(commitTop, KEY_SETTLE)
  else commitTop()
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
  } else if (data.value?.top !== top.value) {
    // The address already says this number and the figure does not: a first load,
    // or a retry. A drag that wandered off and came back asks for nothing.
    load()
  }
}

// Whether the figure is behind the slider — during a drag, since nothing is
// fetched until it ends, and until the new cut arrives. Worth saying quietly: the
// number beside the slider is otherwise a promise the picture has not kept yet.
const topPending = computed(() => !!data.value && data.value.top !== top.value)

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

// --- drill-down: the interjections behind one arrow --------------------------
// The panel is always **one arrow**: a speaker end, a target end, and the shouting
// that ran between them. Either end may be left at "anyone" — `?to=P` is everything
// shouted at P, `?from=P` everything P shouted, `?from=X`+`?to=Y` the single pair —
// and both ends are choosers, so the same control carries the reader from one
// member to one antagonist and back without ever changing shape (INT-11). Never
// both ends at once: that is the panel being closed, not a view of anything.
//
// It is read from the URL, never held beside it, so the address bar and the panel
// cannot disagree — and the browser's own back button walks the arrows a reader
// opened.
const pair = computed(() => {
  // `?who=` — the old two-arrow person panel — is rewritten into an arrow on
  // arrival (see `normalizeLegacy`); until it is, there is nothing to show.
  if (typeof route.query.who === 'string') return null
  const speaker = typeof route.query.from === 'string' ? route.query.from : null
  const target = typeof route.query.to === 'string' ? route.query.to : null
  return speaker || target ? { speaker, target } : null
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
  const node = nodeOf(person_id)
  if (node) return { label: node.label, color: node.faction?.color || null }
  const end = (pairData.value?.interjections || [])
    .flatMap((i) => [i.speaker, i.target]).find((e) => e.person_id === person_id)
  return { label: end ? end.label : person_id, color: null }
}

function nodeOf(person_id) {
  return (data.value?.nodes || []).find((n) => n.person_id === person_id) || null
}

// A row names only the end the heading has *not* already fixed: with a whole pair
// open the heading says both, so repeating "A → B" on twenty-five rows below it
// adds nothing, and with one end left at "anyone" the other is exactly what varies
// from row to row. Never both, since the panel always fixes one of them.
const showSpeaker = computed(() => !(pair.value && pair.value.speaker))
const showTarget = computed(() => !(pair.value && pair.value.target))

const pairPage = computed(() => Math.floor(pairOffset.value / PAGE))
const pairTotalPages = computed(() =>
  pairData.value ? Math.ceil(pairData.value.total / PAGE) : 0)

// Which arrow / node the graph should draw as selected, derived from the chosen
// pair rather than stored beside it — so a reload of the graph (a new cycle, a
// new slider position) cannot leave a stale index highlighted.
const selectedLink = computed(() => {
  if (!pair.value || !data.value) return -1
  const { speaker, target } = pair.value
  if (!speaker || !target) return -1
  const nodes = data.value.nodes
  return data.value.links.findIndex(
    (l) => nodes[l.source]?.person_id === speaker
        && nodes[l.target]?.person_id === target)
})

// One end at "anyone" is one member against the whole House: the figure marks them
// and lights up that half of their arrows — the half the panel below lists, so the
// picture and the list cannot say different things.
const selectedNode = computed(() => {
  if (!pair.value || !data.value) return -1
  const { speaker, target } = pair.value
  if (speaker && target) return -1
  return data.value.nodes.findIndex((n) => n.person_id === (speaker || target))
})

const selectedDir = computed(() => {
  if (!pair.value) return ''
  const { speaker, target } = pair.value
  if (speaker && target) return ''
  return speaker ? 'from' : 'to'
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

// Clicking a person opens one side of their cross-talk against the whole House —
// the side the figure is currently ranked by, since that is the number printed
// beside their name and so the list the click promises. Ranked by the sum, the
// bigger of their two sides opens; either way the arrow turns it round in a click.
function onSelectNode(sel) {
  choose(defaultSide(sel.node) === 'from'
    ? { from: sel.node.person_id } : { to: sel.node.person_id })
}

// Which way round a person's own panel opens (see above). `node` may be missing —
// a link can name somebody the drawn cut leaves out — in which case the ranking
// mode alone decides.
function defaultSide(node) {
  if (rank.value === 'made') return 'from'
  if (rank.value === 'received') return 'to'
  return node && node.in > node.out ? 'to' : 'from'
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

// Links shared before the two panels became one arrow carried the subject as
// `?who=`, with the counterpart (if any) beside it — and each of them names
// exactly one direction, since the subject holds the end the narrowing did not. So
// they are rewritten into that arrow rather than dropped: an old link goes on
// opening what it opened. Returns whether it took over this turn.
function normalizeLegacy() {
  const who = typeof route.query.who === 'string' ? route.query.who : null
  if (!who) return false
  const from = typeof route.query.from === 'string' ? route.query.from : null
  const to = typeof route.query.to === 'string' ? route.query.to : null
  const arrow = from ? { from, to: who }            // `?who=P&from=X` — X shouting at P
    : to ? { from: who, to }                        // `?who=P&to=Y`   — P shouting at Y
    : defaultSide(nodeOf(who)) === 'from' ? { from: who } : { to: who }
  keepScrollOnce(route.path)
  router.replace({
    query: { ...route.query, who: undefined, from: undefined, to: undefined, ...arrow },
  })
  return true
}

let pairSeq = 0

async function loadPair() {
  if (!pair.value) return
  const seq = ++pairSeq
  pairLoading.value = true; pairError.value = false
  try {
    const res = await api.interjectionList({
      period: store.cycles,
      // One end, or both: the arrow is the query, and an end left at "anyone" is
      // simply a filter not sent.
      speaker: pair.value.speaker || undefined,
      target: pair.value.target || undefined,
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

// --- the arrow's two choosers (INT-11) ------------------------------------
// Each end is picked out of the *other* end's counterparts, so a chooser can only
// ever land on a list that has something in it. Those lists are fetched per person
// and kept, keyed by person and cycle scope: turning the arrow round, or walking
// from one counterpart to the next, must not refetch what the choosers are made
// of. They come from the relation and not from the graph payload because a busy
// member's counterparts run into the dozens, most of them outside whatever top-N
// cut is drawn.
const partners = ref({})
const partnersPending = new Set()
let partnersScope = null

async function ensurePartners(id) {
  const scope = store.cycles.join(',')
  if (scope !== partnersScope) {
    partnersScope = scope
    partners.value = {}
    partnersPending.clear()
  }
  if (!id || partners.value[id] || partnersPending.has(id)) return
  partnersPending.add(id)
  try {
    const res = await api.interjectionPartners(store.cycles, id)
    if (partnersScope === scope) partners.value = { ...partners.value, [id]: res }
  } catch {
    // A chooser that cannot be built simply offers less: the panel below it is
    // the point of the page and works without it.
  } finally {
    partnersPending.delete(id)
  }
}

// Both ends, because with a whole pair open each end is the other's chooser.
function loadPartners() {
  if (!pair.value) return
  ensurePartners(pair.value.speaker)
  ensurePartners(pair.value.target)
}

// Who one end may be swapped to. Against a named far end: that member's real
// counterparts on this side, busiest first and carrying their counts, so picking
// one is an informed choice and a count is a promise about the list behind it.
// Against "anyone" there is no such list — the question is then whose panel this
// is — so the offer is the members the chart currently draws, each with the number
// this side of the arrow means for them.
function endOptions(side, id, other) {
  let list
  if (other) {
    const rel = partners.value[other]
    list = (rel ? (side === 'from' ? rel.received_from : rel.made_to) : []).slice()
  } else {
    list = (data.value?.nodes || [])
      .map((n) => ({ person_id: n.person_id, label: n.label,
                     count: side === 'from' ? n.out : n.in }))
      .filter((o) => o.count > 0)
      .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, 'hu'))
  }
  // Whoever is chosen is always in their own chooser, even where the list above
  // leaves them out: a pasted link can name somebody the drawn cut does not hold.
  if (id && !list.some((o) => o.person_id === id)) {
    list.unshift({ person_id: id, label: endLabel(id)?.label || id, count: null })
  }
  return list
}

// The two ends of the one arrow, in reading order: what each chooser says, in what
// colour, and what else it offers. An end can be set back to "anyone" only while
// the other one names somebody — emptying both is closing the panel, and the
// ✕ beside the heading is where that lives.
//
// A native `<select>` is as wide as its *widest* option, which in a heading strands
// the chosen word a long way from its own chevron — so `label` is drawn as text and
// the select is laid over it, transparent (see `.endpick`).
const arrowEnds = computed(() => {
  if (!pair.value) return []
  const { speaker, target } = pair.value
  return [['from', speaker, target], ['to', target, speaker]].map(([side, id, other]) => {
    const seen = id ? endLabel(id) : null
    return {
      side,
      id,
      label: seen ? seen.label : t('interjections.anyone'),
      color: seen ? seen.color : null,
      canClear: !!other,
      options: endOptions(side, id, other),
      aria: t(side === 'from' ? 'interjections.pickFrom' : 'interjections.pickTo'),
    }
  })
})

// Swap one end, leaving the other where it is: the reader walks the relation a
// step at a time rather than landing on an unrelated pair.
function setEnd(side, id) {
  if (!pair.value) return
  const next = { from: pair.value.speaker || undefined,
                 to: pair.value.target || undefined }
  next[side] = id || undefined
  if (!next.from && !next.to) return     // never offered; see `canClear`
  choose(next)
}

// What the other direction holds, where the counts already loaded can say it — so
// an arrow that would turn onto an empty list is offered as unavailable rather
// than as a dead end. `null` is "not known yet", which stays available: the lists
// arrive in a moment, and a reversal is cheap to be wrong about.
const reverseCount = computed(() => {
  if (!pair.value) return null
  const { speaker, target } = pair.value
  const rel = partners.value[speaker || target]
  if (!rel) return null
  if (speaker && target) {
    return rel.received_from.find((o) => o.person_id === target)?.count ?? 0
  }
  return (speaker ? rel.received_from : rel.made_to)
    .reduce((sum, o) => sum + o.count, 0)
})

const flipTitle = computed(() => t(reverseCount.value === 0
  ? 'interjections.reverseNone' : 'interjections.reverse'))

// The same two ends the other way round — the other question about them, and the
// one thing this panel can do that the figure above cannot.
function flip() {
  if (!pair.value || reverseCount.value === 0) return
  choose({ from: pair.value.target || undefined,
           to: pair.value.speaker || undefined })
}

onMounted(() => {
  loadMeta().catch(() => {}).finally(() => {
    load()
    if (!normalizeLegacy()) { loadPair(); loadPartners() }
  })
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
  // A legacy address rewrites itself and comes back here as an arrow.
  if (normalizeLegacy()) return
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

  <!-- The two controls stand *outside* the block that reloads. What they change is
       the figure, and a reader dragging the slider must not have it replaced by a
       spinner under their pointer — nor a keyboard reader lose the focus they were
       stepping with. They appear with the first figure and stay through every
       later one. -->
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
      <!-- These are the figure's numbers, not the slider's: they go quiet with it
           while a new scope is being fetched, rather than standing crisp and stale. -->
      <p class="muted small" :class="{ busy: loading }">
        {{ $t('interjections.count', { n: data.total, people: data.people_total }) }}
        · {{ scopeLabel }}
      </p>
      <label class="topslider" :class="{ pending: topPending }">
        <span class="small topval">{{ $t('interjections.topLabel', { n: top }) }}</span>
        <input
          type="range" :min="MIN_TOP" :max="MAX_TOP" step="1"
          v-model.number="top" list="i-topticks"
          :aria-label="$t('interjections.topLabel', { n: top })"
          @change="onSlideEnd" @keydown="onKeyNav" @pointerdown="onPointerNav"
        />
        <!-- Where the range begins, ends and sits in between: a bare track gives
             no sense of 40 being the end of it. -->
        <datalist id="i-topticks">
          <option v-for="n in [MIN_TOP, 10, 20, 30, MAX_TOP]" :key="n" :value="n" />
        </datalist>
      </label>
    </div>
  </div>

  <StateBlock
    :loading="loading && !data" :error="error"
    :empty="!!data && data.total === 0" :empty-text="$t('interjections.noResults')"
    @retry="load"
  >
    <div v-if="data && data.total">
      <!-- Kept on screen while the next cut loads (`busy`), rather than blanked:
           the reader asked for a different N of the same picture, not a new page. -->
      <div class="card pad" :class="{ busy: loading }" :aria-busy="loading">
        <InterjectionGraph
          :nodes="data.nodes" :links="data.links" :metric="rank"
          :selected="selectedLink" :selected-node="selectedNode"
          :selected-dir="selectedDir"
          :caption="$t('interjections.chartCaption')" :show-caption="false"
          :labels="{ zoomIn: $t('interjections.zoomIn'),
                     zoomOut: $t('interjections.zoomOut'),
                     reset: $t('interjections.resetView'),
                     fullscreen: $t('interjections.fullscreen'),
                     exitFullscreen: $t('interjections.exitFullscreen'),
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
          <!-- One arrow: who shouted on the left, who was interrupted on the right,
               and the arrow itself turns the pair round. Either end can be set back
               to "anyone", which is what makes this one control both a member's own
               panel and a single pair (INT-11). -->
          <h2 class="pairhead">
            <template v-for="(end, i) in arrowEnds" :key="end.side">
              <button
                v-if="i" type="button" class="flip" :disabled="reverseCount === 0"
                :title="flipTitle" :aria-label="flipTitle" @click="flip"
              >→</button>
              <span class="endpick" :class="{ set: !!end.id }">
                <span
                  class="endlabel" aria-hidden="true"
                  :style="end.color ? { color: end.color } : undefined"
                >{{ end.label }}</span>
                <select
                  class="endsel" :value="end.id || ''" :aria-label="end.aria"
                  @change="setEnd(end.side, $event.target.value)"
                >
                  <option v-if="end.canClear" value="">{{ $t('interjections.anyone') }}</option>
                  <option v-for="o in end.options" :key="o.person_id" :value="o.person_id">
                    {{ o.label }}<template v-if="o.count !== null"> ({{ o.count }})</template>
                  </option>
                </select>
              </span>
            </template>
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
                  <!-- Exactly one end varies down the list — the heading fixes the
                       other — so the row names that one, and says which end it is
                       for a reader who is not looking at the heading. -->
                  <template v-if="showSpeaker">
                    <span class="visually-hidden">{{ $t('interjections.rowSpeaker') }}</span>
                    <RouterLink v-if="i.speaker.person_id"
                                :to="{ name: 'profile', params: { id: i.speaker.person_id } }">
                      {{ i.speaker.label }}
                    </RouterLink>
                    <span v-else>{{ i.speaker.label }}</span>
                  </template>
                  <template v-if="showTarget">
                    <span class="visually-hidden">{{ $t('interjections.rowTarget') }}</span>
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
/* The number has moved but the figure has not (mid-drag, or mid-fetch): the label
   says so by taking the accent, which is also the colour of the thumb the reader
   is holding. Nothing here is information the text does not already carry. */
.topslider .topval { transition: color .12s; }
.topslider.pending .topval { color: var(--accent); }
/* A refresh leaves the old figure up rather than blanking the page — dimmed, but
   only once it is slow enough to be worth admitting to: the delay means a cached
   cut (the common case) swaps with no flicker at all. */
.busy { opacity: .5; transition: opacity .2s ease .15s; }
.card.busy { pointer-events: none; }
@media (prefers-reduced-motion: reduce) {
  .busy { transition: opacity 0s linear .15s; }
}
.hint { margin: 0; margin-right: auto; }
.share { max-width: 70ch; margin-top: .6rem; }
.flowpanel { margin-top: 1.5rem; scroll-margin-top: 5rem; }
.flowhead { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; margin-bottom: .5rem; }
.flowhead h2 { margin: 0; font-size: 1.15rem; display: flex; align-items: center; gap: .3rem; flex-wrap: wrap; }
/* The heading *is* the control: both ends are choosers and the arrow between them
   is a button. They sit in the heading rather than under it so they carry its
   weight and stay quiet until used — three form controls in a row would read as a
   toolbar and bury the two names the panel is about. The `<select>` is the real
   control but is laid over its label transparently, because a native one is as
   wide as its widest option: in a heading that strands the chosen word a long way
   from its own chevron. The wrapper is therefore what is styled, and what shows
   focus. */
.pairhead { font-size: 1.15rem; }
.endpick {
  position: relative; display: inline-flex; align-items: center;
  padding: .1rem 1.15rem .1rem .35rem; border-radius: 6px;
  border: 1px solid transparent; color: var(--ink-soft); font-size: .95rem;
}
.endpick.set { color: var(--ink); }
.endpick::after {
  content: ''; position: absolute; right: .45rem; top: 50%;
  width: .3rem; height: .3rem; margin-top: -.22rem;
  border-right: 1.5px solid currentColor; border-bottom: 1.5px solid currentColor;
  transform: rotate(45deg); opacity: .6;
}
.endpick:hover, .endpick:focus-within {
  border-color: var(--line); background: var(--surface);
}
/* An outline on the select itself is drawn at the select's own opacity — that is,
   invisibly — so the site's focus ring (A11Y-1) has to be borrowed by the wrapper.
   `:has(:focus-visible)` rather than `:focus-within`, so it stays a keyboard
   indicator and a click does not flash a ring. */
.endpick:has(.endsel:focus-visible) {
  outline: 3px solid var(--focus); outline-offset: 2px;
}
.endlabel { max-width: 13rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* Transparent, not hidden: it stays the focusable, keyboard-operable control
   (its own ring is invisible, so the wrapper draws one via :focus-within). */
.endsel {
  position: absolute; inset: 0; width: 100%; height: 100%;
  opacity: 0; cursor: pointer; font: inherit;
}
/* The arrow says which way the shouting ran, and turning it round is the other
   question about the same two people — so it is a button, not a glyph: reachable
   by keyboard, and announcing what it does. Unlike the two choosers, which have
   their chevrons, an arrow between two names is read as punctuation unless it is
   drawn as something to press — hence the resting outline. Hovering it shows the
   direction the click would leave behind, which is the whole explanation the
   control needs. */
.flip {
  display: inline-flex; align-items: center; justify-content: center;
  width: 1.8rem; height: 1.8rem; padding: 0; border-radius: 50%;
  border: 1px solid var(--line); background: var(--surface); color: var(--ink-soft);
  font-size: 1.05rem; line-height: 1; cursor: pointer;
  transition: transform .15s ease, color .15s, background .15s, border-color .15s;
}
.flip:hover:not(:disabled), .flip:focus-visible:not(:disabled) {
  color: var(--accent); background: var(--accent-soft); border-color: var(--line);
  transform: scaleX(-1);
}
.flip:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }
/* Nothing was ever shouted the other way: the arrow stays, and stays dead. */
.flip:disabled { opacity: .4; cursor: default; }
@media (prefers-reduced-motion: reduce) { .flip { transition: none; } }
.ijlist { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .6rem; }
.ijcard { display: flex; flex-direction: column; gap: .4rem; }
/* The words themselves are the point of the panel, so they lead each row. */
.ijtext { margin: 0; font-size: 1.02rem; line-height: 1.45; border-left: 3px solid var(--line); padding-left: .7rem; }
.ijmeta { display: flex; gap: .35rem; flex-wrap: wrap; align-items: center; }
.agenda { max-width: 34ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.methodology { margin-top: 1.6rem; max-width: 70ch; }
</style>
