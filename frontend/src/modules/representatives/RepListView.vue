<script setup>
// Browsable, filterable representative list (REP-1). Filter/sort state is in the
// URL so a filtered list is shareable.
import { ref, reactive, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api.js'
import { store, loadMeta } from '../../store.js'
import { formatSpeakingTime } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import SpeakerLink from '../../components/SpeakerLink.vue'

const route = useRoute()
const router = useRouter()

const data = ref(null)
const loading = ref(false)
const error = ref(false)
const factions = ref([])

const f = reactive({
  q: route.query.q || '',
  faction_id: route.query.faction_id || '',
  sort: route.query.sort || 'speaking_time',
})

function apply() {
  const query = {}
  if (f.q) query.q = f.q
  if (f.faction_id) query.faction_id = f.faction_id
  if (f.sort) query.sort = f.sort
  router.push({ name: 'representatives', query })
}

async function load() {
  loading.value = true; error.value = false
  try {
    // `period` comes from the global cycle chooser (store.cycle; null = all) —
    // it scopes the list to MPs serving in that cycle.
    data.value = await api.representatives({
      q: route.query.q, faction_id: route.query.faction_id, period: store.cycle,
      sort: route.query.sort || 'speaking_time', limit: 300,
    })
  } catch { error.value = true } finally { loading.value = false }
}

onMounted(async () => {
  await loadMeta().catch(() => {})
  try { factions.value = (await api.factions()).factions } catch {}
  load()
})
watch(() => route.query, (q) => {
  f.q = q.q || ''; f.faction_id = q.faction_id || ''; f.sort = q.sort || 'speaking_time'
  load()
})
// Re-fetch when the global cycle changes.
watch(() => store.cycle, load)
let t = null
function onSearchInput() { clearTimeout(t); t = setTimeout(apply, 300) }
</script>

<template>
  <h1>{{ $t('reps.title') }}</h1>

  <form class="card pad toolbar" role="search" @submit.prevent="apply">
    <div>
      <label for="r-q">{{ $t('reps.searchPlaceholder') }}</label>
      <input id="r-q" type="search" v-model="f.q" :placeholder="$t('reps.searchPlaceholder')" @input="onSearchInput" />
    </div>
    <div>
      <label for="r-faction">{{ $t('reps.faction') }}</label>
      <select id="r-faction" v-model="f.faction_id" @change="apply">
        <option value="">{{ $t('search.all') }}</option>
        <option v-for="x in factions" :key="x.id" :value="x.id">{{ x.label }}</option>
      </select>
    </div>
    <div>
      <label for="r-sort">↕</label>
      <select id="r-sort" v-model="f.sort" @change="apply">
        <option value="name">{{ $t('reps.sortName') }}</option>
        <option value="speeches">{{ $t('reps.sortSpeeches') }}</option>
        <option value="speaking_time">{{ $t('reps.sortSpeakingTime') }}</option>
      </select>
    </div>
  </form>

  <StateBlock
    :loading="loading" :error="error"
    :empty="!!data && data.representatives.length === 0" :empty-text="$t('reps.noResults')"
    @retry="load"
  >
    <div v-if="data">
      <p class="muted small">{{ data.total }} {{ $t('home.stats.representatives') }}</p>
      <ul class="replist grid">
        <li v-for="r in data.representatives" :key="r.person_id" class="card pad repcard">
          <SpeakerLink :speaker="{ person_id: r.person_id, label: r.label, photo_uri: r.photo_uri }" />
          <div class="repmeta">
            <FactionBadge :faction="r.faction" />
            <span v-if="r.constituency" class="muted small">📍 {{ r.constituency }}</span>
          </div>
          <div class="repstats small muted">
            <span>{{ r.speech_count }} {{ $t('reps.speeches') }}</span>
            <span>·</span>
            <span>{{ formatSpeakingTime(r.speaking_seconds) }}</span>
          </div>
        </li>
      </ul>
    </div>
  </StateBlock>
</template>

<style scoped>
.toolbar { display: grid; grid-template-columns: 2fr 1fr 1fr; gap: .8rem; align-items: end; margin-bottom: 1rem; }
.replist { list-style: none; padding: 0; margin: 0; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); }
.repcard { display: flex; flex-direction: column; gap: .5rem; }
.repmeta { display: flex; gap: .6rem; flex-wrap: wrap; align-items: center; }
.repstats { display: flex; gap: .4rem; }
@media (max-width: 640px) { .toolbar { grid-template-columns: 1fr; } }
</style>
