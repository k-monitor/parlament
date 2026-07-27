<script setup>
// Kérdések (BILL-11): a Sankey diagram of parliamentary questions — who asked
// (grouped by the asking MP's faction) → who answered (the responding portfolio,
// whether answered orally in plenary or in writing, and an "unanswered" node).
// Clicking a flow (ribbon) lists the actual
// questions behind it at the bottom, each linking to its detail view. Part of
// the Bills module: it reads the shared bill/event data via
// `/api/v1/bills/questions/*` and honours the global cycle chooser (§4A).
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { store, loadMeta, currentCycleLabel } from '../../store.js'
import { formatDate } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import SankeyDiagram from '../../components/SankeyDiagram.vue'
import EmbedButton from '../../components/EmbedButton.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import HelpTip from '../../components/HelpTip.vue'
import Pagination from '../../components/Pagination.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const PAGE = 25

const data = ref(null)
const loading = ref(false)
const error = ref(false)

// Whether the leading question-type column is shown. Hidden by default (the
// diagram is just faction → answerer); `?types=1` in the URL shows it, so the
// choice is deep-linkable and carries into an embed. The backend builds the
// matching structure either way.
const showType = ref(route.query.types === '1')

const columnHeadings = computed(() => showType.value
  ? [t('questions.typeHeading'), t('questions.askerHeading'), t('questions.answererHeading')]
  : [t('questions.askerHeading'), t('questions.answererHeading')])

// Toggle by rewriting the URL query; the watcher below syncs state + reloads,
// so the URL stays the single source of truth (and back/forward works).
function toggleType() {
  router.replace({ query: { ...route.query, types: showType.value ? undefined : '1' } })
}

// Resolve backend node "kind"s (which carry no label for the special nodes)
// into localized labels; faction/ministry nodes already carry their own text,
// and question-type nodes carry a main_type code mapped to a localized name.
function nodeLabel(n) {
  if (n.label) return n.label
  if (n.kind === 'type') return t('questions.type.' + n.main_type)
  return t('questions.node.' + n.kind)
}

// Nodes for the diagram, keeping the identity a clicked flow needs to query.
const nodes = computed(() => (data.value ? data.value.nodes.map((n) => ({
  side: n.side,
  column: n.column,
  kind: n.kind,
  faction_id: n.faction_id ?? null,
  main_type: n.main_type ?? null,
  ministry: n.side === 'answerer' && n.kind === 'ministry' ? n.label : null,
  label: nodeLabel(n),
  color: n.color || (n.side === 'asker' ? 'var(--accent)' : '#9c9188'),
})) : []))

const scopeLabel = computed(() => {
  const c = currentCycleLabel()
  return c ? t('cycle.scope', { cycle: c }) : t('cycle.scopeAll')
})

// Monotonic load id: rapid cycle switches can leave fetches racing; only the
// latest may write state.
let loadSeq = 0

async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false
  clearFlow()
  try {
    const res = await api.questionsSankey(store.cycles, showType.value)
    if (seq === loadSeq) data.value = res
  } catch {
    if (seq === loadSeq) error.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

// --- drill-down: the questions behind one clicked flow or node ------------
// `flow` carries the query (faction / main_type / answerer / ministry) plus what
// to highlight: `linkIndex` for a clicked ribbon, `nodeIndex` for a clicked node
// (the other is -1). `segs` is the header trail (source → target, or a single
// node). Every filter a click doesn't fix stays undefined so the backend matches
// all flows through the chosen endpoint(s).
const flow = ref(null)
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

function scrollToPanel() {
  nextTick(() => panelRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
}

// Fold one node's identity into a drill-down filter, by which column it's in:
// an asker fixes the faction, a type node the question type, an answerer the
// responder. Everything a click doesn't touch is left undefined.
function applyNode(n, filt) {
  if (n.side === 'type') filt.main_type = n.main_type
  else if (n.side === 'answerer') { filt.answerer = n.kind; filt.ministry = n.ministry || undefined }
  else filt.faction = n.faction_id != null ? n.faction_id : 'none'
}

// A stage-1 ribbon (faction → type) fixes faction + type; a stage-2 ribbon
// (type → answerer) fixes type + answerer.
function onSelect(sel) {
  const src = sel.source, tgt = sel.target
  const filt = {}
  applyNode(src, filt)
  applyNode(tgt, filt)
  flow.value = {
    linkIndex: sel.index, nodeIndex: -1, ...filt,
    segs: [{ label: src.label, color: src.color }, { label: tgt.label, color: null }],
  }
  flowOffset.value = 0
  loadFlow()
  scrollToPanel()
}

// Clicking a node filters by that endpoint alone — every flow into/out of it.
function onSelectNode(sel) {
  const n = sel.node
  const filt = {}
  applyNode(n, filt)
  flow.value = {
    linkIndex: -1, nodeIndex: sel.index, ...filt,
    segs: [{ label: n.label, color: n.side === 'asker' ? n.color : null }],
  }
  flowOffset.value = 0
  loadFlow()
  scrollToPanel()
}

// Same guard for the drill-down: clicking flows/pages quickly must not let an
// older list overwrite a newer one.
let flowSeq = 0

async function loadFlow() {
  if (!flow.value) return
  const seq = ++flowSeq
  flowLoading.value = true; flowError.value = false
  try {
    const res = await api.questionsList({
      period: store.cycles,
      faction: flow.value.faction,
      main_type: flow.value.main_type,
      answerer: flow.value.answerer,
      ministry: flow.value.ministry,
      limit: PAGE, offset: flowOffset.value,
    })
    if (seq === flowSeq) flowData.value = res
  } catch {
    if (seq === flowSeq) flowError.value = true
  } finally {
    if (seq === flowSeq) flowLoading.value = false
  }
}

function gotoFlowPage(p) {
  flowOffset.value = p * PAGE
  loadFlow()
  scrollToPanel()
}

onMounted(() => { loadMeta().catch(() => {}).finally(load) })
watch(() => store.cycles.join(','), load)
watch(() => route.query.types, (v) => {
  const s = v === '1'
  if (s !== showType.value) { showType.value = s; load() }
})
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
      <div class="q-controls">
        <p class="muted small">
          {{ data.total }} {{ $t('questions.count') }} · {{ scopeLabel }}
        </p>
        <button
          type="button" class="btn secondary small" :aria-pressed="showType"
          @click="toggleType"
        >{{ showType ? $t('questions.hideType') : $t('questions.showType') }}</button>
      </div>
      <div class="card pad">
        <SankeyDiagram
          :nodes="nodes" :links="data.links"
          :selected="flow ? flow.linkIndex : -1" :selected-node="flow ? flow.nodeIndex : -1"
          :caption="$t('questions.chartCaption')" :show-caption="false"
          :column-headings="columnHeadings"
          @select="onSelect" @select-node="onSelectNode"
        />
        <div class="fig-foot">
          <p class="muted small hint">{{ $t('questions.clickHint') }}</p>
          <EmbedButton
            kind="questions-sankey" :title="$t('questions.title')"
            :params="{ types: showType ? '1' : undefined }"
            :height="560" :max-width="900"
          />
        </div>
      </div>

      <!-- drill-down: the questions making up the clicked flow -->
      <section v-if="flow" ref="panelRef" class="flowpanel">
        <div class="flowhead">
          <h2>
            <template v-for="(seg, i) in flow.segs" :key="i">
              <span v-if="i > 0" class="arrow" aria-hidden="true"> → </span>
              <span :style="seg.color ? { color: seg.color } : undefined">{{ seg.label }}</span>
            </template>
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
                <div class="sponsors small" v-if="b.sponsors.length || b.responder">
                  <template v-for="(s, i) in b.sponsors" :key="i">
                    <router-link v-if="s.person_id" :to="{ name: 'profile', params: { id: s.person_id } }">{{ s.name }}</router-link>
                    <span v-else>{{ s.name }}</span>
                    <FactionBadge v-if="s.faction" :faction="s.faction" />
                    <span v-if="i < b.sponsors.length - 1" aria-hidden="true">·</span>
                  </template>
                  <!-- asker → whoever answered the question in plenary -->
                  <template v-if="b.responder">
                    <span class="arrow" aria-hidden="true">→</span>
                    <span class="visually-hidden">{{ $t('documents.answeredBy') }}:</span>
                    <router-link
                      v-if="b.responder.person_id" :title="b.responder.office"
                      :to="{ name: 'profile', params: { id: b.responder.person_id } }"
                    >{{ b.responder.name }}</router-link>
                    <span v-else :title="b.responder.office">{{ b.responder.name }}</span>
                    <FactionBadge v-if="b.responder.faction" :faction="b.responder.faction" />
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
/* Count on the left, the type-axis toggle on the right, above the chart. */
.q-controls { display: flex; align-items: center; justify-content: space-between; gap: .8rem; flex-wrap: wrap; margin-bottom: .75rem; }
.q-controls p { margin: 0; }
/* In the figure footer row the hint sits left, pushing the embed button right. */
.hint { margin: 0; margin-right: auto; }
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
