<script setup>
// One committee's sheet (§6F / BIZ-4): who sits on it, when it met, and what it
// handled.
//
// The roster and the subcommittees come with the sheet — a page cannot draw
// anything without them — while the meetings and the documents are paged
// endpoints of their own, because a committee of a full cycle has hundreds of
// each. Which panel is open is URL state (§CYC-5), so a link can point at a
// committee's meeting record rather than always at its membership.
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { loadMeta } from '../../store.js'
import { formatDate, formatDateTime, formatSpeakingTime } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import Pagination from '../../components/Pagination.vue'

const props = defineProps({ id: { type: String, required: true } })
const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const PAGE = 25
const TABS = ['meetings', 'discussed', 'tabled', 'videos']

const data = ref(null)
const list = ref(null)
const loading = ref(false)
const error = ref(false)
const listLoading = ref(false)
const listError = ref(false)
const allFormer = ref(false)

// A cycle's membership churn can run to dozens of names; shown whole they push
// the meeting record below the fold, so only the most recent few open by
// default.
const FORMER_SHOWN = 6

const tab = computed(() => (TABS.includes(route.query.tab) ? route.query.tab : 'meetings'))
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

function setTab(name) {
  router.push({ query: { ...route.query, tab: name, offset: undefined } })
}
function goto(p) {
  router.push({ query: { ...route.query, offset: p * PAGE || undefined } })
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
  const seq = ++listSeq
  listLoading.value = true; listError.value = false
  const params = { limit: PAGE, offset: page.value * PAGE }
  try {
    const res = tab.value === 'meetings'
      ? await api.committeeMeetings(props.id, params)
      : tab.value === 'videos'
        ? await api.committeeVideos(props.id, params)
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

      <div class="statrow">
        <div class="card pad statcard static">
          <strong>{{ data.members.length }}</strong>
          <span class="small muted">{{ $t('committees.membersShort') }}</span>
        </div>
        <button type="button" class="card pad statcard" @click="setTab('meetings')">
          <strong>{{ data.meetings ?? listedMeetings }}</strong>
          <span class="small muted">{{ $t('committees.meetingsShort') }}</span>
        </button>
        <button type="button" class="card pad statcard" @click="setTab('discussed')">
          <strong>{{ data.counts.discussed }}</strong>
          <span class="small muted">{{ $t('committees.discussed') }}</span>
        </button>
        <button type="button" class="card pad statcard" @click="setTab('tabled')">
          <strong>{{ data.counts.tabled }}</strong>
          <span class="small muted">{{ $t('committees.tabled') }}</span>
        </button>
      </div>

      <section class="card pad members">
        <h2>{{ $t('committees.members') }}</h2>
        <p v-if="!data.members.length" class="small muted">
          {{ $t('committees.noMembers') }}
        </p>
        <div v-for="g in memberGroups" :key="g.role" class="mgroup">
          <h3 class="small muted">{{ $t('committees.roles.' + g.role) }}</h3>
          <ul>
            <li v-for="m in g.items" :key="(m.personId || m.name) + g.role">
              <RouterLink
                v-if="m.personId"
                :to="{ name: 'profile', params: { id: m.personId } }"
              >{{ m.name }}</RouterLink>
              <!-- Not in the roster (a minister sitting ex officio, a name the
                   registry spells differently): the label stands, unlinked. -->
              <span v-else>{{ m.name }}</span>
              <span v-if="m.faction" class="small muted"> · {{ m.faction }}</span>
            </li>
          </ul>
        </div>
      </section>

      <section v-if="data.formerMembers.length" class="card pad members">
        <h2>{{ $t('committees.formerMembers') }}</h2>
        <p class="small muted note">{{ $t('committees.formerMembersNote') }}</p>
        <ul>
          <li v-for="(m, i) in formerShown" :key="(m.personId || m.name) + '-' + i">
            <RouterLink
              v-if="m.personId"
              :to="{ name: 'profile', params: { id: m.personId } }"
            >{{ m.name }}</RouterLink>
            <span v-else>{{ m.name }}</span>
            <span class="small muted">
              <template v-if="m.faction"> · {{ m.faction }}</template>
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

      <section v-if="data.subcommittees.length" class="card pad members">
        <h2>{{ $t('committees.subcommittees') }}</h2>
        <ul>
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

      <div class="tabs" role="tablist">
        <button
          v-for="name in TABS" :key="name" type="button" role="tab"
          class="btn secondary" :class="{ on: tab === name }"
          :aria-selected="tab === name" @click="setTab(name)"
        >{{ $t('committees.' + name) }}</button>
      </div>

      <StateBlock
        :loading="listLoading" :error="listError"
        :empty="!!list && list.items.length === 0"
        :empty-text="tab === 'meetings' ? $t('committees.noMeetings')
          : tab === 'videos' ? $t('committees.noVideos')
          : $t('committees.noDocuments')"
        @retry="loadList"
      >
        <div v-if="list">
          <table v-if="tab === 'meetings'" class="ctable">
            <thead>
              <tr>
                <th>#</th>
                <th>{{ $t('committees.date') }}</th>
                <th>{{ $t('committees.meetingKind') }}</th>
                <th>{{ $t('committees.quorum') }}</th>
                <th class="num">{{ $t('committees.duration') }}</th>
                <th>{{ $t('committees.minutes') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="m in list.items" :key="m.id">
                <td class="num">{{ m.numberInYear || m.number }}</td>
                <td>{{ formatDateTime(m.heldAt) }}</td>
                <td>{{ m.kind }}</td>
                <td>{{ m.quorum }}</td>
                <td class="num">{{ m.durationS ? formatSpeakingTime(m.durationS) : '' }}</td>
                <td>
                  <!-- Three independent states, so three separate things on the
                       row: the House published a PDF, we have read it, someone
                       filmed it. A sitting can be any combination of them. -->
                  <RouterLink
                    v-if="m.speeches"
                    :to="{ name: 'committee-minutes', params: { meetingId: m.id } }"
                  >{{ $t('committees.minutesTitle') }}</RouterLink>
                  <a v-else-if="m.minutesUrl" :href="m.minutesUrl"
                     target="_blank" rel="noopener">PDF ↗</a>
                  <!-- A meeting that published nothing keeps its row and says
                       so: hiding it would make the published minutes look like
                       the whole record. -->
                  <span v-else class="muted small">{{ $t('committees.noMinutes') }}</span>
                  <a v-for="v in m.videos" :key="v.videoId" class="vlink small"
                     :href="v.url" target="_blank" rel="noopener">
                    ▶ {{ $t('committees.video') }}<template v-if="v.continued"
                      > ({{ $t('committees.videoPart') }})</template> ↗
                  </a>
                </td>
              </tr>
            </tbody>
          </table>

          <div v-else-if="tab === 'videos'">
            <!-- Why a committee of 2016 has none: the House's channel simply did
                 not exist. Said in the page rather than left as an empty list,
                 which would read as a gap in the site. -->
            <p v-if="list.coverage" class="small muted note">
              {{ $t('committees.videoCoverage', { from: formatDate(list.coverage.from) }) }}
            </p>
            <ul class="vlist">
              <li v-for="v in list.items" :key="v.videoId" class="card pad vrow">
                <a :href="v.url" target="_blank" rel="noopener" class="vthumb">
                  <img v-if="v.thumbnail" :src="v.thumbnail" alt="" loading="lazy" />
                  <span v-else class="vfallback" aria-hidden="true">▶</span>
                </a>
                <div class="vmain">
                  <a :href="v.url" target="_blank" rel="noopener" class="vtitle">
                    {{ v.title }} ↗
                  </a>
                  <p class="small muted">
                    {{ formatDate(v.heldOn) }}
                    <template v-if="v.durationS">
                      <span aria-hidden="true"> · </span>{{ formatSpeakingTime(v.durationS) }}
                    </template>
                    <template v-if="v.continued">
                      <span aria-hidden="true"> · </span>{{ $t('committees.videoPart') }}
                    </template>
                  </p>
                  <!-- Three states again, and only the first is a link. The
                       recording is up the same day and the minutes follow weeks
                       later, so a recent sitting normally has a meeting and no
                       readable record — linking anyway would 404 on exactly the
                       sittings a reader is most likely to open. -->
                  <RouterLink
                    v-if="v.hasMinutes"
                    class="small"
                    :to="{ name: 'committee-minutes', params: { meetingId: v.meetingId } }"
                  >{{ $t('committees.minutesTitle') }} →</RouterLink>
                  <span v-else-if="!v.meetingId" class="small muted">
                    {{ $t('committees.videoUnmatched') }}
                  </span>
                </div>
              </li>
            </ul>
          </div>

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
.statrow {
  display: grid; gap: .5rem; margin-bottom: 1rem;
  grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
}
.statcard {
  display: flex; flex-direction: column; gap: .1rem; text-align: left;
  border: 1px solid var(--line); cursor: pointer; font: inherit; color: inherit;
}
.statcard.static { cursor: default; }
.statcard strong { font-size: 1.5rem; font-variant-numeric: tabular-nums; }
.members { margin-bottom: 1rem; }
.members h2 { font-size: 1rem; margin: 0 0 .4rem; }
.members .note { margin: -.2rem 0 .5rem; }
.mgroup { margin-bottom: .6rem; }
.mgroup h3 { margin: 0 0 .15rem; font-size: .8rem; text-transform: lowercase; }
.members ul { list-style: none; padding: 0; margin: 0; display: grid; gap: .15rem; }
.tabs { display: flex; flex-wrap: wrap; gap: .4rem; margin: 1rem 0 .8rem; }
.tabs .on { background: var(--accent); color: #fff; border-color: var(--accent); }
.ctable { width: 100%; border-collapse: collapse; }
.ctable th, .ctable td {
  text-align: left; padding: .4rem .5rem; border-bottom: 1px solid var(--line);
  font-size: .9rem;
}
.ctable th { color: var(--ink-soft); font-weight: 600; font-size: .8rem; }
.ctable .num { text-align: right; font-variant-numeric: tabular-nums; }
.vlink { margin-left: .6rem; white-space: nowrap; }
.vlist { list-style: none; padding: 0; margin: 0; display: grid; gap: .5rem; }
.vrow { display: grid; gap: .3rem .8rem; grid-template-columns: auto minmax(0, 1fr); align-items: start; }
.vthumb { display: block; width: 8rem; }
.vthumb img { width: 100%; height: auto; border-radius: .3rem; display: block; }
.vfallback {
  display: flex; align-items: center; justify-content: center; width: 8rem;
  aspect-ratio: 16 / 9; border: 1px solid var(--line); border-radius: .3rem;
  color: var(--ink-soft);
}
.vmain { min-width: 0; display: grid; gap: .1rem; }
.vtitle { font-weight: 600; overflow-wrap: anywhere; }
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
  .vrow { grid-template-columns: 1fr; }
  .vthumb, .vfallback { width: 100%; max-width: 16rem; }
  .drow { grid-template-columns: 1fr; }
  .dmeta { align-items: flex-start; text-align: left; }
  .ctable, .ctable tbody, .ctable tr, .ctable td { display: block; }
  .ctable thead { display: none; }
  .ctable tr { border-bottom: 1px solid var(--line); padding: .4rem 0; }
  .ctable td { border: 0; padding: .1rem 0; }
  .ctable .num { text-align: left; }
}
.methodology summary { cursor: pointer; font-weight: 600; color: var(--ink-soft); font-size: .85rem; }
</style>
