<script setup>
// Readability + lexical-diversity annotation on a speech (READ-5).
//
// Two chips, both precomputed by the backend (app/readability.py, saphes):
//   • LIX   — how hard the speech is to read (long words + sentence length)
//   • MATTR — how varied its vocabulary is (distinct lemmas in a sliding window)
//
// The chip shows **where the speech sits against the House's median, and nothing
// else**. A LIX of 54 and a MATTR of 0.71 are not facts a reader can use: the LIX
// threshold here is recalibrated for Hungarian (READ-6), so the published Swedish
// difficulty labels don't apply and no absolute scale replaces them, while MATTR is
// a bare ratio whose interesting range is a few hundredths wide. Both numbers only
// ever meant something *by comparison* — so the comparison is what the chip says,
// in words: "harder to read", "more varied vocabulary". The score itself, its
// corpus quintile and the counts behind it stay in the tooltip, where there is room
// to say what they are (TRUST-1 / REP-5).
//
// The direction is a collapse of the backend's quintile band, not a fresh
// comparison in the client: the middle fifth (p40–p60) straddles the median and
// counts as typical, the two fifths below it are "easier"/"less varied", the two
// above "harder"/"more varied". Reusing the band avoids a knife-edge split at the
// median, where a speech a tenth of a point above it would be labelled harder to
// read than one a tenth below — a distinction the measurement cannot support.
//
// The value is carried by the **word — not by colour**. A five-step fill was tried
// and dropped: as an ordinal ramp it fails on its own terms (the steps a dense list
// can tolerate sit too close in lightness, and its light end is invisible against
// the page surface), and a fill loud enough to pass would read as a verdict on a
// 400-row page — which is exactly what these two measurements are not. So one
// neutral chip, like every other badge on the site.
//
// A speech with no `metrics` renders nothing at all: procedural speeches, speeches
// without a transcript and speeches under the minimum length are not measurable,
// and a score there would be noise dressed as a number. `mattr` alone can be
// missing — a speech shorter than the sliding window has no length-comparable
// diversity value, and the whole diversity half is absent when the build had no
// lemmatizer (it is never faked from surface forms). A metric whose **band** is
// missing renders nothing either: that happens only on a DB built before the
// distribution table existed, and with no corpus to compare against there is
// nothing left to say — the bare number was the thing this chip stopped showing.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { store } from '../store.js'
import { SPEECH_METRICS_ENABLED } from '../features.js'

// Quintile band → direction against the median. Two maps rather than one, because
// the metrics share the middle label ("average") and nothing else, and because
// "easier to read" and "less varied" are not the same sentence.
const LIX_DIRECTION = {
  'very-easy': 'easier', easy: 'easier', average: 'typical',
  hard: 'harder', 'very-hard': 'harder',
}
const MATTR_DIRECTION = {
  'very-low': 'less', low: 'less', average: 'typical',
  high: 'more', 'very-high': 'more',
}

const props = defineProps({
  metrics: { type: Object, default: null },
  // `compact` shortens the wording on the chip face ("harder" for "harder to
  // read") — the full phrase stays in the tooltip and for screen readers. It is
  // the form the dense sitting-day list uses when the reader switches the chips on.
  compact: { type: Boolean, default: false },
})

const { t } = useI18n()

// Two independent off switches, plus "is there anything to show":
//   • SPEECH_METRICS_ENABLED — the editorial curtain (features.js), currently on;
//   • store.featureEnabled    — the backend's own flag, false when the pass is
//     disabled or the DB carries no measurements;
//   • props.metrics           — this particular speech isn't measurable.
// Whether a *surface* wants the chips at all is the call site's, not this
// component's — and both of them ask the same question: viewer and sitting-day
// list alike render this only behind `store.showSpeechMetrics`, the reader's
// persisted opt-in, which is off by default (ViewerView/SpeechRow, toggled from
// SessionView).
const on = computed(() => SPEECH_METRICS_ENABLED
  && store.featureEnabled('speech_metrics')
  && !!props.metrics)

const lixBand = computed(() => (on.value ? props.metrics.lix_band : null))
const mattrBand = computed(() => (on.value ? props.metrics.mattr_band : null))
// The chip's whole content: no band, no chip (see the header comment).
const lixDir = computed(() => LIX_DIRECTION[lixBand.value] || null)
const mattrDir = computed(() => MATTR_DIRECTION[mattrBand.value] || null)

// The MATTR window is a measurement parameter, not a constant of the UI: it is
// configurable and the tooltip has to state the one actually used.
const window_ = computed(() => store.meta?.speech_metrics?.mattr_window || 100)

const num = (v, digits = 0) => (v == null ? '' : Number(v).toFixed(digits))

// What the chip face says, long or short. The long phrase is also the tooltip's
// and the screen reader's, so the compact list never hides meaning behind a word
// like "harder".
const lixWord = computed(() => (lixDir.value
  ? t(`metrics.lixVs${props.compact ? 'Short' : ''}.${lixDir.value}`) : ''))
const mattrWord = computed(() => (mattrDir.value
  ? t(`metrics.mattrVs${props.compact ? 'Short' : ''}.${mattrDir.value}`) : ''))

// The tooltips spell out what the comparison is against, how it was measured, and
// only then the score itself with the counts behind it — the methodology every
// derived figure on the site owes the reader (TRUST-1 / REP-5).
const lixTitle = computed(() => {
  const m = props.metrics
  return `${t('metrics.lixName')}: ${t(`metrics.lixVs.${lixDir.value}`)}\n`
    + `${t('metrics.vsTip')}\n`
    + `${t('metrics.lixTip')}\n`
    + t('metrics.lixScore', {
      value: num(m.lix, 1), band: t(`metrics.lixBand.${lixBand.value}`),
    }) + '\n'
    + t('metrics.lixCounts', {
      words: m.words, sentences: m.sentences,
      perSentence: num(m.avg_sentence, 1),
      longShare: num((m.long_share || 0) * 100, 0),
    })
})

const mattrTitle = computed(() => {
  const m = props.metrics
  return `${t('metrics.mattrName')}: ${t(`metrics.mattrVs.${mattrDir.value}`)}\n`
    + `${t('metrics.vsTip')}\n`
    + `${t('metrics.mattrTip', { window: window_.value })}\n`
    + t('metrics.mattrScore', {
      value: num(m.mattr, 2), band: t(`metrics.mattrBand.${mattrBand.value}`),
    }) + '\n'
    + t('metrics.mattrCounts', { types: m.types, tokens: m.tokens })
})
</script>

<template>
  <span v-if="on && (lixDir || mattrDir)" class="metrics">
    <span v-if="lixDir" class="badge metric" :title="lixTitle">
      <span aria-hidden="true">📖</span>
      <span class="mdir" aria-hidden="true">{{ lixWord }}</span>
      <span class="visually-hidden">{{ lixTitle }}</span>
    </span>
    <span v-if="mattrDir" class="badge metric" :title="mattrTitle">
      <span aria-hidden="true">🔤</span>
      <span class="mdir" aria-hidden="true">{{ mattrWord }}</span>
      <span class="visually-hidden">{{ mattrTitle }}</span>
    </span>
  </span>
</template>

<style scoped>
.metrics { display: inline-flex; align-items: center; gap: .35rem; }
/* The site's ordinary neutral chip — the glyph tells the two metrics apart, the
   word carries the value. */
.badge.metric {
  cursor: help; white-space: nowrap;
  font-weight: 400; color: var(--ink-soft);
}
.mdir { font-weight: 600; }
</style>
