<script setup>
// Readability + lexical-diversity annotation on a speech (READ-5).
//
// Two chips, both precomputed by the backend (app/readability.py, saphes):
//   • LIX   — how hard the speech is to read (long words + sentence length)
//   • MATTR — how varied its vocabulary is (distinct lemmas in a sliding window)
//
// The value is carried by the **number and the worded band — not by colour**. A
// five-step fill was tried and dropped: as an ordinal ramp it fails on its own
// terms (the steps a dense list can tolerate sit too close in lightness, and its
// light end is invisible against the page surface), and a fill loud enough to pass
// would read as a verdict on a 400-row page — which is exactly what these two
// numbers are not. So one neutral chip, like every other badge on the site, and
// the reader compares the figures directly, which is finer than five buckets
// anyway.
//
// The band is deliberately **corpus-relative** — Björnsson's "easy/difficult"
// labels are calibrated for Swedish prose at a long-word threshold of 6 and are
// meaningless at the Hungarian threshold of 8, so a speech is placed against the
// House's own speeches instead ("harder to read than 80% of what is said here").
// The backend resolves the band from stored quantile cut points and sends it as
// `lix_band` / `mattr_band`; without a corpus distribution it sends none and the
// chip shows the bare number rather than inventing a label.
//
// A speech with no `metrics` renders nothing at all: procedural speeches, speeches
// without a transcript and speeches under the minimum length are not measurable,
// and a score there would be noise dressed as a number. `mattr` alone can be
// missing — a speech shorter than the sliding window has no length-comparable
// diversity value, and the whole diversity half is absent when the build had no
// lemmatizer (it is never faked from surface forms).
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { store } from '../store.js'
import { SPEECH_METRICS_ENABLED } from '../features.js'

const props = defineProps({
  metrics: { type: Object, default: null },
  // `compact` drops the worded band from the chip face (it stays in the tooltip
  // and for screen readers), for the dense sitting-day speech list.
  compact: { type: Boolean, default: false },
})

const { t } = useI18n()

// Two independent off switches, plus "is there anything to show":
//   • SPEECH_METRICS_ENABLED — the editorial curtain (features.js), currently off;
//   • store.featureEnabled    — the backend's own flag, false when the pass is
//     disabled or the DB carries no measurements;
//   • props.metrics           — this particular speech isn't measurable.
const on = computed(() => SPEECH_METRICS_ENABLED
  && store.featureEnabled('speech_metrics')
  && !!props.metrics)

const lix = computed(() => (on.value ? props.metrics.lix : null))
const mattr = computed(() => (on.value ? props.metrics.mattr : null))
const lixBand = computed(() => (lix.value == null ? null : props.metrics.lix_band))
const mattrBand = computed(() => (mattr.value == null ? null : props.metrics.mattr_band))

// The MATTR window is a measurement parameter, not a constant of the UI: it is
// configurable and the tooltip has to state the one actually used.
const window_ = computed(() => store.meta?.speech_metrics?.mattr_window || 100)

const num = (v, digits = 0) => (v == null ? '' : Number(v).toFixed(digits))

// The tooltips spell out what the number is, what the band compares it against,
// and the counts behind it — the methodology every derived figure on the site
// owes the reader (TRUST-1 / REP-5).
const lixTitle = computed(() => {
  const m = props.metrics
  const band = lixBand.value ? ` — ${t(`metrics.lixBand.${lixBand.value}`)}` : ''
  return `${t('metrics.lixName')}: ${num(m.lix, 1)}${band}\n`
    + `${t('metrics.lixTip')}\n`
    + t('metrics.lixCounts', {
      words: m.words, sentences: m.sentences,
      perSentence: num(m.avg_sentence, 1),
      longShare: num((m.long_share || 0) * 100, 0),
    })
})

const mattrTitle = computed(() => {
  const m = props.metrics
  const band = mattrBand.value ? ` — ${t(`metrics.mattrBand.${mattrBand.value}`)}` : ''
  return `${t('metrics.mattrName')}: ${num(m.mattr, 2)}${band}\n`
    + `${t('metrics.mattrTip', { window: window_.value })}\n`
    + t('metrics.mattrCounts', { types: m.types, tokens: m.tokens })
})
</script>

<template>
  <span v-if="on && (lix != null || mattr != null)" class="metrics">
    <span v-if="lix != null" class="badge metric" :title="lixTitle">
      <span aria-hidden="true">📖</span>
      <span class="mval">{{ num(lix, 0) }}</span>
      <span v-if="!compact && lixBand" class="mband">{{ $t(`metrics.lixBand.${lixBand}`) }}</span>
      <span class="visually-hidden">{{ lixTitle }}</span>
    </span>
    <span v-if="mattr != null" class="badge metric" :title="mattrTitle">
      <span aria-hidden="true">🔤</span>
      <span class="mval">{{ num(mattr, 2) }}</span>
      <span v-if="!compact && mattrBand" class="mband">{{ $t(`metrics.mattrBand.${mattrBand}`) }}</span>
      <span class="visually-hidden">{{ mattrTitle }}</span>
    </span>
  </span>
</template>

<style scoped>
.metrics { display: inline-flex; align-items: center; gap: .35rem; }
/* The site's ordinary neutral chip — the glyph tells the two metrics apart, the
   number and the band word carry the value. Tabular figures so a column of
   speeches lines up and can be compared by eye. */
.badge.metric {
  cursor: help; white-space: nowrap; font-variant-numeric: tabular-nums;
  font-weight: 400; color: var(--ink-soft);
}
.mval { font-weight: 700; }
.mband { color: var(--ink-faint); }
</style>
