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
    <router-link v-if="showProceedings" :to="{ name: 'search' }" class="card feature">
      <span class="feature__icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
        </svg>
      </span>
      <h2>{{ $t('home.exploreSearch') }}</h2>
      <p class="soft">{{ $t('home.exploreSearchDesc') }}</p>
      <span class="feature__cta" aria-hidden="true">→</span>
    </router-link>

    <router-link v-if="showReps" :to="{ name: 'representatives' }" class="card feature">
      <span class="feature__icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M16 20v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1" /><circle cx="9.5" cy="7" r="3.5" />
          <path d="M21 20v-1a4 4 0 0 0-3-3.87M16.5 3.6a3.5 3.5 0 0 1 0 6.8" />
        </svg>
      </span>
      <h2>{{ $t('home.exploreReps') }}</h2>
      <p class="soft">{{ $t('home.exploreRepsDesc') }}</p>
      <span class="feature__cta" aria-hidden="true">→</span>
    </router-link>

    <router-link v-if="showProceedings" :to="{ name: 'sessions' }" class="card feature">
      <span class="feature__icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="4.5" width="18" height="16" rx="2" /><path d="M3 9h18M8 2.5v4M16 2.5v4" />
          <path d="M7.5 13h4M7.5 16.5h9" />
        </svg>
      </span>
      <h2>{{ $t('home.exploreSessions') }}</h2>
      <p class="soft">{{ $t('home.exploreSessionsDesc') }}</p>
      <span class="feature__cta" aria-hidden="true">→</span>
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

.feature {
  position: relative;
  display: flex;
  flex-direction: column;
  padding: 1.4rem 1.4rem 1.6rem;
  color: var(--ink);
  transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease;
}
.feature:hover {
  text-decoration: none;
  border-color: var(--accent);
  transform: translateY(-3px);
  box-shadow: 0 2px 6px rgba(0,0,0,.07), 0 12px 28px rgba(0,0,0,.09);
}
.feature__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 12px;
  background: var(--accent-soft);
  color: var(--accent);
  margin-bottom: 1rem;
  transition: background .15s ease, color .15s ease;
}
.feature__icon svg { width: 26px; height: 26px; }
.feature:hover .feature__icon { background: var(--accent); color: var(--accent-ink); }
.feature h2 { margin: 0 0 .4rem; font-size: 1.15rem; }
.feature p { margin: 0; flex: 1; }
.feature__cta {
  color: var(--accent);
  font-size: 1.25rem;
  font-weight: 700;
  line-height: 1;
  margin-top: 1rem;
  transition: transform .15s ease;
}
.feature:hover .feature__cta { transform: translateX(4px); }
@media (prefers-reduced-motion: reduce) {
  .feature, .feature__icon, .feature__cta { transition: none; }
  .feature:hover { transform: none; }
  .feature:hover .feature__cta { transform: none; }
}
</style>
