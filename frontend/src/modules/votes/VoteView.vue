<script setup>
// Single vote detail (Votes module). Shows the vote's metadata and the bill(s)
// it decided (linked to the Bills module, EXT-2), the per-faction breakdown, and
// the full per-MP roll call grouped by vote value — each MP linked to their
// profile through the shared person entity (EXT-2).
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../../api.js'
import { formatDateTime } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'

const props = defineProps({ id: String })

const vote = ref(null)
const loading = ref(false)
const error = ref(false)

// The roll-call columns, in a fixed, meaningful order.
const VALUE_ORDER = ['yes', 'no', 'abstain', 'novote', 'absent', 'other']

const groups = computed(() => {
  if (!vote.value) return []
  const by = {}
  for (const r of vote.value.records) (by[r.value_code] ||= []).push(r)
  return VALUE_ORDER
    .filter((code) => by[code] && by[code].length)
    .map((code) => ({ code, records: by[code] }))
})

function factionParts(fs) {
  const yes = fs.yes || 0, no = fs.no || 0, abstain = fs.abstain || 0
  const total = yes + no + abstain || 1
  return { yes: (100 * yes) / total, no: (100 * no) / total, abstain: (100 * abstain) / total }
}

async function load() {
  loading.value = true; error.value = false; vote.value = null
  try { vote.value = await api.vote(props.id) } catch { error.value = true }
  finally { loading.value = false }
}
onMounted(load)
watch(() => props.id, load)
</script>

<template>
  <StateBlock :loading="loading" :error="error" @retry="load">
    <article v-if="vote" class="vote">
      <router-link :to="{ name: 'votes' }" class="small back">‹ {{ $t('votes.title') }}</router-link>

      <header class="card pad vhead">
        <div class="row" style="gap:.5rem; align-items:center; flex-wrap:wrap;">
          <span class="vdate">{{ formatDateTime(vote.vote_datetime) }}</span>
          <span class="badge status" :class="{ ok: vote.result === $t('votes.accepted') }">{{ vote.result }}</span>
        </div>
        <h1>{{ vote.subject }}</h1>
        <dl class="meta">
          <template v-if="vote.voting_mode">
            <dt>{{ $t('votes.votingMode') }}</dt><dd>{{ vote.voting_mode }}</dd>
          </template>
          <template v-if="vote.total_votes != null">
            <dt>{{ $t('votes.totalVotes') }}</dt><dd>{{ vote.total_votes }}</dd>
          </template>
          <template v-if="vote.remark">
            <dt>{{ $t('votes.remark') }}</dt><dd>{{ vote.remark }}</dd>
          </template>
        </dl>
        <p class="muted small" v-if="vote.source_url" style="margin:.4rem 0 0;">
          <a :href="vote.source_url" target="_blank" rel="noopener">↗ {{ $t('votes.viewOnParlament') }}</a>
        </p>

        <div v-if="vote.subjects.length" class="vbills">
          <span class="muted small">{{ $t('votes.decidedBills') }}:</span>
          <span v-for="(s, i) in vote.subjects" :key="i" class="vbill">
            <router-link v-if="s.bill_id" :to="{ name: 'bill', params: { id: s.bill_id } }">
              <strong>{{ s.bill_number }}</strong> — {{ s.title }}
            </router-link>
            <span v-else><strong>{{ s.bill_number }}</strong> — {{ s.title }}</span>
          </span>
        </div>

        <div class="tallycounts">
          <span class="c yes"><b>{{ vote.yes ?? 0 }}</b> {{ $t('votes.yes') }}</span>
          <span class="c no"><b>{{ vote.no ?? 0 }}</b> {{ $t('votes.no') }}</span>
          <span class="c abstain"><b>{{ vote.abstain ?? 0 }}</b> {{ $t('votes.abstain') }}</span>
        </div>
      </header>

      <!-- Per-faction breakdown -->
      <section v-if="vote.faction_stats.length" class="card pad">
        <h2>{{ $t('votes.byFaction') }}</h2>
        <div class="tablewrap">
          <table class="dtable">
            <thead><tr>
              <th>{{ $t('votes.byFaction') }}</th>
              <th class="num">{{ $t('votes.yes') }}</th><th class="num">{{ $t('votes.no') }}</th>
              <th class="num">{{ $t('votes.abstain') }}</th><th class="num">{{ $t('votes.novote') }}</th>
              <th class="num">{{ $t('votes.absent') }}</th><th class="num">{{ $t('votes.total') }}</th>
              <th></th>
            </tr></thead>
            <tbody>
              <tr v-for="(fs, i) in vote.faction_stats" :key="i">
                <td class="fname">
                  <span class="dot" :style="{ background: fs.faction_color || '#999' }" aria-hidden="true"></span>
                  {{ fs.faction_name }}
                </td>
                <td class="num">{{ fs.yes ?? 0 }}</td><td class="num">{{ fs.no ?? 0 }}</td>
                <td class="num">{{ fs.abstain ?? 0 }}</td><td class="num">{{ fs.not_voting ?? 0 }}</td>
                <td class="num">{{ fs.absent ?? 0 }}</td><td class="num"><b>{{ fs.total ?? 0 }}</b></td>
                <td class="fbarcell">
                  <span class="fbar" aria-hidden="true">
                    <span class="seg yes" :style="{ width: factionParts(fs).yes + '%' }"></span>
                    <span class="seg no" :style="{ width: factionParts(fs).no + '%' }"></span>
                    <span class="seg abstain" :style="{ width: factionParts(fs).abstain + '%' }"></span>
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Per-MP roll call -->
      <section class="card pad">
        <h2>{{ $t('votes.rollCall') }}</h2>
        <template v-if="groups.length">
          <p class="muted small rc-note">{{ $t('votes.rollCallNote') }}</p>
          <div class="rollcall">
            <div v-for="g in groups" :key="g.code" class="rcgroup" :class="g.code">
              <h3 class="rctitle">
                <span class="rcdot" :class="g.code" aria-hidden="true"></span>
                {{ $t('votes.' + g.code) }} <span class="rccount">{{ g.records.length }}</span>
              </h3>
              <ul class="rclist">
                <li v-for="(r, i) in g.records" :key="i">
                  <span class="rcfdot" :style="{ background: r.faction_color || '#bbb' }" aria-hidden="true"></span>
                  <router-link v-if="r.person_id" :to="{ name: 'profile', params: { id: r.person_id } }">{{ r.name }}</router-link>
                  <span v-else>{{ r.name }}</span>
                  <span class="muted small rcfac">{{ r.faction_name }}</span>
                </li>
              </ul>
            </div>
          </div>
        </template>
        <p v-else class="muted small">{{ $t('votes.noRollCall') }}</p>
      </section>
    </article>
  </StateBlock>
</template>

<style scoped>
.vote { display: flex; flex-direction: column; gap: 1rem; }
.back { display: inline-block; }
.vhead { margin-top: .6rem; }
.vhead h1 { margin: .5rem 0 .3rem; font-size: 1.25rem; }
.vdate { font-weight: 800; color: var(--accent); font-variant-numeric: tabular-nums; }
.badge.status { background: var(--accent-soft); color: var(--accent); }
.badge.status.ok { background: #eef6ee; color: #2e7d32; }
.meta {
  display: grid; grid-template-columns: max-content 1fr; gap: .35rem .9rem;
  margin: .9rem 0 0; padding-top: .9rem; border-top: 1px solid var(--line); font-size: .9rem;
}
.meta dt { color: var(--ink-faint); font-weight: 600; }
.meta dd { margin: 0; }
.vbills { display: flex; flex-direction: column; gap: .25rem; margin: .9rem 0 0; }
.vbill { font-size: .92rem; }

.tallycounts { display: flex; gap: 1.4rem; margin-top: 1.1rem; font-size: .95rem; }
.tallycounts .c b { font-variant-numeric: tabular-nums; font-size: 1.05rem; }
.tallycounts .c.yes { color: #2e7d32; }
.tallycounts .c.no { color: #c62828; }
.tallycounts .c.abstain { color: var(--ink-faint); }

.vote section > h2 {
  display: flex; align-items: center; gap: .55rem; margin: 0 0 .8rem; font-size: 1.15rem;
}
.vote section > h2::before {
  content: ''; flex: none; width: .28rem; height: 1.05rem; background: var(--accent); border-radius: 2px;
}

/* Faction table */
.tablewrap { overflow-x: auto; }
.dtable { width: 100%; border-collapse: collapse; font-size: .9rem; }
.dtable th, .dtable td { text-align: left; padding: .45rem .6rem; border-bottom: 1px solid var(--line); white-space: nowrap; }
.dtable tr:last-child td { border-bottom: 0; }
.dtable th { color: var(--ink-faint); font-weight: 600; }
.dtable th.num, .dtable td.num { text-align: right; font-variant-numeric: tabular-nums; }
.fname { display: flex; align-items: center; gap: .45rem; font-weight: 600; }
.dot { width: .7rem; height: .7rem; border-radius: 50%; flex: none; display: inline-block; }
.fbarcell { width: 8rem; }
.fbar { display: flex; height: .65rem; width: 8rem; border-radius: 999px; overflow: hidden; background: var(--line); }
.fbar .seg.yes { background: #2e7d32; }
.fbar .seg.no { background: #c62828; }
.fbar .seg.abstain { background: #b9b6ad; }

/* Roll call grouped by vote value */
.rc-note { margin: 0 0 1rem; }
.rollcall { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 1.2rem; }
.rctitle { display: flex; align-items: center; gap: .45rem; font-size: 1rem; margin: 0 0 .5rem; padding-bottom: .4rem; border-bottom: 2px solid var(--line); }
.rccount { margin-left: auto; font-variant-numeric: tabular-nums; color: var(--ink-faint); font-weight: 700; }
.rcdot { width: .8rem; height: .8rem; border-radius: 50%; flex: none; background: var(--line); }
.rcdot.yes { background: #2e7d32; } .rcdot.no { background: #c62828; }
.rcdot.abstain { background: #b9b6ad; } .rcdot.novote { background: #d8b14a; }
.rcdot.absent { background: #9aa0a6; } .rcdot.other { background: #777; }
.rclist { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: .25rem; }
.rclist li { display: flex; align-items: center; gap: .4rem; font-size: .9rem; }
.rcfdot { width: .55rem; height: .55rem; border-radius: 50%; flex: none; }
.rcfac { margin-left: auto; }
@media (max-width: 560px) { .rcfac { display: none; } }
</style>
