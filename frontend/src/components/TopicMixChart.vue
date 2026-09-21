<script setup>
// The two agendas (TOPIC-9): the CAP policy topics of the corpus, ranked, with
// what the House *said* about each one beside what was *submitted* to it.
//
// One row per topic, two bars in it: the topic's share of the classified policy
// text in the plenary transcript, and its share in the irományok. They are two
// measures of the same kind over the same 21 labels, produced by one model at one
// threshold (TOPIC-8), which is exactly what makes them comparable by eye — and
// the gap between them is the point of the figure. Parliament talks most about
// what it is arguing over; it submits most of what it is administering, and those
// are not the same list.
//
// **Why the bars are scaled to the largest share rather than to 100 %.** These
// are shares of a 21-way split: the largest topic takes a small fraction of the
// floor and most take a few per cent, so a 0–100 % axis would draw every row as a
// stub. Each bar therefore carries its own percentage as a direct label — nothing
// here has to be read off the bar's length alone.
//
// **Colour.** Two series, two hues, both from the site's own brand tokens and
// checked for colour-vision separation against the white card (ΔE 18.6 protan,
// 27.8 normal). Identity is never carried by the hue alone: the legend names both
// series, every bar is labelled with its own number, and each row's accessible
// name reads both. The topics themselves are *not* colour-coded — 21 hues is far
// past what stays distinguishable, the same reason TopicBadge has none.
//
// **A third use, one series and a baseline (TOPIC-10).** A representative profile
// draws the same rows for one member, and passes the floor's mix as `reference`:
// a tick across each track instead of a second bar. That is a deliberate
// asymmetry. Speeches against irományok are two agendas of equal standing, so
// they get two bars; a member against the House is a figure and the norm it is
// high or low against, and drawing the norm as a bar of its own would invite the
// reader to compare their sizes when the only thing that means anything is which
// side of the tick the bar ends on.
//
// Shared with the embed view (§4C), so the figure a journalist drops into their
// page is this component and not a second implementation of it.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { topicGlyph, topicName as capName } from '../lib/topics.js'

const props = defineProps({
  // `{ topics: [{label, code, words, share, count}], … }` as the two `/topics`
  // endpoints answer, or null when that half is unavailable (the module is off,
  // or nothing on that side was ever classified). A missing half is a missing
  // series, not an empty chart.
  speech: { type: Object, default: null },
  bill: { type: Object, default: null },
  // Which series the ranking follows. The document agenda has its own order, and
  // seeing the same 21 rows resorted is how the difference becomes visible.
  sortBy: { type: String, default: 'speech' },   // 'speech' | 'bill'
  // A baseline to read the bars against, as `{ label: share }` — the House's own
  // mix on a representative profile (TOPIC-10), drawn as a tick across the track
  // rather than as a second bar. It is a *reference*, not a series: one member's
  // topics and the floor's are not two things of equal standing to compare, they
  // are a figure and the norm it is high or low against, and a second full bar
  // would say otherwise (and double the height of a card-sized figure).
  reference: { type: Object, default: null },
  // What the tick is, named in the legend — never left to the reader to infer.
  referenceLabel: { type: String, default: '' },
  // Overrides the name of the bar series where "Felszólalások" is not what the
  // bars are ("Ez a képviselő", on a profile). Empty keeps the shared wording.
  seriesLabel: { type: String, default: '' },
  // The label currently opened in the page's detail panel, if any.
  selected: { type: String, default: '' },
  // Rows are buttons on the page (they open a topic) and plain rows in an embed,
  // which has nothing to open them into.
  interactive: { type: Boolean, default: true },
  caption: { type: String, default: '' },
})

const emit = defineEmits(['select'])

const { t, te } = useI18n()

const topicName = (label) => capName(label, t, te)
const pct = (v) => `${((v || 0) * 100).toLocaleString('hu-HU', { maximumFractionDigits: 1 })}%`
const num = (v) => (v || 0).toLocaleString('hu-HU')

const hasSpeech = computed(() => !!props.speech?.topics?.length)
const hasBill = computed(() => !!props.bill?.topics?.length)
const bothSeries = computed(() => hasSpeech.value && hasBill.value)
const speechName = computed(() => props.seriesLabel || t('topicMix.speeches'))
// The reference is per topic and may simply not cover one (a topic the member
// spoke about and the House, in this scope, did not): no tick, not a zero.
const refShare = (label) => {
  const v = props.reference ? props.reference[label] : null
  return typeof v === 'number' ? v : null
}

const rows = computed(() => {
  const byLabel = new Map()
  const add = (side, list) => {
    for (const t_ of list || []) {
      const row = byLabel.get(t_.label) || {
        label: t_.label, code: t_.code, name: topicName(t_.label),
        glyph: topicGlyph(t_.label), speech: null, bill: null,
      }
      row[side] = t_
      row.code = row.code ?? t_.code
      byLabel.set(t_.label, row)
    }
  }
  add('speech', props.speech?.topics)
  add('bill', props.bill?.topics)

  const key = (r, side) => r[side]?.share || 0
  const first = props.sortBy === 'bill' && hasBill.value ? 'bill' : 'speech'
  const second = first === 'bill' ? 'speech' : 'bill'
  // The other series breaks a tie so a topic absent from the sorted one keeps a
  // stable, meaningful place instead of falling to the bottom in name order.
  return [...byLabel.values()].sort((a, b) =>
    key(b, first) - key(a, first) || key(b, second) - key(a, second)
    || a.name.localeCompare(b.name, 'hu'))
})

const hasReference = computed(() =>
  !!props.reference && rows.value.some((r) => refShare(r.label) !== null))

// The longest bar in the figure defines the track, across both series, so the two
// stay on one scale — a series scaled to its own maximum would make a 4 % topic
// and a 14 % one look alike. The reference counts towards it too: a baseline the
// member is far below would otherwise sit off the end of the track.
const max = computed(() => Math.max(
  0.01,
  ...rows.value.flatMap((r) => [r.speech?.share || 0, r.bill?.share || 0,
                                refShare(r.label) || 0])))

const width = (share) => `${Math.max(share ? 1.5 : 0, (share / max.value) * 100)}%`
// Never past the track's own end: at the scale maximum the tick would otherwise
// hang half outside the figure.
const refLeft = (share) =>
  `min(calc(100% - 2px), ${(share / max.value) * 100}%)`

// What one bar is worth spelling out on hover: the share it shows, the text
// behind it, and how many items the topic is the subject of — the same
// dominant-topic count the lists and the chips use.
function barTitle(row, side) {
  const d = row[side]
  if (!d) return ''
  return t(`topicMix.tip.${side}`, {
    topic: row.name, pct: pct(d.share), words: num(d.words), n: num(d.count),
  })
}

// The bars are decoration to a screen reader (`aria-hidden`), so their numbers
// have to reach it as words: on the page they are the row button's accessible
// name, and in the embed — where the row is not a control and an `aria-label`
// on a plain element would simply be ignored — as visually hidden text.
function rowValues(row) {
  const parts = []
  if (row.speech) parts.push(`${speechName.value}: ${pct(row.speech.share)}`)
  if (row.bill) parts.push(`${t('topicMix.bills')}: ${pct(row.bill.share)}`)
  // The tick is the whole point of the row where there is one, so it is read out
  // with the bar rather than left to the hover title no screen reader reaches.
  const ref = refShare(row.label)
  if (ref !== null && props.referenceLabel) {
    parts.push(`${props.referenceLabel}: ${pct(ref)}`)
  }
  return parts.join(' · ')
}

function refTitle(row) {
  const ref = refShare(row.label)
  if (ref === null) return ''
  return `${props.referenceLabel || t('topicMix.reference')}: ${pct(ref)}`
}

function rowLabel(row) {
  return `${row.name} · ${rowValues(row)}`
}
</script>

<template>
  <figure class="mix">
    <figcaption v-if="caption" class="small soft mix-cap">{{ caption }}</figcaption>

    <!-- A legend whenever the figure carries more than one mark — two series, or
         one series and the reference tick. With a single bar and nothing to read
         it against, the caption already names what the bars are and a box with
         one swatch would only restate it. -->
    <ul v-if="bothSeries || hasReference" class="mix-legend">
      <li><span class="swatch sp" aria-hidden="true" />{{ speechName }}</li>
      <li v-if="bothSeries"><span class="swatch bi" aria-hidden="true" />{{ $t('topicMix.bills') }}</li>
      <li v-if="hasReference">
        <span class="swatch ref" aria-hidden="true" />{{ referenceLabel || $t('topicMix.reference') }}
      </li>
    </ul>

    <ul class="mix-rows">
      <li v-for="row in rows" :key="row.label">
        <component
          :is="interactive ? 'button' : 'div'" class="mix-row"
          :type="interactive ? 'button' : undefined"
          :class="{ on: selected === row.label, static: !interactive }"
          :aria-pressed="interactive ? selected === row.label : undefined"
          :aria-label="interactive ? rowLabel(row) : undefined"
          @click="interactive && emit('select', row.label)"
        >
          <span class="mix-name">
            <span class="mix-glyph" aria-hidden="true">{{ row.glyph }}</span>
            <span class="mix-word">{{ row.name }}</span>
          </span>

          <span v-if="!interactive" class="visually-hidden">{{ rowValues(row) }}</span>

          <span class="mix-bars" aria-hidden="true">
            <span class="mix-bar">
              <span class="track">
                <span
                  v-if="row.speech" class="fill sp" :style="{ width: width(row.speech.share) }"
                  :title="barTitle(row, 'speech')"
                />
                <span
                  v-if="refShare(row.label) !== null" class="ref"
                  :style="{ left: refLeft(refShare(row.label)) }" :title="refTitle(row)"
                />
              </span>
              <span class="val">{{ row.speech ? pct(row.speech.share) : '–' }}</span>
            </span>
            <span v-if="hasBill" class="mix-bar">
              <span class="track">
                <span
                  v-if="row.bill" class="fill bi" :style="{ width: width(row.bill.share) }"
                  :title="barTitle(row, 'bill')"
                />
              </span>
              <span class="val">{{ row.bill ? pct(row.bill.share) : '–' }}</span>
            </span>
          </span>
        </component>
      </li>
    </ul>
  </figure>
</template>

<style scoped>
/* Both hues are the site's own brand tokens: the red the site is built on for
   what is said in the chamber, the brand blue for what is put before it. The
   pair clears the colour-vision and contrast checks on a white card; the values
   beside them stay in ink, never in the series colour. */
.mix { --sp: var(--accent); --bi: #0D5F94; margin: 0; }
.mix-cap { margin-bottom: .5rem; }

.mix-legend {
  display: flex; flex-wrap: wrap; gap: .1rem 1rem;
  list-style: none; margin: 0 0 .6rem; padding: 0;
  font-size: .82rem; color: var(--ink-soft);
}
.mix-legend li { display: inline-flex; align-items: center; gap: .35rem; }
.swatch { width: .7rem; height: .7rem; border-radius: 2px; display: inline-block; }
.swatch.sp { background: var(--sp); }
.swatch.bi { background: var(--bi); }
/* The reference reads as a rule, not as a third colour. Its legend key is a
   miniature of the row it appears in — a length of track with the tick across it
   — because the bare 2px tick on the legend's own background is too slight to be
   recognised as the thing on the bars. */
.swatch.ref { position: relative; width: 1rem; height: .55rem; background: #eceae4; }
.swatch.ref::after {
  content: ""; position: absolute; top: 0; bottom: 0; left: .5rem; width: 2px;
  background: var(--ink);
}

.mix-rows { list-style: none; margin: 0; padding: 0; }

.mix-row {
  /* The names are short and the bars are the figure: the label column is capped
     in rem rather than in per cent so a wide card lengthens the bars instead of
     opening a gulf between the word and its bar. */
  display: grid; grid-template-columns: minmax(7.5rem, 16rem) 1fr;
  align-items: center; gap: .1rem .7rem;
  width: 100%; text-align: left; padding: .28rem .35rem;
  background: none; border: 1px solid transparent; border-radius: 6px;
  color: inherit; font: inherit; cursor: pointer;
}
.mix-row.static { cursor: default; }
.mix-row:not(.static):hover { background: var(--bg); }
.mix-row.on { background: var(--accent-soft); border-color: var(--accent); }

.mix-name {
  display: flex; align-items: baseline; gap: .35rem;
  font-size: .85rem; color: var(--ink);
  min-width: 0;
}
.mix-glyph { flex: none; }
/* Several CAP names are long ("Kormányzat és közigazgatás"); they wrap rather
   than ellipsize, because a truncated topic is the word the reader came for. */
.mix-word { line-height: 1.2; }

.mix-bars { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.mix-bar { display: grid; grid-template-columns: 1fr 3.2rem; align-items: center; gap: .5rem; }
.track { position: relative; height: 9px; border-radius: 4px; background: #eceae4; overflow: hidden; }
/* The baseline tick. Ink rather than a hue — it is the norm the bars are read
   against, not a series of its own — with a hairline of surface around it so it
   stays visible where it crosses the fill instead of disappearing into it. */
.ref {
  position: absolute; top: 0; bottom: 0; width: 2px;
  /* Centred on the value rather than starting at it — 1px, but the whole point
     of the mark is where exactly it falls against the bar's end. */
  transform: translateX(-1px);
  background: var(--ink); box-shadow: 0 0 0 1px rgba(255, 255, 255, .85);
}
.fill { display: block; height: 100%; border-radius: 4px; }
.fill.sp { background: var(--sp); }
.fill.bi { background: var(--bi); }
.val {
  font-size: .78rem; font-variant-numeric: tabular-nums;
  color: var(--ink-soft); text-align: right;
}

/* On a phone the name needs the full width, so the bars drop beneath it rather
   than squeezing both into a third of the screen. */
@media (max-width: 560px) {
  .mix-row { grid-template-columns: 1fr; gap: .2rem; }
  .mix-name { font-weight: 600; }
}
</style>
