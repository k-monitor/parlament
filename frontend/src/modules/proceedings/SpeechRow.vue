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
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api.js'
import { store } from '../../store.js'
import { formatDuration, transcriptParagraphs } from '../../format.js'
import EntityText from '../../components/EntityText.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import SpeakerLink from '../../components/SpeakerLink.vue'
import TimingBadge from '../../components/TimingBadge.vue'
import SpeechMetricsBadge from '../../components/SpeechMetricsBadge.vue'
import SpeechTopicBadge from '../../components/SpeechTopicBadge.vue'
import ShareButton from '../../components/ShareButton.vue'

// `playable` is false for a not-yet-processed sitting day: there is no per-speech
// video window, so the "video only" note is hidden (every speech of such a day is
// text-less — the day-level notice says that once, instead of on every row) and the
// ▶ opens the whole-day recording rather than this speech's clip.
//
// `viewable` gates the ▶ itself, and is false only when the day has no recording at
// all to open. It used to follow `playable`, which left the day page as the one
// place the video could NOT be reached on a not-yet-segmented day — the same speech
// played fine from a speaker's profile or a shared link, both of which go through
// the viewer's whole-day fallback (ViewerView `videoSrc`).
const props = defineProps({
  speech: { type: Object, required: true },
  playable: { type: Boolean, default: true },
  viewable: { type: Boolean, default: true },
})

const router = useRouter()
// Shareable deep link to this speech's viewer page + the speaker as post text.
const shareUrl = computed(() => location.origin + router.resolve({ name: 'viewer', params: { uid: props.speech.uid } }).href)
const shareTitle = computed(() => props.speech.speaker?.label || '')

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
// Person + institution names recognized in the transcript, resolved to their
// destinations (NEL, §10): [{ surface, kind, ambiguous, links: [{ type, url|
// person_id, label }] }]. Matched inline in the body text and wrapped as the name
// plus a cluster of destination badges (see linkifyEntities / EntityText).
const entities = ref([])

async function toggle() {
  if (!props.speech.has_text) return
  open.value = !open.value
  if (!open.value || loaded.value || loading.value) return
  loading.value = true; error.value = false
  try {
    const res = await api.speechText(props.speech.uid)
    paragraphs.value = transcriptParagraphs(res.sentences || [])
    entities.value = res.entities || []
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
        <!-- CAP policy topic (TOPIC-1..7). Unlike the readability chips this is not
             behind the reader's opt-in: it answers "what is this speech about",
             which is the question a sitting-day list is scanned with, so it earns
             its place on every row by default. It rides with the speaker's badges
             rather than in the right-hand meta group because that group is
             right-aligned against the duration: there the chips lined up on their
             right edges and their left edges scattered over ~130px (the labels run
             80-229px wide, and the timing badge appears on only some rows), which
             on outlined pills down a 300-row page read as breakage. Here it simply
             flows after the speech type, left-aligned like every other badge.
             Absent for procedural speeches and for any speech the classifier was
             not confident enough about. -->
        <SpeechTopicBadge :topic="speech.topic" compact />
      </div>
      <!-- Meta: the video-only note, timing and duration. On desktop it sits
           inline before the actions; on mobile it drops to its own line under the
           speaker so the long "video only" note never squeezes the name. -->
      <div class="speech-meta">
        <span class="muted small" v-if="playable && !speech.has_text">📼 {{ $t('viewer.videoOnly') }}</span>
        <!-- Readability / lexical diversity (READ-5), only when the reader has
             asked for it on this day (the toggle in SessionView). Compact even
             then: the bare number on the row, the full wording in the tooltip, so
             a 400-speech day stays scannable. Absent for a speech that is not
             measurable, which is most procedural rows. -->
        <SpeechMetricsBadge v-if="store.showSpeechMetrics" :metrics="speech.metrics" compact />
        <TimingBadge :timing="speech.timing" />
        <span class="muted small nowrap" v-if="speech.duration">⏱ {{ formatDuration(speech.duration) }}</span>
      </div>
      <!-- Actions: kept together as one non-shrinking group so the play button is
           never pushed off the card's edge (it used to be clipped on mobile). -->
      <div class="speech-actions">
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
        <!-- Opens the full viewer (video + karaoke transcript). On a day that has
             a recording but no per-speech windows yet, it opens the whole-day
             recording — announced as such, since it does not start at this
             speech — so the video is reachable here too. Hidden only when there is
             no recording at all to open. -->
        <router-link
          v-if="viewable"
          :to="{ name: 'viewer', params: { uid: speech.uid } }" class="play-affordance"
          :title="playable ? $t('sessions.openViewer') : $t('sessions.openDayVideo')"
          :aria-label="playable ? $t('sessions.openViewer') : $t('sessions.openDayVideo')"
        >▶</router-link>
        <!-- Share this speech (always available, even before video is ready). -->
        <ShareButton :title="shareTitle" :url="shareUrl" align="right" compact />
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
            <span class="heckle-what"><EntityText :text="para.text" :entities="entities" /></span>
          </p>
          <p v-else class="transcript-text" :class="{ interjection: para.interjection }"><EntityText :text="paraText(para)" :entities="entities" /></p>
        </template>
      </template>
    </div>
  </li>
</template>

<style scoped>
.speech-item { border-radius: 8px; }
.speech-item.procedural { opacity: .72; }
.speech-row { display: flex; align-items: center; gap: .8rem; padding: .5rem .7rem; border-radius: 8px; color: var(--ink); }
/* Leading info grows to push the meta + actions right (replaces the old .grow
   spacer) and can shrink below its content so its badges wrap on narrow screens. */
.speech-main { flex: 1 1 auto; min-width: 0; display: flex; align-items: center; flex-wrap: wrap; gap: .5rem .8rem; }
.speech-meta { flex: 0 0 auto; display: flex; align-items: center; gap: .6rem; }
/* Actions stay together as one block that never shrinks, so the play button is
   always fully visible instead of being clipped past the card edge. */
.speech-actions { flex: 0 0 auto; display: flex; align-items: center; gap: .4rem; }
.nowrap { white-space: nowrap; }

/* Narrow: stack into an identity block (top-left) with the play controls pinned
   top-right, and the meta line (video-only note · topic · timing · duration) on
   its own row underneath — so a wrapped two-line name and the long "video only"
   note no longer collide or float, vertically centred, in the middle of the row.

   The threshold is 660px rather than the 560px this started at because the topic
   chip (TOPIC-1) joined the meta line. Measured on a real sitting day, between
   about 590 and 650px the chip was the one thing that pushed a row to wrap, so
   rows *with* a topic stood at 94px while rows without stayed at 64px — a
   300-speech day scanned as a ragged mix of two heights. Stacking a little
   earlier costs those widths one predictable line each and gives the rhythm
   back. (Above 660 the chip is affordable: it drops to its glyph at 840, which
   is what keeps 660–840 flat.) */
@media (max-width: 660px) {
  .speech-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    grid-template-areas: "main actions" "meta actions";
    align-items: start;
    column-gap: .5rem; row-gap: .3rem;
  }
  .speech-main { grid-area: main; align-self: center; }
  .speech-meta { grid-area: meta; flex-wrap: wrap; }
  .speech-actions { grid-area: actions; align-self: start; }
}
/* On phones the long speech-type badge ("napirend előttihez hozzászólás", 183px
   in a 198px column) fills a line on its own, so a topic glyph landing after it
   takes another. Ordering the badge last lets the glyph share the line with the
   faction chip, which has room to spare.

   Measured per row, the same rows with and without the chip: at 360px this cuts
   the rows that gain a line from 93 of 108 to 39, at 375px from 66 to 41 and at
   390px from 46 to 35. Above 480px the glyph already fits beside the badge and the
   rule is unnecessary. */
@media (max-width: 480px) {
  .speech-main .badge.subtle { order: 1; }
}

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
