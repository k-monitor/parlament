<script setup>
// Comparing representatives side by side (REP-15). A profile answers "what did
// this person do"; this page answers "compared to whom" — two to four people in
// parallel columns, one row per figure, read across rather than reconstructed from
// two browser tabs.
//
// Three things shape the implementation:
//
// * **The URL is the comparison.** `?ids=` is the *only* state: the fetch and every
//   add/remove go through it, so a comparison is shareable and citable like a
//   profile and Back walks the columns the reader tried (REP-15).
// * **A real <table>.** The content *is* tabular — metrics × people — and a table
//   is the only markup that tells a screen reader which column a cell belongs to
//   (A11Y-1). The visual spec-sheet look is styling on top of it, not a
//   substitute for it.
// * **Every figure comes from the compare endpoint**, which reads the same
//   aggregates the profile reads (REP-7/STAT-1). Nothing is recomputed here, so
//   the two pages cannot disagree; the rows below only choose how to print it.
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { store, loadMeta, currentCycleLabel } from '../../store.js'
import { formatSpeakingTime, formatDuration } from '../../format.js'
import { COMPARE_MAX, parseIds, serializeIds } from '../../lib/compareUrl.js'
import { signsOf } from '../../lib/zodiac.js'
import StateBlock from '../../components/StateBlock.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import HelpTip from '../../components/HelpTip.vue'
import ShareButton from '../../components/ShareButton.vue'

const route = useRoute()
const router = useRouter()
const { t, locale } = useI18n()

const data = ref(null)
const loading = ref(false)
const error = ref(false)
// "Csak a különbségek": hide the rows on which every column says the same thing
// — the webshop move that turns a long spec sheet into the handful of lines that
// actually separate the items.
const diffOnly = ref(false)

const ids = computed(() => parseIds(route.query.ids))
const people = computed(() => (data.value ? data.value.people : []))
// The ids that actually resolved to a person — what the next URL is built from.
// Building it from `?ids=` instead would let a dead id hold a column's worth of
// the limit: at four ids, one of them missing, adding a fifth would be sliced off
// again and the button would do nothing. Rewriting without the dead id is also the
// better answer — you cannot compare someone who is not there.
const resolvedIds = computed(() => people.value.map((p) => p.person_id))
const canAdd = computed(() => people.value.length < COMPARE_MAX)

const scopeText = computed(() => {
  const c = currentCycleLabel()
  return c ? t('cycle.scope', { cycle: c }) : t('cycle.scopeAll')
})

const numberFmt = computed(() => new Intl.NumberFormat(locale.value === 'en' ? 'en-US' : 'hu-HU'))
function num(v) { return v == null ? null : numberFmt.value.format(v) }

// --- the rows ---------------------------------------------------------------
// One descriptor per row: how to pull the value out of a person, how to print it,
// and whether it is a magnitude (bars + the largest-value marker) or a fact
// (printed as it is). `value` returning `null` means the cell has no value — never
// a zero the reader would compare against a real one (TRUST-1).
//
// An empty cell means one of two different things, and saying the wrong one is a
// factual error: **"nem értelmezhető"** (the notion does not apply to this person —
// a non-MP has no constituency and no roll calls to miss) or **"nincs adat"** (it
// applies, we just don't know it — most of the corpus has no birth date, so no
// sign). `absentKey` picks which; it defaults to the first.
//
// `noteKey` is the row's own caveat, shown in a HelpTip: some rows are biography
// rather than cycle statistics, which the reader has to be told or the selected
// cycle silently means nothing for them.

// Participation-pie colours, kept identical to the profile's so a reader moving
// between the two pages reads the same colour as the same thing.
const VB_SEGMENTS = [
  { key: 'voted', color: '#2e7d32' },
  { key: 'novote', color: '#c79a2e' },
  { key: 'absent', color: '#7c8288' },
  { key: 'not_present', color: '#3f434a' },
]
const VB_LABEL = {
  voted: 'profile.vbVoted', novote: 'profile.vbNovote',
  absent: 'profile.vbAbsent', not_present: 'profile.vbNotPresent',
}

function vbSegments(p) {
  const b = p.vote_breakdown
  if (!b || !b.total) return null
  return VB_SEGMENTS
    .map((s) => ({ ...s, label: t(VB_LABEL[s.key]), value: b[s.key] || 0 }))
    .filter((s) => s.value > 0)
    .map((s) => ({ ...s, pct: (100 * s.value) / b.total }))
}

const SECTIONS = computed(() => {
  const bills = data.value ? data.value.bills_available : false
  const votes = data.value ? data.value.votes_available : false
  return [
    {
      key: 'who', titleKey: 'compare.sectionWho',
      rows: [
        { key: 'faction', labelKey: 'reps.faction', kind: 'faction',
          value: (p) => p.faction,
          text: (p) => (p.faction ? p.faction.label : null) },
        // A seat is an MP's notion: an advocate and a non-MP minister hold none, so
        // the cell says "nem értelmezhető" rather than leaving a blank that reads
        // as "we don't know" (REP-9/REP-12).
        { key: 'constituency', labelKey: 'profile.constituency', kind: 'text',
          value: (p) => p.constituency },
        { key: 'office', labelKey: 'profile.office', kind: 'text',
          value: (p) => p.office },
        // Upstream simply has no qualification on file for many people — that is
        // missing data, not a person to whom the notion fails to apply.
        { key: 'education', labelKey: 'profile.education', kind: 'text',
          value: (p) => p.highest_education, absentKey: 'compare.unknown' },
      ],
    },
    {
      key: 'speech', titleKey: 'compare.sectionSpeech',
      rows: [
        { key: 'speeches', labelKey: 'profile.totalSpeeches', kind: 'num',
          value: (p) => p.speech_count, text: (p) => num(p.speech_count) },
        { key: 'time', labelKey: 'profile.totalSpeakingTime', kind: 'num',
          value: (p) => p.speaking_seconds,
          text: (p) => formatSpeakingTime(p.speaking_seconds) },
        // The shape the two totals above hide: many short interjections or few
        // long addresses.
        { key: 'avg', labelKey: 'compare.avgSpeech', kind: 'num',
          value: (p) => p.avg_speech_seconds,
          text: (p) => (p.avg_speech_seconds == null ? null
                                                     : formatDuration(p.avg_speech_seconds)),
          noteKey: 'compare.avgSpeechNote' },
        { key: 'sentences', labelKey: 'compare.sentences', kind: 'num',
          value: (p) => p.sentence_count, text: (p) => num(p.sentence_count) },
        { key: 'days', labelKey: 'compare.speakingDays', kind: 'num',
          value: (p) => p.speaking_days, text: (p) => num(p.speaking_days),
          noteKey: 'compare.speakingDaysNote' },
      ],
    },
    // Both blocks below vanish with their module (EXT-6) rather than showing rows
    // of dashes the reader would read as "this person submitted nothing".
    ...(bills ? [{
      key: 'docs', titleKey: 'compare.sectionDocs',
      rows: [
        { key: 'own', labelKey: 'profile.billsSubmitted', kind: 'num',
          value: (p) => p.own_motions, text: (p) => num(p.own_motions),
          noteKey: 'compare.ownMotionsNote' },
        { key: 'questions', labelKey: 'profile.questions', kind: 'num',
          value: (p) => p.documents && p.documents.questions,
          text: (p) => num(p.documents && p.documents.questions) },
        { key: 'lawbills', labelKey: 'profile.bills', kind: 'num',
          value: (p) => p.documents && p.documents.bills,
          text: (p) => num(p.documents && p.documents.bills) },
        { key: 'otherdocs', labelKey: 'profile.otherDocuments', kind: 'num',
          value: (p) => p.documents && p.documents.other,
          text: (p) => num(p.documents && p.documents.other) },
      ],
    }] : []),
    ...(votes ? [{
      key: 'votes', titleKey: 'compare.sectionVotes',
      rows: [
        { key: 'rollcalls', labelKey: 'compare.rollCalls', kind: 'num',
          value: (p) => p.votes_total, text: (p) => num(p.votes_total),
          noteKey: 'compare.rollCallsNote' },
        { key: 'missed', labelKey: 'profile.votesAbsent', kind: 'num',
          value: (p) => p.votes_missed,
          text: (p) => (p.votes_missed == null ? null
            : num(p.votes_missed) + (p.votes_missed_pct == null ? ''
                                     : ` · ${p.votes_missed_pct}%`)) },
        // The whole five-way split as one stacked bar per column — the profile's
        // pie, flattened so four of them can be read side by side.
        { key: 'breakdown', labelKey: 'profile.voteBreakdown', kind: 'stack',
          value: (p) => vbSegments(p) },
      ],
    }] : []),
    {
      key: 'other', titleKey: 'compare.sectionOther',
      rows: [
        { key: 'committees', labelKey: 'profile.committees', kind: 'num',
          value: (p) => p.committee_count, text: (p) => num(p.committee_count),
          noteKey: 'compare.careerNote' },
        { key: 'declarations', labelKey: 'profile.assetDeclarations', kind: 'num',
          value: (p) => p.declaration_count, text: (p) => num(p.declaration_count),
          noteKey: 'compare.careerNote' },
      ],
    },
  ]
})

// The largest value in a numeric row, so the row's bars have a scale and the
// leading cell can be marked. Deliberately the *largest*, never the "best": the
// page reports who spoke more, not who is better (REP-15).
// What this row's empty cell says — see the descriptor contract above.
function absentText(row) {
  return t(row.absentKey || 'compare.na')
}

function rowMax(row) {
  const vals = people.value.map(row.value).filter((v) => typeof v === 'number')
  return vals.length ? Math.max(...vals) : 0
}
function barPct(row, p) {
  const max = rowMax(row)
  const v = row.value(p)
  if (!max || typeof v !== 'number' || v <= 0) return 0
  return Math.max(1.5, (100 * v) / max)   // a floor, so a tiny value still shows
}
function isLead(row, p) {
  const max = rowMax(row)
  const v = row.value(p)
  // Nobody leads a row where everyone is equal (or empty): the marker exists to
  // point at a difference.
  if (!max || typeof v !== 'number' || v !== max) return false
  return people.value.filter((x) => row.value(x) === max).length < people.value.length
}

/** Whether every column prints the same thing for this row — what "csak a
 *  különbségek" hides. Compared on the *printed* text, since that is what the
 *  reader would be comparing (two speaking times rounding to "12ó 3p" are not a
 *  difference worth a row). */
function isUniform(row) {
  if (people.value.length < 2) return false
  if (row.kind === 'stack') return false   // a shape, not a value: never "equal"
  const printed = people.value.map((p) => {
    const text = row.text ? row.text(p) : row.value(p)
    return text == null ? '' : String(text)
  })
  return printed.every((x) => x === printed[0])
}

const sections = computed(() => SECTIONS.value
  .map((s) => ({ ...s, rows: s.rows.filter((r) => !diffOnly.value || !isUniform(r)) }))
  .filter((s) => s.rows.length))

const hiddenRows = computed(() => SECTIONS.value
  .reduce((n, s) => n + s.rows.filter(isUniform).length, 0))

// --- csillagjegyek (REP-16) -------------------------------------------------
// Trivia, and kept out of the table proper. Every other row on this page is a
// checkable figure; a star sign set in the same register would corrode exactly
// the credibility the rest of it rests on. So the signs sit at the foot of the
// page behind the same ⛎ spoiler the profile uses: nothing astrological is on
// screen as the comparison opens, and a reader only ever sees it by asking.
// Never a `num` row and never part of a section — there is nothing here to rank,
// and "csak a különbségek" has no business hiding a piece of trivia as if it were
// a figure two people happened to share.
const ZODIAC_ROWS = [
  { key: 'zodiac', labelKey: 'profile.zodiac', value: (p) => signsOf(p)?.sun || null },
  { key: 'chineseZodiac', labelKey: 'profile.chineseZodiac', value: (p) => signsOf(p)?.animal || null },
]
const zodiacRevealed = ref(false)
// No spoiler at all unless at least one column has a sign: most of the corpus has
// no day-precision birth date, and a glyph that opens onto two rows of "nincs
// adat" promises something the page cannot deliver. Once it *is* open, the people
// without a sign still get the "nincs adat" cell — in a table a blank has
// neighbours it would have to line up with (TRUST-1).
const hasZodiac = computed(() => people.value.some((p) => signsOf(p)))

// --- adding and removing ----------------------------------------------------
// Both rewrite `?ids=` and let the watcher re-fetch: the URL is the state, so a
// column added is a page the reader can go Back from (REP-15).
function go(list) {
  router.push({ name: 'compare', query: { ...route.query, ids: serializeIds(list) || undefined } })
}
function remove(personId) {
  go(resolvedIds.value.filter((x) => x !== personId))
}
function add(personId) {
  if (resolvedIds.value.includes(personId) || !canAdd.value) return
  closePicker()
  go([...resolvedIds.value, personId])
}

// --- the "add a person" picker ----------------------------------------------
// A name search over the same list endpoint the Felszólalók page uses, in `all`
// mode: a comparison may hold an advocate or a non-MP minister as readily as an
// MP, and the endpoint answers for all of them.
const pickerOpen = ref(false)
const pickerQ = ref('')
const pickerRows = ref([])
const pickerLoading = ref(false)
const pickerInput = ref(null)
let pickSeq = 0
let pickTimer = null

function openPicker() {
  pickerOpen.value = true
  // Suggestions before a single keystroke: the most active people in scope, which
  // is both a useful default comparison and a demonstration of what the box does.
  if (!pickerRows.value.length) search()
  requestAnimationFrame(() => pickerInput.value?.focus())
}
function closePicker() {
  pickerOpen.value = false
  pickerQ.value = ''
}
async function search() {
  const seq = ++pickSeq
  pickerLoading.value = true
  try {
    const res = await api.representatives({
      q: pickerQ.value || undefined, period: store.cycles, role: 'all',
      sort: pickerQ.value ? 'name' : 'speaking_time', limit: 8,
    })
    if (seq === pickSeq) pickerRows.value = res.representatives
  } catch {
    if (seq === pickSeq) pickerRows.value = []
  } finally {
    if (seq === pickSeq) pickerLoading.value = false
  }
}
function onPickerInput() {
  clearTimeout(pickTimer)
  pickTimer = setTimeout(search, 300)
}

// --- load -------------------------------------------------------------------
const PLACEHOLDER =
  'data:image/svg+xml;utf8,' + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="72" height="72"><rect width="72" height="72" fill="#e7e5df"/><circle cx="36" cy="29" r="14" fill="#bdb9af"/><rect x="12" y="48" width="48" height="26" rx="13" fill="#bdb9af"/></svg>')
function onImgErr(e) { e.target.src = PLACEHOLDER }

let loadSeq = 0
async function load() {
  if (!ids.value.length) {
    data.value = null; loading.value = false; error.value = false
    return
  }
  const seq = ++loadSeq
  loading.value = true; error.value = false
  try {
    const res = await api.repCompare(ids.value, store.cycles)
    if (seq !== loadSeq) return
    data.value = res
  } catch {
    if (seq === loadSeq) error.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

// Gate the first fetch on the manifest so store.cycles is the latest cycle rather
// than briefly "all" (as on the profile).
onMounted(async () => { await loadMeta().catch(() => {}); load() })
watch(() => route.query.ids, load)
watch(() => store.cycles.join(','), () => {
  // A cycle switch re-scopes the figures *and* the picker's suggestions.
  pickerRows.value = []
  load()
})

const shareTitle = computed(() => (people.value.length
  ? people.value.map((p) => p.label).join(' vs. ')
  : t('compare.title')))
</script>

<template>
  <div class="chead">
    <h1>{{ $t('compare.title') }}</h1>
    <ShareButton v-if="people.length > 1" :title="shareTitle" align="right" />
  </div>
  <p class="muted small chead-sub">
    {{ $t('compare.intro', { max: COMPARE_MAX }) }} ·
    {{ scopeText }}
    <HelpTip v-if="data" :label="$t('profile.methodology')">
      <p>{{ data.scope.description }}</p>
      <p>{{ data.methodology }}</p>
    </HelpTip>
  </p>

  <!-- Nothing chosen yet: the page is its own picker rather than an error. -->
  <div v-if="!ids.length" class="card pad empty">
    <p>{{ $t('compare.emptyLead') }}</p>
    <p class="muted small">{{ $t('compare.emptyHint') }}</p>
    <button type="button" class="btn" @click="openPicker">{{ $t('compare.addPerson') }}</button>
  </div>

  <StateBlock v-else :loading="loading" :error="error" @retry="load">
    <div v-if="data">
      <!-- An id in the URL that resolves to nobody is said out loud, not dropped
           silently: the reader needs to know a column is missing from what they
           were sent (REP-15). -->
      <p v-if="data.missing.length" class="note warn small">
        ⚠ {{ $t('compare.missing', { ids: data.missing.join(', ') }) }}
      </p>
      <p v-if="data.dropped.length" class="note warn small">
        ⚠ {{ $t('compare.dropped', { n: data.limit }) }}
      </p>
      <p v-if="people.length === 1" class="note small">
        {{ $t('compare.needTwo') }}
      </p>

      <div class="ctools" v-if="people.length > 1">
        <label class="small">
          <input type="checkbox" v-model="diffOnly" />
          {{ $t('compare.diffOnly') }}
          <span class="muted" v-if="hiddenRows">({{ $t('compare.diffOnlyCount', { n: hiddenRows }) }})</span>
        </label>
      </div>

      <!-- The scroller: on a narrow screen the columns scroll sideways under a
           pinned label column rather than being squeezed unreadable. -->
      <div class="cwrap">
        <table class="ctable">
          <caption class="visually-hidden">{{ $t('compare.tableCaption') }}</caption>
          <thead>
            <tr>
              <td class="rowhead corner"></td>
              <th v-for="p in people" :key="p.person_id" scope="col" class="pcol">
                <div class="pcard">
                  <router-link :to="{ name: 'profile', params: { id: p.person_id } }" class="plink">
                    <img class="avatar" :src="p.photo_uri || PLACEHOLDER" @error="onImgErr" alt="" />
                    <span class="pname">{{ p.label }}</span>
                  </router-link>
                  <FactionBadge :faction="p.faction" />
                  <!-- An advocate has no faction; the nationality they speak for
                       is the identifying affiliation (REP-9). -->
                  <span v-if="p.is_advocate && p.nationality" class="chip"
                        :aria-label="$t('reps.advocateFor', { nationality: p.nationality })">{{ p.nationality }}</span>
                  <button type="button" class="drop" @click="remove(p.person_id)"
                          :aria-label="$t('compare.remove', { name: p.label })"
                          :title="$t('compare.remove', { name: p.label })">×</button>
                </div>
              </th>
              <!-- The empty slot: the shelf space that says another column fits. -->
              <th v-if="canAdd" scope="col" class="pcol slot">
                <button type="button" class="addslot" @click="openPicker">
                  <span class="plus" aria-hidden="true">+</span>
                  <span class="small">{{ $t('compare.addPerson') }}</span>
                </button>
              </th>
            </tr>
          </thead>

          <tbody v-for="sec in sections" :key="sec.key">
            <tr class="sec">
              <th class="rowhead" :colspan="people.length + (canAdd ? 2 : 1)" scope="rowgroup">
                {{ $t(sec.titleKey) }}
              </th>
            </tr>
            <tr v-for="row in sec.rows" :key="row.key">
              <th scope="row" class="rowhead">
                {{ $t(row.labelKey) }}
                <HelpTip v-if="row.noteKey" :label="$t(row.labelKey)">
                  <p>{{ $t(row.noteKey) }}</p>
                </HelpTip>
              </th>
              <td v-for="p in people" :key="p.person_id"
                  :class="{ lead: row.kind === 'num' && isLead(row, p) }">
                <!-- A magnitude: the number, a bar scaled to this row's largest
                     value, and — when one column leads — a neutral marker. Colour
                     and bar are never the only carriers (A11Y-1): the marker has
                     text for assistive tech and the number is always printed. -->
                <template v-if="row.kind === 'num'">
                  <span v-if="row.value(p) == null" class="na">{{ absentText(row) }}</span>
                  <template v-else>
                    <span class="val">{{ row.text(p) }}</span>
                    <span v-if="isLead(row, p)" class="leadmark" aria-hidden="true">▲</span>
                    <span v-if="isLead(row, p)" class="visually-hidden">{{ $t('compare.largest') }}</span>
                    <span class="bar" aria-hidden="true">
                      <span class="fill" :style="{ width: barPct(row, p) + '%' }"></span>
                    </span>
                  </template>
                </template>

                <!-- The participation split as a stacked bar, with the numbers in
                     the title/legend so the colours are never the only carrier. -->
                <template v-else-if="row.kind === 'stack'">
                  <span v-if="!row.value(p)" class="na">{{ absentText(row) }}</span>
                  <template v-else>
                    <span class="stack">
                      <span v-for="s in row.value(p)" :key="s.key" class="seg"
                            :style="{ width: s.pct + '%', background: s.color }"
                            :title="`${s.label}: ${s.value}`"></span>
                    </span>
                    <span class="visually-hidden">
                      <template v-for="s in row.value(p)" :key="s.key">{{ s.label }}: {{ s.value }}. </template>
                    </span>
                    <span class="stacklegend small muted">
                      <span v-for="s in row.value(p)" :key="s.key">
                        <span class="dot" :style="{ background: s.color }" aria-hidden="true"></span>{{ Math.round(s.pct) }}%
                      </span>
                    </span>
                  </template>
                </template>

                <template v-else-if="row.kind === 'faction'">
                  <FactionBadge v-if="row.value(p)" :faction="row.value(p)" link />
                  <span v-else class="na">{{ absentText(row) }}</span>
                </template>

                <template v-else>
                  <span v-if="row.value(p)" class="val small">{{ row.value(p) }}</span>
                  <span v-else class="na">{{ absentText(row) }}</span>
                </template>
              </td>
              <td v-if="canAdd" class="slot"></td>
            </tr>
          </tbody>

          <!-- Back to the full record: a comparison is a summary, and every
               figure in it is verifiable one click away (REP-5). -->
          <tbody>
            <tr>
              <th scope="row" class="rowhead">{{ $t('compare.profileRow') }}</th>
              <td v-for="p in people" :key="p.person_id">
                <router-link :to="{ name: 'profile', params: { id: p.person_id } }" class="small">
                  {{ $t('compare.openProfile') }}
                </router-link>
                <span class="links small">
                  <a v-if="p.wikipedia_url" :href="p.wikipedia_url" target="_blank" rel="noopener">{{ $t('profile.wikipedia') }}</a>
                  <a v-if="p.kmonitor_url" :href="p.kmonitor_url" target="_blank" rel="noopener">{{ $t('profile.kmonitor') }}</a>
                  <a v-if="p.website" :href="p.website" target="_blank" rel="noopener">{{ $t('profile.website') }}</a>
                </span>
              </td>
              <td v-if="canAdd" class="slot"></td>
            </tr>
          </tbody>

          <!-- Csillagjegyek (REP-16), at the foot of the table and behind a
               spoiler. Rows of *this* table rather than a table of their own: the
               columns are the same people, so they have to line up under the same
               heads — a second table would resolve its own widths and drift out of
               register the moment a column is missing.
               The trigger is ⛎ (Ophiuchus), the one zodiac glyph that is never
               among the twelve behind it, so the button itself cannot give a sign
               away; the note beside the revealed label says outright that it means
               nothing.
               `v-show`, not `v-if`: the button names the region it controls, so
               that region has to exist for the reference to resolve. Hidden this
               way it is out of the accessibility tree too, so the reveal means the
               same thing to every reader — nobody meets a horoscope on the way to a
               voting record. -->
          <tbody v-if="hasZodiac">
            <tr class="ztrigger" :class="{ zlast: !zodiacRevealed }">
              <th scope="row" class="rowhead">
                <button type="button" class="zodiac-peek" aria-controls="compare-zodiac"
                        :aria-expanded="zodiacRevealed ? 'true' : 'false'"
                        :title="zodiacRevealed ? $t('profile.zodiacHide') : $t('profile.zodiacReveal')"
                        @click="zodiacRevealed = !zodiacRevealed">
                  <span aria-hidden="true">⛎</span>
                  <span class="visually-hidden">{{ zodiacRevealed ? $t('profile.zodiacHide') : $t('profile.zodiacReveal') }}</span>
                </button>
              </th>
              <td :colspan="people.length"></td>
              <td v-if="canAdd" class="slot"></td>
            </tr>
          </tbody>
          <tbody v-if="hasZodiac" v-show="zodiacRevealed" id="compare-zodiac" class="zbody">
            <tr v-for="row in ZODIAC_ROWS" :key="row.key">
              <th scope="row" class="rowhead">
                {{ $t(row.labelKey) }}
                <HelpTip :label="$t(row.labelKey)">
                  <p>{{ $t('profile.zodiacNote') }}</p>
                </HelpTip>
              </th>
              <td v-for="p in people" :key="p.person_id">
                <span v-if="!row.value(p)" class="na">{{ $t('compare.unknown') }}</span>
                <!-- Glyph as decoration, label as the meaning (A11Y-1). -->
                <span v-else class="signval small">
                  <span aria-hidden="true">{{ row.value(p).glyph }}</span>
                  {{ row.value(p).label }}
                </span>
              </td>
              <td v-if="canAdd" class="slot"></td>
            </tr>
          </tbody>
        </table>
      </div>

    </div>
  </StateBlock>

  <!-- The picker. A dialog rather than an inline row: it is opened from two
       places (the empty state and the empty column slot) and must not shift the
       table under the reader's hands while they type. -->
  <div v-if="pickerOpen" class="pickscrim" @click.self="closePicker">
    <div class="pickbox card pad" role="dialog" aria-modal="true"
         :aria-label="$t('compare.addPerson')" @keydown.esc="closePicker">
      <div class="pickhead">
        <h2>{{ $t('compare.addPerson') }}</h2>
        <button type="button" class="drop" @click="closePicker"
                :aria-label="$t('compare.close')">×</button>
      </div>
      <input ref="pickerInput" type="search" v-model="pickerQ" @input="onPickerInput"
             :placeholder="$t('reps.searchOtherPlaceholder')"
             :aria-label="$t('reps.searchOtherPlaceholder')" />
      <p class="muted small" v-if="!pickerQ">{{ $t('compare.pickerHint') }}</p>
      <ul class="picklist">
        <li v-for="r in pickerRows" :key="r.person_id">
          <button type="button" class="pickrow" :disabled="resolvedIds.includes(r.person_id)"
                  @click="add(r.person_id)">
            <img class="avatar xs" :src="r.photo_uri || PLACEHOLDER" @error="onImgErr" alt="" />
            <span class="pickname">{{ r.label }}</span>
            <FactionBadge :faction="r.faction" />
            <span v-if="resolvedIds.includes(r.person_id)" class="muted small">{{ $t('compare.alreadyIn') }}</span>
          </button>
        </li>
      </ul>
      <p v-if="pickerLoading" class="muted small">{{ $t('app.loading') }}</p>
      <p v-else-if="!pickerRows.length" class="muted small">{{ $t('reps.noOtherResults') }}</p>
    </div>
  </div>
</template>

<style scoped>
.chead { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.chead h1 { margin-bottom: 0; }
.chead-sub { margin: .4rem 0 1rem; max-width: 70ch; }
.note { border-left: 3px solid var(--line); padding: .4rem .7rem; margin: 0 0 .8rem; }
.note.warn { border-color: #c79a2e; background: #fdf8ec; }
.empty { text-align: center; }
.empty p { margin: 0 0 .6rem; }
.ctools { margin: 0 0 .7rem; }
.ctools label { display: inline-flex; align-items: center; gap: .4rem; cursor: pointer; }

/* The columns scroll sideways as a block; the label column stays pinned so a row
   never loses its name. `overflow-x` on the wrapper (not the table) is what makes
   `position: sticky` work on the cells inside it. */
.cwrap { overflow-x: auto; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); }
.ctable { border-collapse: separate; border-spacing: 0; width: 100%; }
.ctable td, .ctable th { padding: .55rem .7rem; text-align: left; vertical-align: top; border-bottom: 1px solid var(--line); }
.ctable tbody:last-child tr:last-child td, .ctable tbody:last-child tr:last-child th { border-bottom: none; }

.rowhead {
  position: sticky; left: 0; z-index: 2;
  background: var(--bg); font-weight: 600; font-size: .85rem; color: var(--ink-soft);
  min-width: 9.5rem; max-width: 13rem; border-right: 1px solid var(--line);
}
.corner { background: var(--surface); border-right: 1px solid var(--line); }
/* Section rules: the spec sheet's own headings, so four dozen rows read as four
   blocks rather than one wall. */
tr.sec .rowhead {
  position: static; background: var(--accent-soft); color: var(--ink);
  font-size: .8rem; text-transform: uppercase; letter-spacing: .04em;
  border-right: none;
}

.pcol { min-width: 11rem; background: var(--surface); vertical-align: bottom; }
.pcard { display: flex; flex-direction: column; align-items: flex-start; gap: .35rem; position: relative; padding-right: 1.4rem; }
.plink { display: flex; flex-direction: column; gap: .35rem; color: var(--ink); text-decoration: none; }
.plink:hover .pname { text-decoration: underline; color: var(--accent); }
.pcard .avatar { width: 56px; height: 56px; border-radius: 8px; }
.pname { font-weight: 700; line-height: 1.25; }
.drop {
  position: absolute; top: -.2rem; right: -.3rem;
  border: 1px solid var(--line); background: var(--surface); color: var(--ink-faint);
  width: 1.5rem; height: 1.5rem; border-radius: 50%; cursor: pointer;
  font-size: 1rem; line-height: 1; padding: 0;
}
.drop:hover { color: var(--accent); border-color: var(--accent); }

/* The empty column reads as shelf space, not as a person with no data. */
.slot { background: repeating-linear-gradient(135deg, transparent, transparent 7px, rgba(0,0,0,.02) 7px, rgba(0,0,0,.02) 14px); min-width: 9rem; }
.addslot {
  display: flex; flex-direction: column; align-items: center; gap: .3rem;
  width: 100%; padding: .7rem .4rem; cursor: pointer; color: var(--ink-soft);
  background: none; border: 1px dashed var(--line); border-radius: var(--radius);
}
.addslot:hover { border-color: var(--accent); color: var(--accent); }
.plus { font-size: 1.5rem; line-height: 1; }

.val { font-variant-numeric: tabular-nums; font-weight: 600; }
.signval { white-space: nowrap; }
/* "Not applicable" is a statement, so it is spelled out rather than left blank —
   an empty cell reads as missing data (TRUST-1). */
.na { color: var(--ink-faint); font-size: .85rem; font-style: italic; }
td.lead .val { color: var(--accent); }
.leadmark { color: var(--accent); font-size: .7rem; margin-left: .25rem; }
.bar { display: block; height: 5px; margin-top: .35rem; background: var(--line); border-radius: 3px; overflow: hidden; }
.fill { display: block; height: 100%; background: var(--ink-faint); border-radius: 3px; }
td.lead .fill { background: var(--accent); }

.stack { display: flex; height: 9px; border-radius: 3px; overflow: hidden; background: var(--line); }
.seg { display: block; height: 100%; }
.stacklegend { display: flex; flex-wrap: wrap; gap: .4rem; margin-top: .3rem; }
.stacklegend .dot { display: inline-block; width: .55rem; height: .55rem; border-radius: 50%; margin-right: .2rem; }
.links { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: .2rem; }

/* --- csillagjegyek (REP-16) ---
   The last rows of the spec sheet, dressed as what they are: the faintest ink in
   the table and nothing on them bolded or barred that could be read as a ranking.
   The trigger row is a slim strip carrying nothing but the glyph. Collapsed it is
   the table's last line, so its rule comes off — otherwise it would double with
   the container's own border. */
.ztrigger th, .ztrigger td { padding-top: .35rem; padding-bottom: .35rem; }
.ztrigger.zlast th, .ztrigger.zlast td { border-bottom: none; }
.zbody td, .zbody .rowhead { color: var(--ink-faint); font-weight: 400; }
/* The spoiler trigger, identical to the profile's (REP-16): the glyph is the whole
   affordance, so it needs a hit area a thumb can find and a hover state that
   admits it is a control — but not one drop more ink than trivia has earned. */
.zodiac-peek {
  background: none; border: 0; border-radius: 4px; padding: .1rem .25rem;
  margin: -.1rem 0; cursor: pointer; line-height: 1; font-size: 1rem;
  color: var(--ink-faint); opacity: .7;
}
.zodiac-peek:hover, .zodiac-peek:focus-visible,
.zodiac-peek[aria-expanded="true"] { opacity: 1; color: var(--accent); }

/* --- picker --- */
.pickscrim { position: fixed; inset: 0; background: rgba(0,0,0,.35); display: flex; align-items: flex-start; justify-content: center; padding: 6vh 1rem; z-index: 60; }
.pickbox { width: min(30rem, 100%); max-height: 80vh; overflow-y: auto; }
.pickhead { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.pickhead h2 { margin: 0; }
.pickhead .drop { position: static; }
.picklist { list-style: none; padding: 0; margin: .6rem 0 0; }
.pickrow {
  display: flex; align-items: center; gap: .5rem; width: 100%; text-align: left;
  padding: .45rem .5rem; background: none; border: none; border-radius: 8px; cursor: pointer;
}
.pickrow:hover:not(:disabled) { background: var(--accent-soft); }
.pickrow:disabled { opacity: .5; cursor: not-allowed; }
.pickname { font-weight: 600; }
</style>
