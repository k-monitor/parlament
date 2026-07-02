<script setup>
// Kérdések (BILL-11): a Sankey diagram of parliamentary questions — who asked
// (grouped by the asking MP's faction) → who answered (the responding ministry
// for orally-answered questions, a combined node for questions answered in
// writing, and an "unanswered" node). Clicking a flow (ribbon) lists the actual
// questions behind it at the bottom, each linking to its detail view. Part of
// the Bills module: it reads the shared bill/event data via
// `/api/v1/bills/questions/*` and honours the global cycle chooser (§4A).
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { store, loadMeta, currentCycleLabel } from '../../store.js'
import { formatDate } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import SankeyDiagram from '../../components/SankeyDiagram.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import HelpTip from '../../components/HelpTip.vue'
import Pagination from '../../components/Pagination.vue'

const { t } = useI18n()

const PAGE = 25

const data = ref(null)
const loading = ref(false)
const error = ref(false)

// Resolve backend node "kind"s (which carry no label for the special nodes)
// into localized labels; faction/ministry nodes already carry their own text.
function nodeLabel(n) {
  if (n.label) return n.label
  return t('questions.node.' + n.kind)
}

// Nodes for the diagram, keeping the identity a clicked flow needs to query.
const nodes = computed(() => (data.value ? data.value.nodes.map((n) => ({
  side: n.side,
  kind: n.kind,
  faction_id: n.faction_id ?? null,
  ministry: n.side === 'answerer' && n.kind === 'ministry' ? n.label : null,
  label: nodeLabel(n),
  color: n.color || (n.side === 'asker' ? 'var(--accent)' : '#9c9188'),
})) : []))

const scopeLabel = computed(() => {
  const c = currentCycleLabel()
  return c ? t('cycle.scope', { cycle: c }) : t('cycle.scopeAll')
})

async function load() {
  loading.value = true; error.value = false
  clearFlow()
  try {
    data.value = await api.questionsSankey(store.cycle)
  } catch { error.value = true } finally { loading.value = false }
}

// --- drill-down: the questions behind one clicked flow --------------------
const flow = ref(null)          // { index, faction, answerer, ministry, sourceLabel, targetLabel }
const flowData = ref(null)
const flowLoading = ref(false)
const flowError = ref(false)
const flowOffset = ref(0)
const panelRef = ref(null)

const flowPage = computed(() => Math.floor(flowOffset.value / PAGE))
const flowTotalPages = computed(() =>
  flowData.value ? Math.ceil(flowData.value.total / PAGE) : 0)

function clearFlow() {
  flow.value = null; flowData.value = null; flowOffset.value = 0
}

function onSelect(sel) {
  const src = sel.source, tgt = sel.target
  flow.value = {
    index: sel.index,
    faction: src.faction_id != null ? src.faction_id : 'none',
    answerer: tgt.kind,
    ministry: tgt.ministry || undefined,
    sourceLabel: src.label,
    targetLabel: tgt.label,
  }
  flowOffset.value = 0
  loadFlow()
  nextTick(() => panelRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
}

async function loadFlow() {
  if (!flow.value) return
  flowLoading.value = true; flowError.value = false
  try {
    flowData.value = await api.questionsList({
      period: store.cycle,
      faction: flow.value.faction,
      answerer: flow.value.answerer,
      ministry: flow.value.ministry,
      limit: PAGE, offset: flowOffset.value,
    })
  } catch { flowError.value = true } finally { flowLoading.value = false }
}

function gotoFlowPage(p) {
  flowOffset.value = p * PAGE
  loadFlow()
  nextTick(() => panelRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
}

onMounted(() => { loadMeta().catch(() => {}).finally(load) })
watch(() => store.cycle, load)
</script>

<template>
  <div class="sechead">
    <h1>{{ $t('questions.title') }}</h1>
    <HelpTip :label="$t('questions.title')">
      <p>{{ $t('questions.chartCaption') }}</p>
      <p>{{ $t('questions.methodology') }}</p>
    </HelpTip>
  </div>
  <p class="muted">{{ $t('questions.subtitle') }}</p>

  <StateBlock
    :loading="loading" :error="error"
    :empty="!!data && data.total === 0" :empty-text="$t('questions.noResults')"
    @retry="load"
  >
    <div v-if="data">
      <p class="muted small">
        {{ data.total }} {{ $t('questions.count') }} · {{ scopeLabel }}
      </p>
      <div class="card pad">
        <SankeyDiagram
          :nodes="nodes" :links="data.links" :selected="flow ? flow.index : -1"
          :caption="$t('questions.chartCaption')" :show-caption="false"
          :asker-heading="$t('questions.askerHeading')"
          :answerer-heading="$t('questions.answererHeading')"
          :value-label="$t('questions.count')"
          @select="onSelect"
        />
        <p class="muted small hint">{{ $t('questions.clickHint') }}</p>
      </div>

      <!-- drill-down: the questions making up the clicked flow -->
      <section v-if="flow" ref="panelRef" class="flowpanel">
        <div class="flowhead">
          <h2>
            <span :style="{ color: nodes[data.links[flow.index].source]?.color }">{{ flow.sourceLabel }}</span>
            <span class="arrow" aria-hidden="true"> → </span>
            <span>{{ flow.targetLabel }}</span>
          </h2>
          <button type="button" class="btn secondary small" @click="clearFlow">✕ {{ $t('questions.close') }}</button>
        </div>

        <StateBlock
          :loading="flowLoading" :error="flowError"
          :empty="!!flowData && flowData.bills.length === 0" :empty-text="$t('questions.noResults')"
          @retry="loadFlow"
        >
          <div v-if="flowData">
            <p class="muted small">{{ flowData.total }} {{ $t('questions.count') }}</p>
            <ul class="billlist">
              <li v-for="b in flowData.bills" :key="b.id" class="card pad billcard">
                <div class="billhead">
                  <router-link :to="{ name: 'document', params: { id: b.id } }" class="billnum">{{ b.bill_number }}</router-link>
                  <span class="badge" v-if="b.type">{{ b.type }}</span>
                  <span class="badge status" v-if="b.status">{{ b.status }}</span>
                  <span class="muted small" v-if="b.submitted_date">{{ formatDate(b.submitted_date) }}</span>
                </div>
                <router-link :to="{ name: 'document', params: { id: b.id } }" class="billtitle">{{ b.title }}</router-link>
                <div class="sponsors small" v-if="b.sponsors.length">
                  <span class="muted">{{ $t('documents.submitters') }}:</span>
                  <template v-for="(s, i) in b.sponsors" :key="i">
                    <router-link v-if="s.person_id" :to="{ name: 'profile', params: { id: s.person_id } }">{{ s.name }}</router-link>
                    <span v-else>{{ s.name }}</span>
                    <FactionBadge v-if="s.faction" :faction="s.faction" />
                    <span v-if="i < b.sponsors.length - 1" aria-hidden="true">·</span>
                  </template>
                </div>
              </li>
            </ul>
            <Pagination :page="flowPage" :total-pages="flowTotalPages" @goto="gotoFlowPage" />
          </div>
        </StateBlock>
      </section>
    </div>
  </StateBlock>
</template>

<style scoped>
.sechead { display: flex; align-items: center; gap: .35rem; }
.sechead h1 { margin: 0; }
.hint { margin: .6rem 0 0; }
.flowpanel { margin-top: 1.5rem; scroll-margin-top: 5rem; }
.flowhead { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; margin-bottom: .5rem; }
.flowhead h2 { margin: 0; font-size: 1.15rem; }
.arrow { color: var(--ink-soft); }
.billlist { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .6rem; }
.billcard { display: flex; flex-direction: column; gap: .4rem; }
.billhead { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; }
.billnum { font-weight: 800; color: var(--accent); }
.billtitle { color: var(--ink); font-weight: 600; }
.billtitle:hover { color: var(--accent); }
.sponsors { display: flex; gap: .4rem; flex-wrap: wrap; align-items: center; }
.badge.status { background: var(--accent-soft); }
</style>
