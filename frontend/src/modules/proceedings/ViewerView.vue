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
// * VIE-9: when the clip ends, auto-advance to the next speech and keep playing.
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Hls from 'hls.js'
import { api } from '../../api.js'
import { agendaLabel, formatDate, formatDuration, segmentSentences } from '../../format.js'
import EntityText from '../../components/EntityText.vue'
import StateBlock from '../../components/StateBlock.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import SpeakerLink from '../../components/SpeakerLink.vue'
import TimingBadge from '../../components/TimingBadge.vue'

const props = defineProps({ uid: String })
const route = useRoute()
const router = useRouter()
const { t } = useI18n()

// Hoisted out of the transcript v-for: the copy-link label is a constant, but as
// an inline $t() it was re-translated twice PER SENTENCE on every re-render — and
// the component re-renders on every `timeupdate` tick during playback. On a
// long speech that made vue-i18n's translate ~94% of playback CPU (profiled).
// A computed translates once (recomputing only when the locale changes).
const copyLinkLabel = computed(() => t('viewer.copyLink'))

const data = ref(null)
const loading = ref(false)
const error = ref(false)
const videoEl = ref(null)
const playerEl = ref(null)
const currentOrd = ref(-1)
const copied = ref(false)
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
const segmentedSentences = computed(() => segmentSentences(sentences.value))

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

// The clip ended (VIE-9): hop to the next speech and keep playing. On the last
// speech there's no next, so the player simply rests at the end.
function onSpeechEnd() {
  if (advancing) return
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

function copyLink(s) {
  const url = location.origin + router.resolve({
    name: 'viewer', params: { uid: props.uid }, query: { s: s.ord },
  }).href
  navigator.clipboard?.writeText(url).then(() => {
    copied.value = true; setTimeout(() => (copied.value = false), 1800)
  })
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
// Switching speeches should keep playing (continuous viewing, VIE-9). The click
// that triggers navigation is a user gesture, so autoplay is allowed.
watch(() => props.uid, () => { resumePlaying = true; load() })
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
        <h1 v-if="speech">
          <SpeakerLink :speaker="speech.speaker" size="" />
        </h1>
        <div class="row small" style="gap:.8rem;">
          <FactionBadge :faction="speech.faction" />
          <span class="muted" v-if="session">{{ formatDate(session.date) }} · {{ session.sitting }}. {{ $t('viewer.sittingDay') }}</span>
          <span class="badge" v-if="speech.agenda && speech.agenda.type">{{ speech.agenda.title || agendaLabel(speech.agenda.type) }}</span>
          <span class="badge subtle" v-if="speech.speech_type">{{ $t('viewer.speechType') }}: {{ speech.speech_type }}</span>
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
          <div class="vactions row small">
            <a v-if="sourceLink" :href="sourceLink" target="_blank" rel="noopener" class="btn secondary small">
              ↗ {{ $t('viewer.viewOnParlament') }}
            </a>
            <a v-if="session && session.video_license" :href="session.video_license" target="_blank" rel="noopener" class="muted">
              {{ $t('viewer.license') }}: {{ session.video_creator || 'Magyar Országgyűlés' }}
            </a>
          </div>
          <p v-if="speech.timing && speech.timing.estimated" class="small muted timing-note">
            ⚠ {{ $t('viewer.estimatedTimingTip') }}
          </p>
          <nav class="row" style="justify-content:space-between;margin-top:.6rem;">
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
              v-for="s in segmentedSentences" :key="s.ord" :id="'s-' + s.ord"
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
              <button class="copybtn" :aria-label="copyLinkLabel" :title="copyLinkLabel" @click="copyLink(s)">🔗</button>
            </div>
          </div>
        </div>
      </div>

      <div v-if="copied" class="toast" role="status">{{ $t('viewer.linkCopied') }}</div>
    </div>
  </StateBlock>
</template>

<style scoped>
.vhead { margin-bottom: 1rem; }
.vhead h1 { margin: .4rem 0; font-size: 1.3rem; }
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
.vactions { margin-top: .6rem; gap: 1rem; flex-wrap: wrap; }
.timing-note { margin-top: .4rem; }
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
.copybtn { background: none; border: none; cursor: pointer; opacity: .25; padding: .4rem .3rem; font-size: .85rem; }
.copybtn:hover { opacity: 1; }
.toast {
  position: fixed; bottom: 1.5rem; left: 50%; transform: translateX(-50%);
  background: var(--ink); color: #fff; padding: .6rem 1.1rem; border-radius: 999px; box-shadow: var(--shadow);
}
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
  .vactions { margin-top: 0; gap: .6rem; font-size: .78rem; align-items: center; }
  .vactions .btn { padding: .3rem .6rem; }
  .timing-note { margin: 0; }
  .vcol-video nav { margin-top: 0 !important; }
  .vcol-video nav .btn { padding: .32rem .6rem; font-size: .85rem; }

  /* The transcript is the only scroller; it fills the pane's remaining height.
     Its heading is redundant here, so drop it to reclaim the space. */
  .vcol-text { flex: 1; min-height: 0; display: flex; flex-direction: column; }
  .vcol-text h2 { display: none; }
  .transcript { flex: 1; min-height: 0; max-height: none; }
}
</style>
