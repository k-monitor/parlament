<script setup>
// A minimal embeddable HLS player for a single per-speech clip (VIE-9). Unlike
// the proceedings viewer it uses native controls and no karaoke: the clip is
// already cropped server-side to exactly one speech, so the native bar exposes
// just that speech (not the whole multi-hour day). Used by the bill page to
// embed a question's oral video answer.
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import Hls from 'hls.js'

const props = defineProps({
  src: { type: String, required: true },   // the clip's HLS playlist URL
  playseq: { type: String, default: null }, // activation URL (hit once first)
  label: { type: String, default: '' },
})

const videoEl = ref(null)
let hls = null
let loadedSrc = null

// The clip's smil VOD is generated on demand: its playlist 404s until the
// `playseq.php` activation endpoint has been hit once. It's a cross-origin
// side-effect endpoint (opaque response), so await it (capped, so a hang never
// stalls the player) before requesting the playlist.
function activate() {
  if (!props.playseq) return Promise.resolve()
  let f
  try { f = fetch(props.playseq, { mode: 'no-cors' }).catch(() => {}) }
  catch { return Promise.resolve() }
  return Promise.race([f, new Promise((r) => setTimeout(r, 4000))])
}

async function setup() {
  const video = videoEl.value
  if (!video || !props.src) return
  const src = props.src
  if (loadedSrc === src && (hls || video.src)) return
  destroy()
  await activate()
  if (videoEl.value !== video || props.src !== src) return  // changed meanwhile
  loadedSrc = src
  if (Hls.isSupported()) {
    hls = new Hls({ maxBufferLength: 30 })
    hls.on(Hls.Events.ERROR, (_e, d) => {
      if (!d || !d.fatal) return
      if (d.type === Hls.ErrorTypes.NETWORK_ERROR) activate().then(() => hls && hls.startLoad())
      else if (d.type === Hls.ErrorTypes.MEDIA_ERROR) hls && hls.recoverMediaError()
    })
    hls.loadSource(src)
    hls.attachMedia(video)
  } else {
    video.src = src   // native HLS (Safari/iOS)
  }
}

function destroy() {
  if (hls) { try { hls.destroy() } catch { /* ignore */ } hls = null }
  loadedSrc = null
}

onMounted(setup)
watch(() => props.src, setup)
onBeforeUnmount(destroy)
</script>

<template>
  <div class="hplayer">
    <video ref="videoEl" playsinline preload="metadata" controls
           :aria-label="label || undefined"></video>
  </div>
</template>

<style scoped>
.hplayer { background: #000; border-radius: var(--radius); overflow: hidden; aspect-ratio: 16 / 9; }
.hplayer video { width: 100%; height: 100%; display: block; object-fit: contain; }
</style>
