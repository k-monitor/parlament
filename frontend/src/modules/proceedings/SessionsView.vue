<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api.js'
import { store, loadMeta } from '../../store.js'
import { formatDate } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import Pagination from '../../components/Pagination.vue'

const route = useRoute()
const router = useRouter()

const PAGE = 60

const data = ref(null)
const loading = ref(false)
const error = ref(false)

const page = computed(() => Math.floor((Number(route.query.offset) || 0) / PAGE))
const totalPages = computed(() => (data.value ? Math.ceil(data.value.total / PAGE) : 0))

function gotoPage(p) {
  router.push({ name: 'sessions', query: { ...route.query, offset: p * PAGE } })
}

// Monotonic load id: the query and cycle watchers can leave fetches racing;
// only the latest may write state.
let loadSeq = 0

async function load() {
  const seq = ++loadSeq
  loading.value = true; error.value = false
  // Scoped to the global cycle chooser (store.cycle; null = all cycles).
  try {
    const res = await api.sessions({
      period: store.cycle, limit: PAGE, offset: route.query.offset || 0,
    })
    if (seq === loadSeq) data.value = res
  } catch {
    if (seq === loadSeq) error.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}
onMounted(() => { loadMeta().catch(() => {}).finally(load) })
watch(() => route.query, load)
// Changing the cycle resets to the first page; the offset reset triggers load via the query watcher.
watch(() => store.cycle, () => {
  if (route.query.offset) router.push({ name: 'sessions', query: {} })
  else load()
})
</script>

<template>
  <h1>{{ $t('sessions.title') }}</h1>
  <StateBlock :loading="loading" :error="error" @retry="load">
    <div v-if="data" class="grid sgrid">
      <!-- Announced upcoming sittings and held-but-not-yet-processed sittings (no
           per-speech timings/video/transcript yet) are both shown muted & dashed but
           stay clickable — opening them shows whatever is already available. -->
      <router-link
        v-for="s in data.sessions" :key="s.id"
        :to="{ name: 'session', params: { id: s.id } }" class="card pad scard"
        :class="{ scheduled: s.status === 'scheduled', notready: s.status === 'awaiting_media' }"
      >
        <div class="sdate">{{ formatDate(s.date) }}</div>
        <div class="ssitting">{{ s.sitting }}. {{ $t('sessions.sitting').toLowerCase() }}</div>
        <!-- An announced but not-yet-held sitting: no recording/transcript yet, so
             show it is coming rather than "0 speeches". -->
        <template v-if="s.status === 'scheduled'">
          <div class="badge upcoming">⏳ {{ $t('sessions.upcoming') }}</div>
        </template>
        <template v-else-if="s.status === 'awaiting_media'">
          <div class="badge upcoming">⏳ {{ $t('sessions.notReady') }}</div>
        </template>
        <template v-else>
          <div class="muted small">
            {{ s.speeches }} {{ $t('sessions.speeches') }} · {{ s.agenda_items }} {{ $t('sessions.agendaItems') }}
          </div>
        </template>
      </router-link>
    </div>
    <Pagination v-if="data" :page="page" :total-pages="totalPages" @goto="gotoPage" />
  </StateBlock>
</template>

<style scoped>
.sgrid { grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); }
.scard:hover { text-decoration: none; border-color: var(--accent); }
.sdate { font-weight: 700; font-size: 1.1rem; color: var(--accent); }
.ssitting { font-size: .9rem; color: var(--ink-soft); margin-bottom: .3rem; }
/* Announced upcoming sitting: dashed, muted, so it reads as "coming", not done. */
.scard.scheduled { border-style: dashed; opacity: .85; }
/* Held but not-yet-processed sitting: muted & dashed like an upcoming card, but
   still clickable (opens to whatever speaker/agenda data is already available). */
.scard.notready { border-style: dashed; opacity: .85; }
.badge.upcoming {
  display: inline-block; font-size: .78rem; font-weight: 600;
  padding: .12rem .5rem; border-radius: 999px;
  background: var(--accent-soft); color: var(--ink-soft);
}
</style>
