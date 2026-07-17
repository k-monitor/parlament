<script setup>
// Client-side clip exporter dialog (VIE-10).
//
// Lets the user pick a segment of the speech being watched and download it as a
// self-contained MP4, with one of three subtitle modes (none / soft / burned-in).
// All work happens in the browser (see lib/clipExport.js) — the heavy ffmpeg.wasm
// runtime and this dialog's helpers are dynamically imported only when an export
// actually runs, so normal viewing never pays for them.
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../api.js'
import { formatDuration, formatLongDate } from '../format.js'

const props = defineProps({
  uid: { type: String, required: true },
  speech: { type: Object, required: true },   // needs video_start / video_end
  sentences: { type: Array, default: () => [] },
  // Pre-select a segment when opening (e.g. the download button on a single
  // transcript sentence). Null → default to the whole speech. Indices into
  // `sentences`; out-of-range / null values fall back to the whole-speech range.
  initialStartIdx: { type: Number, default: null },
  initialEndIdx: { type: Number, default: null },
  // Sitting-day ISO date; baked into the watermark (top-left) when it's enabled.
  date: { type: String, default: '' },
})
const emit = defineEmits(['close'])
const { t, locale } = useI18n()

const hasText = computed(() => props.sentences.length > 0)

// --- segment selection -----------------------------------------------------
const lastIdx = Math.max(0, props.sentences.length - 1)
const clampIdx = (v, fallback) =>
  v == null || Number.isNaN(v) ? fallback : Math.min(Math.max(0, Math.floor(v)), lastIdx)
const startIdx = ref(clampIdx(props.initialStartIdx, 0))
const endIdx = ref(clampIdx(props.initialEndIdx, lastIdx))
if (endIdx.value < startIdx.value) endIdx.value = startIdx.value
function onStartChange() { if (endIdx.value < startIdx.value) endIdx.value = startIdx.value }
function onEndChange() { if (startIdx.value > endIdx.value) startIdx.value = endIdx.value }
const isWholeSpeech = computed(() =>
  !hasText.value || (startIdx.value === 0 && endIdx.value === props.sentences.length - 1))
function resetRange() { startIdx.value = 0; endIdx.value = props.sentences.length - 1 }

const clipZero = computed(() => props.speech.video_start ?? 0)
const sentenceOptions = computed(() =>
  props.sentences.map((s, i) => {
    const preview = (s.text || '').replace(/\s+/g, ' ').trim().slice(0, 52)
    const at = s.time_start != null ? formatDuration(s.time_start - clipZero.value) : ''
    return { value: i, label: at ? `${at} · ${preview}` : preview }
  }))

const startSec = computed(() => {
  if (!hasText.value) return props.speech.video_start
  const s = props.sentences[startIdx.value]
  return s && s.time_start != null ? s.time_start : props.speech.video_start
})
const endSec = computed(() => {
  if (!hasText.value) return props.speech.video_end
  const s = props.sentences[endIdx.value]
  return s && s.time_end != null ? s.time_end : props.speech.video_end
})
const estDuration = computed(() => {
  const d = (endSec.value ?? 0) - (startSec.value ?? 0)
  return d > 0 ? d : 0
})

// --- options ---------------------------------------------------------------
// Icons are simple inline SVGs (film / captions outline / captions filled) so the
// three modes read at a glance; static markup, safe to v-html.
const MODES = [
  { key: 'none', title: 'subNone', hint: 'subNoneHint', needsText: false,
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M10 9.2l4.5 2.8L10 14.8z" fill="currentColor" stroke="none"/></svg>' },
  { key: 'soft', title: 'subSoft', hint: 'subSoftHint', needsText: true,
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><rect x="3" y="5" width="18" height="14" rx="2"/><line x1="7" y1="14" x2="11" y2="14"/><line x1="13" y1="14" x2="17" y2="14"/></svg>' },
  { key: 'burn', title: 'subBurn', hint: 'subBurnHint', needsText: true,
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><rect x="3" y="5" width="18" height="14" rx="2" fill="currentColor" stroke="none"/><line x1="7" y1="14" x2="11" y2="14" stroke="var(--surface)"/><line x1="13" y1="14" x2="17" y2="14" stroke="var(--surface)"/></svg>' },
]
const subtitleMode = ref(hasText.value ? 'soft' : 'none')
const QUALITIES = [
  { key: 'low', height: 360 },
  { key: 'medium', height: 504 },
  { key: 'high', height: 720 },
]
const quality = ref('medium')
const qualityHeight = computed(() => (QUALITIES.find((q) => q.key === quality.value) || QUALITIES[1]).height)
// Aspect ratio: landscape keeps the 16:9 source; portrait center-crops to a 9:16
// frame for TikTok/Reels/Shorts. Portrait re-encodes (the crop is a filter).
const ORIENTATIONS = [
  { key: 'landscape', ratio: '16:9',
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="6.5" width="18" height="11" rx="1.5"/></svg>' },
  { key: 'portrait', ratio: '9:16',
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="7.5" y="3" width="9" height="18" rx="1.5"/></svg>' },
]
const orientation = ref('landscape')
const portrait = computed(() => orientation.value === 'portrait')
// Brand watermark (Parlamonitor logo, top-right). On by default; overlaying it
// re-encodes the video, so turning it off keeps the fast stream-copy paths.
// When on, the sitting-day date is baked into the top-left corner.
const watermark = ref(true)
const dateLabel = computed(() => (props.date ? formatLongDate(props.date, locale.value) : ''))

// --- run state -------------------------------------------------------------
// idle | loading (engine) | fetching (segments) | encoding | done | error | cancelled
const phase = ref('idle')
const progress = ref(0)          // 0..1 within the current phase
const errorMsg = ref('')
const resultUrl = ref(null)
const resultName = ref('')
const resultSize = ref(0)
const running = computed(() => ['loading', 'fetching', 'encoding'].includes(phase.value))
const pct = computed(() => Math.round(progress.value * 100))

let abortCtrl = null
let engine = null                // the lazily-imported clipExport module

function fileName() {
  const a = Math.round(startSec.value ?? 0)
  const b = Math.round(endSec.value ?? 0)
  const orient = portrait.value ? '-portrait' : ''
  return `parlamonitor-${props.uid}-${a}-${b}${orient}.mp4`
}
function formatBytes(n) {
  if (!n) return ''
  const mb = n / (1024 * 1024)
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`
}

function revokeResult() {
  if (resultUrl.value) { URL.revokeObjectURL(resultUrl.value); resultUrl.value = null }
}

async function runExport() {
  revokeResult()
  errorMsg.value = ''
  progress.value = 0
  abortCtrl = new AbortController()
  const mode = subtitleMode.value
  try {
    // 1. Load the (heavy) engine on demand.
    phase.value = 'loading'
    engine = await import('../lib/clipExport.js')
    if (!engine.exportSupported()) throw new Error('unsupported')
    await engine.ensureLoaded({ onProgress: (p) => { progress.value = p } })
    if (abortCtrl.signal.aborted) return

    // 2. Ask the backend for the window's cropped clip URL (server-side smil crop).
    const clip = await api.speechClip(props.uid, startSec.value, endSec.value)
    if (abortCtrl.signal.aborted) return

    // 3. Subtitles from the official transcript, rebased to the clip window.
    let srt = ''
    if (mode !== 'none') {
      const { buildSrt } = await import('../lib/subtitles.js')
      srt = buildSrt(props.sentences, clip.start, clip.end)
    }

    // 4. Download the window's raw TS bytes (open CORS — direct fetch).
    phase.value = 'fetching'; progress.value = 0
    const { fetchClipTs } = await import('../lib/hlsFetch.js')
    const { data, height } = await fetchClipTs({
      masterUrl: clip.video_uri,
      playseq: clip.video_playseq,
      targetHeight: qualityHeight.value,
      signal: abortCtrl.signal,
      onProgress: ({ loaded, total }) => { progress.value = total ? loaded / total : 0 },
    })
    if (abortCtrl.signal.aborted) return

    // 5. Mux/encode into MP4.
    phase.value = 'encoding'; progress.value = 0
    const blob = await engine.muxClip({
      tsData: data, srt, mode, watermark: watermark.value, portrait: portrait.value, height,
      date: dateLabel.value,
      onProgress: (p) => { progress.value = p },
    })
    if (abortCtrl.signal.aborted) return

    resultUrl.value = URL.createObjectURL(blob)
    resultName.value = fileName()
    resultSize.value = blob.size
    phase.value = 'done'
  } catch (e) {
    if (abortCtrl && abortCtrl.signal.aborted) { phase.value = 'cancelled'; return }
    errorMsg.value = e && e.message === 'unsupported' ? t('clipExport.unsupported') : t('clipExport.failed')
    phase.value = 'error'
  }
}

function cancel() {
  if (abortCtrl) abortCtrl.abort()
  if (engine && engine.cancel) engine.cancel()   // kill a running encode
  phase.value = 'cancelled'
}

function reset() { revokeResult(); phase.value = 'idle'; progress.value = 0; errorMsg.value = '' }

function close() {
  if (running.value) cancel()
  revokeResult()
  emit('close')
}

const phaseLabel = computed(() => {
  if (phase.value === 'loading') return t('clipExport.phaseLoading')
  if (phase.value === 'fetching') return t('clipExport.phaseFetching')
  if (phase.value === 'encoding') {
    return subtitleMode.value === 'burn' ? t('clipExport.phaseEncodingBurn') : t('clipExport.phaseEncoding')
  }
  return ''
})

function onKeydown(e) { if (e.key === 'Escape') close() }
onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  document.body.style.overflow = 'hidden'   // no background scroll behind the modal
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = ''
  if (running.value) cancel()
  revokeResult()
})
</script>

<template>
  <div class="ex-backdrop" @click.self="close">
    <div class="ex-dialog" role="dialog" aria-modal="true" :aria-label="$t('clipExport.title')">
      <header class="ex-head">
        <h2>⤓ {{ $t('clipExport.title') }}</h2>
        <button class="ex-x" :aria-label="$t('clipExport.close')" @click="close">✕</button>
      </header>

      <!-- RUNNING: a focused progress view (config hidden) -->
      <div v-if="running" class="ex-running">
        <div class="ex-spinner" aria-hidden="true"></div>
        <p class="ex-phase" aria-live="polite">{{ phaseLabel }}</p>
        <div class="ex-bar" role="progressbar" :aria-valuenow="pct" aria-valuemin="0" aria-valuemax="100">
          <div class="ex-bar-fill" :style="{ width: pct + '%' }"></div>
        </div>
        <p class="ex-pct small muted">{{ pct }}%</p>
        <p class="small muted ex-keep">{{ $t('clipExport.keepOpen') }}</p>
        <button class="btn secondary" @click="cancel">{{ $t('clipExport.cancel') }}</button>
      </div>

      <!-- DONE: result -->
      <div v-else-if="phase === 'done'" class="ex-done">
        <div class="ex-ok-badge" aria-hidden="true">✓</div>
        <p class="ex-ok">{{ $t('clipExport.ready') }}</p>
        <p class="ex-filemeta small muted">{{ resultName }}<template v-if="resultSize"> · {{ formatBytes(resultSize) }}</template></p>
        <a class="btn primary ex-dl" :href="resultUrl" :download="resultName">⤓ {{ $t('clipExport.download') }}</a>
        <button class="btn secondary ex-again" @click="reset">{{ $t('clipExport.another') }}</button>
        <p class="ex-note small muted">{{ $t('clipExport.provenance') }}</p>
      </div>

      <!-- CONFIG -->
      <div v-else class="ex-body">
        <p v-if="phase === 'error'" class="ex-alert error small">⚠ {{ errorMsg }}</p>
        <p v-else-if="phase === 'cancelled'" class="ex-alert small">{{ $t('clipExport.cancelled') }}</p>

        <!-- Segment -->
        <section class="ex-field">
          <div class="ex-legend">
            <span>{{ $t('clipExport.segment') }}</span>
            <button v-if="hasText && !isWholeSpeech" class="ex-reset" @click="resetRange">↺ {{ $t('clipExport.wholeSpeech') }}</button>
            <span class="ex-durchip">{{ formatDuration(estDuration) }}</span>
          </div>
          <template v-if="hasText">
            <label class="ex-row">
              <span class="ex-rl">{{ $t('clipExport.from') }}</span>
              <select v-model.number="startIdx" @change="onStartChange">
                <option v-for="o in sentenceOptions" :key="'s'+o.value" :value="o.value">{{ o.label }}</option>
              </select>
            </label>
            <label class="ex-row">
              <span class="ex-rl">{{ $t('clipExport.to') }}</span>
              <select v-model.number="endIdx" @change="onEndChange">
                <option v-for="o in sentenceOptions" :key="'e'+o.value" :value="o.value">{{ o.label }}</option>
              </select>
            </label>
          </template>
        </section>

        <!-- Subtitles: mode tiles -->
        <section class="ex-field">
          <div class="ex-legend"><span>{{ $t('clipExport.subtitles') }}</span></div>
          <div class="ex-modes" role="radiogroup" :aria-label="$t('clipExport.subtitles')">
            <button v-for="m in MODES" :key="m.key" type="button" class="ex-mode"
                    :class="{ active: subtitleMode === m.key, disabled: m.needsText && !hasText }"
                    role="radio" :aria-checked="subtitleMode === m.key"
                    :disabled="m.needsText && !hasText" @click="subtitleMode = m.key">
              <span class="ex-mode-ic" v-html="m.icon"></span>
              <span class="ex-mode-tx">
                <strong>{{ $t('clipExport.' + m.title) }}</strong>
                <em>{{ $t('clipExport.' + m.hint) }}</em>
              </span>
            </button>
          </div>
          <p v-if="!hasText" class="small muted ex-sub-note">{{ $t('clipExport.noTextNote') }}</p>
          <p v-else-if="subtitleMode === 'burn'" class="ex-alert warn small">⚠ {{ $t('clipExport.burnWarn') }}</p>
        </section>

        <!-- Format: aspect ratio tiles -->
        <section class="ex-field">
          <div class="ex-legend"><span>{{ $t('clipExport.format') }}</span></div>
          <div class="ex-orient" role="radiogroup" :aria-label="$t('clipExport.format')">
            <button v-for="o in ORIENTATIONS" :key="o.key" type="button" class="ex-orient-btn"
                    :class="{ active: orientation === o.key }"
                    role="radio" :aria-checked="orientation === o.key" @click="orientation = o.key">
              <span class="ex-orient-ic" v-html="o.icon"></span>
              <span class="ex-orient-tx">
                <strong>{{ $t('clipExport.orient_' + o.key) }}</strong>
                <em>{{ o.ratio }} · {{ $t('clipExport.orientHint_' + o.key) }}</em>
              </span>
            </button>
          </div>
        </section>

        <!-- Quality: segmented -->
        <section class="ex-field">
          <div class="ex-legend"><span>{{ $t('clipExport.quality') }}</span></div>
          <div class="ex-seg" role="group" :aria-label="$t('clipExport.quality')">
            <button v-for="q in QUALITIES" :key="q.key" type="button" class="ex-seg-btn"
                    :class="{ active: quality === q.key }" :aria-pressed="quality === q.key"
                    @click="quality = q.key">
              {{ $t('clipExport.quality_' + q.key) }}<small>{{ q.height }}p</small>
            </button>
          </div>
        </section>

        <!-- Watermark -->
        <label class="ex-toggle">
          <input type="checkbox" v-model="watermark" />
          <span class="ex-toggle-tx">
            <strong>{{ $t('clipExport.watermark') }}</strong>
            <em>{{ $t('clipExport.watermarkHint') }}</em>
          </span>
        </label>

        <button class="btn primary ex-start" @click="runExport">⤓ {{ $t('clipExport.start') }}</button>
        <p class="ex-note small muted">{{ $t('clipExport.provenance') }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ex-backdrop {
  position: fixed; inset: 0; z-index: 100;
  background: rgba(20, 10, 8, .55); backdrop-filter: blur(2px);
  display: flex; align-items: center; justify-content: center; padding: 1rem;
  animation: ex-fade .15s ease;
}
.ex-dialog {
  width: min(30rem, 100%); max-height: 92vh; overflow-y: auto;
  background: var(--surface); border-radius: var(--radius);
  box-shadow: 0 12px 40px rgba(0, 0, 0, .35); padding: 1.1rem 1.2rem 1.2rem;
  animation: ex-pop .16s ease;
}
@keyframes ex-fade { from { opacity: 0 } }
@keyframes ex-pop { from { opacity: 0; transform: translateY(8px) scale(.985) } }

.ex-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: .9rem; }
.ex-head h2 { margin: 0; font-size: 1.15rem; }
.ex-x { background: none; border: none; font-size: 1rem; cursor: pointer; color: var(--muted); padding: .3rem .45rem; border-radius: 8px; line-height: 1; }
.ex-x:hover { background: var(--line); }

.ex-body { display: flex; flex-direction: column; gap: 1rem; }
.ex-field { display: flex; flex-direction: column; gap: .5rem; }
.ex-legend { display: flex; align-items: center; gap: .5rem; font-weight: 600; font-size: .9rem; }
.ex-legend > span:first-child { margin-right: auto; }
.ex-durchip { font-weight: 600; font-size: .8rem; color: var(--accent); background: var(--accent-soft); padding: .12rem .5rem; border-radius: 999px; font-variant-numeric: tabular-nums; }
.ex-reset { background: none; border: none; color: var(--accent); font: inherit; font-size: .8rem; font-weight: 500; cursor: pointer; padding: 0; }
.ex-reset:hover { text-decoration: underline; }

.ex-row { display: flex; align-items: center; gap: .5rem; }
.ex-rl { min-width: 2.6rem; font-size: .85rem; color: var(--muted); }
.ex-row select {
  flex: 1; min-width: 0; padding: .4rem .5rem; border: 1px solid var(--line);
  border-radius: 8px; background: var(--bg); color: var(--ink); font: inherit; font-size: .88rem;
}

/* Subtitle mode tiles */
.ex-modes { display: flex; flex-direction: column; gap: .45rem; }
.ex-mode {
  display: flex; align-items: center; gap: .7rem; text-align: left;
  padding: .55rem .65rem; border: 1.5px solid var(--line); border-radius: 10px;
  background: var(--bg); color: var(--ink); cursor: pointer; font: inherit; transition: border-color .12s, background .12s;
}
.ex-mode:hover:not(.disabled) { border-color: var(--accent); }
.ex-mode.active { border-color: var(--accent); background: var(--accent-soft); }
.ex-mode.disabled { opacity: .45; cursor: not-allowed; }
.ex-mode-ic { flex: none; width: 26px; height: 26px; color: var(--muted); }
.ex-mode.active .ex-mode-ic { color: var(--accent); }
.ex-mode-ic svg { width: 100%; height: 100%; display: block; }
.ex-mode-tx { display: flex; flex-direction: column; line-height: 1.25; min-width: 0; }
.ex-mode-tx strong { font-weight: 600; font-size: .92rem; }
.ex-mode-tx em { font-style: normal; font-size: .78rem; color: var(--muted); }
.ex-sub-note { margin: 0; }

/* Quality segmented control */
.ex-seg { display: flex; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
.ex-seg-btn {
  flex: 1; display: flex; flex-direction: column; align-items: center; gap: .1rem;
  padding: .45rem .3rem; background: var(--bg); color: var(--ink); border: none;
  border-right: 1px solid var(--line); cursor: pointer; font: inherit; font-size: .85rem; font-weight: 500;
}
.ex-seg-btn:last-child { border-right: none; }
.ex-seg-btn small { font-size: .68rem; color: var(--muted); }
.ex-seg-btn.active { background: var(--accent); color: var(--accent-ink); }
.ex-seg-btn.active small { color: var(--accent-ink); opacity: .85; }

/* Orientation tiles */
.ex-orient { display: flex; gap: .45rem; }
.ex-orient-btn {
  flex: 1; display: flex; align-items: center; gap: .55rem; text-align: left; min-width: 0;
  padding: .55rem .6rem; border: 1.5px solid var(--line); border-radius: 10px;
  background: var(--bg); color: var(--ink); cursor: pointer; font: inherit; transition: border-color .12s, background .12s;
}
.ex-orient-btn:hover { border-color: var(--accent); }
.ex-orient-btn.active { border-color: var(--accent); background: var(--accent-soft); }
.ex-orient-ic { flex: none; width: 22px; height: 22px; color: var(--muted); }
.ex-orient-btn.active .ex-orient-ic { color: var(--accent); }
.ex-orient-ic svg { width: 100%; height: 100%; display: block; }
.ex-orient-tx { display: flex; flex-direction: column; line-height: 1.2; min-width: 0; }
.ex-orient-tx strong { font-weight: 600; font-size: .9rem; }
.ex-orient-tx em { font-style: normal; font-size: .74rem; color: var(--muted); }

.ex-alert { margin: 0; padding: .5rem .6rem; border-radius: 8px; font-size: .82rem; }
.ex-alert.warn, .ex-alert.error { background: #fbeceb; color: #a11; }
.ex-alert:not(.warn):not(.error) { background: var(--line); color: var(--ink-soft); }

.ex-toggle { display: flex; align-items: flex-start; gap: .55rem; cursor: pointer; padding: .1rem 0; }
.ex-toggle input { margin-top: .2rem; width: 1rem; height: 1rem; accent-color: var(--accent); cursor: pointer; flex: none; }
.ex-toggle-tx { display: flex; flex-direction: column; line-height: 1.3; }
.ex-toggle-tx strong { font-weight: 600; font-size: .92rem; }
.ex-toggle-tx em { font-style: normal; font-size: .78rem; color: var(--muted); }

.ex-start { justify-content: center; width: 100%; padding: .65rem; font-size: 1rem; }
.ex-note { margin: 0; line-height: 1.4; }

/* Running view */
.ex-running { display: flex; flex-direction: column; align-items: center; gap: .55rem; padding: .8rem 0 .4rem; text-align: center; }
.ex-spinner {
  width: 42px; height: 42px; border-radius: 50%;
  border: 4px solid var(--accent-soft); border-top-color: var(--accent);
  animation: ex-spin .8s linear infinite; margin-bottom: .2rem;
}
@keyframes ex-spin { to { transform: rotate(360deg) } }
.ex-phase { margin: 0; font-weight: 600; }
.ex-bar { width: 100%; height: 9px; background: var(--line); border-radius: 999px; overflow: hidden; }
.ex-bar-fill { height: 100%; background: var(--accent); border-radius: 999px; transition: width .25s ease; }
.ex-pct { margin: 0; font-variant-numeric: tabular-nums; }
.ex-keep { margin: .2rem 0 .4rem; }

/* Done view */
.ex-done { display: flex; flex-direction: column; align-items: center; gap: .5rem; text-align: center; padding: .6rem 0 .2rem; }
.ex-ok-badge {
  width: 46px; height: 46px; border-radius: 50%; background: var(--accent-soft); color: var(--accent);
  display: flex; align-items: center; justify-content: center; font-size: 1.5rem; font-weight: 700;
}
.ex-ok { margin: 0; font-weight: 600; font-size: 1.05rem; }
.ex-filemeta { margin: 0; word-break: break-all; }
.ex-dl { justify-content: center; width: 100%; padding: .65rem; font-size: 1rem; margin-top: .3rem; }
.ex-again { width: 100%; justify-content: center; }
.ex-done .ex-note { margin-top: .3rem; }

@media (max-width: 520px) {
  .ex-dialog { padding: 1rem; }
}
</style>
