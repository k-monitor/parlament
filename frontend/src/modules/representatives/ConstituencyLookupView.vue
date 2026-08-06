<script setup>
// "Who represents me?" — find your own single-member constituency and its MP
// (REP-10).
//
// Two steps, because the data supports exactly two cases. Almost every settlement
// lies wholly inside one constituency, so naming the place *is* the answer and the
// page shows the MP straight away. The 23 that are split — 15 Budapest districts
// and 8 large cities — cannot be resolved from a place name at all, so the page
// hands the reader the boundaries on a map and lets them pick the part they live
// in.
//
// The chosen settlement (and constituency) live in the URL, so a resolved answer
// is shareable and citable like every other view (SEA-6 in spirit). This page is
// deliberately NOT scoped by the global cycle selector: constituency boundaries
// are redrawn between elections, so the map answers for exactly one cycle, which
// the page names rather than infers from the reader's scope.
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { loadMeta, store } from '../../store.js'
import StateBlock from '../../components/StateBlock.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import SpeakerLink from '../../components/SpeakerLink.vue'
import ConstituencyMap from '../../components/ConstituencyMap.vue'
import { copyText } from '../../lib/clipboard.js'

const route = useRoute()
const router = useRouter()
const { t, locale } = useI18n()

// --- step 1: find the settlement -------------------------------------------
const q = ref(route.query.q || '')
const suggestions = ref([])
const searching = ref(false)
const searchFailed = ref(false)
// Distinguishes "nothing typed yet" from "typed, and there is no such place".
const searched = ref(false)

let searchSeq = 0
async function runSearch() {
  const term = q.value.trim()
  const seq = ++searchSeq
  if (!term) {
    suggestions.value = []
    searched.value = false
    searchFailed.value = false
    return
  }
  searching.value = true
  searchFailed.value = false
  try {
    const res = await api.settlementSearch(term)
    if (seq !== searchSeq) return
    suggestions.value = res.settlements
    searched.value = true
  } catch {
    if (seq === searchSeq) { searchFailed.value = true; suggestions.value = [] }
  } finally {
    if (seq === searchSeq) searching.value = false
  }
}

let timer = null
function onInput() {
  clearTimeout(timer)
  timer = setTimeout(() => {
    runSearch()
    // Keep the typed term in the URL so a half-finished search survives a reload.
    const query = { ...route.query, q: q.value.trim() || undefined }
    delete query.maz
    delete query.taz
    delete query.evk
    router.replace({ name: 'lookup', query })
  }, 250)
}
// The debounce outlives the component; without this, typing and then navigating
// away yanks the reader back here when the timer fires (cf. RepListView).
onUnmounted(() => clearTimeout(timer))

// --- step 2: the settlement's constituency (or constituencies) --------------
const result = ref(null)
const loading = ref(false)
const failed = ref(false)
// 503 from the endpoint: the election office's data could not be reached and
// nothing was cached. A different message from a generic failure, because the
// remedy is different — try later, nothing is wrong with the request.
const unavailable = ref(false)

const selectedEvk = ref(route.query.evk || null)

const split = computed(() => !!result.value && result.value.constituencies.length > 1)
const single = computed(() =>
  result.value && result.value.constituencies.length === 1
    ? result.value.constituencies[0] : null)
const chosen = computed(() => {
  if (!result.value) return null
  if (single.value) return single.value
  return result.value.constituencies.find((c) => c.evk === selectedEvk.value) || null
})

// The cycle this answer belongs to, spelled out as years — the same label the rest
// of the site uses for a cycle, so "2026–" reads the same here as in the header.
const periodLabel = computed(() => {
  const p = result.value && result.value.period
  if (!p) return ''
  const periods = (store.meta && store.meta.periods) || []
  const match = periods.find((x) => x.number === p.number)
  const start = (match ? match.date_start : p.date_start) || ''
  const end = (match ? match.date_end : p.date_end) || ''
  if (!start) return String(p.number)
  return end ? `${start.slice(0, 4)}–${end.slice(0, 4)}` : `${start.slice(0, 4)}–`
})

let loadSeq = 0
async function loadSettlement(maz, taz) {
  const seq = ++loadSeq
  loading.value = true
  failed.value = false
  unavailable.value = false
  try {
    const res = await api.settlementConstituencies(maz, taz)
    if (seq !== loadSeq) return
    result.value = res
    // A settlement that turned out not to be split has nothing to pick.
    if (res.constituencies.length === 1) selectedEvk.value = res.constituencies[0].evk
  } catch (e) {
    if (seq !== loadSeq) return
    result.value = null
    if (e.status === 503) unavailable.value = true
    else failed.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function pick(s) {
  q.value = s.name
  suggestions.value = []
  searched.value = false
  selectedEvk.value = null
  router.push({ name: 'lookup', query: { q: s.name, maz: s.maz, taz: s.taz } })
}

function selectConstituency(evk) {
  selectedEvk.value = evk
  router.replace({ name: 'lookup', query: { ...route.query, evk } })
}

function startOver() {
  q.value = ''
  suggestions.value = []
  searched.value = false
  result.value = null
  selectedEvk.value = null
  router.push({ name: 'lookup', query: {} })
}

// The URL is the source of truth for which settlement is open, so back/forward
// and a pasted link all land on the same state.
function syncFromRoute() {
  const { maz, taz, evk } = route.query
  selectedEvk.value = evk || null
  if (maz && taz) {
    if (!result.value
        || result.value.settlement.maz !== maz || result.value.settlement.taz !== taz) {
      loadSettlement(maz, taz)
    }
  } else {
    result.value = null
  }
}

onMounted(async () => {
  await loadMeta().catch(() => {})
  syncFromRoute()
  // A shared link carries `q` as well; re-run it so the reader can keep browsing
  // from where the sharer left off, but not when a settlement is already resolved
  // (the list would cover the answer).
  if (q.value && !route.query.maz) runSearch()
})
watch(() => route.query, syncFromRoute)

const mapAria = computed(() =>
  result.value ? t('lookup.mapAriaFor', { name: result.value.settlement.name }) : '')

function officialNameOf(c) {
  return (locale.value === 'en' && c.official_name_en) || c.official_name || c.label
}

// --- contacting the MP ------------------------------------------------------
// Which MP's address was just copied (the button swaps to a tick for a moment),
// plus the same news as text for screen readers.
const copiedId = ref(null)
const copyStatus = ref('')
let copyTimer = null

async function copyEmail(mp) {
  if (!(await copyText(mp.email))) return   // clipboard blocked: claim nothing
  copiedId.value = mp.person_id
  copyStatus.value = t('lookup.emailCopiedOf', { email: mp.email })
  clearTimeout(copyTimer)
  copyTimer = setTimeout(() => {
    copiedId.value = null
    copyStatus.value = ''
  }, 1800)
}
onUnmounted(() => clearTimeout(copyTimer))
</script>

<template>
  <h1>{{ $t('lookup.title') }}</h1>
  <p class="muted intro">{{ $t('lookup.intro') }}</p>

  <form class="card pad searchform" role="search" @submit.prevent="runSearch">
    <label for="lk-q">{{ $t('lookup.searchLabel') }}</label>
    <div class="row" style="gap:.5rem;">
      <input
        id="lk-q" type="search" v-model="q" autocomplete="address-level2"
        :placeholder="$t('lookup.searchPlaceholder')"
        @input="onInput" style="flex:1;min-width:200px;"
      />
      <button v-if="q" class="btn secondary" type="button" @click="startOver">
        {{ $t('lookup.clear') }}
      </button>
    </div>
    <p class="muted small hint">{{ $t('lookup.searchHint') }}</p>

    <p v-if="searchFailed" class="state small" role="alert" style="margin:.6rem 0 0;">
      {{ $t('app.error') }}
    </p>
    <ul v-else-if="suggestions.length" class="suggestions" :aria-busy="searching">
      <li v-for="s in suggestions" :key="s.maz + '/' + s.taz">
        <button type="button" class="suggestion" @click="pick(s)">
          <span class="s-name">{{ s.name }}</span>
          <span class="muted small s-meta">
            {{ s.county }}
            <template v-if="s.constituency_count > 1">
              · {{ $t('lookup.splitBadge', { count: s.constituency_count }) }}
            </template>
          </span>
        </button>
      </li>
    </ul>
    <p v-else-if="searched && !searching" class="muted small" style="margin:.6rem 0 0;">
      {{ $t('lookup.noSettlement') }}
    </p>
  </form>

  <!-- The election office's data is a live third-party source; when it cannot be
       reached at all, say so rather than showing an empty answer (TRUST-1). -->
  <div v-if="unavailable" class="card pad" role="alert">
    <p style="margin:0;">{{ $t('lookup.unavailable') }}</p>
  </div>

  <StateBlock v-else :loading="loading" :error="failed" @retry="syncFromRoute">
    <section v-if="result" class="answer">
      <h2 class="settlement-h">
        {{ result.settlement.name }}
        <span class="muted small">· {{ result.settlement.county }}</span>
      </h2>

      <!-- The common case: one constituency, so the place name is the answer. -->
      <p v-if="single" class="lead">
        {{ $t('lookup.singleAnswer', { name: result.settlement.name, constituency: single.label }) }}
      </p>

      <!-- The split case: the reader has to say which part of the settlement they
           live in, and the map is how they can. -->
      <template v-else-if="split">
        <p class="lead">
          {{ $t('lookup.splitAnswer', { name: result.settlement.name, count: result.constituencies.length }) }}
        </p>
        <p class="muted small">{{ $t('lookup.splitHelp') }}</p>

        <ConstituencyMap
          v-if="result.geojson && result.map"
          :geojson="result.geojson" :map="result.map"
          :selected="selectedEvk" :aria-label="mapAria"
          @select="selectConstituency"
        />
        <!-- No boundaries available upstream: the list below is still a complete,
             if less convenient, answer — never a dead end. -->
        <p v-else class="muted small">{{ $t('lookup.noGeometry') }}</p>

        <!-- The same choice as buttons: the map is not the only way to pick, so
             the page works without pointing at a polygon (A11Y-1). -->
        <ul class="picker" :aria-label="$t('lookup.pickerLabel')">
          <li v-for="c in result.constituencies" :key="c.evk">
            <button
              type="button" class="pick" :class="{ active: c.evk === selectedEvk }"
              :aria-pressed="c.evk === selectedEvk" @click="selectConstituency(c.evk)"
            >
              <!-- The same number the map pins on that region — the one thing that
                   identifies it, since every region is drawn the same colour. -->
              <span class="numbadge" aria-hidden="true">{{ c.number }}</span>
              <span class="pick-label">{{ c.label }}</span>
              <span v-if="c.representatives.length" class="muted small pick-mp">
                {{ c.representatives.map((m) => m.label).join(', ') }}
              </span>
            </button>
          </li>
        </ul>
      </template>

      <!-- The answer itself: who holds the constituency. -->
      <div v-if="chosen" class="card pad result">
        <p class="muted small" style="margin:0 0 .2rem;">{{ officialNameOf(chosen) }}</p>
        <h3 style="margin:0 0 .6rem;">{{ chosen.label }}</h3>

        <ul v-if="chosen.representatives.length" class="mps">
          <li v-for="mp in chosen.representatives" :key="mp.person_id" class="mp">
            <div class="mp-who">
              <SpeakerLink :speaker="mp" />
              <FactionBadge :faction="mp.faction" link />
            </div>
            <!-- Finding out who represents you is usually a prelude to contacting
                 them, so the address is actionable right here: `mailto:` for
                 whoever has a mail client wired up, and a copy button for whoever
                 doesn't (webmail in another tab is the common case). -->
            <div v-if="mp.email" class="mp-contact">
              <a class="btn secondary small" :href="'mailto:' + mp.email">
                <span aria-hidden="true">✉</span> {{ $t('lookup.writeEmail') }}
              </a>
              <button
                type="button" class="btn secondary small copybtn"
                :aria-label="$t('lookup.copyEmailOf', { email: mp.email })"
                @click="copyEmail(mp)"
              >
                <span aria-hidden="true">{{ copiedId === mp.person_id ? '✓' : '⧉' }}</span>
                <span class="copybtn-text">
                  {{ copiedId === mp.person_id ? $t('lookup.emailCopied') : mp.email }}
                </span>
              </button>
            </div>
            <p v-else class="muted small no-email">{{ $t('lookup.noEmail') }}</p>
          </li>
        </ul>
        <p v-else class="muted">{{ $t('lookup.noMp') }}</p>
        <!-- One live region for the whole list: a per-button one would announce
             nothing on the first copy (the region has to exist before it changes). -->
        <p class="visually-hidden" role="status" aria-live="polite">{{ copyStatus }}</p>

        <p class="muted small scope">
          {{ $t('lookup.scope', { cycle: periodLabel }) }}
        </p>
        <p class="muted small">
          {{ $t('lookup.listNote') }}
          <router-link :to="{ name: 'representatives' }">{{ $t('lookup.listNoteLink') }}</router-link>
        </p>
      </div>
      <p v-else-if="split" class="muted">{{ $t('lookup.pickPrompt') }}</p>
      <!-- Defensive: a settlement the election office lists with no constituency of
           ours. It does not occur in the current data, but a bare heading with no
           answer at all would be worse than saying we don't know. -->
      <p v-else class="muted">{{ $t('lookup.noConstituency') }}</p>

      <!-- Provenance: this is the one page whose data does not come from
           parlament.hu, so it names its own source and method (TRUST-1). -->
      <details class="method">
        <summary class="small muted">{{ $t('lookup.methodology') }}</summary>
        <!-- Translated here rather than shown from the response: the endpoint's own
             `methodology` string is Hungarian (it serves API consumers), and this
             page has an English reading too. -->
        <p class="small muted">{{ $t('lookup.methodologyText') }}</p>
        <p class="small muted" v-if="result.source">
          {{ $t('lookup.sourceLine', { name: result.source.name }) }}
          <a :href="result.source.url" target="_blank" rel="noopener">valasztas.hu</a>
        </p>
      </details>
    </section>
  </StateBlock>
</template>

<style scoped>
.intro { max-width: 62ch; margin-top: -.4rem; }
.searchform label { display: block; font-weight: 600; font-size: .9rem; margin-bottom: .3rem; }
.hint { margin: .4rem 0 0; }

.suggestions {
  list-style: none; margin: .6rem 0 0; padding: 0; display: grid; gap: .2rem;
  /* A short query can match dozens of settlements (Hungary has many a
     "…szentlászló"); scroll the list instead of pushing the answer off screen. */
  max-height: 22rem; overflow-y: auto;
}
.suggestion {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: .5rem; width: 100%;
  text-align: left; cursor: pointer; padding: .5rem .6rem; border-radius: 8px;
  border: 1px solid transparent; background: transparent; color: inherit;
  font: inherit;
}
.suggestion:hover, .suggestion:focus-visible { background: var(--accent-soft); border-color: #f0cfc9; }
.s-name { font-weight: 600; }
.s-meta { margin-left: auto; }

.answer { margin-top: 1.5rem; }
.settlement-h { margin: 0 0 .2rem; }
.lead { font-size: 1.05rem; margin: .2rem 0 .6rem; }

.picker { list-style: none; margin: .8rem 0 1rem; padding: 0; display: grid; gap: .35rem; }
.pick {
  display: flex; align-items: center; gap: .6rem; width: 100%; text-align: left;
  cursor: pointer; padding: .55rem .7rem; border-radius: 8px; font: inherit;
  border: 1px solid var(--line); background: var(--surface); color: inherit;
}
.pick:hover { border-color: var(--accent); }
.pick.active { border-color: var(--accent); background: var(--accent-soft); }
/* Mirrors the pill the map pins on each region, selected state included, so the
   eye can jump straight between the list and the map. */
.numbadge {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 1.5rem; height: 1.5rem; padding: 0 .3rem; flex-shrink: 0;
  border: 1px solid #b9bdc4; border-radius: 999px; background: var(--surface);
  font-weight: 700; font-size: .84rem; line-height: 1;
}
.pick.active .numbadge { background: #3e3b32; color: #fff; border-color: #3e3b32; }
.pick-label { font-weight: 600; }
.pick-mp { margin-left: auto; text-align: right; }

.result { margin-top: .4rem; }
.mps { list-style: none; margin: 0; padding: 0; display: grid; gap: 1rem; }
/* Who they are on one line, how to reach them on the next — the contact row can
   hold a long address, and pairing it with the name on one line would push the
   faction badge off on a phone. */
.mp { display: grid; gap: .5rem; }
.mp-who { display: flex; align-items: center; gap: .8rem; flex-wrap: wrap; }
.mp-contact { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; }
.no-email { margin: 0; }
.btn.small { padding: .35rem .7rem; font-size: .85rem; }
/* The address IS the copy button's label, so it says what will be copied without
   a second line of text. It gives way to an ellipsis before the row wraps
   awkwardly — the full address stays in the button's accessible name. */
.copybtn { font-weight: 600; max-width: 100%; }
.copybtn-text {
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.scope { margin-top: .9rem; }
.method { margin-top: 1.2rem; }
.method summary { cursor: pointer; }
</style>
