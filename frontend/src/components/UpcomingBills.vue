<script setup>
// The irományok the House is about to vote on or debate, each with where it
// stands (HOME-5, `GET /bills/upcoming`).
//
// The order paper card (NR-5) answers "what happens on which day"; this one turns
// the same napirend round to the bills: one entry per iromány, under "up for a
// vote" when a vote on it is still ahead and "up for debate" otherwise. A
// procedural step (urgency, exceptional procedure) never puts a bill under "vote"
// on its own — that is a vote on how the bill is handled, not on the bill.
//
// It is a digest, kept to two lines a bill so it does not push the week digest
// and the order paper down the page: the number and title, then the steps that
// put the bill in its group (its votes, or its debates) beside its stage on the
// legislative diagram (BILL-4). What does not fit — parlament.hu's own status
// line, the stage as the napirend prints it, the bill's other steps — is in the
// tooltips, on the bill page, and in the order paper card right below.
//
// It is a plan like the order paper, so it carries the same provenance line, and
// it drops the days already behind it the same way: on the Hungarian calendar,
// counting today as still coming. Once nothing is ahead it renders nothing at all
// — a highlight with nothing to highlight is not shown as an empty box, and a
// failed load leaves the order paper card below to say so.
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../api.js'
import { formatDateLocal } from '../format.js'

const { t, locale } = useI18n()
const data = ref(null)

// How many bills each group shows before "+N more". A Monday order paper can
// carry a dozen votes, and this block is a pointer, not the list.
const SHOWN = 4
const SHOWN_NARROW = 3
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
const shownCount = computed(() => (narrow.value ? SHOWN_NARROW : SHOWN))
const expanded = reactive({})

const today = formatDateLocal(new Date().toISOString())
const tomorrow = (() => {
  const d = new Date(today + 'T12:00:00Z')
  d.setUTCDate(d.getUTCDate() + 1)
  return d.toISOString().slice(0, 10)
})()

// A step dated by weekday alone cannot be judged, and is kept (as NR-5 keeps it).
const ahead = (s) => !s.date || s.date >= today

const groups = computed(() => {
  const bills = (data.value && data.value.bills) || []
  const vote = []
  const debate = []
  bills.forEach((b, order) => {
    const steps = b.steps.filter(ahead)
    if (!steps.length) return
    const kind = steps.some((s) => s.kind === 'vote') ? 'vote' : 'debate'
    let lead = steps.filter((s) => s.kind === kind)
    if (!lead.length) lead = steps.slice(0, 1)     // only procedure / other ahead
    const entry = { ...b, lead, order, key: lead[0].date || '' }
    ;(kind === 'vote' ? vote : debate).push(entry)
  })
  // Soonest first; on the same day, in the order the napirend takes them.
  const bySoonest = (a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : a.order - b.order)
  return [
    { kind: 'vote', items: vote.sort(bySoonest) },
    { kind: 'debate', items: debate.sort(bySoonest) },
  ].filter((g) => g.items.length)
})

const agenda = computed(() => data.value && data.value.agenda)

// A group only one over the limit is shown whole: "+1 more" costs the line it saves.
const limitOf = (group) => (group.items.length > shownCount.value + 1
  ? shownCount.value : group.items.length)

function shown(group) {
  return expanded[group.kind] ? group.items : group.items.slice(0, limitOf(group))
}

// A törvényjavaslat's canonical page is /bills/:id, every other iromány's
// /documents/:id (SEO-2).
function billRoute(b) {
  return { name: b.main_type === 'T' ? 'bill' : 'document', params: { id: b.bill_id } }
}

function kindLabel(step) {
  if (step.kind === 'vote' && step.final) return t('upcomingBills.kinds.finalVote')
  return t('upcomingBills.kinds.' + step.kind)
}

function intlDate(iso, opts) {
  const d = new Date(iso + 'T12:00:00')
  if (Number.isNaN(d.getTime())) return iso
  try {
    return new Intl.DateTimeFormat(locale.value === 'en' ? 'en-US' : 'hu-HU', opts).format(d)
  } catch { return iso }
}

// Short, since it sits in a chip: "ma", "holnap", "okt. 19.".
function dayLabel(iso) {
  if (!iso) return ''
  if (iso === today) return t('upcomingBills.today')
  if (iso === tomorrow) return t('upcomingBills.tomorrow')
  return intlDate(iso, { month: 'short', day: 'numeric' })
}

// The tooltip carries what the chip leaves out: the weekday, and the stage as
// the napirend prints it ("Döntés az összegző módosító javaslatról és a
// zárószavazás").
function stepTitle(step) {
  const day = step.date ? intlDate(step.date, { month: 'long', day: 'numeric', weekday: 'long' }) : ''
  return [day, step.label].filter(Boolean).join(' — ')
}

function stampLabel(stamp) {
  if (!stamp) return ''
  const d = new Date(stamp)
  if (Number.isNaN(d.getTime())) return stamp
  try {
    return new Intl.DateTimeFormat(locale.value === 'en' ? 'en-US' : 'hu-HU', {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    }).format(d)
  } catch { return stamp }
}

// Where the bill stands on the upstream diagram: the furthest stage it reached.
// The diagram's steps are not always reached in order (an exceptional procedure
// skips the general debate), so each segment shows its own state rather than a
// bar filled up to the current one.
function currentStage(b) {
  const i = b.stages.findIndex((s) => s.current)
  return i < 0 ? null : { n: i + 1, total: b.stages.length, label: b.stages[i].label }
}

// parlament.hu's own status line ("zárószavazásra vár"). Shown in place of the
// stage where the iromány has no diagram (a personnel motion), in the tooltip
// otherwise.
function statusText(b) {
  const s = (b.status || '').trim()
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : ''
}

async function load() {
  try {
    data.value = await api.upcomingBills()
  } catch {
    data.value = null
  }
}
onMounted(load)
</script>

<template>
  <section v-if="groups.length" class="card pad ub" aria-labelledby="ub-title">
    <div class="ub__head">
      <h2 id="ub-title">{{ $t('upcomingBills.title') }}</h2>
      <!-- Still a plan: which document said so, and as of when (TRUST-1). -->
      <p v-if="agenda" class="ub__source soft">
        <a :href="agenda.url" target="_blank" rel="noopener">
          {{ agenda.label || $t('upcoming.sourceDoc') }}<span aria-hidden="true"> ↗</span>
        </a>
        <template v-if="agenda.status_at">
          · {{ $t('upcoming.asOf', { at: stampLabel(agenda.status_at) }) }}
        </template>
      </p>
    </div>

    <div class="ub__groups">
      <div v-for="g in groups" :key="g.kind" class="ub__group" :class="'is-' + g.kind">
        <div class="ub__subhead">
          <h3 :id="'ub-group-' + g.kind">
            {{ $t('upcomingBills.groups.' + g.kind) }}
            <span class="ub__count">{{ g.items.length }}</span>
          </h3>
          <button
            v-if="g.items.length > limitOf(g)" type="button" class="ub__more"
            :aria-expanded="!!expanded[g.kind]" :aria-controls="'ub-list-' + g.kind"
            @click="expanded[g.kind] = !expanded[g.kind]"
          >
            {{ expanded[g.kind]
                ? $t('upcomingBills.showLess')
                : $t('upcomingBills.showMore', { n: g.items.length - limitOf(g) }) }}
          </button>
        </div>
        <ul :id="'ub-list-' + g.kind" class="ub__list" :aria-labelledby="'ub-group-' + g.kind">
          <li v-for="b in shown(g)" :key="b.bill_id" class="ub__bill">
            <router-link :to="billRoute(b)" class="ub__title" :title="b.title || undefined">
              <span class="ub__num">{{ b.bill_number }}</span> {{ b.title }}
            </router-link>
            <div class="ub__row">
              <span class="ub__steps">
                <span
                  v-for="(s, i) in b.lead" :key="i" class="ub__step"
                  :class="['is-' + s.kind, { final: s.final }]" :title="stepTitle(s) || undefined"
                >
                  <span class="ub__kind">{{ kindLabel(s) }}</span>
                  <time v-if="s.date" :datetime="s.date">{{ dayLabel(s.date) }}</time>
                </span>
              </span>
              <span
                v-if="currentStage(b)" class="ub__state"
                :title="statusText(b) ? $t('upcomingBills.statusTip', { status: statusText(b) }) : undefined"
              >
                <span class="ub__stages" aria-hidden="true">
                  <span
                    v-for="(s, i) in b.stages" :key="i" class="seg"
                    :class="{ done: s.done, current: s.current }"
                  ></span>
                </span>
                <span class="ub__stage">
                  <span class="visually-hidden">{{ $t('upcomingBills.stageAria', currentStage(b)) }}</span>
                  <span aria-hidden="true">{{ currentStage(b).label }}</span>
                  <span v-if="statusText(b)" class="visually-hidden">— {{ statusText(b) }}</span>
                </span>
              </span>
              <span v-else-if="statusText(b)" class="ub__state ub__stage">{{ statusText(b) }}</span>
            </div>
          </li>
        </ul>
      </div>
    </div>
  </section>
</template>

<style scoped>
.ub__head {
  display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between;
  gap: .1rem 1rem; margin-bottom: .6rem;
}
.ub__head h2 { margin: 0; font-size: 1.15rem; }
.ub__source { margin: 0; font-size: .78rem; }
.ub__source a { color: var(--focus); }

.ub__groups {
  display: grid; gap: 1rem 1.6rem;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
}
.ub__group { min-width: 0; }
.ub__subhead {
  display: flex; align-items: baseline; justify-content: space-between; gap: .6rem;
  padding-bottom: .25rem; border-bottom: 2px solid var(--line);
}
.ub__group.is-vote .ub__subhead { border-bottom-color: var(--accent); }
.ub__group.is-debate .ub__subhead { border-bottom-color: var(--focus); }
.ub__subhead h3 {
  display: flex; align-items: center; gap: .4rem; margin: 0;
  font-size: .8rem; font-weight: 700; color: var(--ink-faint);
  text-transform: uppercase; letter-spacing: .04em;
}
.ub__count {
  font-size: .7rem; color: var(--ink-soft); letter-spacing: 0;
  background: #f0eee8; border-radius: 999px; padding: .02rem .4rem;
}
/* The week digest's own "+N more" — a text control, not a button block. */
.ub__more {
  flex: none; padding: 0; border: 0; background: none; cursor: pointer;
  color: var(--accent); font: inherit; font-size: .78rem; font-weight: 700;
}
.ub__more:hover { text-decoration: underline; }

.ub__list { list-style: none; margin: 0; padding: 0; }
.ub__bill { padding: .4rem 0; }
.ub__bill + .ub__bill { border-top: 1px solid var(--line); }

/* One line of title; the whole of it is in the tooltip and on the bill page.
   line-clamp rather than nowrap, which would widen the grid track on a phone. */
.ub__title {
  display: -webkit-box; -webkit-line-clamp: 1; -webkit-box-orient: vertical; overflow: hidden;
  color: var(--ink); font-size: .86rem; font-weight: 600; line-height: 1.35;
}
.ub__title:hover { color: var(--accent); }
.ub__num { font-variant-numeric: tabular-nums; font-weight: 700; color: var(--ink-soft); }

.ub__row {
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
  gap: .15rem .8rem; margin-top: .2rem; font-size: .75rem;
}
.ub__steps { display: inline-flex; flex-wrap: wrap; gap: .2rem .6rem; }
.ub__step { display: inline-flex; align-items: baseline; gap: .3rem; cursor: default; }
.ub__step time { font-weight: 600; color: var(--ink-soft); }
.ub__kind {
  font-size: .66rem; font-weight: 700; text-transform: uppercase; letter-spacing: .03em;
  border-radius: 3px; padding: .02rem .32rem; white-space: nowrap;
  background: #f0eee8; color: var(--ink-soft);
}
.ub__step.is-vote .ub__kind { background: var(--accent-soft); color: var(--accent); }
.ub__step.is-vote.final .ub__kind { background: var(--accent); color: var(--accent-ink); }
.ub__step.is-debate .ub__kind { background: #e3eef6; color: var(--focus); }

.ub__state { display: inline-flex; align-items: center; gap: .4rem; min-width: 0; cursor: help; }
.ub__stages { display: inline-flex; gap: 2px; flex: none; }
.ub__stages .seg { width: 9px; height: 5px; border-radius: 2px; background: var(--line); }
.ub__stages .seg.done { background: #8fb3cf; }
.ub__stages .seg.current { background: var(--focus); }
.ub__stage { color: var(--ink-faint); }

@media (max-width: 560px) {
  .ub__groups { grid-template-columns: minmax(0, 1fr); }
  /* A phone column is too narrow for one line to say which bill it is. */
  .ub__title { -webkit-line-clamp: 2; }
}
</style>
