<script setup>
// One settlement's page (§6D TEL-8).
//
// Two things make this page rather than a row in a list: the **citations** — every
// count here opens onto the sentence that produced it, and from there the speech and
// the video moment (TEL-4) — and the **representative**, because "who speaks for this
// place" is the question a reader arrives with (REP-10's join).
//
// A settlement with no mentions still has this page, and says so plainly. That page
// *is* the blind spot, which is why it is a normal, linkable, citable page and not a
// 404 (TEL-8).
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { currentCycleLabel, cycleLabel, loadMeta, store } from '../../store.js'
import { formatLongDate } from '../../format.js'
import FactionBadge from '../../components/FactionBadge.vue'
import Pagination from '../../components/Pagination.vue'
import StateBlock from '../../components/StateBlock.vue'
import TrendChart from '../../components/TrendChart.vue'

const props = defineProps({ maz: String, taz: String })
const route = useRoute()
const { t, locale, n: fmtNumber } = useI18n()

const PER_PAGE = 20

const data = ref(null)
const trend = ref(null)
const mentions = ref(null)
const loading = ref(false)
const error = ref(false)
const page = ref(0)

const scopeText = computed(() => {
  const c = currentCycleLabel()
  return c ? t('settlements.scope', { cycle: c }) : t('cycle.scopeAll')
})

// A seat has one holder per cycle, so "who represents this place" is only an answer once
// a cycle is named — and the API answers for the latest cycle in scope. Where this DB has
// no holder on record for a seat in that cycle it is left out rather than answered from
// another cycle, so the page says which of the settlement's constituencies went
// unanswered instead of quietly listing fewer members than it has seats.
const unansweredSeats = computed(() => {
  if (!data.value) return []
  const answered = new Set(data.value.representatives.map((r) => r.constituency))
  return data.value.constituencies.filter((c) => !answered.has(c))
})
const totalPages = computed(() =>
  mentions.value ? Math.ceil(mentions.value.total / PER_PAGE) : 0)

// The link into proceedings search, scoped to this place — the move from "how often"
// to "where exactly it was said" (WCLOUD-4's pattern applied here).
const searchLink = computed(() => {
  if (!data.value) return null
  return { name: 'search', query: { q: data.value.settlement.name } }
})

async function load() {
  loading.value = true; error.value = false
  try {
    const res = await api.settlement(props.maz, props.taz, store.cycles)
    data.value = res
    // The trend and the citations are separate requests from the page itself, so a
    // settlement with thousands of mentions still renders its header immediately.
    api.settlementTrend(props.maz, props.taz, store.cycles)
      .then((r) => { trend.value = r }).catch(() => { trend.value = null })
    loadMentions()
  } catch (e) {
    error.value = e && e.status === 404 ? 'notfound' : true
  } finally {
    loading.value = false
  }
}

async function loadMentions() {
  try {
    mentions.value = await api.settlementMentions(props.maz, props.taz, {
      period: store.cycles, limit: PER_PAGE, offset: page.value * PER_PAGE,
    })
  } catch { mentions.value = null }
}
function goto(p) { page.value = p; loadMentions() }

// The matched form, marked in the sentence exactly as the search results mark a hit
// (SEA-4) — so a reader can see *what* the count was counted from, which for an
// inflected or adjectival form is not obvious.
function parts(m) {
  const text = m.text || ''
  const a = Number.isInteger(m.char_start) ? m.char_start : -1
  const b = Number.isInteger(m.char_end) ? m.char_end : -1
  if (a < 0 || b <= a || b > text.length) return [{ text, hit: false }]
  return [
    { text: text.slice(0, a), hit: false },
    { text: text.slice(a, b), hit: true },
    { text: text.slice(b), hit: false },
  ].filter((p) => p.text)
}

onMounted(async () => {
  await loadMeta().catch(() => {})
  load()
})
watch(() => [props.maz, props.taz], () => { page.value = 0; load() })
watch(() => store.cycles.join(','), () => { page.value = 0; load() })
</script>

<template>
  <p class="small back">
    <RouterLink :to="{ name: 'settlements' }">‹ {{ $t('nav.settlements') }}</RouterLink>
  </p>

  <StateBlock
    :loading="loading" :error="error === true"
    :empty="error === 'notfound'" :empty-text="$t('settlements.notFound')"
    @retry="load"
  >
    <article v-if="data">
      <h1>{{ data.settlement.name }}</h1>
      <p class="muted small sub">
        <span v-if="data.settlement.county">{{ data.settlement.county }}</span>
        <template v-if="data.settlement.electorate">
          <span aria-hidden="true"> · </span>
          {{ $t('settlements.electorate', { n: fmtNumber(data.settlement.electorate) }) }}
        </template>
        <span aria-hidden="true"> · </span>{{ scopeText }}
      </p>

      <!-- The headline count, or the plain statement of silence. A blind spot is
           the finding, so it is written out, not left as a zero (TEL-7/TEL-8). -->
      <section class="card pad counts">
        <template v-if="data.mentions">
          <div class="head">
            <strong class="big">{{ fmtNumber(data.mentions) }}</strong>
            <span class="small muted">{{ $t('settlements.mentionsTotal') }}</span>
          </div>
          <div class="head">
            <strong class="big">{{ fmtNumber(data.speeches) }}</strong>
            <span class="small muted">{{ $t('settlements.inSpeeches') }}</span>
          </div>
          <div class="head">
            <strong class="big">{{ fmtNumber(data.speakers) }}</strong>
            <span class="small muted">{{ $t('settlements.bySpeakers') }}</span>
          </div>
          <p v-if="data.first_date" class="small muted span">
            {{ $t('settlements.between', {
                 first: formatLongDate(data.first_date, locale),
                 last: formatLongDate(data.last_date, locale) }) }}
          </p>
        </template>
        <template v-else>
          <p class="never">{{ $t('settlements.neverInScope') }}</p>
          <p class="small muted">{{ $t('settlements.neverExplain') }}</p>
        </template>
      </section>

      <!-- Why a homonym village reads low: the name needed corroboration before a
           match counted at all, and the page says so rather than leaving the number
           mysterious (TEL-3/TEL-12). -->
      <p v-if="data.settlement.ambiguity" class="card pad note small">
        {{ data.settlement.ambiguity === 'cue'
             ? $t('settlements.ambiguousCue') : $t('settlements.ambiguousSuffix') }}
        <span v-if="data.settlement.ambiguity_reason" class="muted">
          ({{ data.settlement.ambiguity_reason }})
        </span>
      </p>

      <!-- Who speaks for the place. The reason a reader who came for the mention
           count stays on the page (REP-10's join). -->
      <section v-if="data.representatives.length" class="card pad reps">
        <h2 class="shead">{{ $t('settlements.representedBy') }}</h2>
        <ul class="replist">
          <li v-for="r in data.representatives" :key="r.constituency">
            <RouterLink :to="{ name: 'profile', params: { id: r.person_id } }">
              {{ r.name }}
            </RouterLink>
            <FactionBadge v-if="r.faction" :faction="r.faction" />
            <span class="small muted">{{ r.constituency }}</span>
            <!-- The cycle the answer is for. A seat has one holder per cycle, so an
                 undated name is not an answer — and this is what makes it visible that
                 changing the cycle scope changes who is named. -->
            <span v-if="r.period_number" class="small cyc">
              {{ cycleLabel(r.period_number) }}
            </span>
          </li>
        </ul>
        <p v-if="unansweredSeats.length" class="small muted">
          {{ $t('settlements.repUnknownForCycle', { list: unansweredSeats.join(', ') }) }}
        </p>
        <p class="small muted">{{ $t('settlements.constituencyNote') }}</p>
      </section>
      <p v-else-if="data.constituencies.length" class="card pad small muted">
        {{ $t('settlements.constituencyOnly', { list: data.constituencies.join(', ') }) }}
      </p>

      <section v-if="trend && trend.buckets.length" class="card pad">
        <TrendChart
          :buckets="trend.buckets" :granularity="trend.granularity"
          :caption="$t('settlements.trendCaption')"
          :unit="$t('settlements.mentionsShort')"
        />
      </section>

      <section v-if="data.top_speakers.length" class="card pad">
        <h2 class="shead">{{ $t('settlements.whoNamedIt') }}</h2>
        <ul class="spklist">
          <li v-for="s in data.top_speakers" :key="s.person_id">
            <RouterLink :to="{ name: 'profile', params: { id: s.person_id } }">
              {{ s.name }}
            </RouterLink>
            <FactionBadge v-if="s.faction" :faction="s.faction" />
            <span class="small muted num">{{ fmtNumber(s.mentions) }}</span>
          </li>
        </ul>
      </section>

      <!-- The citations. This is what makes every number above evidence (TEL-4). -->
      <section v-if="mentions && mentions.mentions.length" class="card pad">
        <h2 class="shead">{{ $t('settlements.citations') }}</h2>
        <ul class="mlist">
          <li v-for="m in mentions.mentions" :key="m.sentence_id" class="mrow">
            <p class="mtext">
              <template v-for="(p, i) in parts(m)" :key="i">
                <mark v-if="p.hit">{{ p.text }}</mark>
                <span v-else>{{ p.text }}</span>
              </template>
            </p>
            <p class="small muted mmeta">
              <RouterLink v-if="m.person_id"
                          :to="{ name: 'profile', params: { id: m.person_id } }">
                {{ m.name }}
              </RouterLink>
              <span v-else>{{ m.name }}</span>
              <FactionBadge v-if="m.faction" :faction="m.faction" />
              <span aria-hidden="true"> · </span>
              <RouterLink :to="{ name: 'session', params: { id: m.session_id } }">
                {{ formatLongDate(m.date, locale) }}
              </RouterLink>
              <span aria-hidden="true"> · </span>
              <!-- Straight to the moment it was said (VIE-3/VIE-5). -->
              <RouterLink :to="{ name: 'viewer', params: { uid: m.speech_uid },
                                 hash: '#s=' + m.sentence_id }">
                {{ $t('settlements.watch') }}
              </RouterLink>
            </p>
          </li>
        </ul>
        <Pagination :page="page" :total-pages="totalPages" @goto="goto" />
      </section>

      <p v-if="searchLink && data.mentions" class="small searchlink">
        <RouterLink :to="searchLink">
          {{ $t('settlements.searchFor', { name: data.settlement.name }) }} →
        </RouterLink>
      </p>

      <details class="methodology">
        <summary>{{ $t('factions.methodology') }}</summary>
        <p class="small muted">{{ $t('settlements.methodology') }}</p>
        <p v-if="data.source" class="small muted">
          {{ $t('settlements.sourceNote', { source: data.source.geography }) }}
        </p>
      </details>
    </article>
  </StateBlock>
</template>

<style scoped>
.back { margin: 0 0 .4rem; }
.sub { margin: -.5rem 0 1rem; }
.counts { display: flex; flex-wrap: wrap; gap: .4rem 2rem; align-items: baseline; }
.head { display: flex; flex-direction: column; gap: .1rem; }
.big { font-size: 1.5rem; font-variant-numeric: tabular-nums; }
.span { flex: 1 1 100%; margin: .2rem 0 0; }
.never { margin: 0; font-size: 1.05rem; font-weight: 600; }
.note { margin-top: .6rem; }
section.card { margin-top: 1rem; }
.shead { font-size: 1rem; margin: 0 0 .5rem; color: var(--ink-soft); }
.replist, .spklist, .mlist { list-style: none; padding: 0; margin: 0; }
.replist li, .spklist li {
  display: flex; align-items: center; gap: .45rem; flex-wrap: wrap;
  padding: .25rem 0; border-bottom: 1px solid var(--line);
}
.replist li:last-child, .spklist li:last-child { border-bottom: 0; }
/* The cycle the answer belongs to, pushed to the end of the row: it qualifies the name
   rather than competing with it. */
.cyc { margin-left: auto; color: var(--ink-faint); font-variant-numeric: tabular-nums; }
.spklist .num { margin-left: auto; font-variant-numeric: tabular-nums; }
.mlist { display: grid; gap: .7rem; }
.mrow { padding-bottom: .6rem; border-bottom: 1px solid var(--line); }
.mrow:last-of-type { border-bottom: 0; }
.mtext { margin: 0 0 .2rem; }
.mtext mark { background: var(--mark); color: var(--mark-ink); }
.mmeta { margin: 0; display: flex; align-items: center; gap: .3rem; flex-wrap: wrap; }
.searchlink { margin-top: .8rem; }
.methodology { margin-top: 1rem; }
.methodology summary { cursor: pointer; font-weight: 600; color: var(--ink-soft); font-size: .85rem; }
</style>
