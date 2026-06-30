<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { store, loadMeta, setCycle } from './store.js'
import { setLocale } from './i18n.js'
import { useI18n } from 'vue-i18n'

const { locale } = useI18n()
const route = useRoute()
onMounted(() => { loadMeta().catch(() => {}) })

// Nav is built from the live module manifest (EXT-4): a disabled module's link
// never appears. The brand links home; "about" is a small info icon.
const showProceedings = computed(() => store.moduleEnabled('proceedings'))
const showReps = computed(() => store.moduleEnabled('representatives'))
const showBills = computed(() => store.moduleEnabled('bills'))
const showVotes = computed(() => store.moduleEnabled('votes'))

// Two-level navigation: the top bar holds one entry per section; a section that
// has sibling pages (Képviselők+Frakciók, Törvényjavaslatok+Egyéb irományok)
// reveals a contextual sub-tab bar when the user is anywhere inside it. `detail`
// routes (a profile / a single bill or document) keep their parent tab active.
const NAV_SECTIONS = {
  reps: {
    match: ['representatives', 'factions', 'profile'],
    tabs: [
      { name: 'representatives', key: 'representatives', detail: ['profile'] },
      { name: 'factions', key: 'factions' },
    ],
  },
  bills: {
    match: ['bills', 'documents', 'bill', 'document'],
    tabs: [
      { name: 'bills', key: 'bills', detail: ['bill'] },
      { name: 'documents', key: 'documents', detail: ['document'] },
    ],
  },
}
const currentSection = computed(() =>
  Object.values(NAV_SECTIONS).find((s) => s.match.includes(route.name)) || null)
function sectionActive(id) { return NAV_SECTIONS[id].match.includes(route.name) }
function tabActive(tab) {
  return route.name === tab.name || (tab.detail || []).includes(route.name)
}

// Global electoral-cycle chooser. The selected cycle scopes every period-aware
// view; "all" (the empty value) drops the period filter site-wide.
const periods = computed(() => (store.meta && store.meta.periods) || [])
const cycleValue = computed(() => (store.cycle === null ? 'all' : String(store.cycle)))
function onCycleChange(e) {
  const v = e.target.value
  setCycle(v === 'all' ? null : Number(v))
}

function toggleLang() { setLocale(locale.value === 'hu' ? 'en' : 'hu') }

// Mobile: the main nav collapses behind a hamburger toggle. Any navigation
// closes the panel so it never lingers over the new page.
const menuOpen = ref(false)
watch(() => route.fullPath, () => { menuOpen.value = false })
</script>

<template>
  <a class="skip-link" href="#main">{{ $t('app.skipToContent') }}</a>
  <header class="site-header">
    <div class="container header-bar">
      <router-link :to="{ name: 'home' }" class="brand" aria-label="Parlamonitor">
        <span class="brand-mark" aria-hidden="true">⬢</span>
        <span class="brand-text">Parlamonitor</span>
      </router-link>
      <button
        class="navtoggle" :class="{ open: menuOpen }" @click="menuOpen = !menuOpen"
        :aria-label="$t('nav.menu')" :aria-expanded="menuOpen ? 'true' : 'false'"
      >
        <span class="navtoggle-bars" aria-hidden="true"><span></span><span></span><span></span></span>
      </button>
      <nav class="mainnav" :class="{ open: menuOpen }" :aria-label="$t('nav.menu')">
        <router-link v-if="showProceedings" :to="{ name: 'search' }">{{ $t('nav.search') }}</router-link>
        <router-link v-if="showProceedings" :to="{ name: 'sessions' }">{{ $t('nav.sessions') }}</router-link>
        <router-link v-if="showReps" :to="{ name: 'representatives' }"
                     :class="{ 'router-link-active': sectionActive('reps') }">{{ $t('nav.representatives') }}</router-link>
        <router-link v-if="showBills" :to="{ name: 'bills' }"
                     :class="{ 'router-link-active': sectionActive('bills') }">{{ $t('nav.bills') }}</router-link>
        <router-link v-if="showVotes" :to="{ name: 'votes' }">{{ $t('nav.votes') }}</router-link>
      </nav>
      <div class="header-controls">
        <router-link :to="{ name: 'about' }" class="infolink" :title="$t('nav.about')" :aria-label="$t('nav.about')">
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">
            <circle cx="12" cy="12" r="9.5" fill="none" stroke="currentColor" stroke-width="1.8" />
            <circle cx="12" cy="7.6" r="1.25" fill="currentColor" />
            <rect x="11" y="10.5" width="2" height="6.5" rx="1" fill="currentColor" />
          </svg>
        </router-link>
        <select
          v-if="periods.length" class="cyclesel" :value="cycleValue" @change="onCycleChange"
          :title="$t('cycle.label')" :aria-label="$t('cycle.label')"
        >
          <option value="all">{{ $t('cycle.all') }}</option>
          <option v-for="p in periods" :key="p.number" :value="p.number">{{ p.label || p.number }}</option>
        </select>
        <button class="lang" @click="toggleLang" :aria-label="'Language: ' + locale">
          {{ locale === 'hu' ? 'EN' : 'HU' }}
        </button>
      </div>
    </div>
  </header>

  <nav v-if="currentSection" class="subheader" :aria-label="$t('nav.submenu')">
    <div class="container subnav">
      <router-link
        v-for="t in currentSection.tabs" :key="t.name" :to="{ name: t.name }"
        class="subtab" :class="{ active: tabActive(t) }"
      >{{ $t('nav.' + t.key) }}</router-link>
    </div>
  </nav>

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
/* Three zones: brand (left), nav (flexes + wraps), controls (pinned right). The
   bar itself never wraps, so the cycle/lang controls stay in the top-right
   corner even when the nav links wrap onto a second line. */
.header-bar { display: flex; align-items: center; gap: 1rem; flex-wrap: nowrap; position: relative; }
.brand { display: inline-flex; align-items: center; gap: .5rem; color: #fff; font-weight: 800; font-size: 1.15rem; flex-shrink: 0; }
.brand:hover { text-decoration: none; }
.brand-mark { font-size: 1.3rem; }
.mainnav { display: flex; align-items: center; gap: .25rem; flex-wrap: wrap; flex: 1 1 auto; }
.header-controls { display: flex; align-items: center; gap: .4rem; flex-shrink: 0; margin-left: auto; }
.mainnav a {
  color: #fbe9eb; padding: .4rem .7rem; border-radius: 8px; font-weight: 600; font-size: .95rem;
}
.mainnav a:hover { background: rgba(255,255,255,.14); text-decoration: none; }
.mainnav a.router-link-active { background: rgba(255,255,255,.2); color: #fff; }

/* Contextual second-level bar (Képviselők ↔ Frakciók, Törvényjavaslatok ↔
   Egyéb irományok). Shown only while inside the section — no hover/dropdown. */
.subheader { background: var(--surface); border-bottom: 1px solid var(--line); }
.subnav { display: flex; gap: .2rem; padding-top: .25rem; padding-bottom: 0; flex-wrap: wrap; }
.subtab {
  padding: .55rem .9rem; font-weight: 600; font-size: .92rem; color: var(--ink-soft);
  border-bottom: 2px solid transparent; border-radius: 6px 6px 0 0;
}
.subtab:hover { color: var(--accent); background: var(--accent-soft); text-decoration: none; }
.subtab.active { color: var(--accent); border-bottom-color: var(--accent); }

/* The three header controls share one height + box model so they line up. */
.infolink, .cyclesel, .lang {
  box-sizing: border-box; height: 34px; border: 1px solid rgba(255,255,255,.35);
  background: rgba(255,255,255,.15); border-radius: 8px; color: #fff;
}
.infolink {
  display: inline-flex; align-items: center; justify-content: center; padding: 0 .6rem;
}
.infolink:hover { background: rgba(255,255,255,.28); text-decoration: none; }
.cyclesel {
  padding: 0 .6rem; cursor: pointer; font-weight: 700; font-size: .8rem; vertical-align: middle;
}
.cyclesel option { color: var(--ink); }
.lang {
  display: inline-flex; align-items: center; padding: 0 .7rem; cursor: pointer;
  font-weight: 700; font-size: .8rem;
}
.site-footer { border-top: 1px solid var(--line); padding: 1.5rem 0; margin-top: 2rem; background: var(--surface); }

/* Hamburger toggle: hidden on desktop, revealed at the mobile breakpoint where
   the five nav links no longer fit on one row. */
.navtoggle {
  display: none; box-sizing: border-box; height: 34px; width: 38px;
  align-items: center; justify-content: center; cursor: pointer;
  border: 1px solid rgba(255,255,255,.35); background: rgba(255,255,255,.15);
  border-radius: 8px; color: #fff; padding: 0;
}
.navtoggle:hover { background: rgba(255,255,255,.28); }
.navtoggle-bars, .navtoggle-bars span { display: block; }
.navtoggle-bars { width: 18px; height: 14px; position: relative; }
.navtoggle-bars span {
  position: absolute; left: 0; width: 100%; height: 2px; background: #fff; border-radius: 2px;
  transition: transform .2s ease, opacity .2s ease, top .2s ease;
}
.navtoggle-bars span:nth-child(1) { top: 0; }
.navtoggle-bars span:nth-child(2) { top: 6px; }
.navtoggle-bars span:nth-child(3) { top: 12px; }
.navtoggle.open .navtoggle-bars span:nth-child(1) { top: 6px; transform: rotate(45deg); }
.navtoggle.open .navtoggle-bars span:nth-child(2) { opacity: 0; }
.navtoggle.open .navtoggle-bars span:nth-child(3) { top: 6px; transform: rotate(-45deg); }
@media (prefers-reduced-motion: reduce) { .navtoggle-bars span { transition: none; } }

@media (max-width: 760px) {
  .brand-text { display: none; }
  .navtoggle { display: inline-flex; order: 3; }
  .header-controls { order: 2; }
  /* The nav drops out of the bar into a full-width panel beneath the header,
     toggled by the hamburger. The bar itself stays a single tidy row. */
  .mainnav {
    display: none; position: absolute; top: 100%;
    left: calc(-1 * var(--gutter)); right: calc(-1 * var(--gutter));
    flex-direction: column; align-items: stretch; gap: .15rem;
    background: var(--accent); padding: .5rem var(--gutter) .75rem;
    box-shadow: 0 8px 16px rgba(0,0,0,.18); border-top: 1px solid rgba(255,255,255,.18);
  }
  .mainnav.open { display: flex; }
  .mainnav a { padding: .65rem .75rem; font-size: 1rem; }
}
</style>
