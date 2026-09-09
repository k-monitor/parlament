<script setup>
// The CAP policy topic of a speech (TOPIC-1..7) or of an iromány (TOPIC-8): one
// bubble naming what the text was about, which opens on click into how that was
// decided.
//
// One component for both because the two are the same claim, produced by the same
// model at the same threshold and aggregated by the same code — a reader who has
// learnt what this chip means on a sitting day must not have to relearn it on a
// bill page. Only the wording differs (a speech has paragraphs and greetings; a
// document has blocks and a cover sheet), and that is a `kind` away.
//
// The label is not a measurement of the speech, it is a *model's reading* of it, so
// the two are presented differently. The chip states one thing — the topic — because
// that is the only part a reader can act on while scanning a sitting day. Everything
// that qualifies it lives one click away: how much of the speech the topic actually
// speaks for, what it won against, how much of the speech was too uncertain to call,
// and which model and confidence threshold produced the answer. A chip that tried to
// show the qualifications inline would be unreadable at 400 rows, and one that never
// showed them would be asserting more than the classifier knows (TRUST-1).
//
// **Why click and not hover.** The readability chips next to this one use a hover
// tooltip, because they have a sentence to add. This panel has a breakdown, a
// coverage figure and a methodology footer; it is a small document, and a document
// that appears and vanishes with the pointer cannot be read. Click also gives touch
// users the same affordance, which hover does not.
//
// **No colour coding.** 22 topics is far past the number of categorical hues that
// stay distinguishable, and a sitting-day list would become a stripe of noise. The
// glyph distinguishes topics at a glance and the word carries the meaning — the same
// choice, for the same reason, as SpeechMetricsBadge.
//
// Nothing with no `topic` renders anything. That is the intended answer for roughly
// a fifth of speeches — procedural ones are never classified at all, and a speech
// whose every block fell below the confidence threshold has no topic the site is
// willing to assert — and for about a tenth of irományok, whose document is a scan,
// a bare personnel motion, or too short to place. Silence is the honest output
// there, not a "misc" bucket.
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { store } from '../store.js'
// Glyphs and names are shared with the iromány lists' topic filter, so the chip
// and the control that selects it can never drift apart (see lib/topics.js).
import { TOPIC_GLYPHS, topicGlyph, topicName as capName } from '../lib/topics.js'


// `compact` marks the dense sitting-day list, where the chip competes for a row
// with a speaker, a faction, a speech-type badge, a duration and four action
// buttons. Measured on a real sitting day, the full chip (~230px for "Kormányzat
// és közigazgatás") is free down to ~840px and then forces the row to wrap: rows
// with a topic grow from 64px to 94px while rows without stay at 64px, so a
// 300-speech day loses its scanning rhythm to a ragged mix of one- and two-line
// rows. Below that width the compact chip therefore drops to its glyph — the row
// keeps its height, and the topic is one tap away in the panel, which is where
// its name, its share and its runners-up live anyway.
//
// The viewer passes no `compact`: one chip, a whole header to itself, and no
// rhythm to protect, so the name stays at every width. In the list it is dropped
// below 841px, where it would cost the row a line — the measurement is in the
// stylesheet below.
//
// Note this is a *different* choice from the readability chip's `compact`, which
// shortens a phrase ("harder to read" -> "harder"). A topic name cannot be
// shortened without saying something else, so it is shown in full or not at all.
const props = defineProps({
  topic: { type: Object, default: null },
  compact: { type: Boolean, default: false },
  // Which text this label was read off. Selects the wording (and the backend
  // feature flag), never the behaviour: a topic means the same thing either way.
  kind: { type: String, default: 'speech' },   // 'speech' | 'bill'
})

const { t, te } = useI18n()

// Two off switches, as everywhere else: the backend's own flag (false when the pass
// is off or the DB carries no predictions) and "this item has no topic". The two
// passes are flagged separately because they can genuinely differ — a server can
// carry speech topics and no iromány ones, which is exactly what happens before
// the document cache is first shipped across.
const feature = computed(() =>
  (props.kind === 'bill' ? 'bill_topics' : 'speech_topics'))
const on = computed(() => store.featureEnabled(feature.value) && !!props.topic)

// Wording that differs between the two: a speech has paragraphs, greetings and a
// speaker; an iromány has blocks, a cover sheet and no voice. `topics.bill.*`
// overrides the shared `topics.*` string where one exists, so a key that reads the
// same for both is written once.
function tk(key, params) {
  const scoped = `topics.${props.kind}.${key}`
  return te(scoped) ? t(scoped, params) : t(`topics.${key}`, params)
}

const topicName = (label) => capName(label, t, te)

const glyph = computed(() => topicGlyph(props.topic?.label))
const name = computed(() => (props.topic ? topicName(props.topic.label) : ''))
const pct = (v) => `${Math.round((v || 0) * 100)}%`

// The methodology the panel footer cites. `/meta` carries the threshold actually in
// force, which is an operator setting (it can be retuned without reprocessing), so
// it must be read from the server rather than hardcoded here. The topic object also
// carries the threshold its own aggregation used; prefer that, since it is the one
// that produced *this* answer.
const meta = computed(() =>
  (props.kind === 'bill' ? store.meta?.bill_topics : store.meta?.speech_topics) || {})
const threshold = computed(() => props.topic?.threshold ?? meta.value.threshold ?? 0.9)

// Competing topics, minus the winner — what the chip's one word won against.
const runners = computed(() => (props.topic?.breakdown || []).slice(1))

// ---------------------------------------------------------------------------
// popover placement — teleported and viewport-fixed, exactly as HelpTip does and
// for the same reason: these chips sit inside cards and horizontal scrollers that
// would otherwise clip the panel or paint over it.
// ---------------------------------------------------------------------------
const open = ref(false)
const btn = ref(null)
const panel = ref(null)
const pos = ref({ left: 0, top: 0, placement: 'below' })

const MARGIN = 8
const GAP = 6
const ROOM_BELOW = 280          // this panel is taller than a tooltip

async function place() {
  const el = btn.value
  if (!el) return
  const r = el.getBoundingClientRect()
  const below = window.innerHeight - r.bottom > ROOM_BELOW
  pos.value = {
    left: r.left,
    top: below ? r.bottom + GAP : r.top - GAP,
    placement: below ? 'below' : 'above',
  }
  await nextTick()
  const p = panel.value
  if (!p) return
  const w = p.getBoundingClientRect().width
  const left = Math.max(MARGIN, Math.min(r.left, window.innerWidth - w - MARGIN))
  if (left !== pos.value.left) pos.value = { ...pos.value, left }
}

// A fixed panel does not track its chip, so scrolling and resizing have to be
// handled — but *not* by dismissing, which is where this parts company with
// HelpTip. That tooltip is transient and hover-driven, so a scroll ending it costs
// nothing. This panel is opened deliberately and has several lines to read, and it
// sits in a long scrolling list: closing on any scroll would snatch it away from a
// reader who nudged the page, and the browser's own scroll-the-focused-button-into-
// view can fire one before it is even read. So a scroll **re-anchors** the panel to
// its chip, and only a chip that has actually left the viewport closes it — at
// which point there is nothing for the panel to point at anyway.
//
// Clicking anywhere outside closes it, in the capture phase so a click that a row
// handler would otherwise swallow still dismisses. All of it is bound only while
// open: a sitting day carries hundreds of these and none needs to hear about
// scrolling while shut.
let watching = false

function onDocClick(e) {
  if (btn.value?.contains(e.target) || panel.value?.contains(e.target)) return
  hide()
}

function reanchor() {
  const r = btn.value?.getBoundingClientRect()
  if (!r || r.bottom < 0 || r.top > window.innerHeight) hide()
  else place()
}

function watchViewport(state) {
  if (state === watching) return
  watching = state
  const bind = state ? window.addEventListener : window.removeEventListener
  bind.call(window, 'scroll', reanchor, { passive: true, capture: true })
  bind.call(window, 'resize', reanchor, { passive: true })
  const dbind = state ? document.addEventListener : document.removeEventListener
  dbind.call(document, 'click', onDocClick, true)
}

function show() {
  open.value = true
  watchViewport(true)
  place()
}

function hide() {
  open.value = false
  watchViewport(false)
}

function toggle() {
  if (open.value) hide()
  else show()
}

onBeforeUnmount(() => watchViewport(false))
</script>

<template>
  <span v-if="on" class="topic" :class="{ compact }">
    <button
      ref="btn" type="button" class="badge topic-chip" :aria-expanded="open"
      :title="$t('topics.chipTitle', { topic: name })"
      @click.stop="toggle" @keydown.esc="hide"
    >
      <span aria-hidden="true">{{ glyph }}</span>
      <span class="tname">{{ name }}</span>
      <span class="visually-hidden">{{ $t('topics.chipTitle', { topic: name }) }}</span>
    </button>

    <Teleport to="body">
      <div
        v-if="open" ref="panel" class="topic-panel" :class="pos.placement" role="dialog"
        :aria-label="tk('panelLabel')"
        :style="{ left: pos.left + 'px', top: pos.top + 'px' }"
        @keydown.esc="hide"
      >
        <div class="tp-head">
          <span class="tp-glyph" aria-hidden="true">{{ glyph }}</span>
          <span class="tp-name">{{ name }}</span>
          <span v-if="topic.code" class="tp-code">CAP {{ topic.code }}</span>
        </div>

        <!-- What the label rests on. `share` is the topic's share of the speech's
             confidently-classified policy text — not of the whole speech, which is
             what `coverage` and `otherShare` below qualify. -->
        <p class="tp-lead">
          {{ $t('topics.share', { pct: pct(topic.share), n: topic.paragraphs }) }}
        </p>

        <div v-if="runners.length" class="tp-bars">
          <div class="tp-sub">{{ $t('topics.alsoAbout') }}</div>
          <div v-for="b in runners" :key="b.label" class="tp-bar">
            <span class="tp-bar-label">
              <span aria-hidden="true">{{ TOPIC_GLYPHS[b.label] || '🏷️' }}</span>
              {{ topicName(b.label) }}
            </span>
            <span class="tp-bar-track" aria-hidden="true">
              <span class="tp-bar-fill" :style="{ width: pct(b.share) }" />
            </span>
            <span class="tp-bar-pct">{{ pct(b.share) }}</span>
          </div>
        </div>

        <!-- The two honesty lines: how much of the speech was confident enough to
             count at all, and how much of it was procedural rather than policy. -->
        <ul class="tp-facts">
          <li>{{ tk('coverage', { pct: pct(topic.coverage) }) }}</li>
          <li v-if="topic.other_share > 0.05">
            {{ tk('otherShare', { pct: pct(topic.other_share) }) }}
          </li>
        </ul>

        <p class="tp-method">
          {{ tk('method', { threshold: Math.round(threshold * 100) }) }}
        </p>
      </div>
    </Teleport>
  </span>
</template>

<style scoped>
.topic { display: inline-flex; align-items: center; }
/* The site's ordinary neutral chip, made pressable: the glyph tells topics apart,
   the word carries the meaning, and nothing is colour-coded (see the header). */
.topic-chip {
  display: inline-flex; align-items: center; gap: .3rem;
  cursor: pointer; white-space: nowrap;
  border: 1px solid var(--line); background: var(--surface);
  color: var(--ink-soft); font-weight: 400;
}
.topic-chip:hover, .topic-chip[aria-expanded="true"] {
  color: var(--accent); border-color: var(--accent);
}
.tname { font-weight: 600; }
/* In the dense list the word is dropped once it costs the row a line. Measured on
   a real 300-speech sitting day, comparing rows that carry a topic against rows
   that do not: at 841px and above the ~230px chip rides in the badge group for
   free and every row stays 64px; below that it pushes the group to another line,
   so topic-bearing rows stand taller than their neighbours and the list scans as a
   ragged mix of heights — the one thing this chip must not introduce.

   The chip stays a button with its tooltip and accessible name throughout; only
   the visible word goes, and the panel names the topic on tap. Surfaces that pass
   no `compact` (the viewer) are never narrowed at all. */
@media (max-width: 840px) {
  .topic.compact .tname { display: none; }
}

.topic-panel {
  position: fixed; z-index: 90;
  width: max-content; min-width: 15rem;
  max-width: min(390px, calc(100vw - 16px));
  background: var(--surface); color: var(--ink-soft);
  border: 1px solid var(--line); border-radius: 8px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, .14);
  padding: .6rem .75rem; text-align: left; line-height: 1.45;
}
.topic-panel.above { transform: translateY(-100%); }

.tp-head { display: flex; align-items: baseline; gap: .35rem; margin-bottom: .3rem; }
.tp-glyph { font-size: 1.05em; }
.tp-name { font-weight: 700; color: var(--ink); }
.tp-code { margin-left: auto; font-size: .78em; color: var(--ink-faint); }
.tp-lead { margin: 0 0 .5rem; }

.tp-sub { font-size: .82em; color: var(--ink-faint); margin-bottom: .25rem; }
.tp-bars { margin-bottom: .5rem; }
.tp-bar {
  display: grid; grid-template-columns: 1fr 4.5rem 2.2rem;
  align-items: center; gap: .4rem; font-size: .88em; margin-top: .18rem;
}
/* Wraps rather than ellipsizes: several CAP topics have long Hungarian names
   ("Külügy és nemzetközi kapcsolatok"), and a truncated topic is exactly the
   word the reader opened the panel to see. */
.tp-bar-label { line-height: 1.25; }
.tp-bar-track {
  height: .42rem; border-radius: 3px; background: var(--line); overflow: hidden;
}
.tp-bar-fill { display: block; height: 100%; background: var(--accent); opacity: .55; }
.tp-bar-pct { text-align: right; color: var(--ink-faint); font-variant-numeric: tabular-nums; }

/* Plain lines, not bullets: usually there is only one of them (the second
   appears only when a speech is meaningfully procedural), and a list with one
   item reads as a formatting accident. */
.tp-facts { margin: 0 0 .45rem; padding: 0; list-style: none; font-size: .88em; }
.tp-facts li { margin: .12rem 0; }
.tp-method {
  margin: 0; padding-top: .4rem; border-top: 1px solid var(--line);
  font-size: .8em; color: var(--ink-faint);
}
</style>
