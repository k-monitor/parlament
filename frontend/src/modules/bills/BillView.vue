<script setup>
// Single bill detail (Bills module). Shows the bill's metadata, sponsors (each
// MP linked to their profile, EXT-2), the legislative-stage timeline, and the
// full detail sheet from parlament.hu: event history, votes, committee events,
// negotiating committees, deadlines, justification/background documents and the
// non-self-standing motion summary. Links to the official text (LEGAL-1).
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { api } from '../../api.js'
import { formatDate, formatDateTime } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import FactionBadge from '../../components/FactionBadge.vue'

const props = defineProps({ id: String })

const bill = ref(null)
const loading = ref(false)
const error = ref(false)
// The embedded PDF is heavy, so it isn't loaded until the user asks: the <iframe>
// is only rendered (and its src only fetched) after clicking "show document".
const docRevealed = ref(false)
// Per-motion PDF reveal (keyed by motion index), same on-demand pattern as the
// main bill: a motion's <iframe> only exists once its toggle is clicked.
const motionOpen = reactive({})
function toggleMotion(i) { motionOpen[i] = !motionOpen[i] }

// Header key/value pairs, only those the bill actually carries.
const meta = computed(() => {
  const b = bill.value
  if (!b) return []
  const rows = [
    ['subtype', b.subtype],
    ['character', b.character],
    ['negotiationMode', b.negotiation_mode],
    ['currentEvent', b.current_event],
    ['promulgationNumber', b.promulgation_number],
    ['mkNumber', b.mk_number],
    ['promulgationDate', b.promulgation_date ? formatDate(b.promulgation_date) : null],
    ['lastModifier', b.last_modifier],
    ['remark', b.remark],
  ]
  return rows.filter(([, v]) => v != null && v !== '')
})

// A promulgated bill links to its Magyar Közlöny issue: prefer the direct
// gazette PDF, fall back to the issue-listing page.
const kozlonyHref = computed(() => bill.value?.kozlony_doc_url || bill.value?.kozlony_url || null)

function voteParts(v) {
  const yes = v.yes || 0, no = v.no || 0, abstain = v.abstain || 0
  const total = yes + no + abstain || 1
  return {
    total: yes + no + abstain,
    yes: (100 * yes) / total,
    no: (100 * no) / total,
    abstain: (100 * abstain) / total,
  }
}

// A document is shown by both the bills page (törvényjavaslat) and the other
// irományok page; the back link returns to whichever list it belongs to.
const isBill = computed(() => bill.value?.main_type === 'T')
const backLink = computed(() => (isBill.value
  ? { to: { name: 'bills' }, label: 'bills.title' }
  : { to: { name: 'documents' }, label: 'documents.title' }))

async function load() {
  loading.value = true; error.value = false; bill.value = null; docRevealed.value = false
  Object.keys(motionOpen).forEach((k) => delete motionOpen[k])
  try { bill.value = await api.bill(props.id) } catch { error.value = true }
  finally { loading.value = false }
}
onMounted(load)
watch(() => props.id, load)
</script>

<template>
  <StateBlock :loading="loading" :error="error" @retry="load">
    <article v-if="bill" class="bill">
      <router-link :to="backLink.to" class="small back">‹ {{ $t(backLink.label) }}</router-link>

      <header class="card pad bhead">
        <div class="row" style="gap:.5rem; align-items:center; flex-wrap:wrap;">
          <span class="bnum">{{ bill.bill_number }}</span>
          <span class="badge status" v-if="bill.status">{{ bill.status }}</span>
          <span class="muted small" v-if="bill.type">{{ bill.type }}</span>
        </div>
        <h1>{{ bill.title }}</h1>
        <p class="muted small" v-if="bill.submitted_date">
          {{ $t('bills.submittedDate') }}: {{ formatDate(bill.submitted_date) }}
        </p>
        <dl v-if="meta.length || kozlonyHref" class="meta">
          <template v-for="[k, v] in meta" :key="k">
            <dt>{{ $t('bills.meta.' + k) }}</dt>
            <dd>{{ v }}</dd>
          </template>
          <template v-if="kozlonyHref">
            <dt>{{ $t('bills.meta.kozlony') }}</dt>
            <dd><a :href="kozlonyHref" target="_blank" rel="noopener">{{ $t('bills.kozlonyLink') }}</a></dd>
          </template>
        </dl>
      </header>

      <section v-if="bill.stages && bill.stages.length" class="card pad">
        <h2>{{ $t('bills.timeline') }}</h2>
        <p class="muted small timeline-legend">{{ $t('bills.timelineNote') }}</p>
        <ol class="timeline" :aria-label="$t('bills.timeline')">
          <li
            v-for="(s, i) in bill.stages" :key="i"
            class="tstep" :class="{ done: s.done, current: s.current, future: !s.done }"
            :aria-current="s.current ? 'step' : undefined"
          >
            <span class="tmarker" aria-hidden="true">{{ s.current ? '●' : (s.done ? '✓' : '') }}</span>
            <span class="tlabel">{{ s.label }}</span>
            <span class="visually-hidden">
              — {{ s.current ? $t('bills.stageCurrent') : (s.done ? $t('bills.stageDone') : $t('bills.stagePending')) }}
            </span>
          </li>
        </ol>
      </section>

      <section class="card pad">
        <h2>{{ $t('bills.submitters') }}</h2>
        <ul class="plain">
          <li v-for="(s, i) in bill.sponsors" :key="i" class="sponsor">
            <router-link v-if="s.person_id" :to="{ name: 'profile', params: { id: s.person_id } }">{{ s.name }}</router-link>
            <span v-else>{{ s.name }}</span>
            <FactionBadge v-if="s.faction" :faction="s.faction" />
          </li>
        </ul>
      </section>

      <!-- Votes -->
      <section v-if="bill.votes && bill.votes.length" class="card pad">
        <h2>{{ $t('bills.votes') }}</h2>
        <ul class="plain votes">
          <li v-for="(v, i) in bill.votes" :key="i" class="vote">
            <div class="vote-head">
              <router-link v-if="v.vote_ref" :to="{ name: 'vote', params: { id: v.vote_ref } }" class="vsubject link">{{ v.subject }}</router-link>
              <span v-else class="vsubject">{{ v.subject }}</span>
              <span class="badge" :class="{ ok: v.result === 'Elfogadva' }">{{ v.result }}</span>
              <router-link v-if="v.vote_ref" :to="{ name: 'vote', params: { id: v.vote_ref } }" class="echip link rollcall">{{ $t('bills.viewRollCall') }}</router-link>
            </div>
            <p class="muted small vdate" v-if="v.vote_date">{{ formatDateTime(v.vote_date) }}</p>
            <div class="vbar" role="img"
                 :aria-label="`${$t('bills.yes')} ${v.yes ?? 0}, ${$t('bills.no')} ${v.no ?? 0}, ${$t('bills.abstain')} ${v.abstain ?? 0}`">
              <span class="seg yes" :style="{ width: voteParts(v).yes + '%' }"></span>
              <span class="seg no" :style="{ width: voteParts(v).no + '%' }"></span>
              <span class="seg abstain" :style="{ width: voteParts(v).abstain + '%' }"></span>
            </div>
            <div class="vcounts small">
              <span class="c yes"><b>{{ v.yes ?? 0 }}</b> {{ $t('bills.yes') }}</span>
              <span class="c no"><b>{{ v.no ?? 0 }}</b> {{ $t('bills.no') }}</span>
              <span class="c abstain"><b>{{ v.abstain ?? 0 }}</b> {{ $t('bills.abstain') }}</span>
            </div>
          </li>
        </ul>
      </section>

      <!-- Event history -->
      <section v-if="bill.events && bill.events.length" class="card pad">
        <h2>{{ $t('bills.events') }}</h2>
        <ol class="etimeline">
          <li v-for="(e, i) in bill.events" :key="i" class="ev" :class="{ vote: e.vote_id }">
            <span class="evdot" aria-hidden="true"></span>
            <time class="evdate">{{ formatDateTime(e.event_date) }}</time>
            <div class="evbody">
              <span class="ename">{{ e.name }}</span>
              <span v-if="e.related_label" class="erel muted small">
                <router-link v-if="e.person_id" :to="{ name: 'profile', params: { id: e.person_id } }">{{ e.person_name || e.related_label }}</router-link>
                <span v-else>{{ e.related_label }}</span>
              </span>
              <router-link v-if="e.speech_number && e.speech_uid" class="echip mic link"
                :to="{ name: 'viewer', params: { uid: e.speech_uid } }"
                :title="$t('bills.viewSpeech')">🎙 {{ e.speech_number }}</router-link>
              <span v-else-if="e.speech_number" class="echip mic" :title="$t('bills.speechNumber')">🎙 {{ e.speech_number }}</span>
              <span v-if="e.vote_id" class="echip vote">{{ $t('bills.voteTag') }}</span>
            </div>
          </li>
        </ol>
      </section>

      <!-- Committee events -->
      <section v-if="bill.committee_events && bill.committee_events.length" class="card pad">
        <h2>{{ $t('bills.committeeEvents') }}</h2>
        <div class="tablewrap">
          <table class="dtable">
            <thead><tr>
              <th>{{ $t('bills.date') }}</th><th>{{ $t('bills.committee') }}</th>
              <th>{{ $t('bills.event') }}</th><th>{{ $t('bills.amendment') }}</th>
              <th>{{ $t('bills.report') }}</th>
            </tr></thead>
            <tbody>
              <tr v-for="(c, i) in bill.committee_events" :key="i">
                <td class="nowrap muted small">{{ formatDate(c.event_date) }}</td>
                <td>{{ c.committee }}</td>
                <td>
                  {{ c.name }}
                  <span v-if="c.person_label" class="muted small">
                    —
                    <router-link v-if="c.person_id" :to="{ name: 'profile', params: { id: c.person_id } }">{{ c.person_name || c.person_label }}</router-link>
                    <span v-else>{{ c.person_label }}</span>
                  </span>
                </td>
                <td>{{ c.amendment || '—' }}</td>
                <td>{{ c.report || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Compact reference sections: side-by-side on wide screens -->
      <div class="cols">
        <section v-if="bill.committees && bill.committees.length" class="card pad">
          <h2>{{ $t('bills.committees') }}</h2>
          <ul class="plain">
            <li v-for="(c, i) in bill.committees" :key="i">
              <strong>{{ c.committee }}</strong>
              <span v-if="c.role" class="muted small"> — {{ c.role }}</span>
              <span v-if="c.reference" class="muted small"> ({{ c.reference }})</span>
            </li>
          </ul>
        </section>

        <section v-if="bill.deadlines && bill.deadlines.length" class="card pad">
          <h2>{{ $t('bills.deadlines') }}</h2>
          <div class="tablewrap">
            <table class="dtable">
              <thead><tr>
                <th>{{ $t('bills.name') }}</th><th>{{ $t('bills.deadline') }}</th>
                <th>{{ $t('bills.reference') }}</th>
              </tr></thead>
              <tbody>
                <tr v-for="(d, i) in bill.deadlines" :key="i">
                  <td>{{ d.name }}</td>
                  <td class="nowrap">{{ formatDateTime(d.deadline) }}</td>
                  <td class="muted small">{{ d.reference }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section v-if="bill.motion_summary && bill.motion_summary.length" class="card pad">
          <h2>{{ $t('bills.motionSummary') }}</h2>
          <div class="tablewrap">
            <table class="dtable">
              <thead><tr>
                <th>{{ $t('bills.type') }}</th><th class="num">{{ $t('bills.valid') }}</th>
                <th class="num">{{ $t('bills.withdrawn') }}</th><th class="num">{{ $t('bills.total') }}</th>
              </tr></thead>
              <tbody>
                <tr v-for="(m, i) in bill.motion_summary" :key="i">
                  <td>{{ m.type }}</td>
                  <td class="num">{{ m.valid ?? 0 }}</td>
                  <td class="num">{{ m.withdrawn ?? 0 }}</td>
                  <td class="num">{{ m.total ?? 0 }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <!-- Non-self-standing motions: the individual dependent irományok, each
           linked to the bill with its own number, type, submitters and PDF. -->
      <section v-if="bill.motions && bill.motions.length" class="card pad">
        <h2>{{ $t('bills.motions') }}</h2>
        <ul class="plain motions">
          <li v-for="(m, i) in bill.motions" :key="i" class="motion">
            <div class="motion-head">
              <span class="mnum">{{ m.bill_number }}</span>
              <span class="mtype">{{ m.type }}</span>
              <span v-if="m.submitted_date" class="muted small nowrap">· {{ formatDate(m.submitted_date) }}</span>
            </div>
            <div v-if="m.sponsors && m.sponsors.length" class="motion-sub small muted">
              <span v-for="(s, j) in m.sponsors" :key="j" class="msponsor">
                <router-link v-if="s.person_id" :to="{ name: 'profile', params: { id: s.person_id } }">{{ s.name }}</router-link>
                <span v-else>{{ s.name }}</span>
                <FactionBadge v-if="s.faction" :faction="s.faction" />
              </span>
            </div>
            <div v-if="m.text_url" class="docbar">
              <button type="button" class="btn small" :aria-expanded="!!motionOpen[i]" @click="toggleMotion(i)">
                {{ motionOpen[i] ? $t('bills.hideDocument') : $t('bills.showDocument') }}
              </button>
              <a :href="m.text_url" target="_blank" rel="noopener" class="small">↗ {{ $t('bills.openInNewTab') }}</a>
            </div>
            <p v-else class="muted small">{{ $t('bills.noText') }}</p>
            <!-- iframe (and thus the PDF fetch) only exists once revealed -->
            <div v-if="motionOpen[i] && m.text_url" class="docframe">
              <iframe :src="m.text_url" :title="m.text_caption || m.bill_number" loading="lazy"></iframe>
            </div>
          </li>
        </ul>
      </section>

      <!-- Documents: justifications + background materials -->
      <section v-if="bill.documents && bill.documents.length" class="card pad">
        <h2>{{ $t('bills.documents') }}</h2>
        <ul class="plain docs">
          <li v-for="(d, i) in bill.documents" :key="i" class="doc">
            <span class="echip" :class="d.kind">{{ $t('bills.docKind.' + d.kind) }}</span>
            <a v-if="d.url" :href="d.url" target="_blank" rel="noopener">{{ d.title }}</a>
            <span v-else>{{ d.title }}</span>
            <span v-if="d.published" class="muted small"> · {{ d.published }}</span>
            <span v-else-if="d.doc_date" class="muted small"> · {{ formatDate(d.doc_date) }}</span>
          </li>
        </ul>
      </section>

      <section class="card pad">
        <h2>{{ $t('bills.source') }}</h2>
        <template v-if="bill.text_url">
          <div class="docbar">
            <button type="button" class="btn" :aria-expanded="docRevealed" @click="docRevealed = !docRevealed">
              {{ docRevealed ? $t('bills.hideDocument') : $t('bills.showDocument') }}
            </button>
            <a :href="bill.text_url" target="_blank" rel="noopener" class="small">↗ {{ $t('bills.openInNewTab') }}</a>
          </div>
          <!-- iframe (and thus the PDF fetch) only exists once revealed -->
          <div v-if="docRevealed" class="docframe">
            <iframe :src="bill.text_url" :title="bill.text_caption || bill.bill_number" loading="lazy"></iframe>
          </div>
        </template>
        <p v-else class="muted small">{{ $t('bills.noText') }}</p>
        <p class="small">
          <a :href="bill.source_url" target="_blank" rel="noopener">{{ $t('bills.viewOnParlament') }}</a>
        </p>
      </section>
    </article>
  </StateBlock>
</template>

<style scoped>
.bill { display: flex; flex-direction: column; gap: 1rem; }
.back { display: inline-block; }
.bhead { margin-top: .6rem; }
.bhead h1 { margin: .5rem 0 .3rem; font-size: 1.3rem; }
.bnum { font-weight: 800; color: var(--accent); font-size: 1.15rem; letter-spacing: .01em; }
.badge.status { background: var(--accent-soft); color: var(--accent); }
.meta {
  display: grid; grid-template-columns: max-content 1fr; gap: .35rem .9rem;
  margin: .9rem 0 0; padding-top: .9rem; border-top: 1px solid var(--line); font-size: .9rem;
}
.meta dt { color: var(--ink-faint); font-weight: 600; }
.meta dd { margin: 0; }

/* Each detail section's title gets a small accent tick for scannability. */
.bill section > h2 {
  display: flex; align-items: center; gap: .55rem;
  margin: 0 0 .8rem; font-size: 1.15rem;
}
.bill section > h2::before {
  content: ''; flex: none; width: .28rem; height: 1.05rem;
  background: var(--accent); border-radius: 2px;
}

.plain { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .5rem; }
.sponsor { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; }

/* Reference sections stack full-width like the rest of the page. (A side-by-side
   grid was tried but these cards vary wildly in height — a one-line committee
   next to a six-row deadline table — so any fixed columns left ragged gaps.) */
.cols { display: flex; flex-direction: column; gap: 1rem; }
/* Keep wide tables from stretching to sparse full width on large screens. */
.cols .dtable, .cols .plain { max-width: 760px; }

/* Vertical past→future legislative-stage timeline. Each step's connector (the
   line above its dot) is solid when reached, dashed when still upcoming. */
.timeline-legend { margin: 0 0 1rem; }
.timeline { list-style: none; margin: 0; padding: 0; }
.tstep {
  position: relative; display: flex; align-items: center; gap: .8rem;
  padding: .55rem 0 .55rem 2.2rem; min-height: 1.4rem;
}
.tmarker {
  position: absolute; left: 0; width: 1.7rem; height: 1.7rem; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: .85rem; font-weight: 800; z-index: 1; background: var(--surface);
  border: 2px solid var(--line); color: var(--ink-faint);
}
/* connector line running up from each dot to the previous one */
.tstep::before {
  content: ''; position: absolute; left: calc(0.85rem - 1px); top: -0.55rem;
  width: 2px; height: 0.55rem; background: var(--line);
}
.tstep:first-child::before { display: none; }
.tstep.done .tmarker { background: var(--accent); border-color: var(--accent); color: var(--accent-ink, #fff); }
.tstep.done::before { background: var(--accent); }
.tstep.current .tmarker { box-shadow: 0 0 0 3px var(--accent-soft); }
.tstep.future .tmarker { background: var(--surface); }
/* the segment leading into a not-yet-reached step is dashed/muted */
.tstep.future::before {
  background: none;
  border-left: 2px dashed var(--line); width: 0; left: calc(0.85rem - 1px);
}
.tlabel { font-size: .95rem; }
.tstep.future .tlabel { color: var(--ink-faint); }
.tstep.current .tlabel { font-weight: 700; }

/* Votes */
.votes { gap: 0; }
.vote { padding: .9rem 0; border-top: 1px solid var(--line); }
.vote:first-child { padding-top: 0; border-top: 0; }
.vote-head { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; }
.vsubject { font-weight: 600; }
.vsubject.link { color: var(--accent); }
.echip.rollcall { background: var(--accent-soft); color: var(--accent); }
.vdate { margin: .15rem 0 .45rem; }
.badge.ok { background: #eef6ee; color: #2e7d32; }
.vbar { display: flex; height: .8rem; border-radius: 999px; overflow: hidden; margin: 0 0 .45rem; background: var(--line); box-shadow: inset 0 0 0 1px rgba(0,0,0,.04); }
.vbar .seg { transition: width .3s ease; }
.vbar .seg.yes { background: #2e7d32; }
.vbar .seg.no { background: #c62828; }
.vbar .seg.abstain { background: #b9b6ad; }
.vcounts { display: flex; gap: 1.2rem; }
.vcounts .c b { font-variant-numeric: tabular-nums; }
.vcounts .c.yes { color: #2e7d32; }
.vcounts .c.no { color: #c62828; }
.vcounts .c.abstain { color: var(--ink-faint); }

/* Event history — a vertical rail with a dot per event (red dot = a vote). */
.etimeline { list-style: none; margin: 0; padding: 0; }
.ev {
  position: relative; display: grid; grid-template-columns: 8.5rem 1fr;
  gap: .15rem .9rem; align-items: start; padding: .45rem 0 .45rem 1.5rem;
}
.ev::before {
  content: ''; position: absolute; left: calc(.35rem - 1px); top: 0; bottom: 0;
  width: 2px; background: var(--line);
}
.ev:first-child::before { top: .9rem; }
.ev:last-child::before { bottom: auto; height: .9rem; }
.evdot {
  position: absolute; left: 0; top: .8rem; width: .7rem; height: .7rem; border-radius: 50%;
  background: var(--surface); border: 2px solid var(--line); z-index: 1;
}
.ev.vote .evdot { background: var(--accent); border-color: var(--accent); }
.evdate { color: var(--ink-faint); font-size: .82rem; padding-top: .12rem; white-space: nowrap; font-variant-numeric: tabular-nums; }
.evbody { display: flex; flex-wrap: wrap; gap: .35rem .55rem; align-items: baseline; }
.ename { font-weight: 500; }

/* small inline chips (event speech/vote tags + document kind) */
.echip {
  display: inline-block; font-size: .72rem; font-weight: 600; line-height: 1.5;
  padding: 0 .45rem; border-radius: 999px; white-space: nowrap;
  background: var(--line); color: var(--ink-soft);
}
.echip.mic { background: var(--accent-soft); color: var(--accent); }
.echip.link { text-decoration: none; cursor: pointer; }
.echip.link:hover { background: var(--accent); color: #fff; }
.echip.vote { background: #eef6ee; color: #2e7d32; }
.echip.justification { background: var(--accent-soft); color: var(--accent); }
.echip.background { background: #f0eee8; color: var(--ink-soft); }

/* Tables */
.tablewrap { overflow-x: auto; }
.dtable { width: 100%; border-collapse: collapse; font-size: .9rem; }
.dtable th, .dtable td { text-align: left; padding: .45rem .6rem; border-bottom: 1px solid var(--line); vertical-align: top; }
.dtable tr:last-child td { border-bottom: 0; }
.dtable th { color: var(--ink-faint); font-weight: 600; }
.dtable th.num, .dtable td.num { text-align: right; font-variant-numeric: tabular-nums; }
.nowrap { white-space: nowrap; }
.docs .doc { display: flex; gap: .5rem; align-items: baseline; flex-wrap: wrap; }
.docs a { word-break: break-word; }

/* Non-self-standing motions */
.motions { display: flex; flex-direction: column; gap: .9rem; }
.motion { padding-bottom: .9rem; border-bottom: 1px solid var(--line); }
.motion:last-child { padding-bottom: 0; border-bottom: 0; }
.motion-head { display: flex; gap: .5rem; align-items: baseline; flex-wrap: wrap; }
.motion-head .mnum { font-weight: 800; color: var(--accent); font-variant-numeric: tabular-nums; }
.motion-head .mtype { font-weight: 600; }
.motion-sub { display: flex; flex-wrap: wrap; gap: .15rem .6rem; align-items: center; margin: .3rem 0 .55rem; }
.msponsor { display: inline-flex; align-items: center; gap: .45rem; }
.msponsor:not(:last-child)::after { content: ','; color: var(--ink-faint); margin-left: -.45rem; }

.docbar { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; }
.docframe { margin-top: .9rem; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.docframe iframe { display: block; width: 100%; height: 80vh; border: 0; }

@media (max-width: 560px) {
  .ev { grid-template-columns: 1fr; gap: 0; }
  .evdate { padding-top: 0; }
}
</style>
