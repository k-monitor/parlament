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
import { formatDuration } from '../../format.js'
import FactionBadge from '../../components/FactionBadge.vue'
import SpeakerLink from '../../components/SpeakerLink.vue'
import TimingBadge from '../../components/TimingBadge.vue'

const props = defineProps({ speech: { type: Object, required: true } })

const open = ref(false)
const loading = ref(false)
const error = ref(false)
const loaded = ref(false)
const paragraphs = ref([])

// Re-group the flat sentence list back into the transcript's original
// paragraphs: sentences sharing a `paragraph` index belong together. When the
// index is null (a pre-migration speech) every sentence shares `null`, so the
// whole speech collapses to a single block — the previous behaviour.
function groupParagraphs(sentences) {
  const out = []
  let key
  for (const s of sentences) {
    if (out.length === 0 || s.paragraph !== key) {
      out.push(s.text)
      key = s.paragraph
    } else {
      out[out.length - 1] += ' ' + s.text
    }
  }
  return out
}

async function toggle() {
  if (!props.speech.has_text) return
  open.value = !open.value
  if (!open.value || loaded.value || loading.value) return
  loading.value = true; error.value = false
  try {
    const res = await api.speechText(props.speech.uid)
    paragraphs.value = groupParagraphs(res.sentences || [])
    loaded.value = true
  } catch {
    error.value = true
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <li class="speech-item" :class="{ procedural: speech.procedural, open }">
    <div class="speech-row">
      <SpeakerLink :speaker="speech.speaker" />
      <FactionBadge :faction="speech.faction" />
      <span class="badge subtle" v-if="speech.speech_type">{{ speech.speech_type }}</span>
      <span class="grow"></span>
      <span class="muted small" v-if="!speech.has_text">📼 {{ $t('viewer.videoOnly') }}</span>
      <TimingBadge :timing="speech.timing" />
      <span class="muted small" v-if="speech.duration">⏱ {{ formatDuration(speech.duration) }}</span>
      <!-- Spoiler toggle: reveals the transcript inline. Sits at the end of the
           row, just left of the play button; hidden for video-only speeches. -->
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
    <div v-if="open" class="speech-body">
      <p v-if="loading" class="loadrow muted small"><span class="spinner" aria-hidden="true"></span>{{ $t('sessions.transcriptLoading') }}</p>
      <p v-else-if="error" class="muted small">{{ $t('sessions.transcriptLoadError') }}</p>
      <template v-else>
        <p v-for="(para, i) in paragraphs" :key="i" class="transcript-text">{{ para }}</p>
      </template>
    </div>
  </li>
</template>

<style scoped>
.speech-item { border-radius: 8px; }
.speech-item.procedural { opacity: .72; }
.speech-row { display: flex; align-items: center; gap: .8rem; padding: .5rem .7rem; border-radius: 8px; color: var(--ink); }
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
.grow { flex: 1; }
.play-affordance {
  flex: 0 0 auto; display: inline-flex; align-items: center; justify-content: center;
  width: 1.9rem; height: 1.9rem; border-radius: 6px; color: var(--accent);
}
.play-affordance:hover { background: var(--line); text-decoration: none; }
/* the revealed transcript aligns under the speaker's name (past the avatar) */
.speech-body { padding: .35rem .9rem .8rem calc(.7rem + 48px + .6rem); animation: reveal .13s ease-out; }
.transcript-text { margin: 0; color: var(--ink); line-height: 1.65; white-space: pre-wrap; }
.transcript-text + .transcript-text { margin-top: .7em; }
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
