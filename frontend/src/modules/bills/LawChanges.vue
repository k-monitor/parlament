<script setup>
// What a bill changes in the law (BILL-14): each amended law, each changed
// provision as it reads now against how it would read, from GET /bills/:id/changes.
//
// Where the new text comes from is said up front, because it changes what the
// reader is looking at (TRUST-1): for a bill that became law it is njt.hu's own
// official text before and after the act; for a bill still in Parliament it is
// the bill's amending instructions applied by machine to njt's newest text — a
// projection that can be wrong and that the House may still change.
//
// A law whose text the site holds also opens whole in a dialog, the changes
// standing in place (LawFullDialog) — from the law, or from one change.
import { computed, defineAsyncComponent, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import DiffText from '../../components/DiffText.vue'
import { byLine, stateLine } from './lawChangeText.js'

const LawFullDialog = defineAsyncComponent(() => import('./LawFullDialog.vue'))

const props = defineProps({
  billId: { type: String, required: true },
  changes: { type: Object, required: true },
})
const { t } = useI18n()

// Every law starts closed: the list is a table of contents, and a law's
// diffs show when the reader opens it.
const open = reactive({})
const extra = reactive({})        // doc_id → changes fetched on demand
const loadingLaw = reactive({})
const failedLaw = reactive({})

const laws = computed(() => props.changes.laws || [])
const total = computed(() => laws.value.reduce((n, l) => n + (l.count || 0), 0))
const methods = computed(() => new Set(laws.value.map((l) => l.method)))
const adopted = computed(() => !!props.changes.act)
const unresolved = computed(() => props.changes.unresolved || [])
const unresolvedOpen = ref(false)

const isOpen = (law) => !!open[law.doc_id]
// A law past the response budget arrives as a count; its provisions are
// fetched the first time it is open.
async function ensureLoaded(law) {
  if (!law.truncated || extra[law.doc_id] || loadingLaw[law.doc_id]) return
  loadingLaw[law.doc_id] = true
  failedLaw[law.doc_id] = false
  try {
    const res = await api.billChanges(props.billId, law.doc_id)
    extra[law.doc_id] = res.laws?.[0]?.changes || []
  } catch {
    failedLaw[law.doc_id] = true
  } finally {
    loadingLaw[law.doc_id] = false
  }
}
function toggle(law) {
  open[law.doc_id] = !isOpen(law)
  if (open[law.doc_id]) ensureLoaded(law)
}
const changesOf = (law) => law.changes || extra[law.doc_id] || null

// The law open in the full-text dialog, and the change it opened at.
const full = ref(null)
const openFull = (law, start = 0) => { full.value = { law, start } }

const sourceLabel = computed(() => {
  const s = props.changes.source
  if (!s) return ''
  return s.kind === 'consolidated'
    ? t('lawChanges.sourceConsolidated', { number: s.number })
    : t('lawChanges.sourceBill', { number: s.number })
})
</script>

<template>
  <section class="card pad lawchanges">
    <h2>{{ adopted ? $t('lawChanges.titleAdopted') : $t('lawChanges.title') }}</h2>

    <p class="method small" :class="{ computed: methods.has('computed') }">
      <template v-if="methods.has('njt') && !methods.has('computed')">
        {{ $t('lawChanges.methodNjt', { act: changes.act?.label }) }}
      </template>
      <template v-else-if="methods.has('njt')">
        {{ $t('lawChanges.methodMixed', { act: changes.act?.label }) }}
      </template>
      <template v-else-if="methods.has('computed')">
        {{ $t(adopted ? 'lawChanges.methodComputedAdopted' : 'lawChanges.methodComputed',
              { source: sourceLabel }) }}
      </template>
      <a v-if="changes.source?.url" :href="changes.source.url" target="_blank" rel="noopener"
         class="nowrap"> ↗ {{ sourceLabel }}</a>
    </p>
    <p v-if="laws.length" class="muted small summary">
      {{ $t('lawChanges.summary', { laws: laws.length, changes: total }) }}
      · <span class="legend"><ins>{{ $t('diff.legendIns') }}</ins> <del>{{ $t('diff.legendDel') }}</del></span>
    </p>
    <p v-else-if="changes.scan" class="muted small">{{ $t('lawChanges.scan') }}</p>

    <ul class="plain laws">
      <li v-for="law in laws" :key="law.doc_id" class="law">
        <button type="button" class="lawhead" :aria-expanded="isOpen(law)"
                @click="toggle(law)">
          <span class="caret" aria-hidden="true">{{ isOpen(law) ? '▾' : '▸' }}</span>
          <span class="lawtitle">
            {{ law.title || law.doc_id }}<span v-if="law.abbr" class="muted"> ({{ law.abbr }})</span>
          </span>
          <span v-if="law.method === 'computed' && methods.has('njt')" class="mbadge"
                :title="$t('lawChanges.computedBadgeTitle')">{{ $t('lawChanges.computedBadge') }}</span>
          <span class="count">{{ $t('lawChanges.count', { n: law.count }) }}</span>
        </button>
        <div v-if="isOpen(law)" class="lawbody">
          <p class="muted small statebar">
            {{ stateLine(t, law) }}
            <a :href="law.url" target="_blank" rel="noopener">↗ {{ $t('lawChanges.openNjt') }}</a>
            <button v-if="law.full_text" type="button" class="linkish"
                    @click="openFull(law)">⤢ {{ $t('lawFull.open') }}</button>
          </p>
          <p v-if="loadingLaw[law.doc_id]" class="muted small">{{ $t('lawChanges.loading') }}</p>
          <p v-else-if="failedLaw[law.doc_id]" class="muted small">{{ $t('lawChanges.loadFailed') }}</p>
          <ol v-else class="plain changes">
            <li v-for="(c, j) in changesOf(law) || []" :key="j" class="change">
              <div class="chead">
                <a :href="c.url" target="_blank" rel="noopener" class="cref"
                   :title="$t('lawChanges.openUnit')">{{ c.ref || c.jh }}</a>
                <span class="kind" :class="c.kind">{{ $t('lawChanges.kind.' + c.kind) }}</span>
                <span v-if="byLine(t, law, changes.act, c)" class="muted small by">
                  {{ byLine(t, law, changes.act, c) }}
                </span>
                <button v-if="law.full_text" type="button" class="linkish small inlaw"
                        :title="$t('lawFull.openAtTitle')" @click="openFull(law, j)">
                  ⤢ {{ $t('lawFull.openAt') }}
                </button>
              </div>
              <DiffText :segments="c.diff" :context="160" />
            </li>
          </ol>
        </div>
      </li>
    </ul>

    <div v-if="unresolved.length" class="unresolved">
      <button type="button" class="btn small secondary" :aria-expanded="unresolvedOpen"
              @click="unresolvedOpen = !unresolvedOpen">
        {{ unresolvedOpen ? $t('lawChanges.unresolvedHide')
                            : $t('lawChanges.unresolvedShow', { n: unresolved.length }) }}
      </button>
      <div v-if="unresolvedOpen">
        <p class="muted small">{{ $t('lawChanges.unresolvedNote') }}</p>
        <ol class="plain ulist">
          <li v-for="(u, k) in unresolved" :key="k">
            <span class="cref">{{ u.provision }}</span>
            <span class="muted small"> · {{ u.reason }}</span>
            <p class="utext small">{{ u.text }}</p>
          </li>
        </ol>
      </div>
    </div>

    <LawFullDialog v-if="full" :bill-id="billId" :law="full.law" :act="changes.act"
                   :start="full.start" @close="full = null" />
  </section>
</template>

<style scoped>
/* Same section heading as the rest of the bill page (BillView's scoped
   `.bill section > h2` does not reach into this component). */
section > h2 {
  display: flex; align-items: center; gap: .55rem;
  margin: 0 0 .8rem; font-size: 1.15rem;
}
section > h2::before {
  content: ''; flex: none; width: .28rem; height: 1.05rem;
  background: var(--accent); border-radius: 2px;
}
.method { margin: 0 0 .4rem; color: var(--ink-soft); }
.method.computed { border-left: 3px solid #d9a33b; padding-left: .6rem; }
.summary { margin: 0 0 .8rem; }
.legend ins { background: #dcefdc; color: #1d4d20; text-decoration: none; padding: 0 .25em; border-radius: 3px; }
.legend del { background: #f9dcd8; color: #7a1c10; padding: 0 .25em; border-radius: 3px; }
.plain { list-style: none; padding: 0; margin: 0; }
.laws { display: flex; flex-direction: column; gap: .5rem; }
.law { border: 1px solid var(--line); border-radius: 8px; }
.lawhead {
  display: flex; width: 100%; align-items: baseline; gap: .55rem; text-align: left;
  padding: .6rem .8rem; background: none; border: 0; cursor: pointer;
  font: inherit; color: var(--ink);
}
.lawhead:hover { background: var(--accent-soft); border-radius: 8px; }
.caret { color: var(--ink-faint); flex: none; width: .8rem; }
.lawtitle { font-weight: 600; flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; }
.count { flex: none; font-size: .8rem; color: var(--ink-faint); white-space: nowrap; }
.mbadge {
  flex: none; font-size: .72rem; font-weight: 700; padding: 0 .45rem; border-radius: 999px;
  line-height: 1.5; background: #fbefd5; color: #6b4a00; white-space: nowrap; cursor: help;
}
.lawbody { padding: 0 .8rem .8rem 2.15rem; }
.statebar { display: flex; gap: .8rem; flex-wrap: wrap; margin: 0 0 .6rem; }
.changes { display: flex; flex-direction: column; gap: .9rem; }
.change { border-top: 1px solid var(--line); padding-top: .7rem; }
.change:first-child { border-top: 0; padding-top: 0; }
.chead { display: flex; gap: .5rem; align-items: baseline; flex-wrap: wrap; margin-bottom: .3rem; }
.cref { font-weight: 700; color: var(--accent); }
.kind {
  font-size: .72rem; font-weight: 700; padding: 0 .45rem; border-radius: 999px;
  line-height: 1.5; background: var(--line); color: var(--ink-soft); white-space: nowrap;
}
.kind.new { background: #dcefdc; color: #1d4d20; }
.kind.repealed { background: #f9dcd8; color: #7a1c10; }
.by { overflow-wrap: anywhere; }
/* Buttons that read as the links beside them. */
.linkish {
  border: 0; background: none; padding: 0; font: inherit; color: var(--accent);
  cursor: pointer; white-space: nowrap;
}
.linkish:hover { text-decoration: underline; }
.inlaw { margin-left: auto; }
.unresolved { margin-top: 1rem; }
.ulist { display: flex; flex-direction: column; gap: .6rem; margin-top: .5rem; }
.utext { margin: .2rem 0 0; color: var(--ink-soft); overflow-wrap: anywhere; }
.nowrap { white-space: nowrap; }
@media (max-width: 560px) {
  .lawbody { padding-left: .8rem; }
}
</style>
