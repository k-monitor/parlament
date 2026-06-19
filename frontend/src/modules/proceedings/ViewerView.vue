<script setup>
// Proceedings viewer (§5.2) — the signature feature.
//
// * VIE-2: embeds an HLS player (hls.js where the browser lacks native HLS) on
//   the whole-day stream.
// * VIE-3: click a sentence → seek the player to its day-absolute `time_start`.
// * VIE-4: as the video plays, the currently-spoken sentence highlights and the
//   transcript auto-scrolls (karaoke following on time_start/time_end).
// * VIE-5: deep-linkable — /proceedings/<uid>?s=<ord> or ?t=<seconds> reopens at
//   the exact moment. The current sentence is reflected back into the URL hash.
// * VIE-6/VIE-7/VIE-8: estimated-timing disclosure, source link, and a graceful
//   no-transcript state.
import { ref, shallowRef, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Hls from 'hls.js'
import { api } from '../../api.js'
import { agendaLabel, formatDate, formatDuration } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import SpeakerLink from '../../components/SpeakerLink.vue'
import TimingBadge from '../../components/TimingBadge.vue'

const props = defineProps({ uid: String })
const route = useRoute()
const router = useRouter()

const data = ref(null)
const loading = ref(false)
const error = ref(false)
const videoEl = ref(null)
const playerEl = ref(null)
const currentOrd = ref(-1)
const copied = ref(false)
// Custom-controls state (the native controls expose the whole day stream; we
// drive our own bar scoped to the speech — VIE-9).
const playing = ref(false)
const muted = ref(false)
const volume = ref(1)
const duration = ref(0)        // full day-stream duration
const currentTime = ref(0)     // raw day-absolute playhead
const isFullscreen = ref(false)
let hls = null
let autoFollow = true
// Player state that must survive navigation between speeches of one sitting
// (vue-router reuses this component instance, so plain `let`s persist):
//   resumePlaying — carry the play/pause state across an auto-advance
//   advancing     — guard so onSpeechEnd fires the navigation only once
//   loadedSrc     — the day-stream URL currently attached, to skip reloads
let resumePlaying = false
let advancing = false
let loadedSrc = null

const sentences = computed(() => (data.value && data.value.sentences) || [])
const speech = computed(() => data.value && data.value.speech)
const session = computed(() => data.value && data.value.session)
// The current speech's day-absolute bounds (VIE-9); null when timing is
// unavailable (degraded speech), in which case playback isn't confined.
const speechStart = computed(() => (speech.value && speech.value.time_start != null) ? speech.value.time_start : null)
const speechEnd = computed(() => (speech.value && speech.value.time_end != null) ? speech.value.time_end : null)

// The window the custom controls represent. Falls back to the whole stream when
// the speech has no usable timing (degraded), so controls still work.
const clipStart = computed(() => speechStart.value != null ? speechStart.value : 0)
const clipEnd = computed(() => speechEnd.value != null ? speechEnd.value : duration.value)
const clipDuration = computed(() => Math.max(0, clipEnd.value - clipStart.value))
// Playhead position relative to the speech, clamped to [0, clipDuration].
const relTime = computed(() => Math.min(Math.max(currentTime.value - clipStart.value, 0), clipDuration.value))

async function load() {
  // When navigating between speeches we already have data on screen — keep it
  // (and the live <video>) mounted instead of dropping to the loading state,
  // so the player isn't torn down and recreated. Only the first load (no data
  // yet) shows the spinner, since the <video> doesn't exist until then.
  const navigating = !!data.value
  error.value = false
  if (!navigating) { loading.value = true; data.value = null }
  let next
  try {
    next = await api.speech(props.uid)
  } catch (e) {
    error.value = true; loading.value = false; data.value = null; return
  }
  data.value = next
  loading.value = false
  currentOrd.value = -1
  advancing = false        // ready to detect the next speech boundary
  // Wait a tick so the <video> exists (first load) / data has propagated,
  // then position the player. setupPlayer reuses the live player when the day
  // stream is unchanged, or builds one when it isn't.
  await nextTick()
  setupPlayer()
}

// The whole-day VOD sometimes needs its `playseq` endpoint pinged once to start
// serving segments. It's a cross-origin side-effect endpoint, so we fire it
// no-cors and ignore the (opaque) response. Strictly best-effort and fully
// non-blocking — it never gates player setup or playback.
function activateStream() {
  const playseq = session.value && session.value.video_playseq
  if (!playseq) return
  try { fetch(playseq, { mode: 'no-cors' }).catch(() => {}) } catch { /* ignore */ }
}

function setupPlayer() {
  const video = videoEl.value
  if (!video || !session.value || !session.value.video_uri) return
  const src = session.value.video_uri

  // Position the player at the start of this speech (VIE-9), unless a deep link
  // overrides it. Never set currentTime on an unloaded element — that races the
  // load and aborts it — so this runs once the media is ready.
  const applyInitialSeek = () => {
    const t = route.query.t != null ? Number(route.query.t) : null
    const s = route.query.s != null ? Number(route.query.s) : null
    if (t != null && !Number.isNaN(t)) seekTo(t, resumePlaying)
    else if (s != null && sentences.value[s]) playSentence(sentences.value[s], resumePlaying)
    else if (speechStart.value != null) seekTo(speechStart.value, resumePlaying)
    resumePlaying = false
  }

  // Consecutive speeches share one day stream: when only the speech changed,
  // keep the attached player and just reposition — no reload, no rebuffer.
  if (loadedSrc === src && (hls || video.src)) {
    applyInitialSeek()
    return
  }

  destroyHls()
  loadedSrc = src
  // Ping the activation endpoint in the background; attach the player right away.
  activateStream()

  if (Hls.isSupported()) {
    // hls.js (Chrome/Firefox/Edge): more reliable than trusting canPlayType.
    hls = new Hls({ maxBufferLength: 30 })
    hls.on(Hls.Events.MANIFEST_PARSED, applyInitialSeek)
    hls.on(Hls.Events.ERROR, (_e, d) => {
      if (!d || !d.fatal) return
      if (d.type === Hls.ErrorTypes.NETWORK_ERROR) {
        activateStream(); hls && hls.startLoad()      // re-activate + retry
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
  // Bound once on the build path; the element persists across same-stream
  // navigation, so these survive without re-binding.
  video.addEventListener('timeupdate', onTimeUpdate)
  video.addEventListener('play', onPlayState)
  video.addEventListener('pause', onPlayState)
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

// Reaching the end of the current speech (VIE-9): hop to the next speech and
// keep playing, or stop at the end on the last speech of the sitting.
function onSpeechEnd() {
  if (advancing) return
  advancing = true
  const v = videoEl.value
  const next = data.value && data.value.neighbours && data.value.neighbours.next
  if (next) {
    resumePlaying = !!(v && !v.paused)
    router.push({ name: 'viewer', params: { uid: next } })
  } else if (v) {
    try { v.pause(); if (speechEnd.value != null) v.currentTime = speechEnd.value } catch { /* ignore */ }
  }
}

function onTimeUpdate() {
  const v = videoEl.value
  if (!v) return
  const now = v.currentTime
  currentTime.value = now      // drive the custom control bar
  const start = speechStart.value, end = speechEnd.value
  // Confine playback to this speech (VIE-9): snap back if we drift before its
  // start, advance once we pass its end. (No bounds ⇒ degraded speech, play on.)
  if (start != null && now < start - 1) { seekTo(start, !v.paused); return }
  if (end != null && now >= end) { onSpeechEnd(); return }
  // Find the sentence whose [time_start, time_end) contains `now`.
  const list = sentences.value
  let idx = -1
  for (let i = 0; i < list.length; i++) {
    const a = list[i].time_start, b = list[i].time_end
    if (a != null && now >= a && (b == null || now < b)) { idx = i; break }
  }
  if (idx !== currentOrd.value) {
    currentOrd.value = idx
    if (idx >= 0 && autoFollow) scrollToCurrent()
  }
}

function scrollToCurrent() {
  const el = document.getElementById('s-' + currentOrd.value)
  if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' })
}

function seekTo(seconds, play = true) {
  const v = videoEl.value
  if (!v) return
  const go = () => {
    try { v.currentTime = seconds } catch { /* not seekable yet */ }
    // play() can reject (autoplay policy, or the seek interrupting a pending
    // load). Always swallow it — it's not an error the user needs to see.
    if (play && v.play) { const p = v.play(); if (p && p.catch) p.catch(() => {}) }
  }
  if (v.readyState >= 1) go()
  else v.addEventListener('loadedmetadata', go, { once: true })
}

function playSentence(s, play = true) {
  if (s.time_start == null) return
  autoFollow = true
  seekTo(s.time_start, play)
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
  seekTo(clipStart.value + Number(e.target.value), !v.paused)
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
let scrollTimer = null
function onUserScroll() {
  autoFollow = false
  clearTimeout(scrollTimer)
  scrollTimer = setTimeout(() => { autoFollow = true }, 4000)
}

onMounted(() => { document.addEventListener('fullscreenchange', onFullscreenChange); load() })
// Switching speeches should keep playing (continuous viewing, VIE-9). The click
// that triggers navigation is a user gesture, so autoplay is allowed.
watch(() => props.uid, () => { resumePlaying = true; load() })
onBeforeUnmount(() => {
  document.removeEventListener('fullscreenchange', onFullscreenChange)
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
              <input class="vc-seek" type="range" min="0" :max="clipDuration || 0" step="0.1"
                     :value="relTime" :disabled="!clipDuration"
                     :aria-label="$t('viewer.seek')" @input="onSeekBar" />
              <span class="vc-time">{{ formatDuration(relTime) }} / {{ formatDuration(clipDuration) }}</span>
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
            <a v-if="speech.source_page" :href="speech.source_page" target="_blank" rel="noopener" class="btn secondary small">
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
            <p
              v-for="s in sentences" :key="s.ord" :id="'s-' + s.ord"
              :class="['sentence', { active: s.ord === currentOrd }]"
            >
              <button class="sbtn" :title="formatDuration(s.time_start)" @click="playSentence(s)">
                <span class="sicon" aria-hidden="true">▶</span>
                {{ s.text }}
              </button>
              <button class="copybtn" :aria-label="$t('viewer.copyLink')" :title="$t('viewer.copyLink')" @click="copyLink(s)">🔗</button>
            </p>
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
.sbtn {
  flex: 1; text-align: left; background: none; border: none; cursor: pointer;
  font: inherit; color: var(--ink); padding: .4rem .5rem; border-radius: 8px; line-height: 1.55;
}
.sbtn:hover { background: #f0eee8; }
.sentence.active .sbtn { color: #5a121a; font-weight: 500; }
.sicon { color: var(--accent); font-size: .7rem; margin-right: .35rem; opacity: .55; }
.sbtn:hover .sicon { opacity: 1; }
.copybtn { background: none; border: none; cursor: pointer; opacity: .25; padding: .4rem .3rem; font-size: .85rem; }
.copybtn:hover { opacity: 1; }
.toast {
  position: fixed; bottom: 1.5rem; left: 50%; transform: translateX(-50%);
  background: var(--ink); color: #fff; padding: .6rem 1.1rem; border-radius: 999px; box-shadow: var(--shadow);
}
@media (max-width: 820px) {
  .vgrid { grid-template-columns: 1fr; }
  .vcol-video { position: static; }
  .transcript { max-height: none; }
}
</style>
