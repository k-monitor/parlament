<script setup>
// The home page, top to bottom:
//
//  1. the search, compact, with the corpus in one line under it — and beside it
//     one example search at a time with its popularity histogram (§SEA-8);
//  2. two panels taking turns: a number from Parliament in a sentence, and one of
//     the site's interactive figures — both about the cycles in scope (§4A);
//  3. what the House has just done (last week's sittings) and what it is about
//     to do (the order paper, NR-5);
//  4. the donation card.
//
// The rotating panels and the week digest each fetch their own data, gated on
// the modules behind them, so this view only owns the search and the corpus line.
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { store, loadMeta } from '../store.js'
import DonateCard from '../components/DonateCard.vue'
import UpcomingSitting from '../components/UpcomingSitting.vue'
import HomeTrendCard from '../components/HomeTrendCard.vue'
import HomeFactsCard from '../components/HomeFactsCard.vue'
import HomeFigureCard from '../components/HomeFigureCard.vue'
import LastWeekPanel from '../components/LastWeekPanel.vue'

const router = useRouter()
const q = ref('')
const counts = computed(() => (store.meta && store.meta.counts) || {})
function fmt(n) { return (n ?? 0).toLocaleString('hu-HU') }

// The corpus line: "1990 óta: 9 054 948 mondat · 583 037 felszólalás · …".
const STATS = ['sentences', 'speeches', 'sessions', 'representatives']
// Earliest year the corpus reaches back to, taken from the electoral periods so
// the corpus line reflects whatever data is actually loaded ("1990" once the full
// archive is in, "2014" on a partial dev DB) rather than a hard-coded date. It
// leads the line, since every count on it runs from then.
const startYear = computed(() => {
  const years = ((store.meta && store.meta.periods) || [])
    .map((p) => (p.date_start ? Number(String(p.date_start).slice(0, 4)) : null))
    .filter((y) => Number.isFinite(y))
  return years.length ? Math.min(...years) : null
})
function go() {
  if (q.value.trim()) router.push({ name: 'search', query: { q: q.value.trim() } })
}
const showProceedings = computed(() => store.moduleEnabled('proceedings'))
// The coming sitting's order paper (NR-5). On only where the scrape has
// actually produced one, so a deployment that does not run the stage shows
// nothing rather than an empty promise.
const showUpcoming = computed(() =>
  showProceedings.value && store.featureEnabled('upcoming_agenda'))

onMounted(() => { loadMeta().catch(() => {}) })
</script>

<template>
  <div class="home-top" :class="{ solo: !showProceedings }">
    <section class="card pad home-search">
      <h1 class="home-title">{{ $t('app.tagline') }}</h1>
      <form v-if="showProceedings" class="searchbar" role="search" @submit.prevent="go">
        <label for="home-q" class="searchbar__label">{{ $t('home.searchLabel') }}</label>
        <input
          id="home-q" v-model="q" type="search"
          :placeholder="$t('home.searchPlaceholder')" autofocus
        />
        <button class="btn" type="submit">{{ $t('home.searchButton') }}</button>
      </form>
      <p v-if="store.meta" class="home-counts" :aria-label="$t('home.countsLabel')">
        <span v-if="startYear" class="home-counts__since">{{ $t('home.since', { year: startYear }) }}</span>
        <span v-for="key in STATS" :key="key" class="home-counts__stat">
          <strong>{{ fmt(counts[key]) }}</strong> {{ $t(`home.stats.${key}`) }}
        </span>
      </p>
    </section>
    <HomeTrendCard v-if="showProceedings" />
  </div>

  <div class="home-pair">
    <HomeFactsCard />
    <HomeFigureCard />
  </div>

  <!-- What the House has just done, then what it is about to do. -->
  <LastWeekPanel v-if="showProceedings" class="home-block" />
  <UpcomingSitting v-if="showUpcoming" class="home-block" />

  <DonateCard class="home-block" />
</template>

<style scoped>
.home-top {
  display: grid; gap: 1rem; align-items: stretch;
  grid-template-columns: minmax(0, 1fr) minmax(260px, 330px);
}
.home-top.solo { grid-template-columns: minmax(0, 1fr); }
.home-search { display: flex; flex-direction: column; justify-content: center; }
.home-title { margin: 0 0 .7rem; font-size: 1.35rem; line-height: 1.25; }

.searchbar { display: flex; align-items: center; gap: .6rem; }
.searchbar__label { flex: none; margin: 0; font-size: .85rem; color: var(--ink); }
.searchbar input { flex: 1; min-width: 0; font-size: 1rem; padding: .55rem .75rem; }
.searchbar .btn { flex: none; }

.home-counts {
  display: flex; flex-wrap: wrap; gap: .1rem 0; margin: .7rem 0 0;
  font-size: .82rem; color: var(--ink-faint);
}
.home-counts__since { margin-right: .4rem; }
.home-counts__stat + .home-counts__stat::before { content: '·'; margin: 0 .45rem; }
.home-counts strong { font-weight: 700; color: var(--ink-soft); }

.home-pair {
  display: grid; gap: 1rem; margin-top: 1rem; align-items: stretch;
  grid-template-columns: minmax(0, 5fr) minmax(0, 6fr);
}
.home-block { margin-top: 1.5rem; }

@media (max-width: 820px) {
  .home-top, .home-pair { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 560px) {
  .searchbar { flex-wrap: wrap; }
  .searchbar__label { flex-basis: 100%; }
}
</style>
