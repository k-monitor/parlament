<script setup>
// Témák (TOPIC-9) — what the Parliament is *about*, as an agenda rather than as a
// chip on one speech.
//
// The site already labels every speech and every readable iromány with a CAP
// policy topic (§5.8). Those labels answer "what is this?" one item at a time;
// this page adds up the same stored blocks, at the same read-time threshold, and
// answers the question the corpus can only answer in aggregate: *what does this
// House spend itself on, and has that changed?*
//
// It is the one analysis that reads two modules. The chart's two series are the
// two agendas — what is said on the floor (`/proceedings/topics`) and what is put
// before the House (`/bills/topics`) — and they are deliberately not merged: they
// come from different texts, they cover different populations, and the difference
// between them is the finding. Bills switching off (EXT-6), or an iromány pass
// that never ran, costs the page a series and nothing else; the speech half is
// what the page is registered against.
//
// Everything the reader chooses lives in the URL — `?topic=` for the opened
// topic, `?by=bills` for the ranking — so a claim about one topic is linkable and
// citable (§4D/TRUST-1), and both are absent at their defaults so the page's
// canonical address stays parameter-free (§SEO-2).
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { store, loadMeta, currentCycleLabel, periodLabel } from '../../store.js'
import { keepScrollOnce } from '../../lib/scrollMemory.js'
import { topicGlyph, topicName as capName } from '../../lib/topics.js'
import StateBlock from '../../components/StateBlock.vue'
import TopicMixChart from '../../components/TopicMixChart.vue'
import TrendChart from '../../components/TrendChart.vue'
import SpeakerLink from '../../components/SpeakerLink.vue'
import EmbedButton from '../../components/EmbedButton.vue'
import HelpTip from '../../components/HelpTip.vue'

const { t, te } = useI18n()
const route = useRoute()
const router = useRouter()


const speech = ref(null)       // the floor's mix, or null when it cannot be had
const bill = ref(null)         // the irományok's mix, or null when absent
// Starts true so the first paint is the spinner: the empty state below is a
// claim about the deployment ("nothing here is classified"), and flashing it for
// a tick before the request has even left would be a lie the reader can read.
const loading = ref(true)
const error = ref(false)
// Distinguishes "this deployment has no topics" from "the request failed": the
// first is a thing to explain, the second a thing to retry.
const unclassified = ref(false)

const detail = ref(null)
const detailLoading = ref(false)
const detailError = ref(false)

const selected = computed(() => {
  const raw = route.query.topic
  return typeof raw === 'string' ? raw : ''
})
const sortBy = computed(() => (route.query.by === 'bills' ? 'bill' : 'speech'))

const topicName = (label) => capName(label, t, te)
const pct = (v, digits = 1) =>
  `${((v || 0) * 100).toLocaleString('hu-HU', { maximumFractionDigits: digits })}%`
const num = (v) => (v || 0).toLocaleString('hu-HU')

const scopeLabel = computed(() => {
  const c = currentCycleLabel()
  return c ? t('cycle.scope', { cycle: c }) : t('cycle.scopeAll')
})

// ---------------------------------------------------------------------------
// loading
// ---------------------------------------------------------------------------
// Rapid cycle switches leave fetches racing; only the latest may write state.
let loadSeq = 0

async function load() {
  const seq = ++loadSeq
  loading.value = true
  error.value = false
  unclassified.value = false
  try {
    // The two halves are independent: a server can carry speech topics and no
    // iromány ones (that is the state before the document cache is first shipped
    // across), so the document half failing must cost a series, not the page.
    const [floor, docs] = await Promise.all([
      api.topicMix(store.cycles),
      store.moduleEnabled('bills')
        ? api.billTopicMix(store.cycles).catch(() => null)
        : Promise.resolve(null),
    ])
    if (seq !== loadSeq) return
    speech.value = floor
    bill.value = docs
  } catch (e) {
    if (seq !== loadSeq) return
    speech.value = null
    bill.value = null
    // 503 is this endpoint's "nothing was ever classified here" (TOPIC-7): a
    // deployment that ran no model and was shipped no cache.
    if (e && e.status === 503) unclassified.value = true
    else error.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
  if (seq === loadSeq && selected.value) loadDetail()
}

let detailSeq = 0

async function loadDetail() {
  const label = selected.value
  if (!label) { detail.value = null; return }
  const seq = ++detailSeq
  detailLoading.value = true
  detailError.value = false
  try {
    const res = await api.topicDetail(label, store.cycles)
    if (seq === detailSeq) detail.value = res
  } catch (e) {
    if (seq !== detailSeq) return
    detail.value = null
    // A `?topic=` naming something that is not a CAP topic (a stale link, a typo)
    // is answered 404 by the API. That is not an error to retry — the page simply
    // has no such topic to open, so the selection is dropped and the reader is
    // left on the chart rather than in front of a failure they cannot fix.
    if (e && e.status === 404) router.replace({ query: { ...route.query, topic: undefined } })
    else detailError.value = true
  } finally {
    if (seq === detailSeq) detailLoading.value = false
  }
}

function select(label) {
  const next = label === selected.value ? undefined : label
  // This page only ever rewrites its own query, so it has to claim its scroll
  // position — otherwise the router's "a new address starts at the top" rule
  // would throw the reader back up the chart they just clicked in.
  keepScrollOnce(route.path)
  router.replace({ query: { ...route.query, topic: next } })
}

function setSort(by) {
  if (by === sortBy.value) return
  keepScrollOnce(route.path)
  router.replace({ query: { ...route.query, by: by === 'bill' ? 'bills' : undefined } })
}

onMounted(() => { loadMeta().catch(() => {}).finally(load) })
watch(() => store.cycles.join(','), load)
watch(selected, loadDetail)

// ---------------------------------------------------------------------------
// the chart's framing
// ---------------------------------------------------------------------------

// How much of each side carries a topic at all. This is the first thing on the
// page because every share below it is a share *of the labelled part* — a reader
// who is not told that a third of the floor went unlabelled will read the chart
// as a census (TRUST-1).
const coverage = computed(() => {
  const s = speech.value?.coverage
  const b = bill.value?.coverage
  return {
    speech: s && s.speeches
      ? { labelled: s.labelled, total: s.speeches, share: s.labelled / s.speeches }
      : null,
    bill: b && b.with_text
      ? { labelled: b.labelled, total: b.with_text, share: b.labelled / b.with_text,
          bills: b.bills }
      : null,
  }
})

const threshold = computed(() =>
  Math.round((speech.value?.threshold ?? bill.value?.threshold ?? 0.9) * 100))

const hasBills = computed(() => !!bill.value?.topics?.length)

// The iframe snippet bakes in a fixed height, so it has to be told how tall the
// figure actually is: one row per topic *present in the scope* (a thin cycle has
// fewer than 21), at the measured row height — 52 px with both series, 32 with
// one — plus the embed frame's own title and attribution rows. Measured, not
// guessed; a few pixels of slack costs white space, too few clips the last row.
const embedHeight = computed(() => {
  const labels = new Set([
    ...(speech.value?.topics || []).map((x) => x.label),
    ...(bill.value?.topics || []).map((x) => x.label),
  ])
  return (hasBills.value ? 170 : 145) + labels.size * (hasBills.value ? 52 : 32)
})

// ---------------------------------------------------------------------------
// the opened topic
// ---------------------------------------------------------------------------

const selectedRow = computed(() => ({
  speech: (speech.value?.topics || []).find((x) => x.label === selected.value) || null,
  bill: (bill.value?.topics || []).find((x) => x.label === selected.value) || null,
}))

// The topic's share of each year's policy text, as a percentage — a *share*, not
// a word count, so a quiet year reads as the shape of its agenda rather than as a
// lull. The site's one histogram renders it unchanged (SEA-8's bucket shape).
const trendBuckets = computed(() => {
  const buckets = speech.value?.trend?.buckets || []
  if (!selected.value) return []
  return buckets.map((b) => ({
    period: b.period,
    hits: b.policy_words
      ? Math.round(((b.labels[selected.value] || 0) / b.policy_words) * 1000) / 10
      : 0,
  }))
})

// Cycle boundaries, drawn only when the axis actually spans more than one cycle
// (the same rule the search trend follows).
const cycleMarkers = computed(() => {
  if (store.cycles.length === 1 || !store.meta) return []
  const inScope = (p) => !store.cycles.length || store.cycles.includes(p.number)
  const out = []
  for (const p of (store.meta.periods || []).filter(inScope)) {
    const label = periodLabel(p)
    if (p.date_start) out.push({ date: p.date_start, label })
    if (p.date_end) out.push({ date: p.date_end, label })
  }
  return out
})

// Factions ordered by how much of *their own* floor time the topic takes, which
// is what makes a small faction's specialism visible; the big faction's size
// shows up in the second number (its slice of everything said about it).
const factions = computed(() => (detail.value?.factions || [])
  .filter((f) => f.words > 0)
  .slice()
  .sort((a, b) => b.share_of_own - a.share_of_own))

const factionMax = computed(() =>
  Math.max(0.01, ...factions.value.map((f) => f.share_of_own)))

const speakers = computed(() => (detail.value?.speakers || []).map((s) => ({
  ...s, label: s.name,
})))
</script>

<template>
  <div class="sechead">
    <h1>{{ $t('topics.page.title') }}</h1>
    <HelpTip :label="$t('topics.page.title')">
      <p>{{ $t('topics.page.help1', { threshold }) }}</p>
      <p>{{ $t('topics.page.help2') }}</p>
    </HelpTip>
  </div>
  <p class="muted lead">{{ $t('topics.page.lead') }}</p>

  <StateBlock
    :loading="loading" :error="error"
    :empty="unclassified || (!loading && !speech)"
    :empty-text="$t('topics.page.unclassified')"
    @retry="load"
  >
    <div v-if="speech">
      <p class="muted small cov">
        <span>{{ scopeLabel }}</span>
        <span v-if="coverage.speech">
          · {{ $t('topics.page.coverageSpeech', {
            pct: pct(coverage.speech.share, 0), n: num(coverage.speech.labelled),
            total: num(coverage.speech.total) }) }}
        </span>
        <span v-if="coverage.bill">
          · {{ $t('topics.page.coverageBill', {
            pct: pct(coverage.bill.share, 0), n: num(coverage.bill.labelled),
            total: num(coverage.bill.total) }) }}
        </span>
      </p>

      <div class="card pad chartcard">
        <div v-if="hasBills" class="sortrow">
          <span class="small muted">{{ $t('topics.page.sortBy') }}</span>
          <div class="segmented" role="group" :aria-label="$t('topics.page.sortBy')">
            <button
              type="button" class="btn secondary small" :aria-pressed="sortBy === 'speech'"
              :class="{ on: sortBy === 'speech' }" @click="setSort('speech')"
            >{{ $t('topicMix.speeches') }}</button>
            <button
              type="button" class="btn secondary small" :aria-pressed="sortBy === 'bill'"
              :class="{ on: sortBy === 'bill' }" @click="setSort('bill')"
            >{{ $t('topicMix.bills') }}</button>
          </div>
        </div>

        <TopicMixChart
          :speech="speech" :bill="bill" :sort-by="sortBy" :selected="selected"
          :caption="$t('topics.page.chartCaption')"
          @select="select"
        />

        <div class="fig-foot">
          <p class="muted small hint">{{ $t('topics.page.clickHint') }}</p>
          <EmbedButton
            kind="topic-mix" :title="$t('topics.page.title')"
            :params="{ by: sortBy === 'bill' ? 'bills' : undefined }"
            :height="embedHeight" :max-width="760"
          />
        </div>
      </div>

      <!-- the opened topic -->
      <section v-if="selected" class="panel">
        <div class="panelhead">
          <h2>
            <span class="pglyph" aria-hidden="true">{{ topicGlyph(selected) }}</span>
            {{ topicName(selected) }}
            <span v-if="selectedRow.speech?.code || selectedRow.bill?.code" class="pcode">
              CAP {{ selectedRow.speech?.code ?? selectedRow.bill?.code }}
            </span>
          </h2>
          <button type="button" class="btn secondary small" @click="select(selected)">
            ✕ {{ $t('topics.page.close') }}
          </button>
        </div>

        <p class="plead">
          <template v-if="selectedRow.speech">
            {{ $t('topics.page.leadSpeech', {
              pct: pct(selectedRow.speech.share), n: num(selectedRow.speech.count) }) }}
          </template>
          <template v-else>{{ $t('topics.page.leadNoSpeech') }}</template>
          <!-- Vue drops the newline between two sibling templates, so the two
               sentences need a space of their own. -->
          {{ ' ' }}
          <template v-if="selectedRow.bill">
            {{ $t('topics.page.leadBill', {
              pct: pct(selectedRow.bill.share), n: num(selectedRow.bill.count) }) }}
          </template>
        </p>

        <div v-if="trendBuckets.length > 1" class="subcard">
          <TrendChart
            :buckets="trendBuckets" granularity="year" :markers="cycleMarkers"
            :caption="$t('topics.page.trendCaption', { topic: topicName(selected) })"
            unit="%"
          />
        </div>

        <StateBlock
          :loading="detailLoading" :error="detailError"
          :empty="!!detail && !factions.length && !speakers.length"
          :empty-text="$t('topics.page.noDetail')"
          @retry="loadDetail"
        >
          <div v-if="detail" class="split">
            <div v-if="factions.length" class="subcard">
              <h3>{{ $t('topics.page.factions') }}</h3>
              <p class="small muted">{{ $t('topics.page.factionsNote') }}</p>
              <ul class="fbars">
                <li v-for="f in factions" :key="f.id ?? 'none'">
                  <span class="fname">
                    <span
                      class="fdot" aria-hidden="true"
                      :style="{ background: f.color || 'var(--ink-faint)' }"
                    />
                    {{ f.label || $t('topics.page.noFaction') }}
                  </span>
                  <span class="ftrack" aria-hidden="true">
                    <span
                      class="ffill"
                      :style="{ width: ((f.share_of_own / factionMax) * 100) + '%',
                                background: f.color || 'var(--ink-faint)' }"
                    />
                  </span>
                  <span class="fval">{{ pct(f.share_of_own) }}</span>
                  <span class="fshare small muted">
                    {{ $t('topics.page.shareOfTopic', { pct: pct(f.share_of_topic, 0) }) }}
                  </span>
                </li>
              </ul>
            </div>

            <div v-if="speakers.length" class="subcard">
              <h3>{{ $t('topics.page.speakers') }}</h3>
              <p class="small muted">{{ $t('topics.page.speakersNote') }}</p>
              <ol class="speakers">
                <li v-for="s in speakers" :key="s.person_id">
                  <SpeakerLink :speaker="s" />
                  <span class="sval small muted">{{ pct(s.share_of_topic, 0) }}</span>
                </li>
              </ol>
            </div>
          </div>
        </StateBlock>

        <!-- The one drill-down the corpus can honour today: the iromány list
             filters by dominant topic (TOPIC-8). There is no such filter for
             speeches yet (§10), so no link pretends there is. -->
        <p v-if="store.moduleEnabled('bills') && selectedRow.bill" class="plinks">
          <RouterLink :to="{ name: 'bills', query: { topic: selected } }">
            {{ $t('topics.page.openBills') }} →
          </RouterLink>
        </p>
      </section>

      <p class="small soft note">{{ $t('topics.page.method', { threshold }) }}</p>
    </div>
  </StateBlock>
</template>

<style scoped>
/* The page title with its methodology tip beside it, as on every other analysis. */
.sechead { display: flex; align-items: center; gap: .35rem; }
.sechead h1 { margin: 0; }
.lead { max-width: 70ch; }
.cov { margin: .2rem 0 .8rem; }
.chartcard { position: relative; }   /* EmbedButton anchors to this corner */

.sortrow {
  display: flex; align-items: center; gap: .6rem;
  flex-wrap: wrap; margin-bottom: .7rem;
}
.segmented { display: flex; gap: .3rem; }
.segmented .on { border-color: var(--accent); color: var(--accent); }

.fig-foot { display: flex; align-items: flex-end; justify-content: space-between; gap: 1rem; }
.hint { margin: .6rem 0 0; }

.panel { margin-top: 1.4rem; }
.panelhead {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: 1rem; flex-wrap: wrap;
}
.panelhead h2 { margin: 0; }
.pglyph { margin-right: .25rem; }
.pcode { font-size: .7em; font-weight: 400; color: var(--ink-faint); margin-left: .35rem; }
.plead { max-width: 70ch; color: var(--ink-soft); margin: .4rem 0 1rem; }

.subcard {
  background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 1rem; margin-bottom: 1rem;
}
.subcard h3 { margin: 0 0 .2rem; font-size: 1rem; }
.subcard p { margin: 0 0 .7rem; }

/* Two panels side by side on a wide screen, stacked on a narrow one. */
.split { display: grid; grid-template-columns: 1fr; gap: 1rem; }
@media (min-width: 780px) {
  .split { grid-template-columns: 3fr 2fr; }
  .split .subcard { margin-bottom: 0; }
}

.fbars { list-style: none; margin: 0; padding: 0; }
.fbars li {
  display: grid; grid-template-columns: minmax(6rem, 34%) 1fr 3rem;
  align-items: center; gap: .2rem .6rem; margin-bottom: .35rem;
}
.fname { display: flex; align-items: center; gap: .4rem; font-size: .86rem; }
.fdot { width: .6rem; height: .6rem; border-radius: 2px; flex: none; }
.ftrack { height: 9px; border-radius: 4px; background: #eceae4; overflow: hidden; }
.ffill { display: block; height: 100%; border-radius: 4px; }
.fval { font-size: .8rem; font-variant-numeric: tabular-nums; text-align: right; }
/* The second number sits under the bar rather than beside it: three figures in
   one row is a table, and this is a reading aid. */
.fshare { grid-column: 2 / -1; }

.speakers { list-style: none; margin: 0; padding: 0; }
.speakers li {
  display: flex; align-items: center; justify-content: space-between;
  gap: .6rem; padding: .18rem 0;
}
.sval { font-variant-numeric: tabular-nums; }

.plinks { margin-top: 1rem; }
.note { display: block; max-width: 70ch; margin-top: 1.4rem; }
</style>
