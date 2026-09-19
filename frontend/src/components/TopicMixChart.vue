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

// The longest bar in the figure defines the track, across both series, so the two
// stay on one scale — a series scaled to its own maximum would make a 4 % topic
// and a 14 % one look alike.
const max = computed(() => Math.max(
  0.01, ...rows.value.flatMap((r) => [r.speech?.share || 0, r.bill?.share || 0])))

const width = (share) => `${Math.max(share ? 1.5 : 0, (share / max.value) * 100)}%`

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
  if (row.speech) parts.push(`${t('topicMix.speeches')}: ${pct(row.speech.share)}`)
  if (row.bill) parts.push(`${t('topicMix.bills')}: ${pct(row.bill.share)}`)
  return parts.join(' · ')
}

function rowLabel(row) {
  return `${row.name} · ${rowValues(row)}`
}
</script>

<template>
  <figure class="mix">
    <figcaption v-if="caption" class="small soft mix-cap">{{ caption }}</figcaption>

    <!-- A legend whenever there are two series; with one, the caption already
         names what the bars are (no legend box for a single series). -->
    <ul v-if="bothSeries" class="mix-legend">
      <li><span class="swatch sp" aria-hidden="true" />{{ $t('topicMix.speeches') }}</li>
      <li><span class="swatch bi" aria-hidden="true" />{{ $t('topicMix.bills') }}</li>
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
.track { height: 9px; border-radius: 4px; background: #eceae4; overflow: hidden; }
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
