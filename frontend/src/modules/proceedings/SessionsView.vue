<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api.js'
import { store, loadMeta } from '../../store.js'
import { formatDate, formatDuration } from '../../format.js'
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

async function load() {
  loading.value = true; error.value = false
  // Scoped to the global cycle chooser (store.cycle; null = all cycles).
  try {
    data.value = await api.sessions({
      period: store.cycle, limit: PAGE, offset: route.query.offset || 0,
    })
  } catch { error.value = true } finally { loading.value = false }
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
      <router-link
        v-for="s in data.sessions" :key="s.id"
        :to="{ name: 'session', params: { id: s.id } }" class="card pad scard"
      >
        <div class="sdate">{{ formatDate(s.date) }}</div>
        <div class="ssitting">{{ s.sitting }}. {{ $t('sessions.sitting').toLowerCase() }}</div>
        <div class="muted small">
          {{ s.speeches }} {{ $t('sessions.speeches') }} · {{ s.agenda_items }} {{ $t('sessions.agendaItems') }}
        </div>
        <div class="muted small" v-if="s.video_duration">⏱ {{ formatDuration(s.video_duration) }}</div>
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
</style>
