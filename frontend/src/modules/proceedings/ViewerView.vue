<script setup>
// Proceedings viewer (§5.2) — the signature feature.
//
// * VIE-2/VIE-9: embeds an HLS player (hls.js where the browser lacks native
//   HLS) on a clip of *this speech* — the per-speech smil the backend derives
//   from the day recording — so the player shows only the current speech, not
//   the whole multi-hour day. Falls back to the whole-day stream if no clip.
// * VIE-3: click a sentence → seek the player to its `time_start`.
// * VIE-4: as the video plays, the currently-spoken sentence highlights and the
//   transcript auto-scrolls (karaoke following on time_start/time_end).
// * VIE-5: deep-linkable — /proceedings/<uid>?s=<ord> or ?t=<seconds> reopens at
//   the exact moment. The current sentence is reflected back into the URL hash.
// * VIE-6/VIE-7/VIE-8: estimated-timing disclosure, source link, and a graceful
//   no-transcript state.
// * VIE-9: when the clip ends, auto-advance to the next speech and keep playing
//   — opt-in (`store.autoplayNext`), off unless the reader ticks the box here.
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Hls from 'hls.js'
import { api } from '../../api.js'
import { store, setAutoplayNext } from '../../store.js'
import { agendaLabel, formatDate, formatDuration, segmentSentences } from '../../format.js'
import EntityText from '../../components/EntityText.vue'
import StateBlock from '../../components/StateBlock.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import SpeakerLink from '../../components/SpeakerLink.vue'
import TimingBadge from '../../components/TimingBadge.vue'
import SpeechMetricsBadge from '../../components/SpeechMetricsBadge.vue'
import TopicBadge from '../../components/TopicBadge.vue'
import ShareButton from '../../components/ShareButton.vue'
import ExportDialog from '../../components/ExportDialog.vue'

const props = defineProps({ uid: String })
const route = useRoute()
const router = useRouter()

const data = ref(null)
const loading = ref(false)
const error = ref(false)
const videoEl = ref(null)
const playerEl = ref(null)
const currentOrd = ref(-1)
const showExport = ref(false)   // clip-export dialog (VIE-10)
// Which segment the export dialog opens on. Null = whole speech (header button);
// set to a sentence index by the per-sentence download button so the dialog
// opens pre-scoped to that sentence.
const exportStartIdx = ref(null)
const exportEndIdx = ref(null)
// Custom-controls state. The player loads a per-speech clip (0-based timeline),
// so currentTime/duration already describe just this speech — no windowing.
const playing = ref(false)
const muted = ref(false)
const volume = ref(1)
const duration = ref(0)        // loaded clip duration (≈ the speech length)
const currentTime = ref(0)     // playhead within the clip (clip-relative seconds)
const isFullscreen = ref(false)
let hls = null
let autoFollow = true
// Player state that must survive navigation between speeches (vue-router reuses
// this component instance, so plain `let`s persist):
//   resumePlaying — keep playing across an auto-advance / navigation
//   advancing     — guard so the clip's `ended` fires the next-jump only once
//   loadedSrc     — the clip URL currently attached, to skip redundant reloads
let resumePlaying = false
let advancing = false
let loadedSrc = null

const sentences = computed(() => (data.value && data.value.sentences) || [])
const speech = computed(() => data.value && data.value.speech)
const session = computed(() => data.value && data.value.session)

// name -> { person_id, label, photo_uri } for heckles attributed to a resolvable
// MP (best-effort; see resolveHecklers).
const speakers = ref({})

// Each karaoke sentence, with its text split into segments (spoken runs +
// lifted-out parenthetical stage directions / heckles) — see segmentSentences().
// The original sentence fields (ord/time_start/time_end) are kept so seeking,
// karaoke highlight, deep links and copy-link all still key off the sentence.
// Parenthesis state is threaded sentence-to-sentence so a stage direction split
// across sentence boundaries ("…úrtól. (" / "A miniszterek felállnak." / "…tett
// minisztereknek.)") is lifted out as asides instead of leaking stray "(" / ")".
// Each sentence carries a deep-link to itself (?s=<ord>, VIE-5) and its own text
// as share/post copy. Built here (recomputes only on data change) rather than in
// the template, which re-renders on every playback tick.
const segmentedSentences = computed(() =>
  segmentSentences(sentences.value).map((s) => ({
    ...s,
    shareUrl: location.origin + router.resolve({ name: 'viewer', params: { uid: props.uid }, query: { s: s.ord } }).href,
    shareTitle: s.text || '',
  })),
)
// Whole-speech share link (the canonical viewer URL, no sentence anchor).
const speechShareUrl = computed(() => location.origin + router.resolve({ name: 'viewer', params: { uid: props.uid } }).href)
const speechShareTitle = computed(() => speech.value?.speaker?.label || '')

// Attribute named heckles ("Vitályos Eszter: …") to representatives so they get
// a face + profile link. Best-effort: failure just leaves them as plain text.
// `seq` ties the resolution to the load that started it, so a slow response
// can't attach speech A's hecklers to speech B.
async function resolveHecklers(seq) {
  const names = segmentedSentences.value
    .flatMap((s) => s.segments).filter((g) => g.speaker).map((g) => g.speaker)
  speakers.value = {}
  if (!names.length) return
  try {
    const resolved = await api.resolveSpeakers(names)
    if (seq === loadSeq) speakers.value = resolved
  } catch { /* keep plain */ }
}

// An interjection whose "Name:" prefix didn't resolve is shown verbatim.
function asideText(seg) {
  return seg.speaker ? `${seg.speaker}: ${seg.text}` : seg.text
}

// Open the clip-export dialog for the whole speech (header button).
function openExport() {
  exportStartIdx.value = null
  exportEndIdx.value = null
  showExport.value = true
}
// Open it pre-scoped to a single transcript sentence (per-sentence download). The
// segmentedSentences list maps 1:1 onto `sentences`, so its index is the index
// the dialog selects on.
function openExportForSentence(i) {
  exportStartIdx.value = i
  exportEndIdx.value = i
  showExport.value = true
}

// Person names recognized in the transcript, resolved to Wikidata/Wikipedia
// (NEL, §10); EntityText matches their surfaces inline and wraps them as links.
const entities = computed(() => (data.value && data.value.entities) || [])

// Prefer the per-speech clip (VIE-9); fall back to the whole-day stream when the
// backend couldn't derive one (speech missing real offsets).
const usingClip = computed(() => !!(speech.value && speech.value.video_uri))
const videoSrc = computed(() =>
  (speech.value && speech.value.video_uri) || (session.value && session.value.video_uri) || null)
const videoPlayseq = computed(() => usingClip.value
  ? (speech.value && speech.value.video_playseq) || null
  : (session.value && session.value.video_playseq) || null)
// Day-absolute time that the clip's t=0 maps to, so sentence times (which are
// day-absolute) convert to clip-relative seeks: clipT = time - clipOrigin. The
// clip is cut at the speech's `video_start` (per_speech_clip, media.py), so THAT
// — not `time_start` — is its origin. They differ by the pre-speech preamble
// (chair calling the speaker, walking to the podium, applause) that whisper
// alignment correctly places *before* the first spoken sentence: using
// `time_start` would shift the whole clip by that gap (up to ~20s). For the
// whole-day fallback the clip *is* the day, so the origin is 0.
const clipOrigin = computed(() =>
  (usingClip.value && speech.value.video_start != null) ? speech.value.video_start : 0)

// "View on parlament.hu" (VIE-7): the per-speech playseq player plays exactly
// this speech's clip on parlament.hu's own server — a real per-speech original,
// not the generic portal. Falls back to the generic page when there's no clip.
const sourceLink = computed(() => (speech.value
  && (speech.value.video_playseq || speech.value.source_page)) || null)

// Monotonic load id: rapid prev/next navigation can leave several speech
// fetches in flight, and they may resolve out of order — only the latest may
// write state, or the viewer ends up showing speech A at speech B's URL.
let loadSeq = 0

async function load() {
  // When navigating between speeches we already have data on screen — keep it
  // (and the live <video>) mounted instead of dropping to the loading state,
  // so the player isn't torn down and recreated. Only the first load (no data
  // yet) shows the spinner, since the <video> doesn't exist until then.
  const navigating = !!data.value
  const seq = ++loadSeq
  error.value = false
  if (!navigating) { loading.value = true; data.value = null }
  let next
  try {
    next = await api.speech(props.uid)
  } catch (e) {
    if (seq !== loadSeq) return
    error.value = true; loading.value = false; data.value = null; return
  }
  if (seq !== loadSeq) return  // superseded by a newer navigation
  data.value = next
  loading.value = false
  currentOrd.value = -1
  currentTime.value = 0; duration.value = 0   // new clip; clear stale bar values
  advancing = false        // ready to detect the next speech boundary
  // Wait a tick so the <video> exists (first load) / data has propagated,
  // then position the player. setupPlayer reuses the live player when the day
  // stream is unchanged, or builds one when it isn't.
  await nextTick()
  if (seq !== loadSeq) return
  setupPlayer()
  resolveHecklers(seq)   // non-blocking; updates `speakers` when it resolves
}

// The clip's smil VOD is generated on demand: its playlist 404s until the
// `playseq.php` activation endpoint has been hit once. It's a cross-origin
// side-effect endpoint (opaque response), so we await the request (capped, so a
// hang never stalls the player) before requesting the playlist.
function activateStream() {
  const playseq = videoPlayseq.value
  if (!playseq) return Promise.resolve()
  let f
  try { f = fetch(playseq, { mode: 'no-cors' }).catch(() => {}) }
  catch { return Promise.resolve() }
  return Promise.race([f, new Promise((r) => setTimeout(r, 4000))])
}

async function setupPlayer() {
  const video = videoEl.value
  if (!video || !videoSrc.value) return
  const src = videoSrc.value

  // Position the playhead, unless a deep link overrides it. Never set
  // currentTime on an unloaded element — that races the load and aborts it — so
  // this runs once the media is ready. Sentence/`?t` targets are day-absolute;
  // the clip is 0-based, so they convert through clipOrigin.
  const applyInitialSeek = () => {
    const t = route.query.t != null ? Number(route.query.t) : null
    const s = route.query.s != null ? Number(route.query.s) : null
    if (t != null && !Number.isNaN(t)) seekToDay(t, resumePlaying)
    else if (s != null && sentences.value[s]) playSentence(sentences.value[s], resumePlaying)
    else seekClip(0, resumePlaying)
    resumePlaying = false
    // Bring the deep-linked sentence (?s=<ord>) into view on open, even when
    // autoplay is blocked and no `timeupdate` fires to auto-scroll for us.
    if (currentOrd.value >= 0) nextTick(() => scrollToCurrent())
  }

  // Same clip already attached (e.g. re-entering the same speech): just
  // reposition without a reload.
  if (loadedSrc === src && (hls || video.src)) {
    applyInitialSeek()
    return
  }

  destroyHls()
  await activateStream()                 // make the clip's playlist available
  if (videoEl.value !== video || videoSrc.value !== src) return  // navigated away meanwhile
  loadedSrc = src

  if (Hls.isSupported()) {
    // hls.js (Chrome/Firefox/Edge): more reliable than trusting canPlayType.
    hls = new Hls({ maxBufferLength: 30 })
    hls.on(Hls.Events.MANIFEST_PARSED, applyInitialSeek)
    hls.on(Hls.Events.ERROR, (_e, d) => {
      if (!d || !d.fatal) return
      if (d.type === Hls.ErrorTypes.NETWORK_ERROR) {
        activateStream().then(() => hls && hls.startLoad())   // re-activate + retry
      } else if (d.type === Hls.ErrorTypes.MEDIA_ERROR) {
        hls && hls.recoverMediaError()
      }
    })
    hls.loadSource(src)
    hls.attachMedia(video)
  } else {
    // Native HLS (Safari/iOS): seek once metadata is in.
    video.src = src
    video.addEventListener('loadedmetadata', applyInitialSeek, { once: true })
  }
  // Bound once per element; addEventListener dedupes identical (fn, options)
  // pairs, so rebuilding on the persistent element never double-registers.
  video.addEventListener('timeupdate', onTimeUpdate)
  video.addEventListener('play', onPlayState)
  video.addEventListener('pause', onPlayState)
  video.addEventListener('ended', onSpeechEnd)
  video.addEventListener('durationchange', onDurationChange)
  video.addEventListener('volumechange', onVolumeChange)
  onPlayState(); onDurationChange(); onVolumeChange()
}

function onPlayState() { const v = videoEl.value; if (v) playing.value = !v.paused }
function onDurationChange() { const v = videoEl.value; if (v && isFinite(v.duration)) duration.value = v.duration }
function onVolumeChange() { const v = videoEl.value; if (v) { muted.value = v.muted; volume.value = v.volume } }

function destroyHls() {
  if (hls) { try { hls.destroy() } catch { /* ignore */ } hls = null }
  loadedSrc = null
}

// The clip ended (VIE-9): hop to the next speech and keep playing — but only if
// the reader asked for that (the auto-advance toggle under the player). Off, and
// on the last speech where there's no next, the player simply rests at the end.
function onSpeechEnd() {
  if (advancing || !store.autoplayNext) return
  const next = data.value && data.value.neighbours && data.value.neighbours.next
  if (!next) return
  advancing = true
  resumePlaying = true                  // reached the end by playing — keep going
  router.push({ name: 'viewer', params: { uid: next } })
}

function onTimeUpdate() {
  const v = videoEl.value
  if (!v) return
  currentTime.value = v.currentTime     // clip-relative; drives the control bar
  // Highlight the sentence being spoken. Sentence times are day-absolute, the
  // playhead is clip-relative, so compare in day-absolute coordinates.
  const dayNow = v.currentTime + clipOrigin.value
  const list = sentences.value
  let idx = -1
  for (let i = 0; i < list.length; i++) {
    const a = list[i].time_start, b = list[i].time_end
    if (a != null && dayNow >= a && (b == null || dayNow < b)) { idx = i; break }
  }
  if (idx !== currentOrd.value) {
    currentOrd.value = idx
    if (idx >= 0 && autoFollow) scrollToCurrent()
  }
}

// Karaoke auto-scroll. A smooth scrollIntoView fires the container's own
// `scroll` events, which would otherwise trip onUserScroll and switch OFF
// auto-follow (making the transcript stutter along in 4s bursts). Suppress
// those self-inflicted events for a short window around each programmatic
// scroll; a real user scroll outside that window still pauses following.
function scrollToCurrent() {
  const el = document.getElementById('s-' + currentOrd.value)
  if (!el) return
  suppressScrollUntil = performance.now() + 900
  el.scrollIntoView({ block: 'center', behavior: 'smooth' })
}

// Seek to a clip-relative second (the control bar / clip start work in these).
function seekClip(seconds, play = true) {
  const v = videoEl.value
  if (!v) return
  const go = () => {
    try { v.currentTime = Math.max(0, seconds) } catch { /* not seekable yet */ }
    // play() can reject (autoplay policy, or the seek interrupting a pending
    // load). Always swallow it — it's not an error the user needs to see.
    if (play && v.play) { const p = v.play(); if (p && p.catch) p.catch(() => {}) }
  }
  if (v.readyState >= 1) go()
  else v.addEventListener('loadedmetadata', go, { once: true })
}

// Seek to a day-absolute second (sentence times, `?t` deep links).
function seekToDay(daySeconds, play = true) {
  seekClip(daySeconds - clipOrigin.value, play)
}

function playSentence(s, play = true) {
  if (s.time_start == null) return
  autoFollow = true
  seekToDay(s.time_start, play)
  currentOrd.value = s.ord
  // Reflect the chosen sentence in the URL so the moment is citable (VIE-5).
  router.replace({ query: { ...route.query, s: s.ord, t: undefined } })
}


// --- Custom controls (scoped to the speech window, VIE-9) ---------------
function togglePlay() {
  const v = videoEl.value; if (!v) return
  if (v.paused) { const p = v.play(); if (p && p.catch) p.catch(() => {}) }
  else v.pause()
}
function onSeekBar(e) {
  const v = videoEl.value; if (!v) return
  autoFollow = true
  seekClip(Number(e.target.value), !v.paused)
}
function toggleMute() { const v = videoEl.value; if (v) v.muted = !v.muted }
function onVolumeBar(e) {
  const v = videoEl.value; if (!v) return
  const val = Number(e.target.value); v.volume = val; v.muted = val === 0
}
function toggleFullscreen() {
  const el = playerEl.value; if (!el) return
  if (document.fullscreenElement) document.exitFullscreen && document.exitFullscreen()
  else el.requestFullscreen && el.requestFullscreen()
}
function onFullscreenChange() { isFullscreen.value = !!document.fullscreenElement }

// Pause auto-follow while the user manually scrolls; resume when they stop.
// Scroll events fired by our own scrollToCurrent() (within suppressScrollUntil)
// are ignored so karaoke-follow doesn't disable itself.
let scrollTimer = null
let suppressScrollUntil = 0
function onUserScroll() {
  if (performance.now() < suppressScrollUntil) return
  autoFollow = false
  clearTimeout(scrollTimer)
  scrollTimer = setTimeout(() => { autoFollow = true }, 4000)
}

onMounted(() => {
  document.addEventListener('fullscreenchange', onFullscreenChange)
  // Zero the <main> padding on mobile so the fixed viewer pane fits the screen
  // exactly (see the mobile media query). Scoped to this route via a body class.
  document.body.classList.add('viewer-pane')
  // Also pause karaoke auto-follow on a manual page scroll: if the layout ever
  // falls back to the page scrolling (rather than the transcript), the
  // transcript's own @scroll won't fire. Our scrollToCurrent() is guarded by
  // suppressScrollUntil, which works the same whether window or transcript moved.
  window.addEventListener('scroll', onUserScroll, { passive: true })
  load()
})
// Switching speeches keeps playing (continuous viewing, VIE-9) — the click that
// triggers navigation is a user gesture, so playback is allowed to continue.
// Only when it *was* playing, though: hitting "next speech" on a paused player
// should land on a paused one. Never clears a resume onSpeechEnd already armed
// (the clip is paused by the time `ended` has fired).
watch(() => props.uid, () => { if (playing.value) resumePlaying = true; load() })
onBeforeUnmount(() => {
  document.removeEventListener('fullscreenchange', onFullscreenChange)
  document.body.classList.remove('viewer-pane')
  window.removeEventListener('scroll', onUserScroll)
  if (videoEl.value) videoEl.value.removeEventListener('timeupdate', onTimeUpdate)
  destroyHls()
})
</script>

<template>
  <StateBlock :loading="loading" :error="error" @retry="load">
    <div v-if="data" class="viewer">
      <!-- Header / metadata -->
      <div class="vhead">
        <router-link v-if="session" :to="{ name: 'session', params: { id: session.id } }" class="small">
          ‹ {{ $t('viewer.backToSession') }}
        </router-link>
        <div class="vhead-main">
          <h1 v-if="speech">
            <SpeakerLink :speaker="speech.speaker" size="" />
          </h1>
          <!-- Primary actions: share this speech + export a clip (VIE-10),
               aligned right on the same line as the speaker name. -->
          <div class="vhead-actions" v-if="speech">
            <ShareButton :title="speechShareTitle" :url="speechShareUrl" />
            <button v-if="usingClip" class="btn secondary small" @click="openExport()">
              ⤓ {{ $t('clipExport.button') }}
            </button>
          </div>
        </div>
        <div class="row small" style="gap:.8rem;">
          <FactionBadge :faction="speech.faction" />
          <span class="muted" v-if="session">{{ formatDate(session.date) }} · {{ session.sitting }}. {{ $t('viewer.sittingDay') }}</span>
          <span class="badge" v-if="speech.agenda && speech.agenda.type">{{ speech.agenda.title || agendaLabel(speech.agenda.type) }}</span>
          <span class="badge subtle" v-if="speech.speech_type">{{ $t('viewer.speechType') }}: {{ speech.speech_type }}</span>
          <!-- Readability / lexical diversity (READ-5), off unless the reader has
               asked for it — the same persisted opt-in the sitting-day list is
               behind (`store.showSpeechMetrics`), so the annotation is absent
               everywhere by default and appears on every surface at once when it
               is switched on. Full form here: a single speech has room for the
               whole phrase, where the day list shortens it. -->
          <SpeechMetricsBadge v-if="store.showSpeechMetrics" :metrics="speech.metrics" />
          <!-- CAP policy topic (TOPIC-1..7); always shown when the speech has one. -->
          <TopicBadge :topic="speech.topic" />
          <TimingBadge :timing="speech.timing" />
        </div>
      </div>

      <div class="vgrid">
        <!-- Video column -->
        <div class="vcol-video">
          <!-- Custom controls scoped to the current speech (VIE-9): the native
               control bar would expose the whole multi-hour day stream. -->
          <div class="player" ref="playerEl" :class="{ paused: !playing }">
            <video ref="videoEl" playsinline preload="metadata"
                   :aria-label="speech ? speech.speaker.label : $t('viewer.transcript')"
                   @click="togglePlay"></video>
            <div class="vcontrols" @click.stop>
              <button class="vc-btn" :aria-label="playing ? $t('viewer.pauseBtn') : $t('viewer.playBtn')"
                      :title="playing ? $t('viewer.pauseBtn') : $t('viewer.playBtn')" @click="togglePlay">
                {{ playing ? '⏸' : '▶' }}
              </button>
              <input class="vc-seek" type="range" min="0" :max="duration || 0" step="0.1"
                     :value="currentTime" :disabled="!duration"
                     :aria-label="$t('viewer.seek')" @input="onSeekBar" />
              <span class="vc-time">{{ formatDuration(currentTime) }} / {{ formatDuration(duration) }}</span>
              <button class="vc-btn" :aria-label="muted ? $t('viewer.unmute') : $t('viewer.mute')"
                      :title="muted ? $t('viewer.unmute') : $t('viewer.mute')" @click="toggleMute">
                {{ muted || volume === 0 ? '🔇' : '🔊' }}
              </button>
              <input class="vc-vol" type="range" min="0" max="1" step="0.05"
                     :value="muted ? 0 : volume" :aria-label="$t('viewer.volume')" @input="onVolumeBar" />
              <button class="vc-btn" :aria-label="$t('viewer.fullscreen')" :title="$t('viewer.fullscreen')"
                      @click="toggleFullscreen">{{ isFullscreen ? '🗗' : '⛶' }}</button>
            </div>
          </div>
          <!-- Attribution sits directly under the player it belongs to, on its
               own line, so the credit reads as part of the video rather than as
               one more control in the action row below it. -->
          <p v-if="session && session.video_license" class="vlicense small">
            <a :href="session.video_license" target="_blank" rel="noopener" class="muted">
              {{ $t('viewer.license') }}: {{ session.video_creator || 'Magyar Országgyűlés' }}
            </a>
          </p>
          <div class="vactions row small">
            <a v-if="sourceLink" :href="sourceLink" target="_blank" rel="noopener" class="btn secondary small">
              ↗ {{ $t('viewer.viewOnParlament') }}
            </a>
            <!-- Auto-advance (VIE-9), off by default. Pushed to the right of the
                 same row as the source link; when the two don't fit side by side
                 the switch wraps onto its own line, still right-aligned. -->
            <label class="autoplay" :title="$t('viewer.autoplayNextTip')">
              <input type="checkbox" class="visually-hidden" :checked="store.autoplayNext"
                     @change="setAutoplayNext($event.target.checked)" />
              <span class="autoplay-text">{{ $t('viewer.autoplayNext') }}</span>
              <span class="autoplay-track" aria-hidden="true"><span class="autoplay-knob"></span></span>
            </label>
          </div>
          <p v-if="speech.timing && speech.timing.estimated" class="small muted timing-note">
            ⚠ {{ $t('viewer.estimatedTimingTip') }}
          </p>
          <nav class="row vnav">
            <router-link v-if="data.neighbours.prev" :to="{ name: 'viewer', params: { uid: data.neighbours.prev } }" class="btn secondary small">‹ {{ $t('viewer.prevSpeech') }}</router-link>
            <span v-else></span>
            <router-link v-if="data.neighbours.next" :to="{ name: 'viewer', params: { uid: data.neighbours.next } }" class="btn secondary small">{{ $t('viewer.nextSpeech') }} ›</router-link>
          </nav>
        </div>

        <!-- Transcript column -->
        <div class="vcol-text">
          <h2>{{ $t('viewer.transcript') }}</h2>

          <div v-if="sentences.length === 0" class="card pad muted no-text">
            <p>📼 {{ $t('viewer.noTranscript') }}</p>
            <p class="small">{{ $t('viewer.videoOnly') }}</p>
          </div>

          <div v-else class="transcript" @scroll.passive="onUserScroll">
            <div
              v-for="(s, i) in segmentedSentences" :key="s.ord" :id="'s-' + s.ord"
              :class="['sentence', { active: s.ord === currentOrd }]"
            >
              <div class="scontent">
                <template v-for="(seg, j) in s.segments" :key="j">
                  <!-- Heckle attributed to a representative: face + linked name +
                       remark, set apart as a small aside. -->
                  <p v-if="seg.interjection && seg.speaker && speakers[seg.speaker]" class="aside heckle">
                    <SpeakerLink :speaker="speakers[seg.speaker]" size="xs" class="heckle-who" />
                    <span class="aside-what"><EntityText :text="seg.text" :entities="entities" /></span>
                  </p>
                  <!-- Stage direction / unattributed heckle: italic aside. -->
                  <p v-else-if="seg.interjection" class="aside"><EntityText :text="asideText(seg)" :entities="entities" /></p>
                  <!-- Spoken text: the seekable karaoke unit (VIE-3). A span with
                       role=button (not a <button>) so recognized person names can
                       nest as links inside it; the entity links stop propagation
                       so clicking a name opens its page instead of seeking. -->
                  <span v-else class="sbtn" role="button" tabindex="0"
                    :title="formatDuration(s.time_start)"
                    @click="playSentence(s)"
                    @keydown.enter.prevent="playSentence(s)" @keydown.space.prevent="playSentence(s)">
                    <span class="sicon" aria-hidden="true">▶</span><EntityText :text="seg.text" :entities="entities" />
                  </span>
                </template>
              </div>
              <div class="sentence-actions">
                <button v-if="usingClip" type="button" class="sentence-dl"
                        :title="$t('clipExport.segmentButton')" :aria-label="$t('clipExport.segmentButton')"
                        @click="openExportForSentence(i)">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                       stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M12 3v12" /><path d="m7 10 5 5 5-5" /><path d="M5 21h14" />
                  </svg>
                </button>
                <ShareButton :url="s.shareUrl" :title="s.shareTitle" align="right" compact />
              </div>
            </div>
          </div>
        </div>
      </div>

      <ExportDialog v-if="showExport && speech" :uid="props.uid" :speech="speech"
                    :sentences="sentences" :initial-start-idx="exportStartIdx" :initial-end-idx="exportEndIdx"
                    :date="session && session.date" @close="showExport = false" />
    </div>
  </StateBlock>
</template>

<style scoped>
.vhead { margin-bottom: 1rem; }
/* Name on the left, primary actions (share + download) on the right, same line. */
.vhead-main { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.vhead-main h1 { margin: .4rem 0; font-size: 1.3rem; }
.vhead-actions { display: flex; align-items: center; gap: .6rem; flex-shrink: 0; }
.badge.subtle { background: var(--line); color: var(--muted); font-weight: 400; }
.vgrid { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr); gap: 1.5rem; align-items: start; }
.vcol-video { position: sticky; top: 70px; }
.player { position: relative; background: #000; border-radius: var(--radius); overflow: hidden; aspect-ratio: 16 / 9; }
.player video { width: 100%; height: 100%; display: block; object-fit: contain; cursor: pointer; }
.player:fullscreen { aspect-ratio: auto; width: 100vw; height: 100vh; border-radius: 0; }

/* Custom control bar — scoped to the current speech (VIE-9). */
.vcontrols {
  position: absolute; left: 0; right: 0; bottom: 0;
  display: flex; align-items: center; gap: .5rem;
  padding: .45rem .6rem;
  background: linear-gradient(transparent, rgba(0,0,0,.75));
  opacity: 0; transition: opacity .2s; pointer-events: none;
}
.player:hover .vcontrols, .player:focus-within .vcontrols,
.player.paused .vcontrols { opacity: 1; pointer-events: auto; }
.vc-btn {
  background: none; border: none; color: #fff; cursor: pointer;
  font-size: 1rem; line-height: 1; padding: .25rem .35rem; border-radius: 6px;
}
.vc-btn:hover { background: rgba(255,255,255,.18); }
.vc-time { color: #fff; font-size: .78rem; font-variant-numeric: tabular-nums; white-space: nowrap; }
.vc-seek { flex: 1; min-width: 60px; accent-color: var(--accent); cursor: pointer; }
.vc-vol { width: 64px; accent-color: #fff; cursor: pointer; }
@media (max-width: 520px) { .vc-vol { display: none; } }
/* Video credit: tight under the player, ahead of the action row. */
.vlicense { margin: .4rem 0 0; }
.vactions { margin-top: .5rem; gap: 1rem; flex-wrap: wrap; }
.timing-note { margin-top: .4rem; }
.vnav { gap: .5rem; justify-content: space-between; margin-top: .45rem; }
/* Auto-advance switch: label then track, so it reads left-to-right and the
   control lands on the column's right edge. The checkbox itself is visually
   hidden (but focusable) and the track is drawn from its :checked state.
   `margin-left: auto` keeps it right-aligned whether it shares the action row
   with the source link or wraps below it. */
.autoplay {
  position: relative; display: inline-flex; align-items: center; gap: .5rem;
  margin-left: auto; font-size: .85rem; color: var(--ink-soft);
  cursor: pointer; user-select: none;
}
.autoplay:hover { color: var(--ink); }
.autoplay-track {
  flex: none; position: relative; width: 34px; height: 20px; border-radius: 999px;
  background: var(--line); border: 1px solid #d3d0c8; transition: background .15s, border-color .15s;
}
.autoplay-knob {
  position: absolute; top: 2px; left: 2px; width: 14px; height: 14px; border-radius: 50%;
  background: var(--surface); box-shadow: 0 1px 2px rgba(0, 0, 0, .25); transition: transform .15s;
}
.autoplay input:checked ~ .autoplay-track { background: var(--accent); border-color: var(--accent); }
.autoplay input:checked ~ .autoplay-track .autoplay-knob { transform: translateX(14px); }
/* The hidden input carries focus, so its ring has to be drawn on the track. */
.autoplay input:focus-visible ~ .autoplay-track { outline: 3px solid var(--focus); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) {
  .autoplay-track, .autoplay-knob { transition: none; }
}
.no-text { text-align: center; }
.transcript { max-height: 70vh; overflow-y: auto; padding-right: .4rem; }
.sentence { display: flex; align-items: flex-start; gap: .25rem; margin: 0 0 .15rem; border-radius: 8px; }
.sentence.active { background: var(--accent-soft); }
.scontent { flex: 1; min-width: 0; }
.sbtn {
  display: block; width: 100%; text-align: left; background: none; border: none; cursor: pointer;
  font: inherit; color: var(--ink); padding: .4rem .5rem; border-radius: 8px; line-height: 1.55;
}
.sbtn:hover { background: #f0eee8; }
.sentence.active .sbtn { color: #5a121a; font-weight: 500; }
.sicon { color: var(--accent); font-size: .7rem; margin-right: .35rem; opacity: .55; }
.sbtn:hover .sicon { opacity: 1; }
/* Parenthetical stage directions / heckles lifted out of the speech: italic and
   in a softer tone so they read as asides, not spoken text. */
.aside { margin: 0; padding: .3rem .5rem; font-style: italic; color: var(--ink-soft); line-height: 1.5; font-size: .92em; }
/* A heckle attributed to a known MP: small face + linked name + remark. */
.aside.heckle { display: flex; align-items: center; gap: .45rem; }
.heckle-who { flex: none; flex-wrap: nowrap; font-style: normal; font-weight: 600; gap: .35rem !important; }
.aside-what { font-style: italic; }
/* Per-sentence actions (download this segment + share): kept faint so a long
   transcript stays uncluttered, revealed when the sentence is hovered / is the
   active (playing) one. */
.sentence-actions { flex: none; display: flex; align-items: center; gap: .05rem; opacity: .3; transition: opacity .15s; }
.sentence:hover .sentence-actions, .sentence.active .sentence-actions { opacity: 1; }
/* Download-this-clip trigger — matches the ShareButton's compact icon button. */
.sentence-dl {
  display: inline-flex; align-items: center; justify-content: center; line-height: 0;
  padding: .4rem; border: 0; border-radius: 6px; background: transparent; color: var(--muted); cursor: pointer;
}
.sentence-dl:hover, .sentence-dl:focus-visible { background: var(--line); color: var(--accent); outline: none; }
.sentence-dl svg { width: 16px; height: 16px; display: block; }
@media (max-width: 820px) {
  /* Mobile: turn the viewer into a fixed pane that fills the screen below the
     sticky app header (52px). Everything but the transcript stays put — only
     the transcript scrolls — so the video never drifts off-screen when karaoke
     auto-scroll (VIE-4) advances the text. The <main> padding is zeroed for
     this route (body.viewer-pane, styles.css) so the pane fits exactly with no
     page scroll to pull the video away. The chrome around the video is kept
     deliberately compact so the transcript keeps the majority of the screen. */
  .viewer { display: flex; flex-direction: column; height: calc(100dvh - 52px); }

  /* Compact metadata strip: smaller title, tight rows, and the (often very
     long) agenda title clamped to one line so it can't push the video down. */
  .vhead { flex: none; margin-bottom: .35rem; }
  .vhead h1 { font-size: 1.05rem; margin: .1rem 0; }
  .vhead .small.row { gap: .4rem !important; row-gap: .25rem !important; font-size: .82rem; }
  .vhead .badge { min-width: 0; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .vgrid { display: flex; flex-direction: column; flex: 1; min-height: 0; gap: .4rem; }

  /* Video sits within the page gutter, aligned with the metadata and transcript
     (rounded corners from the base .player rule, no shadow). */
  .vcol-video { flex: none; position: static; display: flex; flex-direction: column; gap: .3rem; }

  /* Slim source/nav strips under the video. */
  .vactions { margin-top: .25rem; gap: .6rem; font-size: .78rem; align-items: center; }
  .vactions .btn { padding: .3rem .6rem; }
  .vlicense { margin: 0; font-size: .78rem; }
  .timing-note { margin: 0; }
  .vcol-video nav { margin-top: 0; }
  .vcol-video nav .btn { padding: .32rem .6rem; font-size: .85rem; }
  .autoplay { font-size: .78rem; gap: .4rem; }
  .autoplay-track { width: 30px; height: 18px; }
  .autoplay-knob { width: 12px; height: 12px; }
  .autoplay input:checked ~ .autoplay-track .autoplay-knob { transform: translateX(12px); }

  /* The transcript is the only scroller; it fills the pane's remaining height.
     Its heading is redundant here, so drop it to reclaim the space. */
  .vcol-text { flex: 1; min-height: 0; display: flex; flex-direction: column; }
  .vcol-text h2 { display: none; }
  .transcript { flex: 1; min-height: 0; max-height: none; }
}
</style>
