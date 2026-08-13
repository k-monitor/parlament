<script setup>
// One tárca's profile (§6C / MIN-6): who held it, what the House put to it, what
// it laid before the House, and what it said in plenary.
//
// The two document panels are the ordinary iromány list scoped to this tárca
// (`/api/v1/bills?portfolio=…`) — the same endpoint, filters and row shape the
// rest of the site uses (MIN-7), so there is one list implementation, not three.
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { store, loadMeta, currentCycleLabel } from '../../store.js'
import { formatDate, formatDateLocal, formatDuration } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import Pagination from '../../components/Pagination.vue'
import TrendChart from '../../components/TrendChart.vue'
import FactionBadge from '../../components/FactionBadge.vue'

const props = defineProps({ slug: { type: String, required: true } })
const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const PAGE = 20
// Which panel is open is per-page URL state (§CYC-5), so a link can point at a
// tárca's submitted irományok rather than always at its answers.
const TABS = ['answered', 'submitted', 'speeches']

const data = ref(null)
const trend = ref(null)
const list = ref(null)
const loading = ref(false)
const error = ref(false)
const listLoading = ref(false)
const listError = ref(false)

const tab = computed(() => (TABS.includes(route.query.tab) ? route.query.tab : 'answered'))
// A ministry's state secretaries run to dozens over several cycles, and listed
// whole they push the page's actual content (the documents, the chart) below the
// fold. Show a few per rank and let the reader open the rest.
const HOLDERS_SHOWN = 6
const allHolders = ref(false)
const page = computed(() => Math.floor((Number(route.query.offset) || 0) / PAGE))
const totalPages = computed(() => (list.value ? Math.ceil(list.value.total / PAGE) : 0))

const scopeText = computed(() => {
  const c = currentCycleLabel()
  return c ? t('portfolios.scope', { cycle: c }) : t('cycle.scopeAll')
})

// Office holders split by rank, so the minister is not buried among a dozen
// state secretaries. Anything the registry files elsewhere keeps its own group.
const holderGroups = computed(() => {
  const all = (data.value && data.value.holders) || []
  // `senior` is the head of a body that is not a ministry (the MNB's governor,
  // the ombudsman, the legfőbb ügyész); left in with the ordinary offices, the
  // governor and their deputies shared one heading.
  const order = ['pm', 'minister', 'senior', 'state-secretary']
  const groups = []
  for (const cat of order) {
    const items = all.filter((h) => h.category === cat)
    if (items.length) groups.push({ cat, items })
  }
  const rest = all.filter((h) => !order.includes(h.category))
  if (rest.length) groups.push({ cat: 'other', items: rest })
  return groups.map((g) => ({
    ...g,
    shown: allHolders.value ? g.items : g.items.slice(0, HOLDERS_SHOWN),
    hidden: allHolders.value ? 0 : Math.max(0, g.items.length - HOLDERS_SHOWN),
  }))
})
const hiddenHolders = computed(() =>
  holderGroups.value.reduce((n, g) => n + g.hidden, 0))

// "2022.05.24 – jelenleg": an office still held has no end date, and the last
// day of the data is never presented as a departure (REP-2).
function term(h) {
  const start = formatDateLocal(h.date_start)
  const end = h.date_end ? formatDateLocal(h.date_end) : t('profile.present')
  return `${start || '?'} – ${end}`
}

function setTab(name) {
  router.push({ name: 'portfolio', params: { slug: props.slug },
                query: { ...route.query, tab: name === 'answered' ? undefined : name,
                         offset: undefined } })
}
function gotoPage(p) {
  router.push({ name: 'portfolio', params: { slug: props.slug },
                query: { ...route.query, offset: p * PAGE } })
}

let headSeq = 0
async function loadHead() {
  const seq = ++headSeq
  loading.value = true; error.value = false
  try {
    const [d, tr] = await Promise.all([
      api.portfolio(props.slug, store.cycles),
      api.portfolioTrend(props.slug, store.cycles).catch(() => null),
    ])
    if (seq === headSeq) { data.value = d; trend.value = tr }
  } catch {
    if (seq === headSeq) error.value = true
  } finally {
    if (seq === headSeq) loading.value = false
  }
}

let listSeq = 0
async function loadList() {
  const seq = ++listSeq
  listLoading.value = true; listError.value = false
  const offset = Number(route.query.offset) || 0
  try {
    const res = tab.value === 'speeches'
      ? await api.portfolioSpeeches(props.slug,
          { period: store.cycles, limit: PAGE, offset })
      // `portfolio_period`, not the generic `period`: the tárca's counts are by
      // the cycle it *acted* in, and the list has to be the same set (§6C).
      : await api.bills({ portfolio: props.slug, portfolio_role: tab.value,
                          portfolio_period: store.cycles, sort: 'date',
                          limit: PAGE, offset })
    if (seq === listSeq) list.value = res
  } catch {
    if (seq === listSeq) listError.value = true
  } finally {
    if (seq === listSeq) listLoading.value = false
  }
}

const rows = computed(() => {
  if (!list.value) return []
  return tab.value === 'speeches' ? list.value.speeches : list.value.bills
})

// Counts for the tab labels, straight from the header aggregate so switching
// tabs never has to fetch to know how many there are.
function tabCount(name) {
  if (!data.value) return null
  return { answered: data.value.answered, submitted: data.value.submitted,
           speeches: data.value.speeches }[name]
}

onMounted(async () => {
  await loadMeta().catch(() => {})
  loadHead(); loadList()
})
watch(() => props.slug, () => { loadHead(); loadList() })
watch(() => [route.query.tab, route.query.offset].join('|'), loadList)
watch(() => store.cycles.join(','), () => { loadHead(); loadList() })
</script>

<template>
  <p class="small">
    <RouterLink :to="{ name: 'portfolios' }">‹ {{ $t('portfolios.backToList') }}</RouterLink>
  </p>

  <StateBlock :loading="loading" :error="error" @retry="loadHead">
    <div v-if="data">
      <h1>{{ data.name }}</h1>
      <p class="muted small" style="margin:-.4rem 0 1rem;">
        {{ $t('portfolios.kinds.' + data.kind) }} · {{ scopeText }}
      </p>

      <!-- The three headline figures, each linking into the panel behind it. -->
      <div class="statrow">
        <button type="button" class="card pad statcard" @click="setTab('answered')">
          <strong>{{ data.answered }}</strong>
          <span class="small muted">{{ $t('portfolios.answered') }}</span>
        </button>
        <button type="button" class="card pad statcard" @click="setTab('submitted')">
          <strong>{{ data.submitted }}</strong>
          <span class="small muted">{{ $t('portfolios.submitted') }}</span>
        </button>
        <button type="button" class="card pad statcard" @click="setTab('speeches')">
          <strong>{{ data.speeches }}</strong>
          <span class="small muted">{{ $t('portfolios.speeches') }}</span>
        </button>
        <!-- Median, not mean: a handful of questions answered years late would
             otherwise stand in for how long a ministry usually takes. -->
        <div v-if="data.response_time" class="card pad statcard static">
          <strong>{{ data.response_time.median_days }}</strong>
          <span class="small muted">{{ $t('portfolios.medianDays') }}</span>
        </div>
      </div>

      <!-- What the corpus cannot say yet, said plainly rather than left to be
           inferred from a number that looks complete (MIN-10a). -->
      <p class="muted small note">{{ $t('portfolios.answeredNote') }}</p>

      <section v-if="holderGroups.length" class="card pad holders">
        <h2>{{ $t('portfolios.holders') }}</h2>
        <div v-for="g in holderGroups" :key="g.cat" class="hgroup">
          <h3 class="small muted">{{ $t('officials.categories.' + g.cat) }}</h3>
          <ul>
            <li v-for="(h, i) in g.shown" :key="h.person_id + '-' + i">
              <RouterLink :to="{ name: 'profile', params: { id: h.person_id } }">{{ h.name }}</RouterLink>
              <span class="small muted">{{ h.title }} · {{ term(h) }}</span>
            </li>
          </ul>
        </div>
        <button
          v-if="hiddenHolders || allHolders" type="button" class="btn secondary small"
          @click="allHolders = !allHolders"
        >
          {{ allHolders ? $t('portfolios.holdersFewer')
                        : $t('portfolios.holdersMore', { count: hiddenHolders }) }}
        </button>
      </section>

      <section v-if="trend && trend.buckets && trend.buckets.length > 1" class="card pad">
        <h2>{{ $t('portfolios.trendTitle') }}</h2>
        <TrendChart
          :buckets="trend.buckets" :granularity="trend.granularity"
          :caption="$t('portfolios.trendCaption')" :unit="$t('portfolios.answeredShort')"
        />
      </section>

      <div class="tabbar" role="tablist">
        <button
          v-for="name in TABS" :key="name" type="button" role="tab"
          class="tabbtn" :class="{ active: tab === name }" :aria-selected="tab === name"
          @click="setTab(name)"
        >
          {{ $t('portfolios.' + name) }}
          <span v-if="tabCount(name) !== null" class="muted small">({{ tabCount(name) }})</span>
        </button>
      </div>

      <StateBlock
        :loading="listLoading" :error="listError"
        :empty="!!list && rows.length === 0" :empty-text="$t('portfolios.emptyPanel')"
        @retry="loadList"
      >
        <div v-if="list">
          <!-- irományok: same row shape as the bills / egyéb irományok lists -->
          <ul v-if="tab !== 'speeches'" class="billlist">
            <li v-for="b in rows" :key="b.id" class="card pad billcard">
              <div class="billhead">
                <RouterLink :to="{ name: 'document', params: { id: b.id } }" class="billnum">
                  {{ b.bill_number }}
                </RouterLink>
                <span class="badge" v-if="b.type">{{ b.type }}</span>
                <span class="badge status" v-if="b.status">{{ b.status }}</span>
                <span class="muted small" v-if="b.submitted_date">{{ formatDate(b.submitted_date) }}</span>
              </div>
              <RouterLink :to="{ name: 'document', params: { id: b.id } }" class="billtitle">
                {{ b.title }}
              </RouterLink>
              <div class="sponsors small" v-if="b.sponsors && b.sponsors.length">
                <template v-for="(s, i) in b.sponsors" :key="i">
                  <RouterLink v-if="s.person_id" :to="{ name: 'profile', params: { id: s.person_id } }">
                    {{ s.name }}
                  </RouterLink>
                  <span v-else>{{ s.name }}</span>
                  <FactionBadge v-if="s.faction" :faction="s.faction" />
                  <span v-if="i < b.sponsors.length - 1" aria-hidden="true">·</span>
                </template>
              </div>
            </li>
          </ul>

          <!-- speeches: who spoke, in which office, and where to watch it -->
          <ul v-else class="billlist">
            <li v-for="s in rows" :key="s.uid" class="card pad billcard">
              <div class="billhead">
                <RouterLink :to="{ name: 'viewer', params: { uid: s.uid } }" class="billnum">
                  {{ formatDate(s.date) }}
                </RouterLink>
                <span class="badge" v-if="s.type">{{ s.type }}</span>
                <span class="muted small" v-if="s.duration">{{ formatDuration(s.duration) }}</span>
              </div>
              <RouterLink :to="{ name: 'viewer', params: { uid: s.uid } }" class="billtitle">
                {{ s.agenda_title || $t('portfolios.noAgenda') }}
              </RouterLink>
              <div class="sponsors small">
                <RouterLink v-if="s.person_id" :to="{ name: 'profile', params: { id: s.person_id } }">
                  {{ s.name }}
                </RouterLink>
                <span v-else>{{ s.name }}</span>
                <span class="muted">· {{ s.office }}</span>
              </div>
            </li>
          </ul>

          <p v-if="tab === 'speeches' && data.speech_coverage_partial" class="muted small note">
            {{ $t('portfolios.speechCoverage') }}
          </p>

          <Pagination :page="page" :total-pages="totalPages" @goto="gotoPage" />
        </div>
      </StateBlock>

      <details class="methodology" style="margin-top:1rem;">
        <summary>{{ $t('factions.methodology') }}</summary>
        <p class="small muted">{{ $t('portfolios.methodology') }}</p>
        <!-- The labels this tárca was collated from, so a reader can check the
             grouping rather than take it on trust (TRUST-1). -->
        <p class="small muted" v-if="data.aliases && data.aliases.length">
          <strong>{{ $t('portfolios.aliases') }}:</strong> {{ data.aliases.join(' · ') }}
        </p>
      </details>
    </div>
  </StateBlock>
</template>

<style scoped>
/* .billlist/.billcard/.billhead/.billnum/.billtitle/.sponsors mirror the bills
   and documents lists; kept local rather than lifted, since those pages own them. */
.statrow { display: flex; flex-wrap: wrap; gap: .6rem; margin-bottom: .6rem; }
.statcard {
  display: flex; flex-direction: column; gap: .1rem; align-items: flex-start;
  min-width: 8.5rem; text-align: left; border: 1px solid var(--line);
  background: var(--surface); cursor: pointer; font: inherit;
}
.statcard.static { cursor: default; }
.statcard strong { font-size: 1.5rem; font-variant-numeric: tabular-nums; }
.statcard:not(.static):hover { border-color: var(--accent); }
.note { max-width: 68ch; margin: 0 0 1rem; }
.holders h2, section h2 { font-size: 1rem; margin: 0 0 .5rem; }
.hgroup { margin-bottom: .6rem; }
.hgroup h3 { margin: 0 0 .2rem; font-size: .8rem; text-transform: uppercase; letter-spacing: .03em; }
.hgroup ul { list-style: none; padding: 0; margin: 0; display: grid; gap: .2rem; }
.hgroup li { display: flex; gap: .5rem; flex-wrap: wrap; align-items: baseline; }
.tabbar { display: flex; gap: .3rem; flex-wrap: wrap; margin: 1.2rem 0 .8rem; }
.tabbtn {
  background: none; border: 1px solid var(--line); border-radius: 999px;
  padding: .3rem .8rem; font: inherit; cursor: pointer; color: var(--ink-soft);
}
.tabbtn.active { border-color: var(--accent); color: var(--accent); font-weight: 600; }
.billlist { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .6rem; }
.billhead { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap; }
.billnum { font-weight: 600; }
.billtitle { display: block; margin: .3rem 0; }
.sponsors { display: flex; gap: .35rem; flex-wrap: wrap; align-items: center; }
.methodology summary { cursor: pointer; font-weight: 600; color: var(--ink-soft); font-size: .85rem; }
</style>
