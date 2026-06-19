<script setup>
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { store } from '../store.js'

const router = useRouter()
const q = ref('')
const counts = computed(() => (store.meta && store.meta.counts) || {})
function fmt(n) { return (n ?? 0).toLocaleString('hu-HU') }
function go() {
  if (q.value.trim()) router.push({ name: 'search', query: { q: q.value.trim() } })
}
const showProceedings = computed(() => store.moduleEnabled('proceedings'))
const showReps = computed(() => store.moduleEnabled('representatives'))
</script>

<template>
  <section class="hero card pad">
    <h1>{{ $t('app.title') }}</h1>
    <p class="soft" style="font-size:1.1rem;max-width:60ch;">{{ $t('app.tagline') }}</p>

    <form v-if="showProceedings" class="searchbar" role="search" @submit.prevent="go">
      <input
        v-model="q" type="search" :placeholder="$t('home.searchPlaceholder')"
        :aria-label="$t('home.searchPlaceholder')" autofocus
      />
      <button class="btn" type="submit">{{ $t('home.searchButton') }}</button>
    </form>

    <dl v-if="store.meta" class="stats" aria-label="corpus statistics">
      <div><dt>{{ fmt(counts.sentences) }}</dt><dd>{{ $t('home.stats.sentences') }}</dd></div>
      <div><dt>{{ fmt(counts.speeches) }}</dt><dd>{{ $t('home.stats.speeches') }}</dd></div>
      <div><dt>{{ fmt(counts.sessions) }}</dt><dd>{{ $t('home.stats.sessions') }}</dd></div>
      <div><dt>{{ fmt(counts.representatives) }}</dt><dd>{{ $t('home.stats.representatives') }}</dd></div>
    </dl>
  </section>

  <section class="grid cards3">
    <router-link v-if="showProceedings" :to="{ name: 'search' }" class="card pad feature">
      <h2>🔎 {{ $t('home.exploreSearch') }}</h2>
      <p class="soft">{{ $t('home.exploreSearchDesc') }}</p>
    </router-link>
    <router-link v-if="showReps" :to="{ name: 'representatives' }" class="card pad feature">
      <h2>👤 {{ $t('home.exploreReps') }}</h2>
      <p class="soft">{{ $t('home.exploreRepsDesc') }}</p>
    </router-link>
    <router-link v-if="showProceedings" :to="{ name: 'sessions' }" class="card pad feature">
      <h2>📅 {{ $t('home.exploreSessions') }}</h2>
      <p class="soft">{{ $t('home.exploreSessionsDesc') }}</p>
    </router-link>
  </section>
</template>

<style scoped>
.hero { margin-bottom: 1.5rem; }
.searchbar { display: flex; gap: .5rem; margin: 1.2rem 0; max-width: 640px; }
.searchbar input { flex: 1; font-size: 1.05rem; padding: .7rem .8rem; }
.stats { display: flex; flex-wrap: wrap; gap: 2rem; margin: 1rem 0 0; }
.stats div { margin: 0; }
.stats dt { font-size: 1.6rem; font-weight: 800; color: var(--accent); }
.stats dd { margin: 0; color: var(--ink-faint); font-size: .9rem; }
.cards3 { grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }
.feature h2 { margin-top: 0; }
.feature:hover { text-decoration: none; border-color: var(--accent); }
</style>
