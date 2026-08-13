<script setup>
// Tárcák — the government side of the record (§6C / MIN-5).
//
// A sibling page of the office holders (REP-11): that page answers "who held
// this post and when", this one answers what the *institution* did — what the
// House put to it and what it put to the House. Grouped by kind rather than
// paginated: there are under a hundred tárcák in the whole corpus, and a reader
// wants to see the government at once, ministries first and the independent
// bodies that answer to the House last.
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { store, loadMeta, currentCycleLabel } from '../../store.js'
import { createSearchClicks } from '../../lib/searchClicks.js'
import StateBlock from '../../components/StateBlock.vue'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const data = ref(null)
const loading = ref(false)
const error = ref(false)

const f = reactive({ q: route.query.q || '' })

const scopeText = computed(() => {
  const c = currentCycleLabel()
  return c ? t('portfolios.scope', { cycle: c }) : t('cycle.scopeAll')
})

// Grouped in the order the API gives (ministries → PM → portfolio-less →
// other → independent bodies); each group keeps the corpus-weight ordering.
const groups = computed(() => {
  if (!data.value) return []
  const out = data.value.kinds.map((kind) => ({
    kind,
    items: data.value.portfolios.filter((p) => p.kind === kind),
  })).filter((g) => g.items.length)
  // Carry each tárca's position in the *rendered* order (the list is grouped by
  // kind rather than paginated), so the search-quality ping can say how far down
  // an opened tárca actually sat.
  let n = 0
  for (const g of out) {
    g.items = g.items.map((p) => {
      const all = collapseLeads(p.holders)
      const leads = all.slice(0, LEADS_SHOWN)
      return { ...p, rank: n++, leads,
               leadsHidden: all.length - leads.length,
               leadTitle: sharedTitle(leads) }
    })
  }
  return out
})

// The speeches column is only meaningful for the cycles whose speeches carry the
// speaker's office at all (MIN-10). Where the scope reaches beyond them, the page
// says so instead of letting a zero read as "this ministry never spoke".
const speechesPartial = computed(() => {
  if (!data.value) return false
  const covered = data.value.speech_coverage || []
  const scope = store.cycles.length ? store.cycles : covered
  return scope.some((p) => !covered.includes(p))
})

function apply() {
  const query = {}
  if (f.q) query.q = f.q
  router.push({ name: 'portfolios', query })
}

// ---------------------------------------------------------------------------
// The card's "who ran it" line. The API sends every holder of the tárca's most
// senior rank in scope (a minister over a state secretary), newest first and one
// row per term — a scope of several cycles usually means several ministers, and
// naming only the newest of them dated the whole row to whoever happens to hold
// the post now. The full bench, with each term's dates, is the profile's job.
// ---------------------------------------------------------------------------
const LEADS_SHOWN = 3

// A minister re-appointed at the next election is filed as a **new term**,
// beginning days after the old one ended (the outgoing government serves until
// the new one is sworn in). Four such terms are one stint of the same person,
// not four names in a row, so consecutive terms are merged into one span.
const HANDOVER_DAYS = 45
function continuous(olderEnd, newerStart) {
  if (!olderEnd || !newerStart) return false
  const gap = (Date.parse(newerStart) - Date.parse(olderEnd)) / 86400000
  return Number.isFinite(gap) && gap <= HANDOVER_DAYS
}

// One entry per person, each with the runs they served — a person who came back
// after someone else (or whose middle cycles are not in scope) keeps both, so
// the years never claim a stint that was interrupted.
function collapseLeads(holders) {
  const byPerson = new Map()
  const out = []
  for (const h of holders || []) {
    let e = byPerson.get(h.person_id)
    if (!e) {
      e = { person_id: h.person_id, name: h.name, title: h.title, runs: [] }
      byPerson.set(h.person_id, e)
      out.push(e)
    }
    const open = e.runs[e.runs.length - 1]   // the newer run this term may extend
    if (open && continuous(h.date_end, open.start)) open.start = h.date_start
    else e.runs.push({ start: h.date_start, end: h.date_end })
  }
  return out
}

// "2010–2014" — years, not full dates: the row places a name in time, and the
// exact days are on the profile. An office still held has no end year, only a
// trailing dash (REP-2), written as the cycle chooser writes a running cycle.
function termYears(r) {
  const start = r.start ? String(r.start).slice(0, 4) : ''
  const end = r.end ? String(r.end).slice(0, 4) : ''
  if (!start) return end
  if (!r.end) return `${start}–`
  return end === start ? start : `${start}–${end}`
}
function leadYears(e) {
  return e.runs.map(termYears).join(', ')
}

// The office's own name, shown once in front of the people who held it: for a
// ministry it merely repeats the tárca ("Belügyminisztérium" → "belügyminiszter"),
// but for a minister without portfolio or an independent body it is the only
// place the post is named. Dropped when the shown holders' titles disagree,
// since one of them would then stand for all.
function sharedTitle(items) {
  const title = items.length ? items[0].title : null
  return title && items.every((e) => e.title === title) ? title : null
}

// Anonymous search-quality signal (SEA-12): which tárca the reader opens after a
// keyword search, and how far down the list it sat.
const clicks = createSearchClicks('portfolios')

let loadSeq = 0
async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false
  const args = { q: route.query.q, period: store.cycles }
  try {
    const res = await api.portfolios(args)
    if (seq === loadSeq) { data.value = res; clicks.arm(args) }
  } catch {
    if (seq === loadSeq) error.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

onMounted(async () => {
  await loadMeta().catch(() => {})
  load()
})
watch(() => route.query, () => { f.q = route.query.q || ''; load() })
watch(() => store.cycles.join(','), load)

let searchTimer = null
function onSearchInput() { clearTimeout(searchTimer); searchTimer = setTimeout(apply, 300) }
onUnmounted(() => clearTimeout(searchTimer))
</script>

<template>
  <h1>{{ $t('portfolios.title') }}</h1>
  <p class="muted small intro">{{ $t('portfolios.intro') }}</p>

  <form class="card pad searchform" role="search" @submit.prevent="apply">
    <input
      type="search" v-model="f.q"
      :placeholder="$t('portfolios.searchPlaceholder')"
      :aria-label="$t('portfolios.searchPlaceholder')"
      @input="onSearchInput" style="width:100%;"
    />
  </form>

  <StateBlock
    :loading="loading" :error="error"
    :empty="!!data && data.portfolios.length === 0"
    :empty-text="$t('portfolios.noResults')"
    @retry="load"
  >
    <div v-if="data">
      <p class="muted small" aria-live="polite" style="margin:.2rem 0 1rem;">
        {{ data.portfolios.length }} {{ $t('portfolios.unit') }} · {{ scopeText }}
      </p>

      <section v-for="g in groups" :key="g.kind" class="kindgroup">
        <h2 class="kindhead">{{ $t('portfolios.kinds.' + g.kind) }}</h2>
        <p v-if="g.kind === 'body'" class="muted small kindnote">
          {{ $t('portfolios.bodyNote') }}
        </p>
        <ul class="plist">
          <li v-for="p in g.items" :key="p.slug" class="card pad prow">
            <div class="pname">
              <RouterLink
                :to="{ name: 'portfolio', params: { slug: p.slug }, query: route.query }"
                @click="clicks.hit(p.rank)"
              >
                {{ p.name }}
              </RouterLink>
              <span v-if="p.leads.length" class="small muted lead">
                <span v-if="p.leadTitle" class="ltitle">{{ p.leadTitle }}:</span>
                <template v-for="(h, i) in p.leads" :key="h.person_id + '-' + i">
                  <span v-if="i" class="sep" aria-hidden="true"> · </span>
                  <RouterLink :to="{ name: 'profile', params: { id: h.person_id } }">
                    {{ h.name }}
                  </RouterLink>
                  <span class="lyears"> ({{ leadYears(h) }})</span>
                </template>
                <span v-if="p.leadsHidden" class="sep" aria-hidden="true"> · </span>
                <span v-if="p.leadsHidden">
                  {{ $t('portfolios.leadsMore', { count: p.leadsHidden }) }}
                </span>
              </span>
            </div>
            <!-- Counts carry a word, not only a number: the three are different
                 kinds of thing and a bare "17 · 2 · 24" says none of it. -->
            <div class="pcounts small">
              <span class="stat">
                <strong>{{ p.answered }}</strong> {{ $t('portfolios.answeredShort') }}
              </span>
              <span class="stat">
                <strong>{{ p.submitted }}</strong> {{ $t('portfolios.submittedShort') }}
              </span>
              <span class="stat" :class="{ dim: speechesPartial && !p.speeches }">
                <strong>{{ p.speeches }}</strong> {{ $t('portfolios.speechesShort') }}
              </span>
            </div>
          </li>
        </ul>
      </section>

      <p v-if="speechesPartial" class="muted small coverage">
        {{ $t('portfolios.speechCoverage') }}
      </p>

      <details class="methodology" style="margin-top:1rem;">
        <summary>{{ $t('factions.methodology') }}</summary>
        <p class="small muted">{{ $t('portfolios.methodology') }}</p>
      </details>
    </div>
  </StateBlock>
</template>

<style scoped>
.intro { margin: -.4rem 0 1rem; max-width: 68ch; }
.kindgroup { margin-bottom: 1.6rem; }
.kindhead { font-size: 1rem; margin: 0 0 .2rem; color: var(--ink-soft); }
.kindnote { margin: 0 0 .5rem; max-width: 60ch; }
.plist { list-style: none; padding: 0; margin: 0; display: grid; gap: .5rem; }
.prow {
  display: grid; gap: .35rem .9rem; align-items: center;
  grid-template-columns: minmax(0, 1fr) auto;
}
.pname { display: flex; flex-direction: column; gap: .1rem; min-width: 0; }
.pname > a { font-weight: 600; }        /* the tárca's own link, not the names under it */
.lead { min-width: 0; overflow-wrap: anywhere; }
/* The names are links to the people, but the row's own link is the tárca above
   them — so they read as part of the line and only colour up on hover. */
.lead a { color: var(--ink-soft); }
.lead a:hover { color: var(--accent); }
.ltitle { margin-right: .3rem; }
.pcounts { display: flex; gap: 1rem; white-space: nowrap; }
.stat strong { font-variant-numeric: tabular-nums; }
/* A zero that only means "not measured for these cycles" must not look like a
   measured zero (MIN-10); the note under the list explains it. */
.stat.dim { opacity: .45; }
.coverage { margin-top: .8rem; max-width: 68ch; }
@media (max-width: 720px) {
  .prow { grid-template-columns: 1fr; }
  .pcounts { white-space: normal; gap: .8rem; }
}
.methodology summary { cursor: pointer; font-weight: 600; color: var(--ink-soft); font-size: .85rem; }
</style>
