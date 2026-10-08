<script setup>
// One sitting day's interjections (§6E), behind the day page's "Közbeszólások"
// button: the Közbeszólások page's graph drawn for that day alone, with the words
// behind it listed underneath, each opening onto the moment it was shouted (INT-7).
//
// A dialog rather than a section of the day page, because on most days it is a
// curiosity, not the record — the page offers it in one small button and keeps
// the transcript where it was. It owns no URL state for the same reason; the
// cycle-wide page is where a claim about two members is made citable.
//
// Part of the Interjections module and loaded only when opened, so the d3 bundle
// costs a day page nothing until someone asks for the figure.
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../../api.js'
import InterjectionGraph from '../../components/InterjectionGraph.vue'
import StateBlock from '../../components/StateBlock.vue'
import Pagination from '../../components/Pagination.vue'

const props = defineProps({
  sessionId: { type: String, required: true },
  // The day's graph — already fetched by the page, which offers the button only
  // when it has something in it.
  graph: { type: Object, required: true },
  // The cycle the day belongs to, for the link out to the cycle-wide page.
  period: { type: Number, default: null },
  // The day as the page heading names it, for this dialog's own heading.
  dateLabel: { type: String, default: '' },
})
const emit = defineEmits(['close'])

const PAGE = 25

// What the list is narrowed to: the whole day (null), one arrow, or one member.
// A member opens both directions at once — unlike on the cycle-wide page, a day's
// cross-talk for one person is a handful of lines, and the figure lights up both
// halves of their arrows to match.
const pick = ref(null)

const selectedLink = computed(() => (pick.value?.kind === 'link' ? pick.value.index : -1))
const selectedNode = computed(() => (pick.value?.kind === 'node' ? pick.value.index : -1))

// More people were involved than the figure draws (the API's top-N cap): say how
// much of the day is on screen rather than implying it is all of it (TRUST-1).
const shownShare = computed(() => {
  const g = props.graph
  if (!g.total || g.nodes.length >= g.people_total) return null
  return Math.round((100 * g.shown) / g.total)
})

function color(node) {
  return node?.faction?.color || null
}

// Clicking the thing already picked puts the whole day back.
function onSelect(sel) {
  pick.value = pick.value?.kind === 'link' && pick.value.index === sel.index ? null
    : { kind: 'link', index: sel.index, speaker: sel.source, target: sel.target }
  reload()
}

function onSelectNode(sel) {
  pick.value = pick.value?.kind === 'node' && pick.value.index === sel.index ? null
    : { kind: 'node', index: sel.index, node: sel.node }
  reload()
}

function showAll() {
  pick.value = null
  reload()
}

// --- the list ---------------------------------------------------------------
const list = ref(null)
const listLoading = ref(false)
const listError = ref(false)
const offset = ref(0)
const listRef = ref(null)
const listHeadRef = ref(null)
const page = computed(() => Math.floor(offset.value / PAGE))
const totalPages = computed(() => (list.value ? Math.ceil(list.value.total / PAGE) : 0))

let seq = 0

async function loadList() {
  const s = ++seq
  const p = pick.value
  listLoading.value = true; listError.value = false
  try {
    const res = await api.interjectionList({
      session: props.sessionId,
      speaker: p?.kind === 'link' ? p.speaker.person_id : undefined,
      target: p?.kind === 'link' ? p.target.person_id : undefined,
      person: p?.kind === 'node' ? p.node.person_id : undefined,
      limit: PAGE, offset: offset.value,
    })
    if (s === seq) list.value = res
  } catch {
    if (s === seq) listError.value = true
  } finally {
    if (s === seq) listLoading.value = false
  }
}

// A new pick starts its list from the top, and makes sure its heading is in view
// — but no further: scrolling the list up to the top of the dialog would push the
// figure out, and picking the next arrow is the likeliest thing to do next.
function reload() {
  offset.value = 0
  loadList()
  nextTick(() => listHeadRef.value?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }))
}

function gotoPage(p) {
  offset.value = p * PAGE
  loadList()
  nextTick(() => listRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
}

// --- the dialog itself ---------------------------------------------------------
const closeRef = ref(null)
const graphBox = ref(null)
// On a wide screen the figure's frame is pinned to a share of the *viewport's*
// height rather than left to follow the layout: a sprawling day would otherwise
// come out taller than the screen, and the list it opens onto would start below
// the fold. On a phone the cloud is width-bound and a thumbnail either way (full
// screen is what reads it there), so a pinned frame would only be empty space
// around it — it follows the layout instead, as on the cycle-wide page.
const NARROW = 600
const graphAspect = ref(0)
const graphReady = ref(false)
let opener = null
let bodyOverflow = ''
// A close on the backdrop has to have *started* there. Panning the figure or
// dragging a member out of it and letting go past the dialog's edge ends in a
// click on the backdrop too, and that must not throw the figure away.
let pressedBackdrop = false

function onBackdropDown(e) { pressedBackdrop = e.target === e.currentTarget }
function onBackdropClick(e) {
  if (pressedBackdrop && e.target === e.currentTarget) emit('close')
  pressedBackdrop = false
}

// While the figure is full screen, Escape is the figure's — it leaves full screen
// and the dialog stays. Native full screen swallows the key by itself; the overlay
// the figure falls back to (iOS) does not, so this listens in the capture phase,
// ahead of the figure's own handler, and checks before that handler collapses it.
const dialogRef = ref(null)
function onKeydown(e) {
  if (e.key !== 'Escape' || dialogRef.value?.querySelector('.igraph.expanded')) return
  emit('close')
}

onMounted(() => {
  opener = document.activeElement
  bodyOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  window.addEventListener('keydown', onKeydown, true)
  const w = graphBox.value?.clientWidth || NARROW
  graphAspect.value = w < NARROW ? 0
    : Math.min(1.1, Math.max(0.5, (window.innerHeight * 0.58) / w))
  graphReady.value = true
  closeRef.value?.focus()
  loadList()
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown, true)
  document.body.style.overflow = bodyOverflow
  // Back to the button that opened it — unless the dialog is going because the
  // reader followed a link out, and the button is going with the page.
  if (opener?.isConnected) opener.focus({ preventScroll: true })
})
</script>

<template>
  <Teleport to="body">
    <div class="ijd-backdrop" @pointerdown="onBackdropDown" @click="onBackdropClick">
      <div ref="dialogRef" class="ijd" role="dialog" aria-modal="true" aria-labelledby="ijd-title">
        <header class="ijd-head">
          <div>
            <h2 id="ijd-title">{{ $t('interjections.dayTitle') }}</h2>
            <p class="muted small ijd-sub">
              <template v-if="dateLabel">{{ dateLabel }} · </template>
              {{ $t('interjections.count', { n: graph.total, people: graph.people_total }) }}
            </p>
          </div>
          <button
            ref="closeRef" type="button" class="ijd-x"
            :aria-label="$t('interjections.close')" :title="$t('interjections.close')"
            @click="emit('close')"
          >✕</button>
        </header>

        <div ref="graphBox" class="ijd-graph">
          <InterjectionGraph
            v-if="graphReady"
            :nodes="graph.nodes" :links="graph.links" metric="total"
            :selected="selectedLink" :selected-node="selectedNode"
            :aspect="graphAspect" :wheel-zoom="false"
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
        </div>
        <p class="muted small ijd-hint">
          {{ $t('interjections.dayClickHint') }}
          <template v-if="shownShare !== null">
            {{ $t('interjections.dayShownShare', { n: graph.nodes.length, share: shownShare }) }}
          </template>
        </p>

        <section ref="listRef" class="ijd-list">
          <div ref="listHeadRef" class="ijd-listhead">
            <h3>
              <template v-if="!pick">{{ $t('interjections.dayAll') }}</template>
              <template v-else-if="pick.kind === 'link'">
                <span class="visually-hidden">{{ $t('interjections.rowSpeaker') }}</span>
                <span :style="{ color: color(pick.speaker) }">{{ pick.speaker.label }}</span>
                <span aria-hidden="true"> → </span>
                <span class="visually-hidden">{{ $t('interjections.rowTarget') }}</span>
                <span :style="{ color: color(pick.target) }">{{ pick.target.label }}</span>
              </template>
              <template v-else>
                <span :style="{ color: color(pick.node) }">{{ pick.node.label }}</span>
                <span class="muted small ijd-person">
                  {{ $t('interjections.dayPerson', { made: pick.node.out, received: pick.node.in }) }}
                </span>
              </template>
            </h3>
            <button v-if="pick" type="button" class="btn secondary small" @click="showAll">
              {{ $t('interjections.dayShowAll') }}
            </button>
          </div>

          <StateBlock
            :loading="listLoading && !list" :error="listError"
            :empty="!!list && list.interjections.length === 0"
            :empty-text="$t('interjections.noResults')"
            @retry="loadList"
          >
            <div v-if="list" :class="{ busy: listLoading }">
              <p class="muted small ijd-count">{{ $t('interjections.listCount', { n: list.total }) }}</p>
              <ul class="ijd-rows">
                <li v-for="i in list.interjections" :key="i.id" class="ijd-row">
                  <blockquote class="ijd-text">{{ i.text }}</blockquote>
                  <p class="small muted ijd-meta">
                    <!-- With one arrow picked the heading already names both ends. -->
                    <template v-if="!pick || pick.kind !== 'link'">
                      <span class="visually-hidden">{{ $t('interjections.rowSpeaker') }}</span>
                      <RouterLink :to="{ name: 'profile', params: { id: i.speaker.person_id } }">
                        {{ i.speaker.label }}
                      </RouterLink>
                      <span aria-hidden="true">→</span>
                      <span class="visually-hidden">{{ $t('interjections.rowTarget') }}</span>
                      <RouterLink :to="{ name: 'profile', params: { id: i.target.person_id } }">
                        {{ i.target.label }}
                      </RouterLink>
                      <span aria-hidden="true"> · </span>
                    </template>
                    <template v-if="i.agenda_title">
                      <span class="agenda" :title="i.agenda_title">{{ i.agenda_title }}</span>
                      <span aria-hidden="true"> · </span>
                    </template>
                    <!-- Straight to the moment it was shouted (VIE-3/VIE-5). -->
                    <RouterLink :to="{ name: 'viewer', params: { uid: i.speech_uid },
                                       query: { s: i.sentence_ord } }">
                      {{ $t('interjections.watch') }}
                    </RouterLink>
                  </p>
                </li>
              </ul>
              <Pagination :page="page" :total-pages="totalPages" @goto="gotoPage" />
            </div>
          </StateBlock>
        </section>

        <p class="small ijd-foot">
          <RouterLink :to="{ name: 'interjections', query: period ? { cycle: String(period) } : {} }">
            {{ $t('interjections.dayCycleLink') }} ›
          </RouterLink>
        </p>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* No backdrop blur and no transform on the dialog, unlike the clip exporter's:
   either one makes this box the containing block for `position: fixed`, and the
   figure's own full-screen mode is a fixed overlay that has to reach the edges of
   the viewport, not of the dialog. */
.ijd-backdrop {
  position: fixed; inset: 0; z-index: 100;
  background: rgba(20, 10, 8, .55);
  display: flex; align-items: center; justify-content: center; padding: 1rem;
  animation: ijd-fade .15s ease;
}
.ijd {
  width: min(54rem, 100%); max-height: 94vh; overflow-y: auto; overscroll-behavior: contain;
  background: var(--surface); border-radius: var(--radius);
  box-shadow: 0 12px 40px rgba(0, 0, 0, .35); padding: 1.1rem 1.2rem 1.2rem;
}
@keyframes ijd-fade { from { opacity: 0 } }
@media (prefers-reduced-motion: reduce) { .ijd-backdrop { animation: none; } }
@media (max-width: 560px) {
  .ijd-backdrop { padding: .5rem; }
  .ijd { padding: .85rem .8rem 1rem; max-height: 100%; }
}

.ijd-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; margin-bottom: .6rem; }
.ijd-head h2 { margin: 0; font-size: 1.15rem; }
.ijd-sub { margin: .15rem 0 0; }
.ijd-x {
  flex: none; background: none; border: none; font-size: 1rem; cursor: pointer;
  color: var(--muted); padding: .3rem .45rem; border-radius: 8px; line-height: 1;
}
.ijd-x:hover { background: var(--line); }
.ijd-hint { margin: .4rem 0 0; }

.ijd-list { margin-top: 1.1rem; scroll-margin-top: .5rem; }
.ijd-listhead { display: flex; align-items: center; justify-content: space-between; gap: .8rem; flex-wrap: wrap; }
.ijd-listhead h3 { margin: 0; font-size: 1rem; }
.ijd-person { font-weight: 400; margin-left: .35rem; }
.ijd-count { margin: .2rem 0 .5rem; }
.ijd-rows { list-style: none; margin: 0 0 .5rem; padding: 0; }
.ijd-row { padding: .55rem 0; border-top: 1px solid var(--line); }
.ijd-text { margin: 0 0 .25rem; line-height: 1.45; border-left: 3px solid var(--line); padding-left: .7rem; }
.ijd-meta { margin: 0; display: flex; gap: .35rem; flex-wrap: wrap; align-items: center; }
.agenda { max-width: 40ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.busy { opacity: .5; transition: opacity .2s ease .15s; }
.ijd-foot { margin: .8rem 0 0; text-align: right; }
</style>
