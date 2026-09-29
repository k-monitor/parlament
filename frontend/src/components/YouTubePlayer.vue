<script setup>
// An embedded YouTube player for a committee sitting's recording (BIZ-30),
// driven the way the proceedings viewer drives its HLS player: the page seeks it
// to a sentence and is told, several times a second, where the playhead is.
//
// * Click-to-load (PRIV-1). Nothing is requested from YouTube until the reader
//   presses play: before that the component is the video's thumbnail with a play
//   button, which is what the page showed before it had a player at all. Only
//   then is the IFrame API loaded and the player built, in YouTube's
//   privacy-enhanced mode (youtube-nocookie.com).
// * A sitting that overran into a second stream has two videos. They are one
//   recording to the reader: a part switch above the player, and the next part
//   follows on when one ends. A seek names the video it is for, so a sentence in
//   part 2 opens part 2.
// * The IFrame API has no timeupdate event, so the playhead is polled while the
//   video plays and reported as `time` ({ videoId, t }).
import { ref, computed, watch, nextTick, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatSpeakingTime } from '../format.js'

const props = defineProps({
  // [{ videoId, url, thumbnail, durationS, continued }] in playing order.
  videos: { type: Array, required: true },
  label: { type: String, default: '' },
  // { videoId, t } for the play button to start at instead of the top — the
  // page passes the speech its address names.
  startAt: { type: Object, default: null },
})
const emit = defineEmits(['time', 'engaged', 'playing'])
const { t, locale } = useI18n()

const hostEl = ref(null)
const engaged = ref(false)      // the reader started it: the iframe exists
const failed = ref(false)       // the API or the video would not load here
const currentId = ref(props.videos[0]?.videoId || null)
const current = computed(() =>
  props.videos.find((v) => v.videoId === currentId.value) || props.videos[0] || null)
const poster = computed(() => {
  const v = current.value
  if (!v) return null
  return v.thumbnail || `https://i.ytimg.com/vi/${v.videoId}/hqdefault.jpg`
})

let player = null
let ready = false
let pending = null            // a seek asked for before the player was ready
let pollTimer = null

// One script tag for the whole app, however many players mount: the API calls a
// single global when it is ready, so the promise is shared.
let apiPromise = null
function loadApi() {
  if (window.YT && window.YT.Player) return Promise.resolve(window.YT)
  if (!apiPromise) {
    apiPromise = new Promise((resolve, reject) => {
      const prev = window.onYouTubeIframeAPIReady
      window.onYouTubeIframeAPIReady = () => {
        if (typeof prev === 'function') prev()
        resolve(window.YT)
      }
      const s = document.createElement('script')
      s.src = 'https://www.youtube.com/iframe_api'
      s.async = true
      s.onerror = () => { apiPromise = null; reject(new Error('YouTube API did not load')) }
      document.head.appendChild(s)
    })
  }
  return apiPromise
}

function report() {
  if (!player || !ready || typeof player.getCurrentTime !== 'function') return
  emit('time', { videoId: currentId.value, t: player.getCurrentTime() || 0 })
}
function startPolling() {
  stopPolling()
  pollTimer = setInterval(report, 250)
}
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

function onStateChange(e) {
  const S = window.YT.PlayerState
  if (e.data === S.PLAYING) { startPolling(); emit('playing', true) }
  else {
    stopPolling()
    report()
    emit('playing', false)
  }
  // A part ended: the sitting goes on in the next one.
  if (e.data === S.ENDED) {
    const i = props.videos.findIndex((v) => v.videoId === currentId.value)
    const next = props.videos[i + 1]
    if (next) seek(next.videoId, 0, true)
  }
}

async function build(videoId, start, play) {
  engaged.value = true
  emit('engaged', true)
  let YT
  try { YT = await loadApi() } catch { failed.value = true; return }
  await nextTick()
  if (!hostEl.value || player) return
  currentId.value = videoId
  // The API replaces the element it is given with its iframe, so it gets a
  // child of ours rather than a node Vue is tracking.
  const target = document.createElement('div')
  hostEl.value.appendChild(target)
  player = new YT.Player(target, {
    host: 'https://www.youtube-nocookie.com',
    videoId,
    playerVars: {
      autoplay: play ? 1 : 0, start: Math.max(0, Math.floor(start || 0)),
      rel: 0, playsinline: 1, hl: locale.value,
    },
    events: {
      onReady: () => {
        ready = true
        // The player starts on a whole second; land on the sentence itself.
        if (start) player.seekTo(start, true)
        if (pending) { const p = pending; pending = null; seek(p.videoId, p.t, p.play) }
        report()
      },
      onStateChange,
      // 101/150: the owner does not allow embedding; 100: gone. Either way the
      // link below the player is the way to it.
      onError: (e) => { if ([100, 101, 150].includes(e.data)) failed.value = true },
    },
  })
}

// Seek to `t` seconds into `videoId` (one of `videos`), and play unless told
// not to. Before the reader has started the player this is what starts it —
// a click on a sentence is as much a request to watch as the play button.
function seek(videoId, t, play = true) {
  const id = videoId || currentId.value
  if (!id) return
  if (!engaged.value) { build(id, t, play); return }
  if (!player || !ready) { pending = { videoId: id, t, play }; return }
  if (id !== currentId.value) {
    currentId.value = id
    if (play) player.loadVideoById({ videoId: id, startSeconds: t })
    else player.cueVideoById({ videoId: id, startSeconds: t })
  } else {
    player.seekTo(Math.max(0, t), true)
    if (play) player.playVideo()
  }
  emit('time', { videoId: id, t })
}

function start() {
  const at = props.startAt
  if (at && props.videos.some((v) => v.videoId === at.videoId)) {
    seek(at.videoId, at.t, true)
    return
  }
  const v = current.value
  if (v) seek(v.videoId, 0, true)
}
function switchPart(v) {
  if (v.videoId === currentId.value) return
  if (engaged.value) seek(v.videoId, 0, true)
  else currentId.value = v.videoId
}

function destroy() {
  stopPolling()
  if (player) { try { player.destroy() } catch { /* ignore */ } }
  player = null; ready = false; pending = null
}

// A new sitting (the router reuses the page): back to the thumbnail.
watch(() => props.videos.map((v) => v.videoId).join(','), () => {
  destroy()
  engaged.value = false
  failed.value = false
  currentId.value = props.videos[0]?.videoId || null
  emit('engaged', false)
})
onBeforeUnmount(destroy)

defineExpose({ seek, start })
</script>

<template>
  <div class="ytp">
    <div v-if="videos.length > 1" class="ytp-parts" role="tablist"
         :aria-label="label || t('committees.videos')">
      <button v-for="(v, i) in videos" :key="v.videoId" type="button" role="tab"
              class="ytp-part" :class="{ active: v.videoId === currentId }"
              :aria-selected="v.videoId === currentId" @click="switchPart(v)">
        {{ t('committees.part', { n: i + 1 }) }}
        <span v-if="v.durationS" class="muted">· {{ formatSpeakingTime(v.durationS) }}</span>
      </button>
    </div>
    <div class="ytp-frame">
      <!-- Holds the iframe the API builds. -->
      <div v-if="engaged" ref="hostEl" class="ytp-host"></div>
      <button v-else type="button" class="ytp-facade" @click="start"
              :aria-label="t('committees.playRecording')">
        <img v-if="poster" :src="poster" alt="" loading="lazy" />
        <span class="ytp-play" aria-hidden="true">▶</span>
      </button>
    </div>
    <p v-if="failed" class="small notice">{{ t('committees.playerError') }}</p>
    <p class="small muted ytp-foot">
      <a v-if="current" :href="current.url" target="_blank" rel="noopener">
        ↗ {{ t('committees.watchOnYoutube') }}
      </a>
      <span v-if="!engaged" class="ytp-consent">{{ t('committees.playerConsent') }}</span>
    </p>
  </div>
</template>

<style scoped>
.ytp-frame {
  position: relative; background: #000; border-radius: var(--radius);
  overflow: hidden; aspect-ratio: 16 / 9;
}
/* The API swaps the host div for its iframe; both fill the frame. */
.ytp-frame :deep(iframe), .ytp-host { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; }
.ytp-facade {
  position: absolute; inset: 0; width: 100%; height: 100%; padding: 0; border: 0;
  background: #000; cursor: pointer; display: block;
}
.ytp-facade img { width: 100%; height: 100%; object-fit: cover; display: block; opacity: .85; }
.ytp-facade:hover img, .ytp-facade:focus-visible img { opacity: 1; }
.ytp-play {
  position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
  width: 64px; height: 44px; border-radius: 12px; display: flex;
  align-items: center; justify-content: center; font-size: 1.3rem;
  background: rgba(0, 0, 0, .72); color: #fff; transition: background .15s;
}
.ytp-facade:hover .ytp-play, .ytp-facade:focus-visible .ytp-play { background: var(--accent); }
.ytp-facade:focus-visible { outline: 3px solid var(--focus); outline-offset: -3px; }
.ytp-parts { display: flex; flex-wrap: wrap; gap: .35rem; margin-bottom: .4rem; }
.ytp-part {
  font: inherit; font-size: .82rem; padding: .2rem .6rem; cursor: pointer;
  border: 1px solid var(--line); border-radius: 999px; background: var(--surface);
  color: var(--ink-soft);
}
.ytp-part.active { border-color: var(--accent); color: var(--accent); font-weight: 600; }
.ytp-foot { display: flex; flex-wrap: wrap; gap: .3rem .8rem; margin: .4rem 0 0; }
.ytp-consent { flex: 1 1 12rem; }
.notice { border-left: 3px solid var(--accent); padding: .3rem .6rem; margin: .4rem 0 0; }
</style>
