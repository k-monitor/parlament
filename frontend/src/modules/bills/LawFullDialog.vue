<script setup>
// One law a bill amends, whole, with the bill's changes in place (BILL-14): the
// full view behind the law diff's "full text" buttons. The diff list shows each
// changed provision on its own; here the same changes stand inside the text
// they amend, so a reader sees what a new paragraph sits between and what the
// rest of the § says.
//
// A long law runs to thousands of paragraphs and points, so the dialog opens at
// the change the reader came from, steps from change to change, and can fold
// the text away from the changes ("around the changes"), each fold opening on
// click. The text is the njt state the changes were read against; a provision
// the machine rebuilt from a pending bill's own wording has no layout to show,
// so it reads as one run of text and says so (TRUST-1).
//
// Loaded only when opened, like the bill page's other dialogs.
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import DiffText from '../../components/DiffText.vue'
import StateBlock from '../../components/StateBlock.vue'
import { byLine, stateLine } from './lawChangeText.js'

const props = defineProps({
  billId: { type: String, required: true },
  // The law as /changes lists it (title, method, base/target, url).
  law: { type: Object, required: true },
  // The act the bill became, for the "amended by" line; null while pending.
  act: { type: Object, default: null },
  // The change to open at, counted as the diff list counts them.
  start: { type: Number, default: 0 },
})
const emit = defineEmits(['close'])
const { t } = useI18n()

const data = ref(null)
const loading = ref(true)
const failed = ref(false)

async function load() {
  loading.value = true
  failed.value = false
  try {
    data.value = await api.billLawFull(props.billId, props.law.doc_id)
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
  if (data.value?.available) {
    await nextTick()
    go(Math.min(props.start, changes.value.length - 1), false)
  }
}

const changes = computed(() => data.value?.changes || [])
const lawInfo = computed(() => data.value?.law || props.law)

// --- what is shown: everything, or the text around each change ---------------
const mode = ref('full')
// Units kept on either side of a change when folding, besides the § heading
// the change stands under.
const CONTEXT = 2
// A fold that would hide no more than this is not worth a button.
const MIN_FOLD = 4
const opened = reactive({})

const rows = computed(() => {
  const blocks = data.value?.blocks || []
  const all = blocks.map((b, i) => ({ key: i, b }))
  if (mode.value === 'full') return all
  const keep = new Array(blocks.length).fill(false)
  blocks.forEach((b, i) => {
    if (b.change == null) return
    for (let k = Math.max(0, i - CONTEXT); k <= Math.min(blocks.length - 1, i + CONTEXT); k++) {
      keep[k] = true
    }
    for (let k = i - 1; k >= 0; k--) {
      if (blocks[k].unit?.cls === 'szakasz') { keep[k] = true; break }
      if (blocks[k].change != null) break
    }
  })
  const out = []
  let run = []
  const flush = () => {
    if (run.length >= MIN_FOLD && !opened[run[0].key]) {
      out.push({ key: 'f' + run[0].key, fold: run.length, start: run[0].key })
    } else {
      out.push(...run)
    }
    run = []
  }
  all.forEach((r, i) => {
    if (keep[i]) { flush(); out.push(r) } else run.push(r)
  })
  flush()
  return out
})

function setMode(m) {
  if (mode.value === m) return
  const at = current.value
  mode.value = m
  nextTick(() => go(at, false))
}

// --- stepping from change to change -------------------------------------------
const bodyRef = ref(null)
const current = ref(0)

function changeEls() {
  return bodyRef.value ? [...bodyRef.value.querySelectorAll('[data-change]')] : []
}

function go(i, smooth = true) {
  const el = bodyRef.value?.querySelector(`[data-change="${i}"]`)
  if (!el) return
  current.value = i
  const still = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  el.scrollIntoView({ block: 'start', behavior: smooth && !still ? 'smooth' : 'auto' })
  el.focus({ preventScroll: true })
}

// Next and previous are reckoned from where the reader is, not from the last
// button press: they may have scrolled anywhere since.
function step(dir) {
  const box = bodyRef.value?.getBoundingClientRect()
  if (!box) return
  // The change at the top of the view stands a scroll margin below it.
  let target = null
  for (const el of changeEls()) {
    const top = el.getBoundingClientRect().top - box.top
    if (dir > 0 && top > 24) { target = el; break }
    if (dir < 0 && top < -4) target = el
  }
  if (target) go(Number(target.dataset.change))
}

let ticking = false
function onScroll() {
  if (ticking) return
  ticking = true
  requestAnimationFrame(() => {
    ticking = false
    const box = bodyRef.value?.getBoundingClientRect()
    if (!box) return
    let at = null
    for (const el of changeEls()) {
      if (el.getBoundingClientRect().top - box.top <= box.height / 3) at = Number(el.dataset.change)
      else break
    }
    if (at != null) current.value = at
  })
}

const atFirst = computed(() => current.value <= 0)
const atLast = computed(() => current.value >= changes.value.length - 1)

function kindLabel(c) {
  return t('lawChanges.kind.' + c.kind)
}

// --- the dialog itself ----------------------------------------------------------
const closeRef = ref(null)
let opener = null
let bodyOverflow = ''
// A close on the backdrop has to have *started* there: selecting text in the
// law and letting go past the dialog's edge ends in a click on the backdrop too.
let pressedBackdrop = false
function onBackdropDown(e) { pressedBackdrop = e.target === e.currentTarget }
function onBackdropClick(e) {
  if (pressedBackdrop && e.target === e.currentTarget) emit('close')
  pressedBackdrop = false
}
function onKeydown(e) {
  if (e.key === 'Escape') emit('close')
}

onMounted(() => {
  opener = document.activeElement
  bodyOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  window.addEventListener('keydown', onKeydown)
  closeRef.value?.focus()
  load()
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = bodyOverflow
  if (opener?.isConnected) opener.focus({ preventScroll: true })
})

</script>

<template>
  <Teleport to="body">
    <div class="lfd-backdrop" @pointerdown="onBackdropDown" @click="onBackdropClick">
      <div class="lfd" role="dialog" aria-modal="true" aria-labelledby="lfd-title">
        <header class="lfd-head">
          <div class="lfd-titles">
            <h2 id="lfd-title">
              {{ lawInfo.title || lawInfo.doc_id }}<span v-if="lawInfo.abbr" class="muted"> ({{ lawInfo.abbr }})</span>
            </h2>
            <p class="muted small lfd-sub">
              <span v-if="lawInfo.method === 'computed'" class="mbadge"
                    :title="$t('lawChanges.computedBadgeTitle')">{{ $t('lawChanges.computedBadge') }}</span>
              {{ stateLine(t, lawInfo) }}
              <a :href="lawInfo.url" target="_blank" rel="noopener" class="nowrap">↗ {{ $t('lawChanges.openNjt') }}</a>
            </p>
          </div>
          <button ref="closeRef" type="button" class="lfd-x"
                  :aria-label="$t('lawFull.close')" :title="$t('lawFull.close')"
                  @click="emit('close')">✕</button>
        </header>

        <div v-if="data?.available" class="lfd-tools">
          <div class="modes" role="group" :aria-label="$t('lawFull.modeLabel')">
            <button type="button" class="mode" :class="{ active: mode === 'full' }"
                    :aria-pressed="mode === 'full'" @click="setMode('full')">
              {{ $t('lawFull.modeFull') }}
            </button>
            <button type="button" class="mode" :class="{ active: mode === 'context' }"
                    :aria-pressed="mode === 'context'" @click="setMode('context')">
              {{ $t('lawFull.modeContext') }}
            </button>
          </div>
          <div class="nav">
            <button type="button" class="btn small secondary" :disabled="atFirst"
                    :title="$t('lawFull.prev')" :aria-label="$t('lawFull.prev')"
                    @click="step(-1)">‹</button>
            <span class="pos small" aria-live="polite"
                  :aria-label="$t('lawFull.positionLabel', { i: current + 1, n: changes.length })">
              {{ $t('lawFull.position', { i: current + 1, n: changes.length }) }}
            </span>
            <button type="button" class="btn small secondary" :disabled="atLast"
                    :title="$t('lawFull.next')" :aria-label="$t('lawFull.next')"
                    @click="step(1)">›</button>
          </div>
          <span class="legend small">
            <ins>{{ $t('diff.legendIns') }}</ins> <del>{{ $t('diff.legendDel') }}</del>
          </span>
        </div>

        <div ref="bodyRef" class="lfd-body" @scroll.passive="onScroll">
          <StateBlock :loading="loading" :error="failed" :empty="!!data && !data.available"
                      :empty-text="$t('lawFull.unavailable')" @retry="load">
            <div v-if="data?.available" class="law">
              <template v-for="r in rows" :key="r.key">
                <button v-if="r.fold" type="button" class="fold" @click="opened[r.start] = true">
                  <span aria-hidden="true">⋯</span> {{ $t('lawFull.fold', { n: r.fold }) }}
                </button>
                <p v-else-if="r.b.unit" class="u" :class="['u-' + r.b.unit.cls, { empty: !r.b.unit.text }]">
                  <span v-if="r.b.unit.label" class="lab">{{ r.b.unit.label }}</span>
                  {{ r.b.unit.text }}
                </p>
                <section v-else class="chg" :class="{ current: r.b.change === current }"
                         :data-change="r.b.change" tabindex="-1"
                         :aria-label="`${changes[r.b.change].ref}: ${kindLabel(changes[r.b.change])}`">
                  <div class="chead small">
                    <a :href="changes[r.b.change].url" target="_blank" rel="noopener" class="cref"
                       :title="$t('lawChanges.openUnit')">{{ changes[r.b.change].ref || changes[r.b.change].jh }}</a>
                    <span class="kind" :class="changes[r.b.change].kind">{{ kindLabel(changes[r.b.change]) }}</span>
                    <span class="muted by">{{ byLine(t, lawInfo, act, changes[r.b.change]) }}</span>
                    <span v-if="r.b.units[0]?.cls === 'flat'" class="muted flatnote">
                      {{ $t('lawFull.flat') }}
                    </span>
                  </div>
                  <p v-for="(u, k) in r.b.units" :key="k" class="u"
                     :class="['u-' + u.cls, 'op-' + u.op, { empty: !u.diff.length }]">
                    <template v-if="u.label"><span class="lab">{{ u.label }}</span>{{ ' ' }}</template>
                    <DiffText tag="span" :segments="u.diff" />
                  </p>
                </section>
              </template>
            </div>
          </StateBlock>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.lfd-backdrop {
  position: fixed; inset: 0; z-index: 100;
  background: rgba(20, 10, 8, .55);
  display: flex; align-items: center; justify-content: center; padding: 1rem;
  animation: lfd-fade .15s ease;
}
.lfd {
  width: min(62rem, 100%); height: 94vh; display: flex; flex-direction: column;
  background: var(--surface); border-radius: var(--radius);
  box-shadow: 0 12px 40px rgba(0, 0, 0, .35); overflow: hidden;
}
@keyframes lfd-fade { from { opacity: 0 } }
@media (prefers-reduced-motion: reduce) { .lfd-backdrop { animation: none; } }

.lfd-head {
  flex: none; display: flex; align-items: flex-start; justify-content: space-between;
  gap: 1rem; padding: 1rem 1.2rem .6rem;
}
.lfd-titles { min-width: 0; }
.lfd-head h2 { margin: 0; font-size: 1.1rem; overflow-wrap: anywhere; }
.lfd-sub { margin: .2rem 0 0; display: flex; gap: .2rem .6rem; flex-wrap: wrap; align-items: baseline; }
.lfd-x {
  flex: none; background: none; border: none; font-size: 1rem; cursor: pointer;
  color: var(--ink-faint); padding: .3rem .45rem; border-radius: 8px; line-height: 1;
}
.lfd-x:hover { background: var(--line); }
.mbadge {
  font-size: .72rem; font-weight: 700; padding: 0 .45rem; border-radius: 999px;
  line-height: 1.5; background: #fbefd5; color: #6b4a00; white-space: nowrap; cursor: help;
}
.nowrap { white-space: nowrap; }

.lfd-tools {
  flex: none; display: flex; align-items: center; gap: .6rem 1rem; flex-wrap: wrap;
  padding: .5rem 1.2rem; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line);
  background: var(--bg);
}
/* The same either/or control as the representatives' search modes. */
.modes {
  display: flex; gap: .2rem; padding: .2rem; border: 1px solid var(--line);
  border-radius: var(--radius); background: var(--surface);
}
.mode {
  border: 0; background: none; font: inherit; cursor: pointer;
  padding: .3rem .8rem; border-radius: calc(var(--radius) - 3px);
  font-size: .85rem; font-weight: 600; line-height: 1.4; color: var(--ink-soft);
}
.mode:hover { color: var(--accent); background: var(--accent-soft); }
.mode.active, .mode.active:hover {
  background: var(--accent); color: var(--accent-ink); box-shadow: var(--shadow);
}
.nav { display: flex; align-items: center; gap: .4rem; }
.nav .btn { min-width: 2.2rem; font-size: 1rem; line-height: 1.1; }
.pos { min-width: 4.5rem; text-align: center; color: var(--ink-soft); font-variant-numeric: tabular-nums; }
.legend { margin-left: auto; }
.legend ins { background: #dcefdc; color: #1d4d20; text-decoration: none; padding: 0 .25em; border-radius: 3px; }
.legend del { background: #f9dcd8; color: #7a1c10; padding: 0 .25em; border-radius: 3px; }

.lfd-body { flex: 1 1 auto; overflow-y: auto; overscroll-behavior: contain; padding: .8rem 1.2rem 2rem; }
.law { max-width: 48rem; margin: 0 auto; line-height: 1.6; }

/* The law's own layout, after njt: chapters and § numbers centred, points and
   sub-points indented under their paragraph. */
.u { margin: 0 0 .45rem; overflow-wrap: anywhere; }
.lab { font-weight: 700; }
/* A number njt still lists with no text (a repealed provision) — not a §
   heading, which never has text of its own. */
.u.empty:not(.u-szakasz) .lab { color: var(--ink-faint); font-weight: 400; }
.u-resz, .u-reszcim, .u-fejezet, .u-fejezetCim {
  text-align: center; font-weight: 700; margin-top: 1.3rem;
}
.u-reszcim, .u-fejezetCim { margin-top: 0; }
.u-alcim { text-align: center; font-style: italic; font-weight: 600; margin-top: 1rem; }
.u-szakasz { text-align: center; margin-top: 1rem; }
.u-preambulum { font-style: italic; }
.u-betusPontNyito, .u-szamosPontNyito, .u-pontZaro { padding-left: 1.6rem; }
.u-ketbetusAlPont { padding-left: 3.2rem; }

.fold {
  display: block; width: 100%; margin: .4rem 0 .8rem; padding: .35rem .8rem;
  border: 1px dashed var(--line); border-radius: 8px; background: var(--bg);
  font: inherit; font-size: .85rem; color: var(--ink-soft); cursor: pointer; text-align: center;
}
.fold:hover, .fold:focus-visible { border-color: var(--accent); color: var(--accent); }

.chg {
  margin: .6rem -.8rem .9rem; padding: .5rem .8rem .3rem;
  border-left: 3px solid #d9a33b; border-radius: 0 8px 8px 0; background: #fdf8ee;
  scroll-margin-top: .8rem;
}
.chg.current { border-left-color: var(--accent); }
.chg:focus { outline: none; }
.chg:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }
.chead { display: flex; gap: .2rem .5rem; align-items: baseline; flex-wrap: wrap; margin-bottom: .35rem; }
.cref { font-weight: 700; color: var(--accent); }
.kind {
  font-size: .72rem; font-weight: 700; padding: 0 .45rem; border-radius: 999px;
  line-height: 1.5; background: var(--line); color: var(--ink-soft); white-space: nowrap;
}
.kind.new { background: #dcefdc; color: #1d4d20; }
.kind.repealed { background: #f9dcd8; color: #7a1c10; }
.by, .flatnote { overflow-wrap: anywhere; }
/* A whole unit added or taken away is marked as a line, label included. */
.op-ins .lab { background: #dcefdc; color: #1d4d20; border-radius: 3px; padding: 0 .1em; }
.op-del .lab { background: #f9dcd8; color: #7a1c10; border-radius: 3px; padding: 0 .1em;
               text-decoration: line-through; }

@media (max-width: 560px) {
  .lfd-backdrop { padding: 0; }
  .lfd { height: 100%; border-radius: 0; }
  .lfd-head { padding: .8rem .9rem .5rem; }
  .lfd-tools { padding: .45rem .9rem; }
  .lfd-body { padding: .6rem .9rem 2rem; }
  .legend { margin-left: 0; }
  .chg { margin-left: -.5rem; margin-right: -.5rem; padding-left: .6rem; padding-right: .6rem; }
  .u-betusPontNyito, .u-szamosPontNyito, .u-pontZaro { padding-left: 1rem; }
  .u-ketbetusAlPont { padding-left: 2rem; }
}
</style>
