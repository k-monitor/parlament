<script setup>
// One committee's sheet (§6F / BIZ-4): who sits on it, when it met, and what it
// handled.
//
// Four questions, four tabs under the title, because a committee of a full
// cycle answers each of them at length and stacked on one page they bury each
// other: the record (what it did, with the headline figures above it), the
// roster, the irományok it discussed, and the ones it tabled. Which tab is open
// is URL state (§CYC-5), so a link can point at a committee's meeting record
// rather than always at its membership.
//
// The sheet's own payload — roster, subcommittees, counts — arrives with the
// page, since nothing can be drawn without it; the meeting record and the two
// document lists are paged endpoints of their own.
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { loadMeta } from '../../store.js'
import { formatDate, formatDateTime, formatSpeakingTime } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import SpeakerLink from '../../components/SpeakerLink.vue'
import CommitteeAgendaPreview from '../../components/CommitteeAgendaPreview.vue'
import Pagination from '../../components/Pagination.vue'

const props = defineProps({ id: { type: String, required: true } })
const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const PAGE = 25
const TABS = ['overview', 'members', 'discussed', 'tabled']
// Where the previous layout's tabs point now. The meeting record moved into the
// overview, and the recordings tab is gone — a recording belongs to the sitting
// it is of, so it rides on that sitting's row instead of being a list of its
// own. Links made before this layout still land somewhere real.
const LEGACY_TABS = { meetings: 'overview', videos: 'overview' }
// The two document tabs keep the names their count cards give them, so the pill
// and the number a reader clicked to get there read the same.
const TAB_LABELS = {
  overview: 'committees.overviewTab',
  members: 'committees.membersTab',
  discussed: 'committees.discussed',
  tabled: 'committees.tabled',
}

// The registry's own words for the ordinary case. A sitting is normally open
// and quorate (3 044 and 3 626 of the corpus's 3 725), so printing that on
// every row says nothing; what earns a chip is the sitting that was closed or
// that could not vote. Compared against the source's Hungarian, which is data
// and does not follow the interface language.
const OPEN_SITTING = 'nyilvános'
const QUORATE = 'Határozatképes'

const data = ref(null)
const list = ref(null)
const loading = ref(false)
const error = ref(false)
const listLoading = ref(false)
const listError = ref(false)
const allFormer = ref(false)

// A cycle's membership churn can run to dozens of names; shown whole they push
// the rest of the roster below the fold, so only the most recent few open by
// default.
const FORMER_SHOWN = 6

const tab = computed(() => {
  const q = LEGACY_TABS[route.query.tab] || route.query.tab
  return TABS.includes(q) ? q : 'overview'
})
const page = computed(() => Math.floor((Number(route.query.offset) || 0) / PAGE))
const totalPages = computed(() => (list.value ? Math.ceil(list.value.total / PAGE) : 0))

// The roster grouped by role, so the chair is not buried among the members.
// The API already orders by seniority; this only cuts it into headed blocks.
const memberGroups = computed(() => {
  const all = (data.value && data.value.members) || []
  const out = []
  for (const role of ['chair', 'deputy-chair', 'member', 'other']) {
    const items = all.filter((m) => m.role === role)
    if (items.length) out.push({ role, items })
  }
  return out
})

const formerShown = computed(() => {
  const all = (data.value && data.value.formerMembers) || []
  return allFormer.value ? all : all.slice(0, FORMER_SHOWN)
})
const formerHidden = computed(() => {
  const all = (data.value && data.value.formerMembers) || []
  return allFormer.value ? 0 : Math.max(0, all.length - FORMER_SHOWN)
})

// Upstream's aggregate counts sittings it does not list individually, so the
// two numbers genuinely differ; the page reports the listed rows and keeps the
// aggregate as the headline rather than silently preferring one.
const listedMeetings = computed(() => (data.value ? data.value.counts.meetings : 0))

// A tab as an address rather than as a click handler: these are four views of
// one committee, so each is linkable and middle-clickable, and the default one
// drops the parameter altogether so the committee keeps one clean URL. The rest
// of the query (the cycle scope, above all) rides along untouched.
function tabTo(name) {
  return { query: {
    ...route.query,
    tab: name === 'overview' ? undefined : name,
    offset: undefined,
  } }
}
function setTab(name) {
  router.push(tabTo(name))
}
function goto(p) {
  router.push({ query: { ...route.query, offset: p * PAGE || undefined } })
}

function speaker(m) {
  return { person_id: m.personId, label: m.name, photo_uri: m.photoUri }
}

function memberTerm(m) {
  if (m.current && !m.dateStart) return null
  const start = formatDate(m.dateStart)
  const end = formatDate(m.dateEnd)
  if (start && end) return `${start} – ${end}`
  if (start) return t('committees.since', { date: start })
  if (end) return t('committees.until', { date: end })
  return null
}

// Where a meeting row goes when it is clicked, so the row itself is the link
// and nothing on it has to be aimed at separately. A sitting whose minutes we
// have read opens in the viewer; so does one we have only a recording of
// (BIZ-27), which the viewer serves from the meeting row alone. Failing both,
// the House's own PDF where it published one. A sitting with none of the three
// is not a link at all: it keeps its row and says so (BIZ-9).
function readable(m) {
  return !!(m.speeches || (m.videos && m.videos.length))
}
function rowTag(m) {
  if (readable(m)) return 'router-link'
  return m.minutesUrl ? 'a' : 'div'
}
function rowProps(m) {
  if (readable(m)) {
    return { to: { name: 'committee-minutes', params: { meetingId: m.id } } }
  }
  if (m.minutesUrl) {
    return { href: m.minutesUrl, target: '_blank', rel: 'noopener' }
  }
  return {}
}
// The agenda hover (BIZ-4b). Offered only for a sitting whose jegyzőkönyv we
// have read, since the agenda is parsed out of that document: hovering anything
// else would fire a request that can only come back empty.
const agenda = ref(null)
function peek(m, ev) {
  if (m.speeches) agenda.value?.open(m.id, ev.currentTarget)
}

function videoLabel(v) {
  const base = t('committees.watchOnYoutube')
  return v.continued ? `${base} (${t('committees.videoPart')})` : base
}

let headSeq = 0
async function loadHead() {
  const seq = ++headSeq
  loading.value = true; error.value = false
  try {
    const res = await api.committee(props.id)
    if (seq === headSeq) data.value = res
  } catch {
    if (seq === headSeq) error.value = true
  } finally {
    if (seq === headSeq) loading.value = false
  }
}

let listSeq = 0
async function loadList() {
  // The seq moves even for the roster, so a list still in flight when the
  // reader switches to it cannot land underneath the tab that replaced it.
  const seq = ++listSeq
  // The roster came with the sheet: nothing to fetch, and the stale list is
  // dropped so switching back re-reads the page it is actually on.
  if (tab.value === 'members') {
    list.value = null; listLoading.value = false; listError.value = false
    return
  }
  listLoading.value = true; listError.value = false
  const params = { limit: PAGE, offset: page.value * PAGE }
  try {
    const res = tab.value === 'overview'
      ? await api.committeeMeetings(props.id, params)
      : await api.committeeDocuments(props.id, { ...params, role: tab.value })
    if (seq === listSeq) list.value = res
  } catch {
    if (seq === listSeq) listError.value = true
  } finally {
    if (seq === listSeq) listLoading.value = false
  }
}

onMounted(async () => {
  await loadMeta().catch(() => {})
  loadHead(); loadList()
})
watch(() => props.id, () => { allFormer.value = false; loadHead(); loadList() })
watch(() => [route.query.tab, route.query.offset].join('|'), loadList)
</script>

<template>
  <p class="small">
    <RouterLink :to="{ name: 'committees' }">‹ {{ $t('committees.backToList') }}</RouterLink>
  </p>

  <StateBlock :loading="loading" :error="error" @retry="loadHead">
    <div v-if="data">
      <h1>{{ data.name }}</h1>
      <p class="muted small sub">
        <span v-if="data.kind">{{ $t('committees.kinds.' + data.kind) }}</span>
        <template v-if="data.parentId">
          <span v-if="data.kind" aria-hidden="true"> · </span>
          <RouterLink :to="{ name: 'committee', params: { id: data.parentId } }">
            {{ $t('committees.subcommitteesOf', { name: data.parentName }) }}
          </RouterLink>
        </template>
        <span aria-hidden="true"> · </span>
        {{ $t('committees.created') }}: {{ formatDate(data.dateStart) }}
        <template v-if="data.dateEnd">
          <span aria-hidden="true"> · </span>
          {{ $t('committees.ended') }}: {{ formatDate(data.dateEnd) }}
        </template>
      </p>

      <p class="small links">
        <a v-if="data.siteUrl" :href="data.siteUrl" target="_blank" rel="noopener">
          {{ $t('committees.site') }} ↗
        </a>
        <!-- The address is published obfuscated upstream ("x[kukac]…"); it is
             shown exactly as published and never turned into a mailto:, which
             would undo the protection the House chose for it. -->
        <span v-if="data.email" class="muted">
          {{ $t('committees.email') }}: {{ data.email }}
        </span>
      </p>

      <!-- The page's own navigation, in the pill the browse sections already
           use for "which slice of this am I looking at" (.filterpill). Links,
           not tab widgets: each one is a real address of this committee, and
           `aria-current` then says which of them is open — which is what a
           reader on a screen reader is actually being told. -->
      <nav class="tabbar" :aria-label="$t('committees.title')">
        <RouterLink
          v-for="name in TABS" :key="name" :to="tabTo(name)"
          class="filterpill" :class="{ active: tab === name }"
          :aria-current="tab === name ? 'page' : undefined"
        >{{ $t(TAB_LABELS[name]) }}</RouterLink>
      </nav>

      <!-- Tagok: the roster as people, portraits and all, rather than as a
           registry listing of names. -->
      <template v-if="tab === 'members'">
        <section class="card pad members">
          <p v-if="!data.members.length" class="small muted nomembers">
            {{ $t('committees.noMembers') }}
          </p>
          <div v-for="g in memberGroups" :key="g.role" class="mgroup">
            <h2 class="rolehead small muted">{{ $t('committees.roles.' + g.role) }}</h2>
            <ul class="people">
              <li v-for="m in g.items" :key="(m.personId || m.name) + g.role">
                <!-- Not in the roster (a minister sitting ex officio, a name the
                     registry spells differently): SpeakerLink drops the link and
                     the label stands on its own. -->
                <SpeakerLink :speaker="speaker(m)">
                  <span class="pname">{{ m.name }}</span>
                  <span v-if="m.faction" class="pfac small muted">{{ m.faction }}</span>
                </SpeakerLink>
              </li>
            </ul>
          </div>
        </section>

        <section v-if="data.formerMembers.length" class="card pad members">
          <h2 class="sechead">{{ $t('committees.formerMembers') }}</h2>
          <p class="small muted note">{{ $t('committees.formerMembersNote') }}</p>
          <ul class="former">
            <li v-for="(m, i) in formerShown" :key="(m.personId || m.name) + '-' + i">
              <SpeakerLink :speaker="speaker(m)" size="sm">
                <span>{{ m.name }}</span>
              </SpeakerLink>
              <span class="small muted fmeta">
                <template v-if="m.faction">{{ m.faction }}</template>
                <template v-if="memberTerm(m)"> · {{ memberTerm(m) }}</template>
                <template v-if="m.reason"> · {{ m.reason }}</template>
              </span>
            </li>
          </ul>
          <button v-if="formerHidden" type="button" class="btn secondary small"
                  @click="allFormer = true">
            {{ $t('committees.more', { count: formerHidden }) }}
          </button>
        </section>
      </template>

      <template v-else>
        <!-- The headline figures, each one the way into the tab that itemises
             it. The meeting record is the list directly below, so its card
             stays a label rather than pretending to lead somewhere. -->
        <div v-if="tab === 'overview'" class="statrow">
          <button type="button" class="card pad statcard" @click="setTab('members')">
            <strong>{{ data.members.length }}</strong>
            <span class="small muted">{{ $t('committees.membersShort') }}</span>
          </button>
          <div class="card pad statcard static">
            <strong>{{ data.meetings ?? listedMeetings }}</strong>
            <span class="small muted">{{ $t('committees.meetingsShort') }}</span>
          </div>
          <button type="button" class="card pad statcard" @click="setTab('discussed')">
            <strong>{{ data.counts.discussed }}</strong>
            <span class="small muted">{{ $t('committees.discussed') }}</span>
          </button>
          <button type="button" class="card pad statcard" @click="setTab('tabled')">
            <strong>{{ data.counts.tabled }}</strong>
            <span class="small muted">{{ $t('committees.tabled') }}</span>
          </button>
        </div>

        <section v-if="tab === 'overview' && data.subcommittees.length"
                 class="card pad members">
          <h2 class="sechead">{{ $t('committees.subcommittees') }}</h2>
          <ul class="subs">
            <li v-for="s in data.subcommittees" :key="s.id">
              <RouterLink :to="{ name: 'committee', params: { id: s.id } }">
                {{ s.name }}
              </RouterLink>
              <span class="small muted">
                · {{ s.memberCount }} {{ $t('committees.membersShort') }}
                <template v-if="s.dateStart"> · {{ formatDate(s.dateStart) }}</template>
              </span>
            </li>
          </ul>
        </section>

        <StateBlock
          :loading="listLoading" :error="listError"
          :empty="!!list && list.items.length === 0"
          :empty-text="tab === 'overview' ? $t('committees.noMeetings')
            : $t('committees.noDocuments')"
          @retry="loadList"
        >
          <div v-if="list">
            <ul v-if="tab === 'overview'" class="mlist">
              <li v-for="m in list.items" :key="m.id" class="card mrow">
                <component
                  :is="rowTag(m)" v-bind="rowProps(m)" class="mbody"
                  :class="{ plain: rowTag(m) === 'div' }"
                  :title="readable(m) ? $t('committees.openMinutes') : undefined"
                  @mouseenter="peek(m, $event)" @mouseleave="agenda?.close()"
                  @focus="peek(m, $event)" @blur="agenda?.close()"
                >
                  <span class="micon" :class="{ on: !!m.speeches }" aria-hidden="true">
                    <svg viewBox="0 0 24 24" width="18" height="18" fill="none"
                         stroke="currentColor" stroke-width="1.7"
                         stroke-linecap="round" stroke-linejoin="round">
                      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
                      <path d="M14 3v5h5" /><path d="M9 13h6" /><path d="M9 17h4" />
                    </svg>
                  </span>
                  <span class="mwhen">
                    <span class="mdate">{{ formatDateTime(m.heldAt) }}</span>
                    <span class="mnum small muted">{{ m.numberInYear || m.number }}</span>
                  </span>
                  <span class="mnotes">
                    <!-- Only what departs from the ordinary sitting, so the two
                         columns that used to repeat "nyilvános · Határozatképes"
                         down the whole page now mark the sittings that were
                         neither. -->
                    <span v-if="m.kind && m.kind !== OPEN_SITTING" class="chip">
                      {{ m.kind }}
                    </span>
                    <span v-if="m.quorum && m.quorum !== QUORATE" class="chip">
                      {{ m.quorum }}
                    </span>
                    <!-- A meeting whose jegyzőkönyv is not published keeps its
                         row and says so, whether or not it is a link: hiding
                         the fact would make the published minutes look like the
                         whole record (BIZ-9), and on the newest sittings — the
                         ones a reader is likeliest to open — the recording is
                         genuinely all there is yet (BIZ-27). Where the House
                         published a PDF we have not read, the row says that is
                         what it opens, since it leaves the site. -->
                    <span v-if="!m.speeches && !m.minutesUrl" class="small muted">
                      {{ $t('committees.noMinutes') }}
                    </span>
                    <span v-else-if="rowTag(m) === 'a'" class="small muted">
                      {{ $t('committees.minutesSource') }} ↗
                    </span>
                  </span>
                  <span v-if="m.durationS" class="mdur small muted">
                    {{ formatSpeakingTime(m.durationS) }}
                  </span>
                </component>
                <!-- The recording, as the mark of the channel it is on. Beside
                     the row rather than inside it: it leaves the site, and the
                     row itself opens the sitting. -->
                <a v-for="v in m.videos" :key="v.videoId" class="ytbtn"
                   :href="v.url" target="_blank" rel="noopener"
                   :title="videoLabel(v)" :aria-label="videoLabel(v)">
                  <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
                    <path fill="currentColor" d="M23.5 6.5a3 3 0 0 0-2.1-2.1C19.5 3.9 12 3.9 12 3.9s-7.5 0-9.4.5A3 3 0 0 0 .5 6.5C0 8.4 0 12 0 12s0 3.6.5 5.5a3 3 0 0 0 2.1 2.1c1.9.5 9.4.5 9.4.5s7.5 0 9.4-.5a3 3 0 0 0 2.1-2.1c.5-1.9.5-5.5.5-5.5s0-3.6-.5-5.5z" />
                    <path fill="#fff" d="M9.6 15.6V8.4l6.3 3.6z" />
                  </svg>
                </a>
              </li>
            </ul>

            <ul v-else class="dlist">
              <li v-for="(d, i) in list.items" :key="d.billId + '-' + i" class="card pad drow">
                <div class="dmain">
                  <div class="dnum">
                    <!-- Linked only where this deployment actually holds the
                         document; otherwise the number stands as a plain label. -->
                    <RouterLink
                      v-if="d.held"
                      :to="{ name: 'document', params: { id: d.billId } }"
                    >{{ d.billNumber }}</RouterLink>
                    <a v-else-if="d.textUrl" :href="d.textUrl" target="_blank" rel="noopener">
                      {{ d.billNumber }} ↗
                    </a>
                    <span v-else>{{ d.billNumber }}</span>
                    <span v-if="d.docType" class="chip kind">{{ d.docType }}</span>
                  </div>
                  <p class="dtitle">{{ d.title }}</p>
                  <p v-if="d.sponsors && d.sponsors.length" class="small muted">
                    {{ $t('committees.sponsors') }}:
                    <template v-for="(sp, j) in d.sponsors" :key="j">
                      <span v-if="j" aria-hidden="true"> · </span>
                      <RouterLink
                        v-if="sp.personID"
                        :to="{ name: 'profile', params: { id: sp.personID } }"
                      >{{ sp.name }}</RouterLink>
                      <span v-else>{{ sp.name }}</span>
                    </template>
                  </p>
                </div>
                <div class="dmeta small muted">
                  <span v-if="d.status">{{ d.status }}</span>
                  <span v-if="d.referredAt">
                    {{ $t('committees.referredAt') }}: {{ formatDate(d.referredAt) }}
                  </span>
                </div>
              </li>
            </ul>

            <Pagination :page="page" :total-pages="totalPages" @goto="goto" />
          </div>
        </StateBlock>
        <!-- One preview for the whole list: the row being hovered drives it. -->
        <CommitteeAgendaPreview v-if="tab === 'overview'" ref="agenda" />
      </template>

      <details class="methodology" style="margin-top:1rem;">
        <summary>{{ $t('factions.methodology') }}</summary>
        <p class="small muted">{{ $t('committees.methodology') }}</p>
      </details>
    </div>
  </StateBlock>
</template>

<style scoped>
.sub { margin: -.4rem 0 .4rem; }
.links { display: flex; flex-wrap: wrap; gap: .3rem 1rem; margin: 0 0 1rem; }

/* .filterpill is global (styles.css) — the same pill the list page filters
   with. A tab bar here rather than a second row of links: these are four views
   of one committee, and the pill already reads as "this is the slice you are
   looking at". */
.tabbar { display: flex; flex-wrap: wrap; gap: .4rem; margin: 0 0 1rem; }

.statrow {
  display: grid; gap: .5rem; margin-bottom: 1rem;
  grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
}
.statcard {
  display: flex; flex-direction: column; gap: .1rem; text-align: left;
  border: 1px solid var(--line); cursor: pointer; font: inherit; color: inherit;
}
.statcard:hover:not(.static) { border-color: var(--accent); }
.statcard.static { cursor: default; }
.statcard strong { font-size: 1.5rem; font-variant-numeric: tabular-nums; }

.members { margin-bottom: 1rem; }
.sechead { font-size: 1rem; margin: 0 0 .4rem; }
.members .note { margin: -.2rem 0 .6rem; }
.nomembers { margin: 0; }
.mgroup + .mgroup { margin-top: 1rem; }
.rolehead {
  margin: 0 0 .5rem; font-size: .8rem; font-weight: 600; text-transform: lowercase;
}
/* The roster as faces. Wide enough a track that a "Dr. Simon Krisztián Márk"
   sits beside its portrait instead of wrapping under it. */
.people {
  list-style: none; padding: 0; margin: 0; display: grid; gap: .6rem;
  grid-template-columns: repeat(auto-fill, minmax(16rem, 1fr));
}
.pname { display: block; }
.pfac { display: block; }
.former { list-style: none; padding: 0; margin: 0 0 .6rem; display: grid; gap: .45rem; }
.former li { display: flex; flex-wrap: wrap; align-items: center; gap: .2rem .5rem; }
.fmeta { min-width: 0; }
.subs { list-style: none; padding: 0; margin: 0; display: grid; gap: .2rem; }

/* The meeting record. A list of sittings, not a grid of fields: every row is
   one link to the sitting itself, and what used to be a "Jegyzőkönyv" column is
   now the row. */
.mlist { list-style: none; padding: 0; margin: 0; display: grid; gap: .4rem; }
.mrow {
  display: flex; align-items: stretch; gap: .2rem; padding: 0 .5rem 0 0;
  overflow: hidden;
}
.mbody {
  flex: 1; min-width: 0; display: flex; align-items: center; gap: .7rem;
  padding: .55rem .7rem; color: inherit; text-decoration: none;
}
a.mbody:hover, .mbody:focus-visible { background: var(--accent-soft); }
.mbody.plain { color: var(--ink-soft); }
/* The record's own mark: lit where the sitting has a readable jegyzőkönyv,
   quiet where the row opens the House's PDF, a recording, or nothing. */
.micon { display: flex; color: var(--ink-soft); opacity: .55; flex: none; }
.micon.on { color: var(--accent); opacity: 1; }
.mwhen { display: flex; flex-wrap: wrap; align-items: baseline; gap: .1rem .5rem; }
.mdate { font-weight: 600; font-variant-numeric: tabular-nums; }
.mnum { font-variant-numeric: tabular-nums; }
.mnotes {
  flex: 1; min-width: 0; display: flex; flex-wrap: wrap; align-items: center;
  gap: .3rem;
}
.mdur { flex: none; font-variant-numeric: tabular-nums; }
/* The YouTube mark, at the size of a touch target rather than of a word. */
.ytbtn {
  flex: none; display: flex; align-items: center; padding: 0 .35rem;
  color: #c4302b; opacity: .85;
}
.ytbtn:hover, .ytbtn:focus-visible { opacity: 1; }

.dlist { list-style: none; padding: 0; margin: 0; display: grid; gap: .5rem; }
.drow {
  display: grid; gap: .3rem .9rem; align-items: start;
  grid-template-columns: minmax(0, 1fr) auto;
}
.dmain { min-width: 0; }
.dnum { display: flex; flex-wrap: wrap; align-items: baseline; gap: .4rem; }
.dnum > a, .dnum > span:first-child { font-weight: 600; }
.kind { font-size: .72rem; font-weight: 400; }
.dtitle { margin: .1rem 0; overflow-wrap: anywhere; }
.dmeta { display: flex; flex-direction: column; align-items: flex-end; gap: .1rem; text-align: right; }

@media (max-width: 720px) {
  /* Two lines instead of one: when it sat, then what came of it. The duration
     keeps the right edge of the second line so the column still reads down the
     list, rather than trailing whatever note happens to precede it. */
  .mbody { flex-wrap: wrap; gap: .15rem .6rem; }
  .mwhen { flex: 1 0 calc(100% - 2.2rem); }
  .mnotes { flex: 1 1 auto; }
  .mdur { margin-left: auto; }
  .drow { grid-template-columns: 1fr; }
  .dmeta { align-items: flex-start; text-align: left; }
}
.methodology summary { cursor: pointer; font-weight: 600; color: var(--ink-soft); font-size: .85rem; }
</style>
