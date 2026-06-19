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
const currentOrd = ref(-1)
const copied = ref(false)
let hls = null
let autoFollow = true

const sentences = computed(() => (data.value && data.value.sentences) || [])
const speech = computed(() => data.value && data.value.speech)
const session = computed(() => data.value && data.value.session)

async function load() {
  loading.value = true; error.value = false; data.value = null
  try {
    data.value = await api.speech(props.uid)
  } catch (e) {
    error.value = true; loading.value = false; return
  }
  // The <video> is rendered inside StateBlock's slot, which only mounts once
  // loading is false. Flip loading first, then wait a tick so the element
  // exists before we attach the player — otherwise setupPlayer finds no
  // <video> and silently does nothing (no source ⇒ play() aborts the load).
  loading.value = false
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
  destroyHls()

  // Ping the activation endpoint in the background; attach the player right away.
  activateStream()

  // Apply the deep-link start point once the media is ready (never set
  // currentTime on an unloaded element — that races the load and aborts it).
  const applyInitialSeek = () => {
    const t = route.query.t != null ? Number(route.query.t) : null
    const s = route.query.s != null ? Number(route.query.s) : null
    if (t != null && !Number.isNaN(t)) seekTo(t, false)
    else if (s != null && sentences.value[s]) playSentence(sentences.value[s], false)
  }

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
  video.addEventListener('timeupdate', onTimeUpdate)
}

function destroyHls() {
  if (hls) { try { hls.destroy() } catch { /* ignore */ } hls = null }
}

function onTimeUpdate() {
  const now = videoEl.value.currentTime
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

// Pause auto-follow while the user manually scrolls; resume when they stop.
let scrollTimer = null
function onUserScroll() {
  autoFollow = false
  clearTimeout(scrollTimer)
  scrollTimer = setTimeout(() => { autoFollow = true }, 4000)
}

onMounted(load)
watch(() => props.uid, load)
onBeforeUnmount(() => {
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
          <div class="player">
            <video ref="videoEl" controls playsinline preload="metadata"
                   :aria-label="$t('viewer.transcript')"></video>
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
.player { background: #000; border-radius: var(--radius); overflow: hidden; aspect-ratio: 16 / 9; }
.player video { width: 100%; height: 100%; display: block; }
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
