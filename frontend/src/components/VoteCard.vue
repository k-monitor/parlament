<script setup>
// One vote as a list card: when, how it ended, what it decided, the tally as a
// bar and in numbers, its attendance and cross-voting. Shared by the vote list
// (§7) and the votes scope of the site search (SEA-14).
//
// `personScoped` marks a list scoped to one MP's roll call (EXT-2), where every
// vote carries that MP's own cast value and the card shows it as a chip.
// Emits `open` when the reader follows the card to the vote (SEA-12).
import { formatDateTime } from '../format.js'
import { motionLocation } from '../lib/motionLink.js'

defineProps({
  vote: { type: Object, required: true },
  personScoped: { type: Boolean, default: false },
})
const emit = defineEmits(['open'])

// How many defecting factions a card names before collapsing the rest into "+N".
const CROSS_FACTIONS_SHOWN = 4

// Chip colour for an MP's own cast value (matches the roll-call palette); a
// "nem volt jelen" vote has no record, so it falls through to the neutral class.
const VOTE_CLASS = { yes: 'yes', no: 'no', abstain: 'abstain', novote: 'novote', absent: 'absent' }

// Attendance (votes cast / seats held) as served by the API — shown on every card
// so the attendance orderings are readable, and null on a vote with no
// per-faction breakdown (a list or show-of-hands vote).
function attendancePct(v) {
  return v.attendance == null ? '' : Math.round(v.attendance * 100) + '%'
}

// Cross-voting: the MPs who broke their own faction's line, as served by the API.
// Null on a vote with no per-faction breakdown and on a presence check (where the
// figure would not mean dissent), so the chip simply doesn't appear there.
function crossPct(v) {
  if (v.defector_share == null) return ''
  return (100 * v.defector_share).toLocaleString('hu-HU',
    { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + '%'
}
function crossFactions(v) {
  return (v.defector_factions || []).slice(0, CROSS_FACTIONS_SHOWN)
}
function crossFactionsMore(v) {
  return Math.max(0, (v.defector_factions || []).length - CROSS_FACTIONS_SHOWN)
}

function voteParts(v) {
  const yes = v.yes || 0, no = v.no || 0, abstain = v.abstain || 0
  const total = yes + no + abstain || 1
  return { yes: (100 * yes) / total, no: (100 * no) / total, abstain: (100 * abstain) / total }
}
</script>

<template>
  <li class="card pad votecard">
    <div class="vhead">
      <router-link :to="{ name: 'vote', params: { id: vote.id } }" class="vdate" @click="emit('open')">{{ formatDateTime(vote.vote_datetime) }}</router-link>
      <span class="badge" :class="{ ok: vote.result === $t('votes.accepted') }">{{ vote.result }}</span>
      <span v-if="vote.has_per_mp" class="badge rollcall">{{ $t('votes.rollCall') }}</span>
      <!-- How this MP voted (only in the person-scoped list). -->
      <span v-if="personScoped" class="mpvote" :class="VOTE_CLASS[vote.person_value_code] || 'notpresent'">
        {{ vote.person_value_code ? $t('votes.' + vote.person_value_code) : $t('profile.vbNotPresent') }}
      </span>
    </div>
    <router-link :to="{ name: 'vote', params: { id: vote.id } }" class="vsubject" @click="emit('open')">{{ vote.subject }}</router-link>
    <div v-if="vote.subjects.length" class="vbills small">
      <span class="muted">{{ $t('votes.decidedBills') }}:</span>
      <span v-for="(s, i) in vote.subjects" :key="i" class="vbill">
        <router-link v-if="s.bill_id" :to="{ name: 'bill', params: { id: s.bill_id } }">
          <strong>{{ s.bill_number }}</strong><template v-if="s.title"> — {{ s.title }}</template>
        </router-link>
        <!-- A motion (amendment, consolidated text) links to its entry on its
             bill's page, where its text is. -->
        <router-link v-else-if="s.motion" :to="motionLocation(s.motion.bill_id, s.bill_number)">
          <strong>{{ s.bill_number }}</strong> {{ s.motion.type }}<template v-if="s.title"> — {{ s.title }}</template>
        </router-link>
        <span v-else><strong>{{ s.bill_number }}</strong><template v-if="s.title"> — {{ s.title }}</template></span>
      </span>
    </div>
    <div class="vbar" role="img"
         :aria-label="`${$t('votes.yes')} ${vote.yes ?? 0}, ${$t('votes.no')} ${vote.no ?? 0}, ${$t('votes.abstain')} ${vote.abstain ?? 0}`">
      <span class="seg yes" :style="{ width: voteParts(vote).yes + '%' }"></span>
      <span class="seg no" :style="{ width: voteParts(vote).no + '%' }"></span>
      <span class="seg abstain" :style="{ width: voteParts(vote).abstain + '%' }"></span>
    </div>
    <div class="vcounts small">
      <span class="c yes"><b>{{ vote.yes ?? 0 }}</b> {{ $t('votes.yes') }}</span>
      <span class="c no"><b>{{ vote.no ?? 0 }}</b> {{ $t('votes.no') }}</span>
      <span class="c abstain"><b>{{ vote.abstain ?? 0 }}</b> {{ $t('votes.abstain') }}</span>
      <span v-if="vote.attendance != null" class="c att"
            :title="$t('votes.attendanceTitle', { present: vote.present, seats: vote.seats })">
        {{ $t('votes.attendance') }} <b>{{ attendancePct(vote) }}</b>
      </span>
      <span v-if="vote.defectors" class="c cross"
            :title="$t('votes.crossVotingTitle', { n: vote.defectors, pct: crossPct(vote) })">
        <b>{{ vote.defectors }}</b> {{ $t('votes.crossVoting') }} ({{ crossPct(vote) }})
      </span>
    </div>
    <!-- Which factions actually split — the cross-voting number broken down,
         so the ranking is readable without opening the vote. -->
    <div v-if="vote.defectors" class="vcross small muted">
      <span v-for="fx in crossFactions(vote)" :key="fx.name" class="xf">
        <span class="dot" :style="{ background: fx.color || '#bbb' }" aria-hidden="true"></span>
        {{ fx.name }} <b>{{ fx.defectors }}</b>
      </span>
      <span v-if="crossFactionsMore(vote)" class="xf">+{{ crossFactionsMore(vote) }}</span>
    </div>
  </li>
</template>

<style scoped>
/* the MP's own cast value on each card, in the roll-call palette */
.mpvote { font-size: .72rem; font-weight: 700; line-height: 1.6; padding: 0 .5rem; border-radius: 999px; color: #fff; white-space: nowrap; }
.mpvote.yes { background: #2e7d32; } .mpvote.no { background: #c62828; }
.mpvote.abstain { background: #8a8780; } .mpvote.novote { background: #c79a2e; }
.mpvote.absent { background: #7c8288; } .mpvote.notpresent { background: #3f434a; }
.votecard { display: flex; flex-direction: column; gap: .45rem; }
.vhead { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; }
.vdate { font-weight: 800; color: var(--accent); font-variant-numeric: tabular-nums; }
.vsubject { color: var(--ink); font-weight: 600; }
.vsubject:hover { color: var(--accent); }
.badge.ok { background: #eef6ee; color: #2e7d32; }
.badge.rollcall { background: var(--accent-soft); color: var(--accent); }
.vbills { display: flex; flex-direction: column; gap: .2rem; }
.vbills .vbill strong { font-variant-numeric: tabular-nums; }
.vbar { display: flex; height: .7rem; border-radius: 999px; overflow: hidden; background: var(--line); box-shadow: inset 0 0 0 1px rgba(0,0,0,.04); }
.vbar .seg.yes { background: #2e7d32; }
.vbar .seg.no { background: #c62828; }
.vbar .seg.abstain { background: #b9b6ad; }
.vcounts { display: flex; gap: 1.2rem; }
.vcounts .c b { font-variant-numeric: tabular-nums; }
.vcounts .c.yes { color: #2e7d32; }
.vcounts .c.no { color: #c62828; }
.vcounts .c.abstain { color: var(--ink-faint); }
/* attendance and cross-voting sit apart from the yes/no/abstain triplet — they are
   derived measures, not tallies */
.vcounts .c.att { color: var(--ink-faint); margin-left: auto; cursor: help; }
/* cross-voting always follows attendance (a vote only has one if it has the
   other), so it rides that item's margin-left:auto to the right edge */
.vcounts .c.cross { color: var(--ink-faint); cursor: help; }
/* the factions behind the cross-voting number — right-aligned so they sit under
   it rather than under the yes/no/abstain tallies */
.vcross { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: .15rem .8rem; }
.vcross .xf { display: inline-flex; align-items: center; gap: .3rem; white-space: nowrap; }
.vcross .dot { width: .5rem; height: .5rem; border-radius: 50%; flex: none; display: inline-block; }
</style>
