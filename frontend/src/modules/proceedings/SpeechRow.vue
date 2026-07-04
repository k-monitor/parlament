<script setup>
// One speech in the sitting-day list, with an inline transcript spoiler.
//
// Its open/loading/text state lives HERE, locally, on purpose: a sitting day can
// have many hundreds of speeches, and if this state lived on the parent then
// toggling one spoiler would re-run the whole page's render and re-patch every
// row (measured at 300–750ms per click on a 461-speech day). As its own
// component, toggling a row re-renders only that row, so it's instant. The text
// is fetched lazily on first expand and cached (kept once loaded). The ▶
// affordance still opens the full viewer (video + karaoke transcript).
import { ref } from 'vue'
import { api } from '../../api.js'
import { formatDuration, transcriptParagraphs } from '../../format.js'
import FactionBadge from '../../components/FactionBadge.vue'
import SpeakerLink from '../../components/SpeakerLink.vue'
import TimingBadge from '../../components/TimingBadge.vue'

const props = defineProps({ speech: { type: Object, required: true } })

const open = ref(false)
const loading = ref(false)
const error = ref(false)
const loaded = ref(false)
// Rendered paragraphs: [{ text, interjection, speaker }]. The flat sentence list
// is re-grouped into the source <p> paragraphs, the redundant leading speaker
// label is stripped, and parenthetical stage directions / heckles are lifted out
// into their own italic paragraphs — see transcriptParagraphs() in format.js.
const paragraphs = ref([])
// name -> { person_id, label, photo_uri } for heckles attributed to a resolvable
// MP. Only these render as a compact avatar + linked name; the rest stay text.
const speakers = ref({})

async function toggle() {
  if (!props.speech.has_text) return
  open.value = !open.value
  if (!open.value || loaded.value || loading.value) return
  loading.value = true; error.value = false
  try {
    const res = await api.speechText(props.speech.uid)
    paragraphs.value = transcriptParagraphs(res.sentences || [])
    loaded.value = true
    // Attribute named heckles to representatives (best-effort, non-blocking:
    // failure just leaves them as plain "Name: remark" text).
    const names = paragraphs.value.filter((p) => p.speaker).map((p) => p.speaker)
    if (names.length) {
      try { speakers.value = await api.resolveSpeakers(names) } catch { /* keep plain */ }
    }
  } catch {
    error.value = true
  } finally {
    loading.value = false
  }
}

// An interjection whose "Name:" prefix didn't resolve is shown verbatim.
function paraText(p) {
  return p.speaker ? `${p.speaker}: ${p.text}` : p.text
}
</script>

<template>
  <li class="speech-item" :class="{ procedural: speech.procedural, open }">
    <div class="speech-row">
      <!-- Leading info: speaker + badges. Grows to fill and, on narrow screens,
           wraps its badges under the speaker's name (min-width:0 lets it shrink). -->
      <div class="speech-main">
        <SpeakerLink :speaker="speech.speaker" />
        <FactionBadge :faction="speech.faction" />
        <span class="badge subtle" v-if="speech.speech_type">{{ speech.speech_type }}</span>
      </div>
      <!-- Trailing controls: kept together as one non-shrinking group so the play
           button is never pushed off the card's edge (it used to be clipped on
           mobile). -->
      <div class="speech-controls">
        <span class="muted small" v-if="!speech.has_text">📼 {{ $t('viewer.videoOnly') }}</span>
        <TimingBadge :timing="speech.timing" />
        <span class="muted small nowrap" v-if="speech.duration">⏱ {{ formatDuration(speech.duration) }}</span>
        <!-- Spoiler toggle: reveals the transcript inline. Sits just left of the
             play button; hidden for video-only speeches. -->
        <button
          v-if="speech.has_text"
          type="button" class="disclosure" :class="{ active: open }"
          :aria-expanded="open"
          :title="open ? $t('sessions.hideTranscript') : $t('sessions.showTranscript')"
          :aria-label="open ? $t('sessions.hideTranscript') : $t('sessions.showTranscript')"
          @click="toggle"
        >
          <svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true"
               fill="none" stroke="currentColor" stroke-width="2"
               stroke-linecap="round" stroke-linejoin="round">
            <rect x="4" y="3" width="16" height="18" rx="2" />
            <line x1="8" y1="8" x2="16" y2="8" />
            <line x1="8" y1="12" x2="16" y2="12" />
            <line x1="8" y1="16" x2="13" y2="16" />
          </svg>
        </button>
        <!-- Still opens the full viewer (video + karaoke transcript). -->
        <router-link
          :to="{ name: 'viewer', params: { uid: speech.uid } }" class="play-affordance"
          :title="$t('sessions.openViewer')" :aria-label="$t('sessions.openViewer')"
        >▶</router-link>
      </div>
    </div>
    <div v-if="open" class="speech-body">
      <p v-if="loading" class="loadrow muted small"><span class="spinner" aria-hidden="true"></span>{{ $t('sessions.transcriptLoading') }}</p>
      <p v-else-if="error" class="muted small">{{ $t('sessions.transcriptLoadError') }}</p>
      <template v-else>
        <template v-for="(para, i) in paragraphs" :key="i">
          <!-- Heckle attributed to a representative: a compact face + linked
               name + the remark, deliberately smaller so it reads as an aside,
               not a separate speech. -->
          <p v-if="para.interjection && para.speaker && speakers[para.speaker]"
             class="transcript-text interjection heckle">
            <SpeakerLink :speaker="speakers[para.speaker]" size="xs" class="heckle-who" />
            <span class="heckle-what">{{ para.text }}</span>
          </p>
          <p v-else class="transcript-text"
             :class="{ interjection: para.interjection }">{{ paraText(para) }}</p>
        </template>
      </template>
    </div>
  </li>
</template>

<style scoped>
.speech-item { border-radius: 8px; }
.speech-item.procedural { opacity: .72; }
.speech-row { display: flex; align-items: center; gap: .8rem; padding: .5rem .7rem; border-radius: 8px; color: var(--ink); }
/* Leading info grows to push the controls right (replaces the old .grow spacer)
   and can shrink below its content so its badges wrap on narrow screens. */
.speech-main { flex: 1 1 auto; min-width: 0; display: flex; align-items: center; flex-wrap: wrap; gap: .5rem .8rem; }
/* Controls stay together as one block that never shrinks, so the play button is
   always fully visible instead of being clipped past the card edge. */
.speech-controls { flex: 0 0 auto; display: flex; align-items: center; gap: .6rem; }
.nowrap { white-space: nowrap; }
/* Highlight only the header row when open (or hovered) — the transcript itself
   stays on the plain surface so a long block reads at full contrast (~9.5:1
   with --ink) instead of washed out on the pink tint. */
.speech-item.open .speech-row,
.speech-item:not(.open) .speech-row:hover { background: var(--accent-soft); }
.disclosure {
  flex: 0 0 auto; display: inline-flex; align-items: center; justify-content: center;
  width: 1.9rem; height: 1.9rem; padding: 0; border: 0; border-radius: 6px;
  background: transparent; color: var(--muted); cursor: pointer;
}
.disclosure:hover { background: var(--line); color: var(--accent); }
.disclosure.active { color: var(--accent); }
.badge.subtle { background: var(--line); color: var(--muted); font-weight: 400; }
.play-affordance {
  flex: 0 0 auto; display: inline-flex; align-items: center; justify-content: center;
  width: 1.9rem; height: 1.9rem; border-radius: 6px; color: var(--accent);
}
.play-affordance:hover { background: var(--line); text-decoration: none; }
/* the revealed transcript aligns under the speaker's name (past the avatar) */
.speech-body { padding: .35rem .9rem .8rem calc(.7rem + 48px + .6rem); animation: reveal .13s ease-out; }
.transcript-text { margin: 0; color: var(--ink); line-height: 1.65; white-space: pre-wrap; }
.transcript-text + .transcript-text { margin-top: .7em; }
/* Stage directions / heckles lifted out of the parentheses: italic and set apart
   in a softer (but still AA, ~7:1) tone so they read as asides, not speech. */
.transcript-text.interjection { font-style: italic; color: var(--ink-soft); }
/* A heckle attributed to a known MP: face + linked name inline with the remark,
   at a smaller size so it never reads as a full speech — just a quick aside. */
.transcript-text.heckle {
  display: flex; align-items: center; gap: .45rem;
  font-size: .9em; margin-top: .45em;
}
.heckle-who { flex: none; flex-wrap: nowrap; font-style: normal; font-weight: 600; gap: .35rem !important; }
.heckle-what { font-style: italic; color: var(--ink-soft); }
.loadrow { display: flex; align-items: center; gap: .5rem; margin: 0; }
.spinner {
  width: .8rem; height: .8rem; border-radius: 50%; flex: none;
  border: 2px solid var(--line); border-top-color: var(--accent);
  animation: spin .7s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes reveal { from { opacity: 0; transform: translateY(-2px); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) {
  .speech-body { animation: none; }
  .spinner { animation-duration: 1.4s; }
}
</style>
