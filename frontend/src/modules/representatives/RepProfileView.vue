<script setup>
// Representative profile (REP-2/REP-3/REP-5). Shows bio + faction history, the
// precomputed statistics (with explicit scope + methodology), an accessible
// trend chart, and a reverse-chronological speech list linking into the viewer.
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { api } from '../../api.js'
import { store, loadMeta } from '../../store.js'
import { formatDate, formatSpeakingTime, formatDuration, agendaLabel } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import BarChart from '../../components/BarChart.vue'
import ActivityBoard from '../../components/ActivityBoard.vue'
import HelpTip from '../../components/HelpTip.vue'

const props = defineProps({ id: String })

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
const speeches = ref(null)
const bills = ref(null)
const votes = ref(null)
const loading = ref(false)
const error = ref(false)

// The submitted-irományok section lists every document type the MP submitted; a
// filter narrows it to one main type (the iromány-number prefix), e.g. T to see
// only their bills (törvényjavaslatok). `docTypes` holds the codes this MP
// actually has, so the dropdown only offers relevant options.
const docTypes = ref([])
const docFilter = ref('')   // '' = all types; else a main_type code

// The bills/votes sections are only meaningful when their module is live (EXT-6);
// a disabled module simply means no section, not an error.
const showBills = computed(() => store.moduleEnabled('bills'))
const showVotes = computed(() => store.moduleEnabled('votes'))

// Long lists (bills, votes, speeches) are collapsed to a preview so the profile
// stays scannable; a per-section toggle reveals the rest of what's loaded.
const COLLAPSE_LIMIT = 8
const expanded = reactive({ bills: false, votes: false, speeches: false })
function shown(list, key) {
  return expanded[key] ? list : list.slice(0, COLLAPSE_LIMIT)
}

const PLACEHOLDER =
  'data:image/svg+xml;utf8,' + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="110" height="110"><rect width="110" height="110" fill="%23e7e5df"/><circle cx="55" cy="44" r="22" fill="%23bdb9af"/><rect x="18" y="74" width="74" height="40" rx="20" fill="%23bdb9af"/></svg>')
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

// Fetch (or re-fetch) the submitted irományok with the current type filter.
async function loadBills() {
  if (!showBills.value) { bills.value = null; return }
  const params = { sponsor: props.id, limit: 100, period: store.cycle }
  if (docFilter.value) params.main_type = docFilter.value
  bills.value = await api.bills(params).catch(() => null)
  expanded.bills = false
}

async function load() {
  loading.value = true; error.value = false
  profile.value = stats.value = activity.value = speeches.value = bills.value = votes.value = null
  docFilter.value = ''; docTypes.value = []
  expanded.bills = expanded.votes = expanded.speeches = false
  try {
    // Everything on the profile is scoped to the global cycle (store.cycle; null
    // = all cycles), so the page never mixes in a previous cycle's data (§4A).
    const period = store.cycle
    // A feature-module failure must not break the profile, so each resolves to
    // null on error (and is skipped entirely when its module is disabled).
    const billsReq = showBills.value
      ? api.bills({ sponsor: props.id, limit: 100, period }).catch(() => null)
      : Promise.resolve(null)
    const facetsReq = showBills.value
      ? api.billFacets({ sponsor: props.id, period }).catch(() => null)
      : Promise.resolve(null)
    const votesReq = showVotes.value
      ? api.repVotes(props.id, { limit: 20, period }).catch(() => null)
      : Promise.resolve(null)
    // The activity board is part of the representatives module (always on); a
    // failure must not break the profile, so it resolves to null on error.
    const activityReq = api.repActivity(props.id, period).catch(() => null)
    const [p, s, act, sp, b, fac, v] = await Promise.all([
      api.representative(props.id, period),
      api.repStatistics(props.id, period),
      activityReq,
      api.repSpeeches(props.id, { limit: 50, period }),
      billsReq,
      facetsReq,
      votesReq,
    ])
    profile.value = p; stats.value = s; activity.value = act
    speeches.value = sp; bills.value = b; votes.value = v
    docTypes.value = fac ? [...new Set(fac.types.map((x) => x.main_type).filter(Boolean))] : []
  } catch { error.value = true } finally { loading.value = false }
}
// Gate the first fetch on the manifest so store.cycle is resolved to the latest
// cycle before we query (otherwise the profile would briefly be scoped to "all").
onMounted(async () => { await loadMeta().catch(() => {}); load() })
watch(() => props.id, load)
// Re-fetch when the user switches the type filter or the global cycle.
watch(docFilter, loadBills)
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
          <section class="card pad" v-if="showBills && bills && (bills.total || docFilter)">
            <div class="billhead">
              <h2>{{ $t('profile.documents') }} <span class="muted small">({{ bills.total }})</span></h2>
              <label v-if="docTypes.length > 1" class="docfilter">
                <span class="visually-hidden">{{ $t('profile.documentType') }}</span>
                <select v-model="docFilter">
                  <option value="">{{ $t('profile.allDocuments') }}</option>
                  <option v-for="c in docTypes" :key="c" :value="c">{{ $t('documents.mainType.' + c) }}</option>
                </select>
              </label>
            </div>
            <p v-if="!bills.total" class="small muted">{{ $t('documents.noResults') }}</p>
            <ul class="billmini">
              <li v-for="b in shown(bills.bills, 'bills')" :key="b.id">
                <router-link :to="{ name: 'bill', params: { id: b.id } }" class="billitem">
                  <span class="billitem-head">
                    <span class="bnum">{{ b.bill_number }}</span>
                    <span class="badge" v-if="b.status">{{ b.status }}</span>
                  </span>
                  <span class="btitle">{{ b.title }}</span>
                </router-link>
              </li>
            </ul>
            <button v-if="bills.bills.length > COLLAPSE_LIMIT" type="button" class="btn small showmore"
              :aria-expanded="expanded.bills" @click="expanded.bills = !expanded.bills">
              {{ expanded.bills ? $t('profile.showLess') : $t('profile.showMore') }}
            </button>
          </section>

          <section class="card pad" v-if="showVotes && votes && votes.total">
            <div class="sechead">
              <h2>{{ $t('profile.votes') }} <span class="muted small">({{ votes.total }})</span></h2>
              <HelpTip :label="$t('profile.votes')">
                <p>{{ $t('profile.votesNote') }}</p>
              </HelpTip>
            </div>
            <ul class="votemini">
              <li v-for="v in shown(votes.votes, 'votes')" :key="v.id">
                <router-link :to="{ name: 'vote', params: { id: v.id } }" class="voteitem">
                  <span class="vchip" :class="VOTE_CLASS[v.value_code] || 'other'">{{ $t('votes.' + v.value_code) }}</span>
                  <span class="vmeta">
                    <span class="vsub">{{ v.subject }}</span>
                    <span class="muted small">
                      {{ formatDate(v.vote_datetime) }}
                      <template v-for="(s, i) in v.subjects" :key="i"> · {{ s.bill_number }}</template>
                    </span>
                  </span>
                </router-link>
              </li>
            </ul>
            <button v-if="votes.votes.length > COLLAPSE_LIMIT" type="button" class="btn small showmore"
              :aria-expanded="expanded.votes" @click="expanded.votes = !expanded.votes">
              {{ expanded.votes ? $t('profile.showLess') : $t('profile.showMore') }}
            </button>
          </section>

          <section class="card pad">
            <h2>{{ $t('profile.speeches') }} <span class="muted small" v-if="speeches">({{ speeches.total }})</span></h2>
            <p v-if="speeches && speeches.total === 0" class="muted">{{ $t('profile.noSpeeches') }}</p>
            <ul v-else-if="speeches" class="speechlist">
              <li v-for="s in shown(speeches.speeches, 'speeches')" :key="s.uid">
                <router-link :to="{ name: 'viewer', params: { uid: s.uid } }" class="speechitem">
                  <div class="row small" style="gap:.6rem;">
                    <strong>{{ formatDate(s.date) }}</strong>
                    <span class="badge" v-if="s.agenda_type">{{ agendaLabel(s.agenda_type) }}</span>
                    <span class="muted" v-if="s.duration">⏱ {{ formatDuration(s.duration) }}</span>
                  </div>
                  <p class="excerpt">{{ s.excerpt || (s.has_text ? '' : $t('viewer.videoOnly')) }}</p>
                </router-link>
              </li>
            </ul>
            <button v-if="speeches && speeches.speeches.length > COLLAPSE_LIMIT" type="button" class="btn small showmore"
              :aria-expanded="expanded.speeches" @click="expanded.speeches = !expanded.speeches">
              {{ expanded.speeches ? $t('profile.showLess') : $t('profile.showMore') }}
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
.billhead { display: flex; gap: .6rem; align-items: baseline; justify-content: space-between; flex-wrap: wrap; margin-bottom: .6rem; }
.billhead h2 { margin: 0; }
.docfilter select { font-size: .85rem; padding: .15rem .4rem; }
.billmini { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .4rem; }
.billitem { display: flex; flex-direction: column; gap: .3rem; padding: .5rem .7rem; border-radius: 8px; color: var(--ink); border: 1px solid var(--line); }
.billitem:hover { background: var(--accent-soft); text-decoration: none; }
.billitem-head { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap; }
.billitem .bnum { font-weight: 800; color: var(--accent); }
.billitem .btitle { color: var(--ink); }
.votemini { list-style: none; padding: 0; margin: .5rem 0 0; display: flex; flex-direction: column; gap: .4rem; }
.voteitem { display: flex; gap: .6rem; align-items: flex-start; padding: .5rem .7rem; border-radius: 8px; color: var(--ink); border: 1px solid var(--line); }
.voteitem:hover { background: var(--accent-soft); text-decoration: none; }
.vmeta { display: flex; flex-direction: column; gap: .15rem; min-width: 0; }
.vsub { font-weight: 600; }
.vchip { flex: none; font-size: .72rem; font-weight: 700; line-height: 1.6; padding: 0 .5rem; border-radius: 999px; color: #fff; white-space: nowrap; }
.vchip.yes { background: #2e7d32; } .vchip.no { background: #c62828; }
.vchip.abstain { background: #8a8780; } .vchip.novote { background: #c79a2e; }
.vchip.absent { background: #7c8288; } .vchip.other { background: #777; }
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
