<script setup>
// One sitting day (use case 2): transcript segmented agenda item → speech, each
// speech links into the viewer.
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { store, setShowSpeechMetrics } from '../../store.js'
import { SPEECH_METRICS_ENABLED } from '../../features.js'
import { agendaLabel, formatDate, formatSpeakingTime } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import SpeakerLink from '../../components/SpeakerLink.vue'
import WordCloud from '../../components/WordCloud.vue'
import HelpTip from '../../components/HelpTip.vue'
import ShareButton from '../../components/ShareButton.vue'
import SpeechRow from './SpeechRow.vue'

const props = defineProps({ id: String })
const router = useRouter()
const { t } = useI18n()
const data = ref(null)
const loading = ref(false)
const error = ref(false)
// Word cloud (WCLOUD-1): a separate, non-blocking request so it never slows the
// transcript load (WCLOUD-5). A failure here leaves the transcript untouched.
const cloud = ref(null)
// New words (NEW-1): words that debuted on this day, never said before in
// parliament (previous cycles included). Again a separate, non-blocking request.
const newWords = ref(null)
// The new-words list is a secondary curiosity, not the main event — start it
// collapsed so it doesn't push the toplist below the fold (NEW-2).
const newWordsOpen = ref(false)
// Speaker toplist (TOPSPK-1): likewise a separate, non-blocking request (TOPSPK-5).
const topSpeakers = ref(null)
// Longest single speaking time, for sizing the (decorative) bars (TOPSPK-4).
const topMax = computed(() => Math.max(1, ...(topSpeakers.value?.speakers || []).map((s) => s.seconds || 0)))
// Held, but parlament.hu has not published per-speech timings/video/transcript yet:
// there are no durations or clips, so suppress the (meaningless) toplist and show a
// not-yet-ready notice instead (the sittings list also renders it disabled).
const notReady = computed(() => data.value?.session?.status === 'awaiting_media')
// The whole-day recording, on the other hand, is often already published on such a
// day — the segmentation into per-speech clips is what is missing. When it is there
// the speeches stay openable in the viewer (which falls back to the day stream), so
// the day's video is reachable from the day page and not only from a speaker's
// profile or a shared speech link.
const hasDayVideo = computed(() => !!data.value?.session?.video_uri)
// Whether to offer the readability / lexical-diversity toggle on this day at all
// (READ-5). Only when the feature is on, the DB carries measurements, *and* this
// particular day has at least one measurable speech — a toggle that reveals
// nothing is worse than no toggle. Most speeches are unmeasurable (procedural,
// text-less, or under the ~50-word floor), so a short day can genuinely have none.
const hasSpeechMetrics = computed(() => SPEECH_METRICS_ENABLED
  && store.featureEnabled('speech_metrics')
  && (data.value?.agenda || []).some((a) => (a.speeches || []).some((sp) => sp.metrics)))
// Held and browsable, but still being published (SIT-2): a transcript or per-speech
// video is missing from some speech and is still expected. Flagged in the heading
// exactly as in the sittings list, so a half-published day never looks finished.
const partial = computed(() => data.value?.session?.processing === 'pending')
// Title used when sharing this sitting day (mirrors the page heading).
const shareTitle = computed(() => {
  const s = data.value?.session
  return s ? `${formatDate(s.date)} · ${s.sitting}. ${t('sessions.sitting').toLowerCase()}` : ''
})

// Monotonic load id: navigating session A → B with A's requests still in
// flight must not let A's transcript/word-cloud land on B's page.
let loadSeq = 0

async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false; cloud.value = null; newWords.value = null; topSpeakers.value = null
  try {
    const res = await api.session(props.id)
    if (seq === loadSeq) data.value = res
  } catch {
    if (seq === loadSeq) error.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
  api.sessionWordcloud(props.id).then((c) => { if (seq === loadSeq) cloud.value = c }).catch(() => {})
  api.sessionNewWords(props.id).then((n) => { if (seq === loadSeq) newWords.value = n }).catch(() => {})
  api.sessionTopSpeakers(props.id).then((t) => { if (seq === loadSeq) topSpeakers.value = t }).catch(() => {})
}
// Agenda table of contents (TOC-1): a sticky right-hand outline shown only when
// there is horizontal room (wide screens) and there are several items worth
// jumping between. It stays in view as you scroll; a lightweight scroll-spy
// highlights the section being read, and a long list auto-scrolls inside the
// panel to keep that item visible.
const rootEl = ref(null)
const activeAgenda = ref(null)
const showToc = computed(() => (data.value?.agenda?.length || 0) > 1)

let spyRaf = 0
function updateActive() {
  spyRaf = 0
  const sections = rootEl.value ? rootEl.value.querySelectorAll('section.agenda') : []
  if (!sections.length) { activeAgenda.value = null; return }
  // The last section whose top has scrolled above the sticky-header line is the
  // one we're reading; default to the first before any has passed it.
  let current = sections[0].dataset.agendaId
  for (const s of sections) {
    if (s.getBoundingClientRect().top - 80 <= 0) current = s.dataset.agendaId
    else break
  }
  activeAgenda.value = current
}
function onScroll() { if (!spyRaf) spyRaf = requestAnimationFrame(updateActive) }

function goToAgenda(id) {
  const el = document.getElementById('agenda-' + id)
  if (!el) return
  const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches
  el.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' })
  activeAgenda.value = String(id)
}

// Keep the highlighted entry visible inside the sticky panel as the active
// section changes. Only ever adjusts the panel's own scroll — never the window
// — so it can't fight the reader's page scroll.
function scrollActiveIntoView() {
  const panel = rootEl.value ? rootEl.value.querySelector('.toc-inner') : null
  const link = rootEl.value ? rootEl.value.querySelector('.toc-link.active') : null
  if (!panel || !link) return
  const p = panel.getBoundingClientRect()
  const l = link.getBoundingClientRect()
  if (l.top < p.top) panel.scrollTop -= (p.top - l.top) + 8
  else if (l.bottom > p.bottom) panel.scrollTop += (l.bottom - p.bottom) + 8
}

onMounted(() => {
  load()
  window.addEventListener('scroll', onScroll, { passive: true })
})
onUnmounted(() => {
  window.removeEventListener('scroll', onScroll)
  if (spyRaf) cancelAnimationFrame(spyRaf)
})
watch(() => props.id, load)
// Re-evaluate the active section once a freshly loaded day has rendered.
watch(data, () => { nextTick(updateActive) })
// Track the highlight within the sticky panel whenever it changes.
watch(activeAgenda, () => { nextTick(scrollActiveIntoView) })

// Clicking a word opens proceedings search scoped to this sitting day (WCLOUD-4).
function searchWord(word) {
  const date = data.value?.session?.date
  router.push({ name: 'search', query: { q: word, date_from: date, date_to: date } })
}
</script>

<template>
  <StateBlock :loading="loading" :error="error" @retry="load">
    <div v-if="data" ref="rootEl">
      <router-link :to="{ name: 'sessions' }" class="small">‹ {{ $t('sessions.title') }}</router-link>
      <div class="titlebar">
        <h1>
          {{ formatDate(data.session.date) }} · {{ data.session.sitting }}. {{ $t('sessions.sitting').toLowerCase() }}
          <span class="badge upcoming" v-if="data.session.status === 'scheduled'">⏳ {{ $t('sessions.upcoming') }}</span>
          <span class="badge upcoming" v-else-if="notReady">⏳ {{ $t('sessions.notReady') }}</span>
          <span class="badge upcoming" v-else-if="partial" :title="$t('sessions.partialNote')">⏳ {{ $t('sessions.partial') }}</span>
        </h1>
        <ShareButton :title="shareTitle" align="right" />
      </div>
      <p class="muted small" v-if="data.session.source_page">
        <a :href="data.session.source_page" target="_blank" rel="noopener">↗ {{ $t('viewer.viewOnParlament') }}</a>
      </p>

      <div class="session-body">
        <div class="session-main">
          <!-- Held but not-yet-available sitting: parlament.hu has published no
               per-speech timings/transcript. When the day's own recording is
               already up, say so (and the ▶ on each speech opens it) instead of
               claiming the video is missing too. -->
          <section v-if="notReady" class="card pad empty-day">
            <p>{{ hasDayVideo ? $t('sessions.notReadyVideoNote') : $t('sessions.notReadyNote') }}</p>
          </section>
          <!-- An announced/upcoming sitting (no speeches yet) or one parlament.hu has
               not populated: show it is coming instead of an empty transcript. -->
          <section v-else-if="!data.agenda.length" class="card pad empty-day">
            <p>{{ data.session.status === 'scheduled' ? $t('sessions.upcomingNote') : $t('sessions.notProcessed') }}</p>
          </section>

          <section v-if="cloud && cloud.words.length" class="card pad wcloud">
            <div class="sechead">
              <h2 class="wcloud-title">{{ $t('sessions.wordcloud') }}</h2>
              <HelpTip :label="$t('sessions.wordcloud')"><p>{{ $t('sessions.wordcloudCaption') }}</p></HelpTip>
            </div>
            <WordCloud :words="cloud.words" :caption="$t('sessions.wordcloudCaption')" :show-caption="false" @pick="searchWord" />
          </section>

          <section v-if="newWords && newWords.words.length" class="card pad newwords">
            <div class="sechead sechead-collapsible" :class="{ open: newWordsOpen }">
              <button
                class="sechead-toggle" type="button"
                :aria-expanded="newWordsOpen" @click="newWordsOpen = !newWordsOpen"
              >
                <span class="chev" :class="{ open: newWordsOpen }" aria-hidden="true">▸</span>
                <h2 class="wcloud-title">{{ $t('sessions.newWords') }}</h2>
              </button>
              <HelpTip :label="$t('sessions.newWords')"><p>{{ $t('sessions.newWordsCaption') }}</p></HelpTip>
            </div>
            <ul v-show="newWordsOpen" class="chips">
              <li v-for="w in newWords.words" :key="w.text">
                <button
                  type="button" class="chip" :title="`${w.text}: ${w.count}`"
                  @click="searchWord(w.text)"
                >{{ w.text }}<span class="chip-count" v-if="w.count > 1">{{ w.count }}</span></button>
              </li>
            </ul>
          </section>

          <section v-if="!notReady && topSpeakers && topSpeakers.speakers.length" class="card pad toplist">
            <div class="sechead">
              <h2 class="wcloud-title">{{ $t('sessions.topSpeakers') }}</h2>
              <HelpTip :label="$t('sessions.topSpeakers')"><p>{{ $t('sessions.topSpeakersCaption') }}</p></HelpTip>
            </div>
            <ol class="top-rows">
              <li v-for="sp in topSpeakers.speakers" :key="sp.person_id" class="top-row">
                <SpeakerLink :speaker="sp" size="sm" />
                <FactionBadge v-if="sp.faction" :faction="sp.faction" />
                <span v-else aria-hidden="true"></span>
                <span class="bar-track" aria-hidden="true">
                  <span class="bar-fill" :style="{ width: ((sp.seconds / topMax) * 100) + '%', background: sp.faction && sp.faction.color || 'var(--accent)' }"></span>
                </span>
                <span class="top-time">⏱ {{ formatSpeakingTime(sp.seconds) }}</span>
                <span class="muted small top-count">{{ sp.speeches }} {{ $t('sessions.speeches') }}</span>
              </li>
            </ol>
          </section>

          <!-- Beszédmetrikák (READ-5). The chips are OFF by default on this list:
               a sitting day runs to hundreds of rows, and two extra chips on each
               would bury the speaker, faction and duration people actually scan
               for. One control turns the column on for a reader who came to
               compare, and the choice is remembered across days and visits. The
               single-speech viewer shows the same numbers unconditionally, so the
               annotation is discoverable whether or not this is ever pressed. -->
          <div v-if="hasSpeechMetrics" class="metrics-toggle">
            <button
              type="button" class="btn secondary small"
              :aria-pressed="store.showSpeechMetrics ? 'true' : 'false'"
              @click="setShowSpeechMetrics(!store.showSpeechMetrics)"
            >📖 {{ store.showSpeechMetrics ? $t('sessions.metricsHide') : $t('sessions.metricsShow') }}</button>
            <HelpTip :label="$t('sessions.metricsLabel')"><p>{{ $t('sessions.metricsCaption') }}</p></HelpTip>
          </div>

          <!-- The agenda + speaker list is shown even for a not-yet-processed day
               (names/order exist); only the timing toplist above is suppressed. -->
          <section
            v-for="a in data.agenda" :key="a.id" class="agenda card"
            :id="'agenda-' + a.id" :data-agenda-id="a.id"
          >
            <!-- A day parlament.hu has not linked to agenda acts yet has ONE
                 unnamed item holding every speech in order (see the pipeline's
                 `_unlinked_agenda_item`); name that section generically rather
                 than heading the day with a blank line. -->
            <h2 class="pad agenda-title">
              {{ a.official_title || a.title || $t('sessions.unlistedAgenda') }}
              <span class="badge" v-if="a.type">{{ agendaLabel(a.type) }}</span>
            </h2>
            <ul class="speeches">
              <SpeechRow
                v-for="sp in a.speeches" :key="sp.uid" :speech="sp"
                :playable="!notReady" :viewable="!notReady || hasDayVideo"
              />
            </ul>
          </section>

          <!-- Move to the chronologically adjacent sitting day of the same cycle.
               Absent at the cycle's first/last sitting (neighbour is null). -->
          <nav
            v-if="data.neighbours && (data.neighbours.prev || data.neighbours.next)"
            class="day-nav" :aria-label="$t('sessions.dayNav')"
          >
            <router-link
              v-if="data.neighbours.prev" class="day-nav-btn prev"
              :to="{ name: 'session', params: { id: data.neighbours.prev.id } }"
            >
              <span class="day-nav-dir">‹ {{ $t('sessions.prevDay') }}</span>
              <span class="day-nav-date">{{ formatDate(data.neighbours.prev.date) }} · {{ data.neighbours.prev.sitting }}. {{ $t('sessions.sitting').toLowerCase() }}</span>
            </router-link>
            <span v-else aria-hidden="true"></span>
            <router-link
              v-if="data.neighbours.next" class="day-nav-btn next"
              :to="{ name: 'session', params: { id: data.neighbours.next.id } }"
            >
              <span class="day-nav-dir">{{ $t('sessions.nextDay') }} ›</span>
              <span class="day-nav-date">{{ formatDate(data.neighbours.next.date) }} · {{ data.neighbours.next.sitting }}. {{ $t('sessions.sitting').toLowerCase() }}</span>
            </router-link>
            <span v-else aria-hidden="true"></span>
          </nav>
        </div>

        <!-- Agenda jump list (TOC-1). Hidden on narrow screens (see CSS); a
             sticky outline of the day's agenda items, current one highlighted. -->
        <aside v-if="showToc" class="session-toc">
          <nav class="toc-inner" :aria-label="$t('sessions.agenda')">
            <p class="toc-title">{{ $t('sessions.agenda') }}</p>
            <ol class="toc-list">
              <li v-for="(a, i) in data.agenda" :key="a.id">
                <a
                  :href="'#agenda-' + a.id" class="toc-link"
                  :class="{ active: activeAgenda === String(a.id) }"
                  :aria-current="activeAgenda === String(a.id) ? 'true' : undefined"
                  @click.prevent="goToAgenda(a.id)"
                >
                  <span class="toc-num" aria-hidden="true">{{ i + 1 }}</span>
                  <span class="toc-text">{{ a.official_title || a.title || $t('sessions.unlistedAgenda') }}</span>
                </a>
              </li>
            </ol>
          </nav>
        </aside>
      </div>
    </div>
  </StateBlock>
</template>

<style scoped>
/* Two-column layout: transcript + a sticky agenda ToC. Single column by
   default; the ToC appears alongside the content once there is horizontal room
   (≥1100px). The content column stays left-aligned with the page header — the
   sidebar is what gives ground, never the alignment. */
.session-body { display: block; }
/* No room for a sidebar on narrow screens — the outline is desktop-only. */
.session-toc { display: none; }
@media (min-width: 1100px) {
  .session-body {
    --toc-w: 240px;
    --toc-gap: 2rem;
    display: grid;
    grid-template-columns: minmax(0, 1fr) var(--toc-w);
    gap: var(--toc-gap);
    align-items: start;
  }
  .session-toc { display: block; }
}
/* On wide screens the transcript would be needlessly narrow, so let the sidebar
   spill into the right gutter and hand the content its full container width
   back. Extends right only (never left) so the content stays aligned with the
   header above it. Enabled only once the gutter is wide enough to hold it. */
@media (min-width: 1650px) {
  .session-body { margin-right: calc(-1 * (var(--toc-w) + var(--toc-gap))); }
}

/* The outline is a self-contained panel pinned below the site header so it
   stays with the reader; a long list scrolls inside the panel (never chaining
   to the page) rather than growing past the viewport. The column must stretch
   to the full transcript height (align-self: stretch, overriding the grid's
   align-items: start) — otherwise it collapses to the panel's height and
   `position: sticky` has no room to travel, so the panel scrolls away. */
.session-toc { align-self: stretch; }
.toc-inner {
  position: sticky; top: 72px;
  max-height: calc(100vh - 72px - 1rem);
  overflow-y: auto; overscroll-behavior: contain;
  background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius); box-shadow: var(--shadow);
  padding: .85rem 1rem 1rem;
}
.toc-title {
  margin: 0 0 .5rem; padding-bottom: .5rem; border-bottom: 1px solid var(--line);
  font-size: .72rem; font-weight: 700; letter-spacing: .06em;
  text-transform: uppercase; color: var(--ink-faint);
}
.toc-list { list-style: none; margin: 0; padding: 0; }
.toc-link {
  display: flex; gap: .55rem; align-items: baseline;
  /* Negative side margin lets the hover/active fill reach the panel's padding
     edges while the text keeps a comfortable inset. */
  margin: 0 -.5rem; padding: .32rem .5rem;
  color: var(--ink-soft); font-size: .86rem; line-height: 1.35;
  border-radius: 6px; border-left: 2px solid transparent;
  transition: background .12s, color .12s, border-color .12s;
}
.toc-link:hover { background: var(--bg); color: var(--ink); text-decoration: none; }
.toc-link.active {
  background: var(--accent-soft); color: var(--accent); font-weight: 600;
  border-left-color: var(--accent);
}
.toc-num { flex: none; color: var(--ink-faint); font-variant-numeric: tabular-nums; font-size: .78rem; min-width: 1.3em; text-align: right; }
.toc-link.active .toc-num { color: var(--accent); }
.toc-text {
  /* Clamp long agenda titles to two lines so the outline stays scannable. */
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}

/* Heading + share control on one line; the share button drops below the title
   on narrow screens rather than crowding it. */
.titlebar { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.titlebar h1 { margin-bottom: 0; }
.wcloud { margin-bottom: 1rem; }
.sechead { display: flex; align-items: center; gap: .35rem; margin-bottom: .6rem; }
.sechead .wcloud-title { margin: 0; }
/* Collapsible section header: the title doubles as the expand/collapse control. */
.sechead-toggle {
  display: inline-flex; align-items: center; gap: .4rem;
  background: none; border: none; padding: 0; margin: 0;
  cursor: pointer; color: inherit; font: inherit; text-align: left;
}
.sechead-toggle .chev { font-size: .8em; opacity: .6; transition: transform .15s ease; }
.sechead-toggle .chev.open { transform: rotate(90deg); }
.sechead-toggle:hover .wcloud-title, .sechead-toggle:focus-visible .wcloud-title { color: var(--accent); }
/* Collapsed, the header row IS the whole card, so the hit target is stretched
   over it (out to the card's 1rem/1.2rem padding) instead of ending at the
   title — clicking anywhere on the widget toggles. The row also drops its
   bottom margin while collapsed, which otherwise pushed the title off-centre.
   Expanded, the overlay stops at the row so it can't swallow the chips. */
.sechead-collapsible { position: relative; margin-bottom: 0; }
.sechead-collapsible.open { margin-bottom: .6rem; }
.sechead-toggle::after { content: ''; position: absolute; inset: -1rem -1.2rem; }
.sechead-collapsible.open .sechead-toggle::after { bottom: 0; }
/* Keep the "?" above that overlay so its own popover stays clickable. */
.sechead-collapsible .helptip { z-index: 1; }
.wcloud-title { font-size: 1.05rem; margin: 0 0 .6rem; }
.newwords { margin-bottom: 1rem; }
.chips { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: .4rem; }
/* Scoped to `.chips` on purpose: an unqualified `.chip` also matches the root
   element of the FactionBadge child component (Vue scoped CSS applies to a
   child's root), which turned every faction badge in the top-speaker list into
   a stretched pink pill. */
.chips .chip {
  display: inline-flex; align-items: baseline; gap: .32rem;
  font: inherit; font-size: .9rem; cursor: pointer;
  padding: .2rem .55rem; border-radius: 999px;
  border: 1px solid var(--line); background: var(--accent-soft); color: var(--ink);
  transition: border-color .12s, background .12s;
}
.chips .chip:hover, .chips .chip:focus-visible { border-color: var(--accent); outline: none; }
.chips .chip-count { font-size: .72rem; color: var(--muted); font-variant-numeric: tabular-nums; }
.toplist { margin-bottom: 1rem; }
/* Right-aligned control row above the agenda, so the toggle reads as a display
   option for the list below rather than as part of the day's content. */
.metrics-toggle { display: flex; align-items: center; justify-content: flex-end; gap: .4rem; margin: 0 0 .5rem; }
.top-rows { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: minmax(130px, 1.5fr) auto 1fr auto auto; row-gap: .24rem; }
.top-row { display: grid; grid-template-columns: subgrid; grid-column: 1 / -1; column-gap: .55rem; align-items: center; padding: .12rem 0; font-size: .9rem; }
.top-row :deep(.row span) { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.top-row .bar-track { background: #eceae4; border-radius: 5px; height: 9px; overflow: hidden; }
.top-row .bar-fill { display: block; height: 100%; border-radius: 5px; min-width: 2px; }
.top-time { font-size: .82rem; font-variant-numeric: tabular-nums; color: var(--ink); white-space: nowrap; }
.top-count { white-space: nowrap; }
@media (max-width: 560px) {
  .top-rows { grid-template-columns: 1fr auto; column-gap: .5rem; }
  .top-row .bar-track { grid-column: 1 / -1; }
}
.badge.upcoming {
  font-size: .72rem; font-weight: 600; vertical-align: middle;
  padding: .15rem .55rem; border-radius: 999px;
  background: var(--accent-soft); color: var(--ink-soft);
}
.empty-day { color: var(--ink-soft); text-align: center; }
.empty-day p { margin: .3rem 0; }
/* scroll-margin keeps a jumped-to section clear of the sticky site header. */
.agenda { margin-bottom: 1rem; scroll-margin-top: 72px; }
.agenda-title { font-size: 1.05rem; margin: 0; border-bottom: 1px solid var(--line); display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; }
.speeches { list-style: none; margin: 0; padding: .3rem; }

/* Prev/next sitting-day navigation at the end of the transcript. space-between
   keeps prev anchored left / next right; the empty placeholder <span> on a
   missing side preserves that alignment when only one neighbour exists. */
.day-nav { display: flex; justify-content: space-between; gap: 1rem; margin-top: 1.5rem; }
.day-nav-btn {
  display: flex; flex-direction: column; gap: .12rem; max-width: 47%;
  padding: .55rem .85rem; border: 1px solid var(--line); border-radius: var(--radius);
  background: var(--surface); box-shadow: var(--shadow); text-decoration: none;
  transition: border-color .12s, background .12s;
}
.day-nav-btn:hover, .day-nav-btn:focus-visible { border-color: var(--accent); text-decoration: none; }
.day-nav-btn.next { text-align: right; align-items: flex-end; }
.day-nav-dir { font-size: .78rem; font-weight: 600; color: var(--accent); }
.day-nav-date { font-size: .92rem; color: var(--ink); }
</style>
