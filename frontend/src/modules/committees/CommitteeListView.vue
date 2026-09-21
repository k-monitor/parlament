<script setup>
// Bizottságok — the bodies the House does most of its work in (§6F / BIZ-1).
//
// A sibling of Tárcák and Frakciók in the same tab group: those answer "which
// institution" and "which party", this one "which working body". A cycle has
// a couple of dozen main committees, so the list is not paginated — the point
// is to see the whole set at once and compare how busy they were.
//
// Subcommittees are off by default. There are twice as many of them as there
// are committees, each with three members, and mixed into one flat list they
// bury the bodies a reader came for; asked for, they are nested under the
// committee they belong to rather than listed as peers.
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { store, loadMeta, currentCycleLabel } from '../../store.js'
import { formatSpeakingTime } from '../../format.js'
import { createSearchClicks } from '../../lib/searchClicks.js'
import StateBlock from '../../components/StateBlock.vue'

const route = useRoute()
const router = useRouter()
const { t, locale } = useI18n()

const data = ref(null)
const upcoming = ref([])
const loading = ref(false)
const error = ref(false)

const SORTS = ['official', 'name', 'meetings', 'duration', 'members']

const f = reactive({
  q: route.query.q || '',
  kind: route.query.kind || '',
  sort: SORTS.includes(route.query.sort) ? route.query.sort : 'official',
  subs: route.query.subs === '1',
})

const scopeText = computed(() => {
  const c = currentCycleLabel()
  return c ? t('committees.scope', { cycle: c }) : t('cycle.scopeAll')
})

// The main committees, each carrying the subcommittees the API returned under
// it. Nesting rather than flattening keeps the two levels legible: a
// subcommittee of three people is not comparable with a committee of fifteen,
// and sorting them together by size would interleave them meaninglessly.
const rows = computed(() => {
  if (!data.value) return []
  const items = data.value.items
  const children = new Map()
  for (const c of items) {
    if (!c.parentId) continue
    if (!children.has(c.parentId)) children.set(c.parentId, [])
    children.get(c.parentId).push(c)
  }
  return items
    .filter((c) => !c.parentId)
    .map((c, i) => ({ ...c, rank: i, subs: children.get(c.id) || [] }))
})

// A subcommittee whose parent is not itself in the result — it was filtered out
// by the search or the kind chip. Orphaned it would simply vanish, so it is
// listed on its own with the parent named on the row.
const orphans = computed(() => {
  if (!data.value) return []
  const held = new Set(data.value.items.filter((c) => !c.parentId).map((c) => c.id))
  return data.value.items.filter((c) => c.parentId && !held.has(c.parentId))
})

// How long the committee sat, in the site's own length idiom ("4ó 46p" /
// "4h 46m") — the same one the meeting table uses, so the total and the rows
// under it read alike. Upstream gives whole minutes; a body that never met has
// no figure rather than a zero, which would read as a measured nothing.
function sittingTime(minutes) {
  return minutes ? formatSpeakingTime(minutes * 60) : null
}

// "szept. 22., hétfő 10:30" — a scheduled meeting is read as "when", so the
// weekday earns its place here in a way it does not in the meeting record.
function whenLabel(u) {
  const d = new Date(`${u.date}T00:00:00`)
  const day = Number.isNaN(d.getTime())
    ? u.date
    : new Intl.DateTimeFormat(locale.value === 'en' ? 'en-GB' : 'hu-HU', {
      month: 'short', day: 'numeric', weekday: 'short',
    }).format(d)
  return u.time ? `${day} ${u.time}` : day
}

function apply() {
  const query = {}
  if (f.q) query.q = f.q
  if (f.kind) query.kind = f.kind
  if (f.sort !== 'official') query.sort = f.sort
  if (f.subs) query.subs = '1'
  router.push({ name: 'committees', query })
}

function pickKind(kind) {
  f.kind = f.kind === kind ? '' : kind
  apply()
}

// Anonymous search-quality signal (SEA-12): which committee the reader opens
// after a keyword search, and how far down the list it sat.
const clicks = createSearchClicks('committees')

let loadSeq = 0
async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false
  const args = {
    q: route.query.q,
    kind: route.query.kind,
    sort: SORTS.includes(route.query.sort) ? route.query.sort : undefined,
    include_subcommittees: route.query.subs === '1' ? true : undefined,
    period: store.cycles,
    limit: 500,
  }
  // The schedule ahead is a separate, small request, and its failure must not
  // cost the page its list — a deployment whose DB predates the table simply
  // has no such block.
  const aheadReq = api.committeesUpcoming(store.cycles).catch(() => null)
  try {
    const [res, ahead] = await Promise.all([api.committees(args), aheadReq])
    if (seq === loadSeq) {
      data.value = res
      upcoming.value = (ahead && ahead.items) || []
      clicks.arm(args)
    }
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
watch(() => route.query, () => {
  f.q = route.query.q || ''
  f.kind = route.query.kind || ''
  f.sort = SORTS.includes(route.query.sort) ? route.query.sort : 'official'
  f.subs = route.query.subs === '1'
  load()
})
watch(() => store.cycles.join(','), load)

let searchTimer = null
function onSearchInput() { clearTimeout(searchTimer); searchTimer = setTimeout(apply, 300) }
onUnmounted(() => clearTimeout(searchTimer))
</script>

<template>
  <h1>{{ $t('committees.title') }}</h1>
  <p class="muted small intro">{{ $t('committees.intro') }}</p>

  <form class="card pad searchform" role="search" @submit.prevent="apply">
    <input
      type="search" v-model="f.q"
      :placeholder="$t('committees.searchPlaceholder')"
      :aria-label="$t('committees.searchPlaceholder')"
      @input="onSearchInput" style="width:100%;"
    />
    <div class="controls">
      <!-- Kind chips are built from what the scope actually holds, so a filter
           that would return nothing is never offered. -->
      <div v-if="data && data.kinds.length" class="chips" role="group"
           :aria-label="$t('committees.kindFilter')">
        <button
          type="button" class="chip chip-btn" :class="{ on: !f.kind }"
          :aria-pressed="!f.kind" @click="pickKind('')"
        >{{ $t('committees.allKinds') }}</button>
        <button
          v-for="k in data.kinds" :key="k.kind"
          type="button" class="chip chip-btn" :class="{ on: f.kind === k.kind }"
          :aria-pressed="f.kind === k.kind" @click="pickKind(k.kind)"
        >{{ $t('committees.kinds.' + k.kind) }} <span class="n">{{ k.count }}</span></button>
      </div>
      <label class="small subs">
        <input type="checkbox" v-model="f.subs" @change="apply" />
        {{ $t('committees.showSubcommittees') }}
      </label>
      <label class="small sortsel">
        {{ $t('committees.sort') }}
        <select v-model="f.sort" @change="apply">
          <option v-for="s in SORTS" :key="s" :value="s">
            {{ $t('committees.sorts.' + s) }}
          </option>
        </select>
      </label>
    </div>
  </form>

  <StateBlock
    :loading="loading" :error="error"
    :empty="!!data && data.items.length === 0"
    :empty-text="$t('committees.noResults')"
    @retry="load"
  >
    <div v-if="data">
      <p class="muted small" aria-live="polite" style="margin:.2rem 0 1rem;">
        {{ rows.length }} {{ $t('committees.unit') }} · {{ scopeText }}
      </p>

      <!-- What the committees are about to do (BIZ-14) — the only forward-looking
           thing on this page, so it goes above the record rather than under it. -->
      <section v-if="upcoming.length" class="card pad ahead">
        <h2>{{ $t('committees.upcoming') }}</h2>
        <p class="small muted note">{{ $t('committees.upcomingNote') }}</p>
        <ul>
          <li v-for="u in upcoming" :key="u.id" class="small"
              :class="{ off: u.cancelled }">
            <span class="when">{{ whenLabel(u) }}</span>
            <RouterLink
              v-if="u.committeeId"
              :to="{ name: 'committee', params: { id: u.committeeId }, query: route.query }"
            >{{ u.name }}</RouterLink>
            <!-- A body announced before it is constituted is listed, unlinked. -->
            <span v-else>{{ u.name }}</span>
            <span v-if="u.venue" class="muted"> · {{ u.venue }}</span>
            <span v-if="u.cancelled" class="chip off-chip">
              {{ $t('committees.cancelled') }}
            </span>
          </li>
        </ul>
      </section>

      <ul class="clist">
        <li v-for="c in rows" :key="c.id" class="card pad crow">
          <div class="cmain">
            <div class="cname">
              <RouterLink
                :to="{ name: 'committee', params: { id: c.id }, query: route.query }"
                @click="clicks.hit(c.rank)"
              >{{ c.name }}</RouterLink>
              <span v-if="c.kind" class="chip kind">{{ $t('committees.kinds.' + c.kind) }}</span>
            </div>
            <p v-if="c.chairs.length" class="small muted chairline">
              {{ $t('committees.chair') }}:
              <template v-for="(m, i) in c.chairs" :key="m.personId || m.name">
                <span v-if="i" aria-hidden="true"> · </span>
                <RouterLink
                  v-if="m.personId"
                  :to="{ name: 'profile', params: { id: m.personId } }"
                >{{ m.name }}</RouterLink>
                <span v-else>{{ m.name }}</span>
                <span v-if="m.faction" class="cfaction"> ({{ m.faction }})</span>
              </template>
            </p>
          </div>
          <!-- Each number carries its unit: "17 · 88 · 132" says nothing. -->
          <div class="ccounts small">
            <span class="stat">
              <strong>{{ c.memberCount }}</strong> {{ $t('committees.membersShort') }}
            </span>
            <span class="stat">
              <strong>{{ c.meetings ?? 0 }}</strong> {{ $t('committees.meetingsShort') }}
            </span>
            <span v-if="sittingTime(c.totalMinutes)" class="stat time">
              {{ sittingTime(c.totalMinutes) }}
            </span>
          </div>

          <ul v-if="c.subs.length" class="subslist small">
            <li v-for="s in c.subs" :key="s.id">
              <RouterLink :to="{ name: 'committee', params: { id: s.id }, query: route.query }">
                {{ s.name }}
              </RouterLink>
              <span class="muted"> · {{ s.memberCount }} {{ $t('committees.membersShort') }}</span>
            </li>
          </ul>
        </li>

        <!-- Subcommittees whose parent the filter excluded: shown rather than
             dropped, with the parent named so the row still places itself. -->
        <li v-for="c in orphans" :key="c.id" class="card pad crow orphan">
          <div class="cmain">
            <div class="cname">
              <RouterLink :to="{ name: 'committee', params: { id: c.id }, query: route.query }">
                {{ c.name }}
              </RouterLink>
            </div>
            <p class="small muted chairline">
              {{ $t('committees.subcommitteesOf', { name: c.parentName }) }}
            </p>
          </div>
          <div class="ccounts small">
            <span class="stat">
              <strong>{{ c.memberCount }}</strong> {{ $t('committees.membersShort') }}
            </span>
          </div>
        </li>
      </ul>

      <details class="methodology" style="margin-top:1rem;">
        <summary>{{ $t('factions.methodology') }}</summary>
        <p class="small muted">{{ $t('committees.methodology') }}</p>
      </details>
    </div>
  </StateBlock>
</template>

<style scoped>
.intro { margin: -.4rem 0 1rem; max-width: 68ch; }
.controls {
  display: flex; flex-wrap: wrap; align-items: center; gap: .6rem 1rem;
  margin-top: .6rem;
}
.chips { display: flex; flex-wrap: wrap; gap: .35rem; }
.chip-btn { cursor: pointer; border: 1px solid var(--line); background: none; }
.chip-btn.on { background: var(--accent); color: #fff; border-color: var(--accent); }
.chip-btn .n { opacity: .65; margin-left: .15rem; font-variant-numeric: tabular-nums; }
.subs, .sortsel { display: flex; align-items: center; gap: .35rem; }
.sortsel { margin-left: auto; }
.ahead { margin-bottom: 1rem; }
.ahead h2 { font-size: 1rem; margin: 0 0 .2rem; }
.ahead .note { margin: 0 0 .5rem; }
.ahead ul { list-style: none; padding: 0; margin: 0; display: grid; gap: .2rem; }
.ahead .when {
  display: inline-block; min-width: 11rem; color: var(--ink-soft);
  font-variant-numeric: tabular-nums;
}
/* A called-off sitting stays on the list — that it was called off is the point
   — but must not read as one that is still going ahead. */
.ahead .off .when, .ahead .off a, .ahead .off > span { text-decoration: line-through; }
.ahead .off-chip { text-decoration: none; font-size: .72rem; margin-left: .3rem; }
@media (max-width: 720px) {
  .ahead .when { display: block; min-width: 0; }
}
.clist { list-style: none; padding: 0; margin: 0; display: grid; gap: .5rem; }
.crow {
  display: grid; gap: .35rem .9rem; align-items: start;
  grid-template-columns: minmax(0, 1fr) auto;
}
.cmain { min-width: 0; }
.cname { display: flex; flex-wrap: wrap; align-items: baseline; gap: .4rem; }
.cname > a { font-weight: 600; }
.kind { font-size: .72rem; }
.chairline { margin: .15rem 0 0; overflow-wrap: anywhere; }
/* The names link to the people, but the row's own link is the committee above
   them — so they read as part of the line and only colour up on hover. */
.chairline a { color: var(--ink-soft); }
.chairline a:hover { color: var(--accent); }
.cfaction { opacity: .75; }
.ccounts { display: flex; gap: 1rem; white-space: nowrap; }
.stat strong { font-variant-numeric: tabular-nums; }
.stat.time { color: var(--ink-soft); font-variant-numeric: tabular-nums; }
.subslist {
  grid-column: 1 / -1; list-style: none; margin: .5rem 0 0; padding: .5rem 0 0 .9rem;
  border-top: 1px solid var(--line); display: grid; gap: .2rem;
}
.orphan .cname > a { font-weight: 500; }
@media (max-width: 720px) {
  .crow { grid-template-columns: 1fr; }
  .ccounts { white-space: normal; gap: .8rem; }
  .sortsel { margin-left: 0; }
}
.methodology summary { cursor: pointer; font-weight: 600; color: var(--ink-soft); font-size: .85rem; }
</style>
