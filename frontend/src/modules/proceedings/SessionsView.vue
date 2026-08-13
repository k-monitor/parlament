<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api.js'
import { store, loadMeta } from '../../store.js'
import { formatDate } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import Pagination from '../../components/Pagination.vue'

const route = useRoute()
const router = useRouter()

const PAGE = 60

const data = ref(null)
const loading = ref(false)
const error = ref(false)

const page = computed(() => Math.floor((Number(route.query.offset) || 0) / PAGE))
const totalPages = computed(() => (data.value ? Math.ceil(data.value.total / PAGE) : 0))

function gotoPage(p) {
  router.push({ name: 'sessions', query: { ...route.query, offset: p * PAGE } })
}

// Monotonic load id: the query and cycle watchers can leave fetches racing;
// only the latest may write state.
let loadSeq = 0

async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false
  // Scoped to the global cycle chooser (store.cycles; empty = all cycles).
  try {
    const res = await api.sessions({
      period: store.cycles, limit: PAGE, offset: route.query.offset || 0,
    })
    if (seq === loadSeq) data.value = res
  } catch {
    if (seq === loadSeq) error.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}
onMounted(() => { loadMeta().catch(() => {}).finally(load) })
watch(() => route.query, load)
// Changing the cycle resets to the first page; the offset reset triggers load via the query watcher.
watch(() => store.cycles.join(','), () => {
  if (route.query.offset) router.push({ name: 'sessions', query: {} })
  else load()
})

// Word-cloud hover preview (SESS-HOVER): hovering a sitting-day card previews the
// day's most distinctive words (the top of its word cloud) in a small popup, so
// you can scan what each day was about without opening it. It reuses the sitting's
// existing wordcloud endpoint (a small `limit`), cached per day and shown after a
// short hover delay so sweeping the grid never fires a burst of fetches. The popup
// is purely informational (pointer-events: none) and never intercepts the click on
// the card. Keyboard focus triggers it too, for parity with the mouse.
const PREVIEW_WORDS = 12
const HOVER_DELAY = 140          // ms — ignore a quick mouse pass over a card

const preview = ref(null)        // { id, words, left, top, placement, width } — the shown popup
const previewCache = new Map()   // session id → words[] (empty array once known empty/failed)
let hoverTimer = 0
let hoverId = null               // the card currently under pointer/focus (debounce guard)

// Only days with published, processed content have a word cloud; skip announced and
// awaiting-media sittings, any empty day, and a day whose transcript hasn't landed
// yet (the cloud is built from text, so it would be empty) — a hover there does
// nothing. `speeches_with_text` is absent on a pre-SIT-2 API, which reads as "not
// known to be zero" and keeps the old behaviour.
function previewable(s) {
  return s.status !== 'scheduled' && s.status !== 'awaiting_media'
    && s.speeches > 0 && s.speeches_with_text !== 0
}

async function fetchPreview(id) {
  if (previewCache.has(id)) return previewCache.get(id)
  try {
    const res = await api.sessionWordcloud(id, PREVIEW_WORDS)
    const words = (res.words || []).slice(0, PREVIEW_WORDS)
    previewCache.set(id, words)
    return words
  } catch {
    previewCache.set(id, [])     // don't re-request a failed day on every hover
    return []
  }
}

const PREVIEW_MAX_W = 300         // px — cap so the popup stays tooltip-sized

function positionPreview(el) {
  const r = el.getBoundingClientRect()
  // Prefer below the card; flip above when there is not enough room beneath it.
  const below = window.innerHeight - r.bottom > 200
  const width = Math.min(Math.max(r.width, 200), PREVIEW_MAX_W)
  // Anchor to the card's left edge, but keep the whole popup inside the viewport.
  const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8))
  return {
    left,
    top: below ? r.bottom + 8 : r.top - 8,
    placement: below ? 'below' : 'above',
    width,
  }
}

function openPreview(s, ev) {
  if (!previewable(s)) return
  const el = ev.currentTarget
  hoverId = s.id
  clearTimeout(hoverTimer)
  hoverTimer = setTimeout(async () => {
    const id = s.id
    const words = await fetchPreview(id)
    if (hoverId !== id || !words.length) return   // moved away, or nothing to show
    preview.value = { id, words, ...positionPreview(el) }
  }, HOVER_DELAY)
}

function closePreview() {
  hoverId = null
  clearTimeout(hoverTimer)
  preview.value = null
}

// A fixed-position popup goes stale on scroll (it doesn't track the card), so just
// dismiss it; the next hover re-opens it in the right place.
onMounted(() => window.addEventListener('scroll', closePreview, { passive: true }))
onBeforeUnmount(() => {
  clearTimeout(hoverTimer)
  window.removeEventListener('scroll', closePreview)
})
</script>

<template>
  <h1>{{ $t('sessions.title') }}</h1>
  <StateBlock :loading="loading" :error="error" @retry="load">
    <div v-if="data" class="grid sgrid">
      <!-- Announced upcoming sittings and held-but-not-yet-processed sittings (no
           per-speech timings/video/transcript yet) are both shown muted & dashed but
           stay clickable — opening them shows whatever is already available. -->
      <router-link
        v-for="s in data.sessions" :key="s.id"
        :to="{ name: 'session', params: { id: s.id } }" class="card pad scard"
        :class="{ scheduled: s.status === 'scheduled', notready: s.status === 'awaiting_media' }"
        @mouseenter="openPreview(s, $event)" @mouseleave="closePreview"
        @focus="openPreview(s, $event)" @blur="closePreview"
      >
        <div class="sdate">{{ formatDate(s.date) }}</div>
        <div class="ssitting">{{ s.sitting }}. {{ $t('sessions.sitting').toLowerCase() }}</div>
        <!-- An announced but not-yet-held sitting: no recording/transcript yet, so
             show it is coming rather than "0 speeches". -->
        <template v-if="s.status === 'scheduled'">
          <div class="badge upcoming">⏳ {{ $t('sessions.upcoming') }}</div>
        </template>
        <template v-else-if="s.status === 'awaiting_media'">
          <div class="badge upcoming">⏳ {{ $t('sessions.notReady') }}</div>
        </template>
        <template v-else>
          <div class="muted small">
            {{ s.speeches }} {{ $t('sessions.speeches') }} · {{ s.agenda_items }} {{ $t('sessions.agendaItems') }}
          </div>
          <!-- Held and browsable, but parlament.hu is still publishing it (SIT-2):
               some speech has no transcript or no per-speech video yet, and the day
               is recent enough that the rest is expected. Marked so the list says
               so rather than presenting a half-published day as finished. A day past
               that window ('incomplete' — permanently video-only, VIE-8) carries no
               badge: nothing is coming, so nothing is pending. -->
          <div class="badge partial" v-if="s.processing === 'pending'"
               :title="$t('sessions.partialNote')">⏳ {{ $t('sessions.partial') }}</div>
        </template>
      </router-link>
    </div>
    <Pagination v-if="data" :page="page" :total-pages="totalPages" @goto="gotoPage" />
  </StateBlock>

  <!-- Word-cloud hover preview: teleported to body so its fixed position is
       viewport-relative (unaffected by any ancestor overflow/transform) and it
       stacks above the page. Purely informational — pointer-events: none. -->
  <Teleport to="body">
    <Transition name="wcp">
      <div
        v-if="preview" class="wc-preview card pad" :class="preview.placement"
        :style="{ left: preview.left + 'px', top: preview.top + 'px', width: preview.width + 'px' }"
        aria-hidden="true"
      >
        <div class="wcp-label small soft">{{ $t('sessions.topicsPreview') }}</div>
        <div class="wcp-words">
          <span
            v-for="w in preview.words" :key="w.text"
            class="wcp-word" :class="{ 'is-entity': w.kind === 'entity' }"
          >{{ w.text }}</span>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.sgrid { grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); }
.scard:hover { text-decoration: none; border-color: var(--accent); }
.sdate { font-weight: 700; font-size: 1.1rem; color: var(--accent); }
.ssitting { font-size: .9rem; color: var(--ink-soft); margin-bottom: .3rem; }
/* Announced upcoming sitting: dashed, muted, so it reads as "coming", not done. */
.scard.scheduled { border-style: dashed; opacity: .85; }
/* Held but not-yet-processed sitting: muted & dashed like an upcoming card, but
   still clickable (opens to whatever speaker/agenda data is already available). */
.scard.notready { border-style: dashed; opacity: .85; }
.badge.upcoming {
  display: inline-block; font-size: .78rem; font-weight: 600;
  padding: .12rem .5rem; border-radius: 999px;
  background: var(--accent-soft); color: var(--ink-soft);
}
/* Still-being-published day (SIT-2): the same pill, one step quieter and set
   under the counts — the day IS browsable, so the marker annotates the card
   rather than replacing what it says. */
.badge.partial {
  display: inline-block; margin-top: .35rem;
  font-size: .72rem; font-weight: 600;
  padding: .1rem .45rem; border-radius: 999px;
  background: var(--line); color: var(--ink-soft);
}

/* Word-cloud hover preview popup (teleported to body; scoped styles still apply
   because the vnodes belong to this component). Informational only, so it never
   swallows the pointer. */
.wc-preview {
  position: fixed; z-index: 60; pointer-events: none;
  padding: .6rem .75rem;
  box-shadow: 0 4px 20px rgba(0,0,0,.14), 0 1px 4px rgba(0,0,0,.08);
}
/* Anchored above the card: its top is the card's top, so lift it by its own height. */
.wc-preview.above { transform: translateY(-100%); }
.wcp-label { margin-bottom: .4rem; font-weight: 600; }
.wcp-words { display: flex; flex-wrap: wrap; gap: .3rem; }
.wcp-word {
  font-size: .82rem; line-height: 1.2;
  padding: .1rem .45rem; border-radius: 999px;
  background: var(--accent-soft); color: var(--ink);
}
/* Recognized names read as a distinct token, matching the word cloud's convention. */
.wcp-word.is-entity { font-style: italic; }

.wcp-enter-active, .wcp-leave-active { transition: opacity .12s ease; }
.wcp-enter-from, .wcp-leave-to { opacity: 0; }
</style>
