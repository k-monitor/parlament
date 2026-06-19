<script setup>
import { computed, onMounted } from 'vue'
import { store, loadMeta } from './store.js'
import { setLocale } from './i18n.js'
import { useI18n } from 'vue-i18n'

const { locale } = useI18n()
onMounted(() => { loadMeta().catch(() => {}) })

// Nav is built from the live module manifest (EXT-4): a disabled module's link
// never appears. Core links (home/about) are always shown.
const showProceedings = computed(() => store.moduleEnabled('proceedings'))
const showReps = computed(() => store.moduleEnabled('representatives'))
const showBills = computed(() => store.moduleEnabled('bills'))

function toggleLang() { setLocale(locale.value === 'hu' ? 'en' : 'hu') }
</script>

<template>
  <a class="skip-link" href="#main">{{ $t('app.skipToContent') }}</a>
  <header class="site-header">
    <div class="container row" style="justify-content:space-between;">
      <router-link :to="{ name: 'home' }" class="brand" aria-label="Parlamonitor">
        <span class="brand-mark" aria-hidden="true">⬢</span>
        <span class="brand-text">Parlamonitor</span>
      </router-link>
      <nav class="mainnav" :aria-label="$t('nav.home')">
        <router-link :to="{ name: 'home' }">{{ $t('nav.home') }}</router-link>
        <router-link v-if="showProceedings" :to="{ name: 'search' }">{{ $t('nav.search') }}</router-link>
        <router-link v-if="showProceedings" :to="{ name: 'sessions' }">{{ $t('nav.sessions') }}</router-link>
        <router-link v-if="showReps" :to="{ name: 'representatives' }">{{ $t('nav.representatives') }}</router-link>
        <router-link v-if="showReps" :to="{ name: 'factions' }">{{ $t('nav.factions') }}</router-link>
        <router-link v-if="showBills" :to="{ name: 'bills' }">{{ $t('nav.bills') }}</router-link>
        <router-link :to="{ name: 'about' }">{{ $t('nav.about') }}</router-link>
        <button class="lang" @click="toggleLang" :aria-label="'Language: ' + locale">
          {{ locale === 'hu' ? 'EN' : 'HU' }}
        </button>
      </nav>
    </div>
  </header>

  <main id="main" class="container page">
    <router-view v-slot="{ Component }">
      <component :is="Component" />
    </router-view>
  </main>

  <footer class="site-footer">
    <div class="container">
      <p class="small soft">
        {{ $t('app.sourceNote') }}
        <template v-if="store.meta">
          ·
          <a :href="store.meta.source_attribution.url" target="_blank" rel="noopener">parlament.hu</a>
          ·
          <a :href="store.meta.source_attribution.license_url" target="_blank" rel="noopener">{{ $t('viewer.license') }}</a>
        </template>
        · <a href="/api/docs" target="_blank" rel="noopener">API</a>
      </p>
    </div>
  </footer>
</template>

<style scoped>
.site-header {
  background: var(--accent); color: var(--accent-ink);
  position: sticky; top: 0; z-index: 20; box-shadow: var(--shadow);
}
.site-header .container { padding-top: .6rem; padding-bottom: .6rem; }
.brand { display: inline-flex; align-items: center; gap: .5rem; color: #fff; font-weight: 800; font-size: 1.15rem; }
.brand:hover { text-decoration: none; }
.brand-mark { font-size: 1.3rem; }
.mainnav { display: flex; align-items: center; gap: .25rem; flex-wrap: wrap; }
.mainnav a {
  color: #fbe9eb; padding: .4rem .7rem; border-radius: 8px; font-weight: 600; font-size: .95rem;
}
.mainnav a:hover { background: rgba(255,255,255,.14); text-decoration: none; }
.mainnav a.router-link-active { background: rgba(255,255,255,.2); color: #fff; }
.lang {
  margin-left: .4rem; background: rgba(255,255,255,.15); color: #fff; border: 1px solid rgba(255,255,255,.35);
  border-radius: 8px; padding: .3rem .6rem; cursor: pointer; font-weight: 700; font-size: .8rem;
}
.site-footer { border-top: 1px solid var(--line); padding: 1.5rem 0; margin-top: 2rem; background: var(--surface); }
@media (max-width: 700px) {
  .brand-text { display: none; }
}
</style>
