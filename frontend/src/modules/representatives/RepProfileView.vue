<script setup>
// Representative profile (REP-2/REP-3/REP-5). Shows bio + faction history, the
// precomputed statistics (with explicit scope + methodology), an accessible
// trend chart, and a reverse-chronological speech list linking into the viewer.
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { store, loadMeta } from '../../store.js'
import { formatDate, formatSpeakingTime, formatDuration, agendaLabel } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import BarChart from '../../components/BarChart.vue'
import PieChart from '../../components/PieChart.vue'
import ActivityBoard from '../../components/ActivityBoard.vue'
import HelpTip from '../../components/HelpTip.vue'

const props = defineProps({ id: String })
const { t } = useI18n()

// First day of the selected cycle, so the activity board always starts there
// (null under "all cycles" → the board starts at the MP's first active day).
const cycleStart = computed(() => {
  if (store.cycle === null) return null
  const p = (store.meta?.periods || []).find((x) => x.number === store.cycle)
  return p?.date_start ? String(p.date_start).slice(0, 10) : null
})

const profile = ref(null)
const stats = ref(null)
const activity = ref(null)
// Speeches are grouped by sitting day: `speechDays` holds the day rows (count
// per day, reverse-chronological), and each day's speeches are fetched on demand
// when the user expands it — see `dayCache`/`toggleDay`.
const speechDays = ref(null)
const dayCache = reactive({})   // session_id -> { loading, error, list }
const openDays = reactive({})   // session_id -> bool (expanded?)
// Submitted irományok are split into three sections by main type: questions
// (kérdés/azonnali kérdés/interpelláció — K/A/I), bills (törvényjavaslat +
// határozati javaslat — T/H), and everything else (rare per-MP types like
// politikai nyilatkozat/vita, személyi döntés). Each is fetched separately so
// its count badge is exact (not capped by a shared page limit).
const questions = ref(null)
const lawBills = ref(null)
const otherDocs = ref(null)
// Votes are grouped by sitting day too (same lazy-load pattern as speeches):
// `voteDays` holds the day rows (count per day), each day's votes fetched on
// demand via `voteDayCache`/`toggleVoteDay`. Days are keyed by date (YYYY-MM-DD).
const voteDays = ref(null)
const voteDayCache = reactive({})   // date -> { loading, error, list }
const openVoteDays = reactive({})   // date -> bool
const loading = ref(false)
const error = ref(false)

// The bills/votes sections are only meaningful when their module is live (EXT-6);
// a disabled module simply means no section, not an error.
const showBills = computed(() => store.moduleEnabled('bills'))
const showVotes = computed(() => store.moduleEnabled('votes'))

// Long lists (bills, votes, speeches) are collapsed to a preview so the profile
// stays scannable; a per-section toggle reveals the rest of what's loaded.
const COLLAPSE_LIMIT = 8
const expanded = reactive({ questions: false, lawbills: false, other: false, votes: false, days: false })
function shown(list, key) {
  return expanded[key] ? list : list.slice(0, COLLAPSE_LIMIT)
}

// The submitted-irományok sections, in display order, dropping any the MP has
// none of in the current scope (so a section never shows an empty "(0)").
const docSections = computed(() => [
  { key: 'questions', titleKey: 'profile.questions', data: questions.value },
  { key: 'lawbills', titleKey: 'profile.bills', data: lawBills.value },
  { key: 'other', titleKey: 'profile.otherDocuments', data: otherDocs.value },
].filter((s) => s.data && s.data.total))

// Expand/collapse a sitting day; on first expand, lazily fetch that day's items
// (cached so re-opening doesn't refetch). `open`/`cache` are the per-section
// reactive maps; `fetcher(key)` returns the day's list. Shared by the speeches
// (keyed by session_id) and votes (keyed by date) lists.
async function toggleDayWith(open, cache, key, fetcher) {
  open[key] = !open[key]
  if (!open[key]) return
  if (cache[key] && (cache[key].list || cache[key].loading)) return
  cache[key] = { loading: true, error: false, list: null }
  try {
    cache[key] = { loading: false, error: false, list: await fetcher(key) }
  } catch {
    cache[key] = { loading: false, error: true, list: null }
  }
}
function toggleDay(day) {
  toggleDayWith(openDays, dayCache, day.session_id, (sid) =>
    api.repSpeeches(props.id, { session_id: sid, period: store.cycle, limit: 200 })
      .then((r) => r.speeches))
}
function toggleVoteDay(day) {
  toggleDayWith(openVoteDays, voteDayCache, day.date, (date) =>
    api.repVotes(props.id, { date, period: store.cycle, limit: 200 })
      .then((r) => r.votes))
}

const PLACEHOLDER =
  'data:image/svg+xml;utf8,' + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="110" height="110"><rect width="110" height="110" fill="#e7e5df"/><circle cx="55" cy="44" r="22" fill="#bdb9af"/><rect x="18" y="74" width="74" height="40" rx="20" fill="#bdb9af"/></svg>')
function onImgErr(e) { e.target.src = PLACEHOLDER }

const overTimeItems = computed(() => {
  if (!stats.value) return []
  return stats.value.over_time.map((p) => ({
    label: `${formatDate(p.date)}`,
    value: p.speech_count,
    sub: p.session_id,
  }))
})

// Vote-value chip colour by normalized code, matching the Votes module palette.
const VOTE_CLASS = { yes: 'yes', no: 'no', abstain: 'abstain', novote: 'novote', absent: 'absent' }

// Roll-call participation pie: the backend's `vote_breakdown` mapped to
// labelled, coloured segments (ordered fully-participated → fully-absent).
// "Szavazott" counts every cast vote (igen/nem/tartózkodás) and is green;
// "nem szavazott"/"igazoltan távol" reuse the Votes-module chip palette, and
// "nem volt jelen" is the darkest, so the pie reads as a participation
// gradient. Empty when the Votes module is off or the MP has no roll-call votes.
const VOTE_BREAKDOWN_SEGMENTS = [
  { key: 'voted', color: '#2e7d32' },
  { key: 'novote', color: '#c79a2e' },
  { key: 'absent', color: '#7c8288' },
  { key: 'not_present', color: '#3f434a' },
]
const VB_LABEL = {
  voted: 'profile.vbVoted', novote: 'profile.vbNovote',
  absent: 'profile.vbAbsent', not_present: 'profile.vbNotPresent',
}
const voteBreakdownSegments = computed(() => {
  const b = stats.value?.totals?.vote_breakdown
  if (!b) return []
  return VOTE_BREAKDOWN_SEGMENTS.map((s) => ({
    ...s, label: t(VB_LABEL[s.key]), value: b[s.key] || 0,
  }))
})

// Monotonic load id: profile switches (props.id / cycle watchers) can overlap
// in flight; only the latest request may write state, or a slow earlier load
// would display MP A's data under MP B's URL.
let loadSeq = 0

async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false
  profile.value = stats.value = activity.value = speechDays.value = voteDays.value = null
  questions.value = lawBills.value = otherDocs.value = null
  for (const m of [dayCache, openDays, voteDayCache, openVoteDays])
    for (const k of Object.keys(m)) delete m[k]
  expanded.questions = expanded.lawbills = expanded.other = expanded.votes = expanded.days = false
  try {
    // Everything on the profile is scoped to the global cycle (store.cycle; null
    // = all cycles), so the page never mixes in a previous cycle's data (§4A).
    const period = store.cycle
    // A feature-module failure must not break the profile, so each resolves to
    // null on error (and is skipped entirely when its module is disabled).
    // Submitted irományok are fetched in three buckets by main type so each
    // section's count is exact: questions (K/A/I), bills (T/H), everything else.
    const docReq = (filter) => showBills.value
      ? api.bills({ sponsor: props.id, limit: 100, period, ...filter }).catch(() => null)
      : Promise.resolve(null)
    const questionsReq = docReq({ main_type_in: 'K,A,I' })
    const lawBillsReq = docReq({ main_type_in: 'T,H' })
    const otherReq = docReq({ main_type_not_in: 'K,A,I,T,H' })
    const votesReq = showVotes.value
      ? api.repVoteDays(props.id, period).catch(() => null)
      : Promise.resolve(null)
    // The activity board is part of the representatives module (always on); a
    // failure must not break the profile, so it resolves to null on error.
    const activityReq = api.repActivity(props.id, period).catch(() => null)
    const [p, s, act, days, qd, lb, od, v] = await Promise.all([
      api.representative(props.id, period),
      api.repStatistics(props.id, period),
      activityReq,
      api.repSpeechDays(props.id, period),
      questionsReq,
      lawBillsReq,
      otherReq,
      votesReq,
    ])
    if (seq !== loadSeq) return  // superseded by a newer navigation
    profile.value = p; stats.value = s; activity.value = act
    speechDays.value = days; voteDays.value = v
    questions.value = qd; lawBills.value = lb; otherDocs.value = od
  } catch {
    if (seq === loadSeq) error.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}
// Gate the first fetch on the manifest so store.cycle is resolved to the latest
// cycle before we query (otherwise the profile would briefly be scoped to "all").
onMounted(async () => { await loadMeta().catch(() => {}); load() })
watch(() => props.id, load)
// Re-fetch when the user switches the global cycle.
watch(() => store.cycle, load)
</script>

<template>
  <StateBlock :loading="loading" :error="error" @retry="load">
    <div v-if="profile" class="profile">
      <router-link :to="{ name: 'representatives' }" class="small">‹ {{ $t('reps.title') }}</router-link>

      <header class="phead card pad">
        <img class="avatar lg" :src="profile.photo_uri || PLACEHOLDER" @error="onImgErr" alt="" />
        <div class="pinfo">
          <h1>{{ profile.label }}</h1>
          <div class="row" style="gap:.8rem;">
            <FactionBadge :faction="profile.current_faction" />
            <span v-if="profile.constituency" class="muted">📍 {{ profile.constituency }}</span>
          </div>
          <div class="row small links" style="gap:1rem;margin-top:.5rem;">
            <a v-if="profile.website" :href="profile.website" target="_blank" rel="noopener">🌐 {{ $t('profile.website') }}</a>
            <a v-if="profile.email" :href="'mailto:' + profile.email">✉ {{ profile.email }}</a>
            <a v-if="profile.wikidata_id" :href="'https://www.wikidata.org/wiki/' + profile.wikidata_id" target="_blank" rel="noopener">Wikidata</a>
          </div>
        </div>

        <!-- GitHub-style activity board (REP-8) lives in the header, to the right
             of the bio; shown only when the MP has any activity in scope. -->
        <div class="pactivity" v-if="activity && activity.totals.active_days">
          <ActivityBoard :days="activity.days" :documents-available="activity.documents_available" :from="cycleStart" />
        </div>
      </header>

      <div class="pgrid">
        <!-- left: stats + bio -->
        <div class="pcol">
          <section class="card pad">
            <div class="sechead">
              <h2>{{ $t('profile.statistics') }}</h2>
              <HelpTip :label="$t('profile.methodology') + ' · ' + $t('profile.scope')">
                <p>{{ stats.scope.description }} ({{ stats.scope.sessions_covered }} {{ $t('profile.sessionsCovered') }})</p>
                <p>{{ stats.methodology }}</p>
              </HelpTip>
            </div>
            <div class="bignums">
              <div><span class="num">{{ stats.totals.speech_count }}</span><span class="lbl">{{ $t('profile.totalSpeeches') }}</span></div>
              <div><span class="num">{{ formatSpeakingTime(stats.totals.speaking_seconds) }}</span><span class="lbl">{{ $t('profile.totalSpeakingTime') }}</span></div>
              <div v-if="stats.totals.bills_available">
                <router-link :to="{ name: 'bills', query: { sponsor: id } }" class="num biglink">{{ stats.totals.bills_submitted }}</router-link>
                <span class="lbl">{{ $t('profile.billsSubmitted') }}</span>
              </div>
              <!-- Attendance (REP-3): absences from roll-call votes, nominal + % -->
              <div v-if="stats.totals.votes_available && stats.totals.votes_total">
                <span class="num">{{ stats.totals.votes_absent }}<span class="pct" v-if="stats.totals.votes_absent_pct !== null"> · {{ stats.totals.votes_absent_pct }}%</span></span>
                <span class="lbl">{{ $t('profile.votesAbsent') }}</span>
              </div>
            </div>
            <!-- REP-3: bills metric hidden, not faked, until the Bills module ships -->
            <p v-if="!stats.totals.bills_available" class="small muted bills-note">ⓘ {{ $t('profile.billsUnavailable') }}</p>

            <!-- Roll-call participation pie (szavazott / tartózkodott / nem
                 szavazott / igazoltan távol / nem volt jelen). Shown only when
                 the Votes module is live and the MP has roll-call votes in scope. -->
            <div v-if="stats.totals.vote_breakdown && stats.totals.vote_breakdown.total"
                 style="margin-top:1.2rem;">
              <PieChart
                :segments="voteBreakdownSegments"
                :caption="$t('profile.voteBreakdown')"
                :total-label="$t('profile.vbUnit')"
              />
            </div>

            <div v-if="overTimeItems.length" style="margin-top:1rem;">
              <BarChart
                :items="overTimeItems"
                :caption="$t('profile.speechesOverTime')"
                :unit="$t('reps.speeches')"
              />
            </div>
          </section>

          <section class="card pad" v-if="profile.faction_history && profile.faction_history.length">
            <h2>{{ $t('profile.factionHistory') }}</h2>
            <ul class="timeline">
              <li v-for="(h, i) in profile.faction_history" :key="i">
                <FactionBadge :faction="h.faction" />
                <span class="muted small">{{ h.cycle }}</span>
              </li>
            </ul>
          </section>

          <section class="card pad" v-if="profile.committees && profile.committees.length">
            <h2>{{ $t('profile.committees') }}</h2>
            <ul class="plain">
              <li v-for="(c, i) in profile.committees.slice(0, 12)" :key="i" class="small">
                {{ c.committee || c }} <span class="muted" v-if="c.role">— {{ c.role }}</span>
              </li>
            </ul>
          </section>

          <section class="card pad" v-if="profile.education && profile.education.length">
            <h2>{{ $t('profile.education') }}</h2>
            <ul class="plain">
              <li v-for="(e, i) in profile.education" :key="i" class="small">
                {{ e.degree }} <span class="muted" v-if="e.institution">— {{ e.institution }}</span>
              </li>
            </ul>
          </section>
        </div>

        <!-- right: bills + speeches -->
        <div class="pcol">
          <!-- Submitted irományok, split by main type: questions (K/A/I), bills
               (T/H), and everything else — each its own section (BILL-9). -->
          <section class="card pad" v-for="sec in docSections" :key="sec.key">
            <h2>{{ $t(sec.titleKey) }} <span class="muted small">({{ sec.data.total }})</span></h2>
            <ul class="billmini">
              <li v-for="b in shown(sec.data.bills, sec.key)" :key="b.id">
                <router-link :to="{ name: 'bill', params: { id: b.id } }" class="billitem">
                  <span class="billitem-head">
                    <span class="bnum">{{ b.bill_number }}</span>
                    <span class="badge" v-if="b.status">{{ b.status }}</span>
                  </span>
                  <span class="btitle">{{ b.title }}</span>
                </router-link>
              </li>
            </ul>
            <button v-if="sec.data.bills.length > COLLAPSE_LIMIT" type="button" class="btn small showmore"
              :aria-expanded="expanded[sec.key]" @click="expanded[sec.key] = !expanded[sec.key]">
              {{ expanded[sec.key] ? $t('profile.showLess') : $t('profile.showMore') }}
            </button>
            <!-- The buckets are fetched with limit 100, but the header shows the
                 true total — say so instead of silently truncating. -->
            <p v-if="expanded[sec.key] && sec.data.total > sec.data.bills.length" class="muted small">
              {{ $t('profile.showingFirst', { n: sec.data.bills.length }) }}
            </p>
          </section>

          <!-- Votes grouped by sitting day; each day is a spoiler that lazily
               loads its roll-call votes on first expand (EXT-2). -->
          <section class="card pad" v-if="showVotes && voteDays && voteDays.total">
            <div class="sechead">
              <h2>{{ $t('profile.votes') }} <span class="muted small">({{ voteDays.total }})</span></h2>
              <HelpTip :label="$t('profile.votes')">
                <p>{{ $t('profile.votesNote') }}</p>
              </HelpTip>
            </div>
            <ul class="daylist">
              <li v-for="d in shown(voteDays.days, 'votes')" :key="d.date" class="dayitem">
                <button type="button" class="dayhead" :aria-expanded="!!openVoteDays[d.date]" @click="toggleVoteDay(d)">
                  <span class="caret" aria-hidden="true">{{ openVoteDays[d.date] ? '▾' : '▸' }}</span>
                  <strong>{{ formatDate(d.date) }}</strong>
                  <span class="muted small daycount">{{ d.count }} {{ $t('profile.votesDayCount') }}</span>
                </button>
                <div v-if="openVoteDays[d.date]" class="daybody">
                  <p v-if="voteDayCache[d.date] && voteDayCache[d.date].loading" class="muted small">…</p>
                  <p v-else-if="voteDayCache[d.date] && voteDayCache[d.date].error" class="muted small">
                    {{ $t('profile.votesLoadError') }}
                  </p>
                  <ul v-else-if="voteDayCache[d.date]" class="votemini">
                    <li v-for="v in voteDayCache[d.date].list" :key="v.id">
                      <router-link :to="{ name: 'vote', params: { id: v.id } }" class="voteitem">
                        <span class="vchip" :class="VOTE_CLASS[v.value_code] || 'other'">{{ $t('votes.' + v.value_code) }}</span>
                        <span class="vmeta">
                          <span class="vsub">{{ v.subject }}</span>
                          <span class="muted small" v-if="v.subjects.length">
                            <template v-for="(s, i) in v.subjects" :key="i">{{ i ? ' · ' : '' }}{{ s.bill_number }}</template>
                          </span>
                        </span>
                      </router-link>
                    </li>
                  </ul>
                </div>
              </li>
            </ul>
            <button v-if="voteDays.days.length > COLLAPSE_LIMIT" type="button" class="btn small showmore"
              :aria-expanded="expanded.votes" @click="expanded.votes = !expanded.votes">
              {{ expanded.votes ? $t('profile.showLess') : $t('profile.showMore') }}
            </button>
          </section>

          <!-- Speeches grouped by sitting day; each day is a spoiler that lazily
               loads its speeches on first expand (REP-2). -->
          <section class="card pad">
            <h2>{{ $t('profile.speeches') }} <span class="muted small" v-if="speechDays">({{ speechDays.total }})</span></h2>
            <p v-if="speechDays && speechDays.total === 0" class="muted">{{ $t('profile.noSpeeches') }}</p>
            <ul v-else-if="speechDays" class="daylist">
              <li v-for="d in shown(speechDays.days, 'days')" :key="d.session_id" class="dayitem">
                <button type="button" class="dayhead" :aria-expanded="!!openDays[d.session_id]" @click="toggleDay(d)">
                  <span class="caret" aria-hidden="true">{{ openDays[d.session_id] ? '▾' : '▸' }}</span>
                  <strong>{{ formatDate(d.date) }}</strong>
                  <span class="muted small daycount">{{ d.count }} {{ $t('profile.speechesDayCount') }}</span>
                  <span class="muted small" v-if="d.seconds">⏱ {{ formatSpeakingTime(d.seconds) }}</span>
                </button>
                <div v-if="openDays[d.session_id]" class="daybody">
                  <p v-if="dayCache[d.session_id] && dayCache[d.session_id].loading" class="muted small">…</p>
                  <p v-else-if="dayCache[d.session_id] && dayCache[d.session_id].error" class="muted small">
                    {{ $t('profile.speechesLoadError') }}
                  </p>
                  <ul v-else-if="dayCache[d.session_id]" class="speechlist">
                    <li v-for="s in dayCache[d.session_id].list" :key="s.uid">
                      <router-link :to="{ name: 'viewer', params: { uid: s.uid } }" class="speechitem">
                        <div class="row small" style="gap:.6rem;">
                          <span class="badge" v-if="s.agenda_type">{{ agendaLabel(s.agenda_type) }}</span>
                          <span class="muted" v-if="s.duration">⏱ {{ formatDuration(s.duration) }}</span>
                        </div>
                        <p class="excerpt">{{ s.excerpt || (s.has_text ? '' : $t('viewer.videoOnly')) }}</p>
                      </router-link>
                    </li>
                  </ul>
                </div>
              </li>
            </ul>
            <button v-if="speechDays && speechDays.days.length > COLLAPSE_LIMIT" type="button" class="btn small showmore"
              :aria-expanded="expanded.days" @click="expanded.days = !expanded.days">
              {{ expanded.days ? $t('profile.showLess') : $t('profile.showMore') }}
            </button>
          </section>
        </div>
      </div>
    </div>
  </StateBlock>
</template>

<style scoped>
.phead { display: flex; gap: 1.2rem; align-items: center; margin: .8rem 0 1rem; flex-wrap: wrap; }
.phead h1 { margin: 0 0 .4rem; }
/* activity board sits in the header corner; can scroll horizontally if wide */
.pactivity { margin-left: auto; max-width: 100%; min-width: 0; }
.pgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 1.2rem; align-items: start; }
.pcol { display: flex; flex-direction: column; gap: 1.2rem; }
.bignums { display: flex; gap: 2rem; flex-wrap: wrap; }
.bignums .num { font-size: 1.8rem; font-weight: 800; color: var(--accent); display: block; }
.bignums .lbl { font-size: .82rem; color: var(--ink-faint); }
.bignums .biglink { text-decoration: none; }
.bignums .pct { font-size: 1rem; font-weight: 700; color: var(--ink-soft); }
.bignums .biglink:hover { text-decoration: underline; }
.bills-note { margin-top: .8rem; }
/* heading + help-icon row; the icon carries the section's description (REP-5) */
.sechead { display: flex; align-items: center; gap: .35rem; margin-bottom: .6rem; }
.sechead h2 { margin: 0; }
.timeline, .plain { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .4rem; }
.timeline li { display: flex; gap: .6rem; align-items: center; }
.billmini { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .4rem; }
.billitem { display: flex; flex-direction: column; gap: .3rem; padding: .5rem .7rem; border-radius: 8px; color: var(--ink); border: 1px solid var(--line); }
.billitem:hover { background: var(--accent-soft); text-decoration: none; }
.billitem-head { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap; }
.billitem .bnum { font-weight: 800; color: var(--accent); }
.billitem .btitle { color: var(--ink); }
.votemini { list-style: none; padding: 0; margin: .5rem 0 0; display: flex; flex-direction: column; gap: .4rem; }
/* the vote chip sits in a bottom-right gutter (absolute) so its varying width
   (Igen / Tartózkodás …) never shifts the subject text's left edge */
.voteitem { position: relative; display: block; padding: .5rem 5.5rem .5rem .7rem; border-radius: 8px; color: var(--ink); border: 1px solid var(--line); }
.voteitem:hover { background: var(--accent-soft); text-decoration: none; }
.vmeta { display: flex; flex-direction: column; gap: .15rem; min-width: 0; }
.vsub { font-weight: 600; }
.vchip { position: absolute; right: .7rem; bottom: .5rem; font-size: .72rem; font-weight: 700; line-height: 1.6; padding: 0 .5rem; border-radius: 999px; color: #fff; white-space: nowrap; }
.vchip.yes { background: #2e7d32; } .vchip.no { background: #c62828; }
.vchip.abstain { background: #8a8780; } .vchip.novote { background: #c79a2e; }
.vchip.absent { background: #7c8288; } .vchip.other { background: #777; }
/* sitting-day spoilers: a clickable header row, body revealed on expand */
.daylist { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .5rem; }
.dayitem { border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.dayhead {
  display: flex; align-items: center; gap: .6rem; width: 100%; text-align: left;
  padding: .6rem .7rem; background: transparent; border: 0; cursor: pointer; color: var(--ink);
}
.dayhead:hover { background: var(--accent-soft); }
.dayhead .caret { color: var(--ink-faint); font-size: .8rem; }
.dayhead .daycount { margin-right: auto; }
.daybody { padding: 0 .7rem .6rem; }
.daybody .speechlist { margin-top: .2rem; }
.daybody .speechitem { border-color: var(--line); }
.speechlist { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .5rem; }
.speechitem { display: block; padding: .6rem .7rem; border-radius: 8px; color: var(--ink); border: 1px solid var(--line); }
.speechitem:hover { background: var(--accent-soft); text-decoration: none; }
.excerpt { margin: .3rem 0 0; color: var(--ink-soft); font-size: .9rem; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
/* quiet, full-width "show more / less" toggle for a collapsed long list */
.showmore {
  display: block; width: 100%; margin-top: .7rem; padding: .5rem;
  background: transparent; color: var(--ink-soft);
  border: 1px solid var(--line); border-radius: 8px; font-weight: 600;
}
.showmore:hover { background: var(--accent-soft); color: var(--accent); border-color: var(--accent-soft); }
@media (max-width: 820px) { .pgrid { grid-template-columns: 1fr; } }
</style>
