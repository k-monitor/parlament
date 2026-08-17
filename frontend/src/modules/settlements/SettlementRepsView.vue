<script setup>
// "Beszélnek a saját körzetükről?" — the own-constituency measures (§6D TEL-9).
//
// Two columns, and the page's whole job is to keep them apart:
//   * **Focus** — of the places this MP names, what share are in their own
//     constituency;
//   * **Coverage** — of the settlements *in* their constituency, what share they
//     have ever named. This is the local question: has our member ever said the name
//     of our village out loud?
// A high focus with a low coverage is an MP who talks about one town of theirs
// constantly; the reverse is one who names all of them in passing. Collapsed into a
// single score, both readings are lost.
//
// It is a **neutral fact, not a league table of diligence** (§1.2). A minister
// speaks to the country; an inner-city member has no village to name; a low share is
// not a dereliction. The caveat sits with the numbers, not in a footnote, and the
// sort control offers no "best first" framing beyond the plain measures.
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { currentCycleLabel, loadMeta, store } from '../../store.js'
import FactionBadge from '../../components/FactionBadge.vue'
import Pagination from '../../components/Pagination.vue'
import StateBlock from '../../components/StateBlock.vue'

const route = useRoute()
const router = useRouter()
const { t, n: fmtNumber } = useI18n()

const PER_PAGE = 50
const data = ref(null)
const loading = ref(false)
const error = ref(false)

const sort = computed(() => ['focus', 'coverage', 'mentions'].includes(route.query.sort)
  ? route.query.sort : 'focus')
const page = computed(() => Math.max(0, Number(route.query.page || 0)))
const totalPages = computed(() => data.value ? Math.ceil(data.value.total / PER_PAGE) : 0)
const scopeText = computed(() => {
  const c = currentCycleLabel()
  return c ? t('settlements.scope', { cycle: c }) : t('cycle.scopeAll')
})

function pct(v) { return v == null ? '—' : Math.round(v * 100) + '%' }
function apply(extra) {
  router.push({ name: 'settlementReps', query: { sort: sort.value, ...extra } })
}
function setSort(s) { router.push({ name: 'settlementReps', query: { sort: s } }) }
function goto(p) { apply({ page: p || undefined }) }

async function load() {
  loading.value = true; error.value = false
  try {
    data.value = await api.settlementReps({
      sort: sort.value, period: store.cycles,
      limit: PER_PAGE, offset: page.value * PER_PAGE,
    })
  } catch { error.value = true } finally { loading.value = false }
}

onMounted(async () => { await loadMeta().catch(() => {}); load() })
watch(() => route.query, load)
watch(() => store.cycles.join(','), load)
</script>

<template>
  <h1>{{ $t('settlements.repsTitle') }}</h1>
  <p class="muted small intro">{{ $t('settlements.repsIntro') }}</p>

  <!-- The caveat before the table, not under it: it changes how every number below
       should be read (TEL-9 / §1.2). -->
  <p class="card pad small caveat">{{ $t('settlements.repsCaveat') }}</p>

  <div class="sorts" role="group" :aria-label="$t('settlements.sortBy')">
    <button
      v-for="s in ['focus', 'coverage', 'mentions']" :key="s"
      class="btn" :class="sort === s ? '' : 'secondary'"
      :aria-pressed="sort === s ? 'true' : 'false'" @click="setSort(s)"
    >{{ $t('settlements.sort' + s.charAt(0).toUpperCase() + s.slice(1)) }}</button>
  </div>

  <StateBlock
    :loading="loading" :error="error"
    :empty="!!data && data.representatives.length === 0"
    :empty-text="$t('settlements.repsEmpty')"
    @retry="load"
  >
    <div v-if="data">
      <p class="muted small" aria-live="polite" style="margin:.2rem 0 .6rem;">
        {{ fmtNumber(data.total) }} {{ $t('settlements.repsUnit') }} · {{ scopeText }}
        <!-- A share over three mentions is noise, so a ratio ordering has a floor —
             and the page says so rather than implying the list is everyone. -->
        <template v-if="data.min_mentions">
          · {{ $t('settlements.repsFloor', { n: data.min_mentions }) }}
        </template>
      </p>
      <!-- A real table: three numeric columns per row that a reader will want to
           compare down the column, which is what a table is for. -->
      <div class="tablewrap">
        <table class="reptable">
          <thead>
            <tr>
              <th scope="col">{{ $t('settlements.colRep') }}</th>
              <th scope="col">{{ $t('settlements.colConstituency') }}</th>
              <th scope="col" class="num">{{ $t('settlements.colFocus') }}</th>
              <th scope="col" class="num">{{ $t('settlements.colCoverage') }}</th>
              <th scope="col" class="num">{{ $t('settlements.colMentions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in data.representatives" :key="r.person_id">
              <th scope="row" class="who">
                <RouterLink :to="{ name: 'profile', params: { id: r.person_id } }">
                  {{ r.name }}
                </RouterLink>
                <FactionBadge v-if="r.faction" :faction="r.faction" />
              </th>
              <td class="small muted">{{ r.constituency }}</td>
              <td class="num">
                {{ pct(r.focus) }}
                <span class="small muted frac">
                  ({{ fmtNumber(r.own_mentions) }}/{{ fmtNumber(r.mentions) }})
                </span>
              </td>
              <td class="num">
                {{ pct(r.coverage) }}
                <span class="small muted frac">
                  ({{ r.own_named }}/{{ r.own_total }})
                </span>
              </td>
              <td class="num">{{ fmtNumber(r.mentions) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <Pagination :page="page" :total-pages="totalPages" @goto="goto" />
    </div>
  </StateBlock>

  <details class="methodology">
    <summary>{{ $t('factions.methodology') }}</summary>
    <p class="small muted">{{ $t('settlements.repsMethodology') }}</p>
    <p class="small muted">{{ $t('settlements.methodology') }}</p>
  </details>
</template>

<style scoped>
.intro { margin: -.4rem 0 .8rem; max-width: 68ch; }
.caveat { max-width: 72ch; }
.sorts { display: flex; gap: .4rem; margin: 1rem 0; flex-wrap: wrap; }
/* A wide table scrolls inside its own box rather than making the page scroll. */
.tablewrap { overflow-x: auto; }
.reptable { width: 100%; border-collapse: collapse; font-size: .92rem; }
.reptable th, .reptable td {
  text-align: left; padding: .4rem .6rem; border-bottom: 1px solid var(--line);
  vertical-align: baseline;
}
.reptable thead th { color: var(--ink-soft); font-size: .82rem; white-space: nowrap; }
.reptable .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.who { display: flex; align-items: center; gap: .4rem; flex-wrap: wrap; font-weight: 600; }
/* The raw counts behind each share, so a 100% over two settlements never reads as
   the same fact as a 100% over thirty. */
.frac { font-variant-numeric: tabular-nums; }
.methodology { margin-top: 1rem; }
.methodology summary { cursor: pointer; font-weight: 600; color: var(--ink-soft); font-size: .85rem; }
</style>
