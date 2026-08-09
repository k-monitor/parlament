<script setup>
// Tárcák — the government side of the record (§6C / MIN-5).
//
// A sibling page of the office holders (REP-11): that page answers "who held
// this post and when", this one answers what the *institution* did — what the
// House put to it and what it put to the House. Grouped by kind rather than
// paginated: there are under a hundred tárcák in the whole corpus, and a reader
// wants to see the government at once, ministries first and the independent
// bodies that answer to the House last.
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { store, loadMeta, currentCycleLabel } from '../../store.js'
import StateBlock from '../../components/StateBlock.vue'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const data = ref(null)
const loading = ref(false)
const error = ref(false)

const f = reactive({ q: route.query.q || '' })

const scopeText = computed(() => {
  const c = currentCycleLabel()
  return c ? t('portfolios.scope', { cycle: c }) : t('cycle.scopeAll')
})

// Grouped in the order the API gives (ministries → PM → portfolio-less →
// other → independent bodies); each group keeps the corpus-weight ordering.
const groups = computed(() => {
  if (!data.value) return []
  return data.value.kinds.map((kind) => ({
    kind,
    items: data.value.portfolios.filter((p) => p.kind === kind),
  })).filter((g) => g.items.length)
})

// The speeches column is only meaningful for the cycles whose speeches carry the
// speaker's office at all (MIN-10). Where the scope reaches beyond them, the page
// says so instead of letting a zero read as "this ministry never spoke".
const speechesPartial = computed(() => {
  if (!data.value) return false
  const covered = data.value.speech_coverage || []
  const scope = store.cycles.length ? store.cycles : covered
  return scope.some((p) => !covered.includes(p))
})

function apply() {
  const query = {}
  if (f.q) query.q = f.q
  router.push({ name: 'portfolios', query })
}

// The most senior office holder in scope, for the card's "who runs it" line —
// the API already orders them minister-first.
function lead(p) {
  return p.holders && p.holders.length ? p.holders[0] : null
}

let loadSeq = 0
async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false
  try {
    const res = await api.portfolios({ q: route.query.q, period: store.cycles })
    if (seq === loadSeq) data.value = res
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
watch(() => route.query, () => { f.q = route.query.q || ''; load() })
watch(() => store.cycles.join(','), load)

let searchTimer = null
function onSearchInput() { clearTimeout(searchTimer); searchTimer = setTimeout(apply, 300) }
onUnmounted(() => clearTimeout(searchTimer))
</script>

<template>
  <h1>{{ $t('portfolios.title') }}</h1>
  <p class="muted small intro">{{ $t('portfolios.intro') }}</p>

  <form class="card pad searchform" role="search" @submit.prevent="apply">
    <input
      type="search" v-model="f.q"
      :placeholder="$t('portfolios.searchPlaceholder')"
      :aria-label="$t('portfolios.searchPlaceholder')"
      @input="onSearchInput" style="width:100%;"
    />
  </form>

  <StateBlock
    :loading="loading" :error="error"
    :empty="!!data && data.portfolios.length === 0"
    :empty-text="$t('portfolios.noResults')"
    @retry="load"
  >
    <div v-if="data">
      <p class="muted small" aria-live="polite" style="margin:.2rem 0 1rem;">
        {{ data.portfolios.length }} {{ $t('portfolios.unit') }} · {{ scopeText }}
      </p>

      <section v-for="g in groups" :key="g.kind" class="kindgroup">
        <h2 class="kindhead">{{ $t('portfolios.kinds.' + g.kind) }}</h2>
        <p v-if="g.kind === 'body'" class="muted small kindnote">
          {{ $t('portfolios.bodyNote') }}
        </p>
        <ul class="plist">
          <li v-for="p in g.items" :key="p.slug" class="card pad prow">
            <div class="pname">
              <RouterLink :to="{ name: 'portfolio', params: { slug: p.slug }, query: route.query }">
                {{ p.name }}
              </RouterLink>
              <span v-if="lead(p)" class="small muted lead">
                {{ lead(p).name }} · {{ lead(p).title }}
              </span>
            </div>
            <!-- Counts carry a word, not only a number: the three are different
                 kinds of thing and a bare "17 · 2 · 24" says none of it. -->
            <div class="pcounts small">
              <span class="stat">
                <strong>{{ p.answered }}</strong> {{ $t('portfolios.answeredShort') }}
              </span>
              <span class="stat">
                <strong>{{ p.submitted }}</strong> {{ $t('portfolios.submittedShort') }}
              </span>
              <span class="stat" :class="{ dim: speechesPartial && !p.speeches }">
                <strong>{{ p.speeches }}</strong> {{ $t('portfolios.speechesShort') }}
              </span>
            </div>
          </li>
        </ul>
      </section>

      <p v-if="speechesPartial" class="muted small coverage">
        {{ $t('portfolios.speechCoverage') }}
      </p>

      <details class="methodology" style="margin-top:1rem;">
        <summary>{{ $t('factions.methodology') }}</summary>
        <p class="small muted">{{ $t('portfolios.methodology') }}</p>
      </details>
    </div>
  </StateBlock>
</template>

<style scoped>
.intro { margin: -.4rem 0 1rem; max-width: 68ch; }
.kindgroup { margin-bottom: 1.6rem; }
.kindhead { font-size: 1rem; margin: 0 0 .2rem; color: var(--ink-soft); }
.kindnote { margin: 0 0 .5rem; max-width: 60ch; }
.plist { list-style: none; padding: 0; margin: 0; display: grid; gap: .5rem; }
.prow {
  display: grid; gap: .35rem .9rem; align-items: center;
  grid-template-columns: minmax(0, 1fr) auto;
}
.pname { display: flex; flex-direction: column; gap: .1rem; min-width: 0; }
.pname a { font-weight: 600; }
.lead { min-width: 0; overflow-wrap: anywhere; }
.pcounts { display: flex; gap: 1rem; white-space: nowrap; }
.stat strong { font-variant-numeric: tabular-nums; }
/* A zero that only means "not measured for these cycles" must not look like a
   measured zero (MIN-10); the note under the list explains it. */
.stat.dim { opacity: .45; }
.coverage { margin-top: .8rem; max-width: 68ch; }
@media (max-width: 720px) {
  .prow { grid-template-columns: 1fr; }
  .pcounts { white-space: normal; gap: .8rem; }
}
.methodology summary { cursor: pointer; font-weight: 600; color: var(--ink-soft); font-size: .85rem; }
</style>
