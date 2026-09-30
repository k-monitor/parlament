<script setup>
// "A múlt héten" — the sitting week that has just gone, at a glance
// (`GET /proceedings/week`): its days, what they add up to, what the House
// decided (`GET /bills/decisions`), the votes that split a faction, who spoke the
// most, what the week was distinctively about, and how many votes it held.
//
// The home page's other perishable block, the order paper, says what the House
// is about to do; this one says what it has just done. Its hard part is the same
// honesty problem the sittings list has (SIT-2): parlament.hu publishes a held
// sitting in instalments, so a day from last Thursday can still have nothing in
// it, or only half. Every day is therefore listed whatever its state, a day with
// nothing yet is marked as such instead of being counted as a quiet one, and the
// totals say when they cover only part of the week.
//
// When the House did not sit last week the endpoint hands back the latest week it
// did sit in, and the heading says "the latest sitting days" instead — a recess
// must not leave the home page claiming that last week was empty of *record*.
// This week's days already behind us are listed too (never summed): the order
// paper shows only the days still ahead, so a Monday sitting would otherwise be
// on the home page nowhere until the week was over.
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../api.js'
import { store } from '../store.js'
import { formatSpeakingTime } from '../format.js'
import StateBlock from './StateBlock.vue'
import WeekDayCards, { dayState } from './WeekDayCards.vue'

const { t, locale } = useI18n()
const data = ref(null)
const loading = ref(true)
const failed = ref(false)
const votes = ref(null)       // { total, adopted } — only with the votes module
const decisions = ref([])     // what the House decided — only with the bills module
const splits = ref([])        // the votes that split a faction — votes module

// How much of each decision group shows before "+N more": a week at the end of
// a session can pass twenty laws, and what comes after the list — the split
// votes, the speakers — still has to be reachable, on a phone most of all, where
// it all stacks into one column.
const DECISIONS_SHOWN = 5
const DECISIONS_SHOWN_NARROW = 3
const narrow = ref(false)
let narrowQuery = null
const onNarrow = (e) => { narrow.value = e.matches }
onMounted(() => {
  if (!window.matchMedia) return
  narrowQuery = window.matchMedia('(max-width: 560px)')
  narrow.value = narrowQuery.matches
  narrowQuery.addEventListener('change', onNarrow)
})
onBeforeUnmount(() => { if (narrowQuery) narrowQuery.removeEventListener('change', onNarrow) })
const decisionsShown = computed(() => (narrow.value ? DECISIONS_SHOWN_NARROW : DECISIONS_SHOWN))
// One or two MPs off their faction's line is a mis-press as often as a stance;
// the digest names only the votes where a faction visibly split.
const SPLIT_MIN = 3
const SPLIT_VOTES = 4

const week = computed(() => data.value && data.value.week)
const days = computed(() => (data.value && data.value.days) || [])
const thisWeek = computed(() => (data.value && data.value.this_week) || [])
const totals = computed(() => (data.value && data.value.totals) || {})
const speakers = computed(() => (data.value && data.value.top_speakers) || [])
// "The latest sitting days" rather than "last week" when the week reported is an
// earlier one, or when there is none and only this week's days are listed.
const earlier = computed(() => (week.value ? !week.value.last_week : thisWeek.value.length > 0))
// Raw counts stand in for the TF·IDF score only on a DB without the cycle's
// document frequencies, and then they are the everyday vocabulary ("magyar",
// "fontos") — not what the heading promises, so they are not shown.
const words = computed(() => (data.value && data.value.words_measure !== 'frequency'
  && data.value.words) || [])

const waitingDays = computed(() => days.value.filter((d) => dayState(d) === 'waiting').length)
const partialDays = computed(() => days.value.filter((d) => dayState(d) === 'partial').length)

const intlLocale = computed(() => (locale.value === 'en' ? 'en-US' : 'hu-HU'))
const asDate = (iso) => new Date(`${iso}T12:00:00`)
const rangeLabel = computed(() => {
  if (!week.value) return ''
  const fmt = new Intl.DateTimeFormat(intlLocale.value,
    { year: 'numeric', month: 'long', day: 'numeric' })
  try {
    return fmt.formatRange(asDate(week.value.start), asDate(week.value.end))
  } catch {
    return `${week.value.start} – ${week.value.end}`
  }
})
// A vote's moment on the Budapest clock (the API's timestamps are UTC).
function voteWhen(iso) {
  try {
    return new Intl.DateTimeFormat(intlLocale.value, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
      timeZone: 'Europe/Budapest',
    }).format(new Date(iso))
  } catch { return iso }
}

const num = (n) => (n ?? 0).toLocaleString('hu-HU')
const searchWord = (w) => ({ name: 'search',
  query: { q: w, date_from: week.value.start, date_to: week.value.end } })
const votesLink = computed(() => week.value && ({ name: 'votes',
  query: { date_from: week.value.start, date_to: week.value.end } }))
const splitsLink = computed(() => week.value && ({ name: 'votes',
  query: { date_from: week.value.start, date_to: week.value.end, sort: 'crossvoting_desc' } }))
const votesOn = computed(() => store.moduleEnabled('votes'))

// ── The decisions ────────────────────────────────────────────────────────────
// One tab per kind of decision, in the API's order (laws first, the
// interpellation answers last), so a heavy week costs one list's height, not
// five. The tabs follow the WAI-ARIA pattern: arrow keys and Home/End move
// between them, and only the selected one is in the tab order.
const decisionTabs = computed(() => {
  const tabs = []
  for (const d of decisions.value) {
    let tb = tabs.find((x) => x.category === d.category)
    if (!tb) tabs.push(tb = { category: d.category, items: [] })
    tb.items.push(d)
  }
  return tabs
})
const activeTab = ref(null)
const currentTab = computed(() =>
  decisionTabs.value.find((g) => g.category === activeTab.value) || decisionTabs.value[0] || null)
function onTabKey(e) {
  const keys = decisionTabs.value.map((g) => g.category)
  const i = keys.indexOf(currentTab.value.category)
  const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: keys.length - 1 }[e.key]
  if (to === undefined) return
  e.preventDefault()
  const key = keys[(to + keys.length) % keys.length]
  activeTab.value = key
  nextTick(() => document.getElementById(`lw-dtab-${key}`)?.focus())
}
const expanded = ref({})
const shownItems = (g) =>
  (expanded.value[g.category] ? g.items : g.items.slice(0, decisionsShown.value))

// What the vote settled, worded for what was decided: an immunity is waived or
// kept, an interpellation answer accepted or not, a law adopted or rejected.
const OUTCOME_KINDS = ['immunity', 'discipline', 'interpellation']
function outcomeLabel(d) {
  if (d.outcome === 'mixed') {
    return t('lastWeek.outcome.mixed', { votes: d.votes, adopted: d.adopted, rejected: d.rejected })
  }
  const kind = d.stage === 'agenda' ? 'agenda'
    : OUTCOME_KINDS.includes(d.category) ? d.category : 'plain'
  return t(`lastWeek.outcome.${kind}.${d.outcome}`)
}

// yes / no / abstain as shares of the votes cast, for the tally bar.
function tallyParts(v) {
  const sum = (v.yes || 0) + (v.no || 0) + (v.abstain || 0)
  if (!sum) return { yes: 0, no: 0, abstain: 0 }
  return { yes: (100 * (v.yes || 0)) / sum, no: (100 * (v.no || 0)) / sum,
           abstain: (100 * (v.abstain || 0)) / sum }
}
const tallyTitle = (v) => t('lastWeek.tallyTitle',
  { yes: num(v.yes), no: num(v.no), abstain: num(v.abstain) })

// ── The split votes ──────────────────────────────────────────────────────────
// Several votes on one iromány (an election takes one per nominee) share its
// title, so they are listed under it once rather than as repeated rows.
const splitGroups = computed(() => {
  const out = []
  for (const v of splits.value) {
    const s = (v.subjects && v.subjects[0]) || {}
    const key = s.title || v.subject || v.id
    let g = out.find((x) => x.key === key)
    if (!g) out.push(g = { key, title: s.title || v.subject, bill_id: s.bill_id, votes: [] })
    g.votes.push(v)
  }
  return out
})

async function loadVotes(w) {
  if (!votesOn.value) return
  const range = { date_from: w.start, date_to: w.end }
  try {
    // Quorum calls are divisions too, but not decisions: the count is what the
    // House decided, adopted or rejected.
    const [yes, no, cross] = await Promise.all([
      api.votes({ ...range, limit: 1, result: 'Elfogadva' }),
      api.votes({ ...range, limit: 1, result: 'Elutasítva' }),
      api.votes({ ...range, limit: SPLIT_VOTES, sort: 'crossvoting_desc' }),
    ])
    votes.value = { total: (yes.total || 0) + (no.total || 0), adopted: yes.total || 0 }
    splits.value = (cross.votes || []).filter((v) => (v.defectors || 0) >= SPLIT_MIN)
  } catch {
    votes.value = null
    splits.value = []
  }
}

async function loadDecisions(w) {
  if (!store.moduleEnabled('bills')) return
  try {
    decisions.value = (await api.billDecisions(w.start, w.end)).decisions || []
  } catch {
    decisions.value = []
  }
}

async function load() {
  loading.value = true
  failed.value = false
  try {
    data.value = await api.sittingWeek()
    if (data.value && data.value.week) {
      loadVotes(data.value.week)
      loadDecisions(data.value.week)
    }
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<template>
  <section v-if="loading || failed || week || thisWeek.length" class="card pad lastweek"
           aria-labelledby="lastweek-title">
    <div class="lw__head">
      <div>
        <h2 id="lastweek-title">
          {{ earlier ? $t('lastWeek.titleEarlier') : $t('lastWeek.title') }}
        </h2>
        <p v-if="week" class="lw__range soft">{{ rangeLabel }}</p>
      </div>
      <router-link :to="{ name: 'sessions' }" class="lw__all">
        {{ $t('lastWeek.allSittings') }} <span aria-hidden="true">→</span>
      </router-link>
    </div>

    <StateBlock :loading="loading" :error="failed" @retry="load">
      <template v-if="week || thisWeek.length">
        <dl v-if="totals.speeches" class="lw__stats">
          <div><dt>{{ num(totals.days) }}</dt><dd>{{ $t('lastWeek.stats.days') }}</dd></div>
          <div><dt>{{ num(totals.speeches) }}</dt><dd>{{ $t('lastWeek.stats.speeches') }}</dd></div>
          <div><dt>{{ formatSpeakingTime(totals.seconds) }}</dt><dd>{{ $t('lastWeek.stats.time') }}</dd></div>
          <div v-if="votes && votes.total">
            <dt><router-link :to="votesLink">{{ num(votes.total) }}</router-link></dt>
            <dd>{{ $t('lastWeek.stats.votes', { adopted: num(votes.adopted) }) }}</dd>
          </div>
        </dl>

        <!-- The totals above cover only what is in; say so, and say what is not. -->
        <p v-if="waitingDays" class="lw__note">
          <span aria-hidden="true">⏳</span>
          {{ totals.speeches
              ? $t('lastWeek.someWaiting', { n: waitingDays }, waitingDays)
              : $t('lastWeek.allWaiting', {}, days.length) }}
        </p>
        <p v-else-if="partialDays" class="lw__note">
          <span aria-hidden="true">⏳</span> {{ $t('lastWeek.somePartial', {}, partialDays) }}
        </p>

        <!-- The days the totals add up, each opening its sitting-day page. -->
        <WeekDayCards v-if="days.length" :days="days" class="lw__daystrip" />

        <!-- What the House decided; beside it who broke ranks, who spoke, and
             what about. -->
        <div v-if="decisions.length || splitGroups.length || speakers.length || words.length"
             class="lw__grid" :class="{ solo: !currentTab }">
          <div v-if="currentTab" class="lw__decisions">
            <h3 id="lw-decisions-title" class="lw__subhead">{{ $t('lastWeek.decisions') }}</h3>
            <div class="lw__tabs" role="tablist" aria-labelledby="lw-decisions-title" @keydown="onTabKey">
              <button
                v-for="g in decisionTabs" :id="`lw-dtab-${g.category}`" :key="g.category"
                type="button" role="tab" class="lw__tab"
                :class="{ active: g.category === currentTab.category }"
                :aria-selected="g.category === currentTab.category"
                :tabindex="g.category === currentTab.category ? 0 : -1"
                aria-controls="lw-dpanel" @click="activeTab = g.category"
              >
                {{ $t(`lastWeek.categories.${g.category}`) }}
                <span class="lw__tabcount">{{ num(g.items.length) }}</span>
              </button>
            </div>

            <div id="lw-dpanel" role="tabpanel" class="lw__dpanel"
                 :aria-labelledby="`lw-dtab-${currentTab.category}`">
              <!-- The House votes on an interpellation answer only when the MP
                   who asked rejected it; the tab would not make sense without it. -->
              <p v-if="currentTab.category === 'interpellation'" class="lw__dnote">
                {{ $t('lastWeek.interpNote') }}
              </p>
              <ul class="lw__dlist">
                <li v-for="d in shownItems(currentTab)" :key="d.bill_id + d.stage" class="lw__decision">
                  <router-link :to="{ name: 'bill', params: { id: d.bill_id } }" class="lw__dtitle">
                    {{ d.title || d.bill_number }}
                  </router-link>
                  <div class="lw__dmeta">
                    <span class="lw__dnum">{{ d.bill_number }}</span>
                    <span class="lw__outcome" :class="'is-' + d.outcome">{{ outcomeLabel(d) }}</span>
                    <component
                      :is="votesOn && d.tally.vote_ref ? 'router-link' : 'span'" v-if="d.tally"
                      :to="votesOn && d.tally.vote_ref ? { name: 'vote', params: { id: d.tally.vote_ref } } : undefined"
                      class="lw__tally" :title="tallyTitle(d.tally)"
                    >
                      <span class="lw__vbar" aria-hidden="true">
                        <span class="seg yes" :style="{ width: tallyParts(d.tally).yes + '%' }"></span>
                        <span class="seg no" :style="{ width: tallyParts(d.tally).no + '%' }"></span>
                        <span class="seg abstain" :style="{ width: tallyParts(d.tally).abstain + '%' }"></span>
                      </span>
                      <span class="lw__tnums">
                        <span class="visually-hidden">{{ tallyTitle(d.tally) }}</span>
                        <span aria-hidden="true">{{ d.tally.yes }}–{{ d.tally.no }}–{{ d.tally.abstain }}</span>
                      </span>
                    </component>
                  </div>
                </li>
              </ul>
              <button
                v-if="currentTab.items.length > decisionsShown" type="button" class="lw__more"
                :aria-expanded="!!expanded[currentTab.category]"
                @click="expanded[currentTab.category] = !expanded[currentTab.category]"
              >
                {{ expanded[currentTab.category]
                    ? $t('lastWeek.fewerDecisions')
                    : $t('lastWeek.moreDecisions', { n: currentTab.items.length - decisionsShown }) }}
              </button>
            </div>
          </div>

          <div v-if="splitGroups.length || speakers.length || words.length" class="lw__side">
            <section v-if="splitGroups.length" class="lw__block">
              <h3 class="lw__subhead" :title="$t('votes.crossVotingNote')">{{ $t('lastWeek.splits') }}</h3>
              <ul class="lw__slist">
                <li v-for="g in splitGroups" :key="g.key" class="lw__split">
                  <router-link v-if="g.bill_id" :to="{ name: 'bill', params: { id: g.bill_id } }" class="lw__dtitle">
                    {{ g.title }}
                  </router-link>
                  <span v-else class="lw__dtitle">{{ g.title }}</span>
                  <ul class="lw__svotes">
                    <li v-for="v in g.votes" :key="v.id">
                      <router-link :to="{ name: 'vote', params: { id: v.id } }" class="lw__svote">
                        <span class="lw__when">{{ voteWhen(v.vote_datetime) }}</span>
                        <span class="lw__outcome" :class="v.result === 'Elfogadva' ? 'is-adopted' : 'is-rejected'">
                          {{ v.result === 'Elfogadva' ? $t('lastWeek.outcome.plain.adopted') : $t('lastWeek.outcome.plain.rejected') }}
                        </span>
                        <span class="lw__tnums" :title="tallyTitle(v)">{{ v.yes }}–{{ v.no }}–{{ v.abstain }}</span>
                      </router-link>
                      <p class="lw__defectors">
                        {{ $t('lastWeek.splitDefectors', { n: num(v.defectors) }, v.defectors) }}
                        <span v-for="fx in v.defector_factions" :key="fx.name" class="chip lw__xf">
                          <span class="dot" :style="{ background: fx.color || '#bbb' }" aria-hidden="true"></span>
                          {{ fx.name }} {{ num(fx.defectors) }}
                        </span>
                      </p>
                    </li>
                  </ul>
                </li>
              </ul>
              <router-link :to="splitsLink" class="lw__alllink">
                {{ $t('lastWeek.allSplits') }} <span aria-hidden="true">→</span>
              </router-link>
            </section>

            <section v-if="speakers.length" class="lw__block">
              <h3 class="lw__subhead">{{ $t('lastWeek.topSpeakers') }}</h3>
              <ol class="lw__speakers">
                <li v-for="s in speakers" :key="s.person_id">
                  <img v-if="s.photo_uri" :src="s.photo_uri" alt="" class="avatar xs" loading="lazy" />
                  <span v-else class="avatar xs" aria-hidden="true"></span>
                  <span class="lw__whobox">
                    <router-link :to="{ name: 'profile', params: { id: s.person_id } }" class="lw__who">
                      {{ s.label }}
                    </router-link>
                    <!-- A minister answering Monday's questions tops the list by
                         office; the office says so. -->
                    <span v-if="s.office" class="lw__office" :title="s.office">{{ s.office }}</span>
                  </span>
                  <span v-if="s.faction" class="chip">
                    <span class="dot" :style="{ background: s.faction.color }" aria-hidden="true"></span>
                    {{ s.faction.label }}
                  </span>
                  <span class="lw__time">{{ formatSpeakingTime(s.seconds) }}</span>
                </li>
              </ol>
            </section>

            <section v-if="words.length" class="lw__block">
              <h3 class="lw__subhead">{{ $t('lastWeek.words') }}</h3>
              <p class="lw__words">
                <router-link
                  v-for="w in words" :key="w.text" :to="searchWord(w.text)"
                  class="lw__word" :class="{ 'is-entity': w.kind === 'entity' }"
                  :title="$t('lastWeek.wordTitle', { n: num(w.count) })"
                >{{ w.text }}</router-link>
              </p>
            </section>
          </div>
        </div>

        <!-- This week so far: the order paper below has dropped these days, and
             the totals above are last week's, so they are listed and not summed. -->
        <div v-if="thisWeek.length" class="lw__thisweek">
          <h3 class="lw__subhead">{{ $t('lastWeek.thisWeek') }}</h3>
          <WeekDayCards :days="thisWeek" />
        </div>
      </template>
    </StateBlock>
  </section>
</template>

<style scoped>
.lw__head { display: flex; align-items: flex-start; justify-content: space-between; gap: .6rem 1rem; flex-wrap: wrap; }
.lw__head h2 { margin: 0; font-size: 1.15rem; }
.lw__range { margin: .15rem 0 0; font-size: .85rem; }
.lw__all { font-weight: 700; font-size: .9rem; white-space: nowrap; }

/* The same four-column rhythm as the corpus figures used to have on this page. */
.lw__stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 1rem 0 0; max-width: 680px; }
.lw__stats div { margin: 0; padding: 0 1.4rem; border-left: 1px solid var(--line); }
.lw__stats div:first-child { padding-left: 0; border-left: 0; }
.lw__stats dt { font-size: 1.45rem; font-weight: 800; color: var(--accent); white-space: nowrap; }
.lw__stats dt a { color: inherit; }
.lw__stats dd { margin: 0; color: var(--ink-faint); font-size: .85rem; }
@media (max-width: 620px) {
  .lw__stats { grid-template-columns: repeat(2, minmax(0, 1fr)); row-gap: .9rem; }
  .lw__stats div:nth-child(odd) { padding-left: 0; border-left: 0; }
}

.lw__note {
  margin: .9rem 0 0; padding: .5rem .75rem; border-radius: 8px;
  background: #fdf3e3; color: #6b4600; font-size: .88rem;
}

.lw__daystrip { margin-top: 1rem; }
/* The decisions run longer than anything beside them, so they get the wider
   column; the side column stacks the split votes, the speakers and the words. */
.lw__grid { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: 1.2rem 2.2rem; margin-top: 1.2rem; }
.lw__grid.solo { grid-template-columns: minmax(0, 1fr); }
.lw__grid.solo .lw__side { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 300px), 1fr)); gap: 1.2rem 2.2rem; }
@media (max-width: 760px) { .lw__grid { grid-template-columns: minmax(0, 1fr); } }

.lw__subhead { margin: 0 0 .45rem; font-size: .8rem; font-weight: 700; color: var(--ink-faint); text-transform: uppercase; letter-spacing: .04em; }
.lw__block + .lw__block { margin-top: 1.3rem; }
.lw__grid.solo .lw__block + .lw__block { margin-top: 0; }

/* ── decisions ── */
/* Underline tabs, each carrying its count: four or five labelled kinds of
   decision are more than the site's pill switch is for, and these wrap cleanly
   on a phone where the pills would not. */
.lw__tabs { display: flex; flex-wrap: wrap; gap: 0 .2rem; margin: 0 0 .7rem; border-bottom: 1px solid var(--line); }
.lw__tab {
  display: inline-flex; align-items: center; gap: .35rem; margin-bottom: -1px;
  padding: .35rem .55rem .45rem; border: 0; border-bottom: 2px solid transparent; background: none; cursor: pointer;
  color: var(--ink-soft); font: inherit; font-size: .86rem; font-weight: 600;
}
.lw__tab:hover { color: var(--ink); }
.lw__tab.active { color: var(--accent); border-bottom-color: var(--accent); }
.lw__tabcount { font-size: .72rem; font-weight: 700; color: var(--ink-faint); background: #f0eee8; border-radius: 999px; padding: 0 .4rem; }
.lw__tab.active .lw__tabcount { background: var(--accent-soft); color: var(--accent); }
.lw__dnote { margin: 0 0 .5rem; font-size: .82rem; color: var(--ink-faint); }
.lw__dlist, .lw__slist, .lw__svotes { list-style: none; margin: 0; padding: 0; }
.lw__decision { padding: .4rem 0; border-top: 1px solid var(--line); }
.lw__decision:first-child { border-top: 0; padding-top: .1rem; }
.lw__dtitle {
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  color: var(--ink); font-size: .9rem; font-weight: 600; line-height: 1.35;
}
a.lw__dtitle:hover { color: var(--accent); }
.lw__dmeta { display: flex; align-items: center; flex-wrap: wrap; gap: .15rem .7rem; margin-top: .2rem; font-size: .8rem; color: var(--ink-faint); }
.lw__dnum { font-variant-numeric: tabular-nums; font-weight: 600; }
.lw__outcome { font-weight: 700; }
.lw__outcome.is-adopted { color: #2e7d32; }
.lw__outcome.is-rejected { color: #c62828; }
.lw__outcome.is-mixed { color: var(--ink-soft); }
.lw__tally { display: inline-flex; align-items: center; gap: .45rem; color: var(--ink-soft); }
a.lw__tally:hover { text-decoration: none; color: var(--accent); }
.lw__vbar { display: inline-flex; width: 72px; height: .5rem; border-radius: 999px; overflow: hidden; background: var(--line); }
.lw__vbar .seg.yes { background: #2e7d32; }
.lw__vbar .seg.no { background: #c62828; }
.lw__vbar .seg.abstain { background: #b9b6ad; }
.lw__tnums { font-variant-numeric: tabular-nums; white-space: nowrap; }
.lw__more {
  margin-top: .25rem; padding: .15rem 0; border: 0; background: none; cursor: pointer;
  color: var(--accent); font: inherit; font-size: .82rem; font-weight: 700;
}
.lw__more:hover { text-decoration: underline; }

/* ── split votes ── */
.lw__split + .lw__split { margin-top: .7rem; padding-top: .6rem; border-top: 1px solid var(--line); }
.lw__svotes { margin-top: .25rem; display: grid; gap: .35rem; }
.lw__svote { display: flex; align-items: baseline; flex-wrap: wrap; gap: .1rem .6rem; font-size: .8rem; color: var(--ink-soft); }
.lw__svote:hover { text-decoration: none; }
.lw__svote:hover .lw__when { color: var(--accent); text-decoration: underline; }
.lw__when { font-weight: 600; color: var(--ink); }
.lw__defectors { margin: .1rem 0 0; font-size: .8rem; color: var(--ink-faint); }
.lw__xf { margin-left: .5rem; font-size: .78rem; }
.lw__alllink { display: inline-block; margin-top: .6rem; font-size: .85rem; font-weight: 700; }

/* ── this week so far ── */
.lw__thisweek { margin-top: 1.3rem; padding-top: 1rem; border-top: 1px solid var(--line); }

/* ── speakers and words ── */
.lw__speakers { list-style: none; margin: 0; padding: 0; display: grid; gap: .35rem; }
.lw__speakers li { display: flex; align-items: center; gap: .5rem; min-width: 0; font-size: .9rem; }
.lw__whobox { display: flex; flex-direction: column; min-width: 0; line-height: 1.25; }
.lw__who { color: var(--ink); font-weight: 600; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lw__who:hover { color: var(--accent); }
.lw__office { font-size: .75rem; color: var(--ink-faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lw__speakers .chip { font-weight: 500; white-space: nowrap; }
.lw__time { margin-left: auto; color: var(--ink-soft); font-variant-numeric: tabular-nums; white-space: nowrap; }
.lw__words { display: flex; flex-wrap: wrap; gap: .35rem; margin: 0; }
.lw__word {
  padding: .15rem .6rem; border-radius: 999px; background: #f0eee8;
  color: var(--ink); font-size: .85rem; font-weight: 600;
}
.lw__word.is-entity { background: var(--accent-soft); color: var(--accent); }
.lw__word:hover { text-decoration: none; background: var(--accent); color: var(--accent-ink); }
</style>
