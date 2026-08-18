<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { store, loadMeta } from './store.js'
import { setLocale } from './i18n.js'
import { formatLongDate } from './format.js'
import { COHESION_ENABLED } from './features.js'
import { useI18n } from 'vue-i18n'
import CycleSelect from './components/CycleSelect.vue'

const { locale } = useI18n()
const route = useRoute()
onMounted(() => { loadMeta().catch(() => {}) })

// Embeddable figures (see EmbedView) render inside a third-party <iframe> with no
// site chrome: the whole header / sub-nav / footer are suppressed so
// only the chart shows. Detected from the matched route's meta.embed flag.
const isEmbed = computed(() => route.meta.embed === true)

// Nav is built from the live module manifest (EXT-4): a disabled module's link
// never appears. The brand links home; "about" is a small info icon.
const showProceedings = computed(() => store.moduleEnabled('proceedings'))
const showReps = computed(() => store.moduleEnabled('representatives'))
const showBills = computed(() => store.moduleEnabled('bills'))
const showVotes = computed(() => store.moduleEnabled('votes'))
// Települések has no top-bar entry of its own: it is a tab of the Felszólalók
// section, so its module switch is read where that bar is built (`sectionTabs`).

// Two-level navigation: the top bar holds one entry per section; a section that
// has sibling pages (Képviselők+Frakciók, Törvényjavaslatok+Egyéb irományok)
// reveals a contextual sub-tab bar when the user is anywhere inside it. `detail`
// routes (a profile / a single bill or document) keep their parent tab active.
const NAV_SECTIONS = {
  reps: {
    match: ['representatives', 'lookup', 'advocates', 'speakers', 'allSpeakers',
            'officials', 'portfolios', 'portfolio', 'factions', 'profile',
            'compare', 'settlements', 'settlement', 'settlementReps'],
    // `profile` is a detail page of every person tab — which one is decided at
    // runtime by the kind of person the profile is (see `tabActive`).
    tabs: [
      // Felszólalók (REP-1/REP-9/REP-12): one page for everyone who takes the
      // floor. `advocates` / `speakers` / `allSpeakers` are its category chips,
      // not tabs of their own — they keep their URLs, so the tab has to claim
      // them the way it claims a detail page. `lookup` ("Ki a képviselőm?",
      // REP-10) is claimed the same way: it is that page's second search mode —
      // the same question keyed by place rather than by name — reached from the
      // switch in its search card rather than from a tab of its own, which put
      // the two ways of finding an MP in two different places.
      // `group` splits the bar with rules (see `visibleTabs`): the first two tabs
      // list *people*, the next two the *bodies* they act in, and the last the
      // *places* they answer to — equal-looking tabs in a row hid that.
      // `compare` (REP-15) is claimed the same way as `lookup` and the chips: it is
      // reached from a profile or the list's compare tray, not from a tab of its
      // own — a tool for the page you are on rather than a fifth place to browse.
      { name: 'representatives', key: 'representatives', group: 'people',
        detail: ['advocates', 'speakers', 'allSpeakers', 'lookup', 'profile',
                 'compare'] },
      { name: 'officials', key: 'officials', group: 'people', detail: ['profile'] },
      // Tárcák (§6C): the institution behind those offices. Its own module, so
      // the tab goes when the module is switched off (EXT-6) while the rest of
      // the section stays.
      { name: 'portfolios', key: 'portfolios', group: 'bodies', detail: ['portfolio'] },
      { name: 'factions', key: 'factions', group: 'bodies' },
      // Települések (§6D): its own module too, and it sits here rather than in the
      // top bar because the question it answers is a question about members — whose
      // places get named. `settlementReps` (the TEL-9 table, "Saját körzet") is
      // deliberately **not a tab of its own for now**: it is claimed as a detail
      // route, so the page stays reachable and citable while nothing advertises it.
      { name: 'settlements', key: 'settlements', group: 'places',
        detail: ['settlement', 'settlementReps'] },
    ],
  },
  bills: {
    match: ['bills', 'documents', 'questions', 'bill', 'document'],
    tabs: [
      { name: 'bills', key: 'bills', detail: ['bill'] },
      { name: 'questions', key: 'questions' },
      { name: 'documents', key: 'documents', detail: ['document'] },
    ],
  },
  votes: {
    match: ['votes', 'cohesion', 'vote'],
    tabs: [
      { name: 'votes', key: 'votes', detail: ['vote'] },
      { name: 'cohesion', key: 'cohesion' },
    ],
  },
}
const currentSection = computed(() =>
  Object.values(NAV_SECTIONS).find((s) => s.match.includes(route.name)) || null)
function sectionActive(id) { return NAV_SECTIONS[id].match.includes(route.name) }

// The Frakcióelemzés (cohesion) sub-tab compares how factions vote *within* the
// cycles in scope; across the whole corpus that comparison is meaningless, so
// it's hidden while the global scope is "all cycles" (router.js bounces the
// route to match). It is also hidden outright while COHESION_ENABLED is off
// (features.js). Either way a section can be left with a single tab, in which
// case the sub-tab bar is redundant with the top nav and hidden entirely (see the
// `v-if` below). (The constituency lookup switches off the same way — its
// external source can be unconfigured — but it is a mode of the Felszólalók page
// now, so its own search card hides the switch, not this bar.)
const sectionTabs = computed(() =>
  (currentSection.value ? currentSection.value.tabs : []).filter((t) => {
    if (t.name === 'cohesion') return COHESION_ENABLED && store.cycles.length > 0
    if (t.name === 'portfolios') return store.moduleEnabled('portfolios')
    if (t.name === 'settlements') return store.moduleEnabled('settlements')
    // The rest of this bar belongs to the representatives module, and two of its tabs
    // (Tárcák, Települések) are modules that can outlive it — so a deployment with
    // representatives switched off must not be left with tabs that 404 (EXT-6).
    if (currentSection.value === NAV_SECTIONS.reps) {
      return store.moduleEnabled('representatives')
    }
    return true
  }))
// The rule between two `group`s is carried by the first tab of the later group,
// and only once both sides survived the filter above — a group left empty (the
// Tárcák module off) must not leave a rule hanging at the edge of the bar.
const visibleTabs = computed(() => sectionTabs.value.map((t, i, all) => ({
  ...t, sep: i > 0 && t.group !== all[i - 1].group,
})))
// The person lists that are chips of the Felszólalók page rather than tabs of
// their own: a profile opened from one of them highlights that page's tab.
const CHIP_OF_REPS = { advocates: 'representatives', speakers: 'representatives' }

function tabActive(tab) {
  // A person profile is a detail page of two different tabs: an MP's, a
  // nationality advocate's (REP-9) and a non-MP minister's (REP-12) all belong
  // under Felszólalók, while an office holder who never spoke here belongs under
  // Tisztségviselők (REP-11). They share one route, so the open profile itself
  // reports which list it came from (store.profileTab); until it has loaded,
  // treat it as an MP.
  if (route.name === 'profile') {
    const from = store.profileTab || 'representatives'
    return tab.name === (CHIP_OF_REPS[from] || from)
  }
  return route.name === tab.name || (tab.detail || []).includes(route.name)
}

function toggleLang() { setLocale(locale.value === 'hu' ? 'en' : 'hu') }

// Feedback is collected through a hosted Partimap survey rather than e-mail, so
// responses arrive structured and don't depend on the visitor having a mail client.
const FEEDBACK_URL = 'https://www.partimap.eu/hu/p/Parlamonitor-visszajelzes/'

// "Utolsó adatfrissítés" — the loader stamps build_meta.data_updated_at on every
// (re)build/incremental update; older DBs lack it, so the footer line is hidden
// until a fresh load populates it. Re-formats when the language toggles.
const dataUpdatedAt = computed(() => {
  const iso = store.meta && store.meta.build && store.meta.build.data_updated_at
  return iso ? formatLongDate(iso, locale.value) : ''
})

// Mobile: the main nav collapses behind a hamburger toggle. Any navigation
// closes the panel so it never lingers over the new page.
const menuOpen = ref(false)
watch(() => route.fullPath, () => { menuOpen.value = false })
</script>

<template>
  <a v-if="!isEmbed" class="skip-link" href="#main">{{ $t('app.skipToContent') }}</a>
  <!-- The main nav (with the cycle chooser in it) sticks to the top, so
       scrolling never leaves the page without its active-cycle context. -->
  <div v-if="!isEmbed" class="site-top">
  <header class="site-header">
    <div class="container header-bar">
      <router-link :to="{ name: 'home' }" class="brand" aria-label="Parlamonitor">
        <img class="brand-mark" src="/parlamonitor.png" alt="" aria-hidden="true" />
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
        <router-link v-if="showVotes" :to="{ name: 'votes' }"
                     :class="{ 'router-link-active': sectionActive('votes') }">{{ $t('nav.votes') }}</router-link>
      </nav>
      <div class="header-controls">
        <a class="infolink" :href="FEEDBACK_URL" target="_blank" rel="noopener"
           :title="$t('footer.feedback')" :aria-label="$t('footer.feedback')">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"
               stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
            <path d="M21 11.5a8.4 8.4 0 0 1-8.5 8.5 8.9 8.9 0 0 1-3.9-.9L3 21l1.4-4.6A8.4 8.4 0 0 1 3.5 11.5 8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5z" />
          </svg>
        </a>
        <router-link :to="{ name: 'about' }" class="infolink" :title="$t('nav.about')" :aria-label="$t('nav.about')">
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">
            <circle cx="12" cy="12" r="9.5" fill="none" stroke="currentColor" stroke-width="1.8" />
            <circle cx="12" cy="7.6" r="1.25" fill="currentColor" />
            <rect x="11" y="10.5" width="2" height="6.5" rx="1" fill="currentColor" />
          </svg>
        </router-link>
        <button class="lang" @click="toggleLang" :aria-label="'Language: ' + locale">
          {{ locale === 'hu' ? 'EN' : 'HU' }}
        </button>
        <CycleSelect />
      </div>
    </div>
  </header>

  </div>

  <nav v-if="!isEmbed && visibleTabs.length > 1" class="subheader" :aria-label="$t('nav.submenu')">
    <div class="container subnav">
      <template v-for="t in visibleTabs" :key="t.name">
        <span v-if="t.sep" class="subnav-sep" aria-hidden="true"></span>
        <router-link
          :to="{ name: t.name }" class="subtab" :class="{ active: tabActive(t) }"
        >{{ $t('nav.' + t.key) }}</router-link>
      </template>
    </div>
  </nav>

  <main id="main" :class="isEmbed ? 'embed-main' : 'container page'">
    <router-view v-slot="{ Component }">
      <component :is="Component" />
    </router-view>
  </main>

  <footer v-if="!isEmbed" class="site-footer">
    <div class="container footer-main">
      <div class="footer-brand">
        <a
          class="footer-logo" href="https://k-monitor.hu"
          target="_blank" rel="noopener noreferrer" :aria-label="$t('footer.kmonitorHome')"
        >
          <img src="/kmonitor-logo.jpg" alt="K-Monitor" />
        </a>
        <div class="footer-social">
          <a
            class="social-link" href="https://www.instagram.com/kmonitorhu/"
            target="_blank" rel="noopener noreferrer" :aria-label="$t('footer.instagram')"
          >
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
                 stroke-width="1.8" aria-hidden="true" focusable="false">
              <rect x="3" y="3" width="18" height="18" rx="5" />
              <circle cx="12" cy="12" r="4" />
              <circle cx="17.4" cy="6.6" r="1.1" fill="currentColor" stroke="none" />
            </svg>
          </a>
          <a
            class="social-link" href="https://www.facebook.com/Kmonitor/"
            target="_blank" rel="noopener noreferrer" :aria-label="$t('footer.facebook')"
          >
            <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"
                 aria-hidden="true" focusable="false">
              <path d="M14 8.5V6.9c0-.7.2-1.1 1.2-1.1h1.5V3.1C16.3 3 15.4 3 14.5 3 12 3 10.4 4.5 10.4 7v1.5H8v2.9h2.4V21H14v-9.6h2.4l.4-2.9H14z" />
            </svg>
          </a>
        </div>
      </div>

      <nav class="footer-col" :aria-label="$t('footer.aboutHeading')">
        <h2 class="footer-h">{{ $t('footer.aboutHeading') }}</h2>
        <router-link :to="{ name: 'about' }">{{ $t('footer.about') }}</router-link>
        <a href="/api/docs" target="_blank" rel="noopener">API</a>
      </nav>

      <div class="footer-col">
        <h2 class="footer-h">{{ $t('footer.contact') }}</h2>
        <a href="mailto:info@k-monitor.hu">info@k-monitor.hu</a>
        <a href="tel:+3617895005">+36 1 789 5005</a>
      </div>
    </div>

    <div class="container footer-bar">
      <p class="small soft" style="margin:0;">
        {{ $t('app.sourceNote') }}
        <template v-if="store.meta">
          ·
          <a :href="store.meta.source_attribution.license_url" target="_blank" rel="noopener">{{ $t('app.dataTerms') }}</a>
        </template>
        <template v-if="dataUpdatedAt">
          · {{ $t('footer.lastUpdate') }}: {{ dataUpdatedAt }}
        </template>
      </p>
      <a
        class="donate-link"
        href="https://tamogatas.k-monitor.hu/?utm_source=parlamonitor"
        target="_blank" rel="noopener noreferrer"
      >
        <span aria-hidden="true">❤️</span> {{ $t('donate.footer') }}
      </a>
    </div>
  </footer>
</template>

<style scoped>
/* Sticky wrapper for the main nav, pinned to the top on scroll. The shadow sits
   at its bottom edge, separating the nav from the scrolling content beneath. */
.site-top { position: sticky; top: 0; z-index: 20; box-shadow: var(--shadow); }
.site-header {
  background: var(--accent); color: var(--accent-ink);
}
.site-header .container { padding-top: .6rem; padding-bottom: .6rem; }
/* Three zones: brand (left), nav (flexes + wraps), controls (pinned right). The
   bar itself never wraps, so the cycle/lang controls stay in the top-right
   corner even when the nav links wrap onto a second line. */
.header-bar { display: flex; align-items: center; gap: 1rem; flex-wrap: nowrap; position: relative; }
.brand { display: inline-flex; align-items: center; gap: .5rem; color: #fff; font-weight: 800; font-size: 1.15rem; flex-shrink: 0; }
.brand:hover { text-decoration: none; }
.brand-mark { width: 2rem; height: 2rem; object-fit: contain; display: block; border-radius: .4rem; background: #fff; padding: .2rem; box-sizing: border-box; }
.mainnav { display: flex; align-items: center; gap: .25rem; flex-wrap: wrap; flex: 1 1 auto; }
.header-controls { display: flex; align-items: center; gap: .4rem; flex-shrink: 0; margin-left: auto; }
.mainnav a {
  color: #f6dcd7; padding: .4rem .7rem; border-radius: 8px; font-weight: 600; font-size: .95rem;
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
/* Groups the tabs without adding a second row: the rule sits inside the bar's own
   line, short enough to stay under the underline of an active tab beside it. */
.subnav-sep {
  align-self: center; flex: 0 0 auto; width: 1px; height: 1.15rem;
  margin: 0 .45rem; background: var(--line);
}

/* The header controls share one height + box model so they line up (the cycle
   chooser next to them matches it from its own scoped styles). */
.infolink, .lang {
  box-sizing: border-box; height: 34px; border: 1px solid rgba(255,255,255,.35);
  background: rgba(255,255,255,.15); border-radius: 8px; color: #fff;
}
.infolink {
  display: inline-flex; align-items: center; justify-content: center; padding: 0 .6rem;
}
.infolink:hover { background: rgba(255,255,255,.28); text-decoration: none; }
.lang {
  display: inline-flex; align-items: center; padding: 0 .7rem; cursor: pointer;
  font-weight: 700; font-size: .8rem;
}
/* Embed layout: no site chrome, no page gutters — the EmbedView fills the whole
   iframe viewport (it supplies its own compact padding). */
.embed-main { display: block; }

.site-footer { border-top: 1px solid var(--line); padding: 2rem 0 1.5rem; margin-top: 2rem; background: var(--surface); }

/* Upper footer: K-Monitor brand + socials, an "about" link column and a contact
   column. Columns wrap onto their own rows on narrow screens. */
.footer-main {
  display: flex; flex-wrap: wrap; gap: 2rem 3rem; align-items: flex-start;
  padding-bottom: 1.25rem; margin-bottom: 1.25rem; border-bottom: 1px solid var(--line);
}
.footer-brand { display: flex; flex-direction: column; gap: .8rem; }
.footer-logo { display: inline-block; }
.footer-logo img { height: 52px; width: auto; display: block; }
.footer-social { display: flex; gap: .5rem; }
.social-link {
  display: inline-flex; align-items: center; justify-content: center;
  width: 36px; height: 36px; border-radius: 8px; color: var(--ink-soft);
  border: 1px solid var(--line); background: var(--surface);
}
.social-link:hover, .social-link:focus-visible { color: var(--accent); border-color: var(--accent); text-decoration: none; }
.footer-col { display: flex; flex-direction: column; gap: .35rem; }
.footer-h { font-size: .95rem; font-weight: 700; color: var(--ink); margin: 0 0 .35rem; }
.footer-col a { color: var(--ink-soft); font-size: .9rem; }
.footer-col a:hover, .footer-col a:focus-visible { color: var(--accent); }

.footer-bar { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.donate-link {
  display: inline-flex; align-items: center; gap: .4rem; flex-shrink: 0;
  padding: .4rem .9rem; border-radius: 999px; font-weight: 700; font-size: .9rem;
  color: var(--accent); background: var(--accent-soft); border: 1px solid #f0cfc9;
}
.donate-link:hover, .donate-link:focus-visible { background: #f4d8d3; text-decoration: none; }

/* Hamburger toggle: hidden on desktop, revealed at the mobile breakpoint where
   the five nav links no longer fit on one row. */
.navtoggle {
  display: none; box-sizing: border-box; height: 34px; width: 38px; flex-shrink: 0;
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
  /* Tight on a phone: pull the spacing in, and let the control cluster be the
     part that gives when the row still doesn't fit — everything in it holds its
     size except the cycle chooser, which ellipsises (see CycleSelect). Without
     this the hamburger, as the only shrinkable item left, got squashed to a
     sliver at the screen edge. */
  .header-bar { gap: .5rem; }
  .header-controls { order: 2; gap: .3rem; flex-shrink: 1; min-width: 0; }
  .infolink, .lang { flex-shrink: 0; }
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

/* Smallest phones (≈320 px): claw back a couple of dozen pixels from the icon
   buttons' padding so the cycle chooser can still spell out its scope instead of
   ellipsising to a single character. Tap targets keep their 34 px height. */
@media (max-width: 380px) {
  .infolink { padding: 0 .4rem; }
  .lang { padding: 0 .45rem; }
}
</style>
