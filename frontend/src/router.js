import { createRouter, createWebHistory } from 'vue-router'
import { defaultCycles, loadMeta, parseCycles, serializeCycles, setCycles, store } from './store.js'
import {
  claimsOwnScroll, keepInPlace, rememberScroll, scrollTarget, whenReachable,
} from './lib/scrollMemory.js'
// The Elemzések section describes itself in one place (§4E/ANA-3); the guard
// below reads the feature gate from there rather than naming routes again.
import { ANALYSIS_BY_ROUTE } from './modules/analyses/registry.js'

// The global electoral-cycle scope is carried in a `?cycle=` query param so that
// a shared link reproduces the exact scope the sharer was viewing: one or more
// comma-separated period numbers ("43" / "43,44"), or `all` for the "all cycles"
// scope — matching the localStorage sentinel in store.js. The guard below keeps
// it in sync in both directions.
const CYCLE_QUERY = 'cycle'

// A page that has moved: keep the reader's query and hash, and hand the old
// path's params (a settlement's `<maz>/<taz>`) to the new route.
const moved = (name) => (to) => ({
  name, params: to.params, query: to.query, hash: to.hash,
})

// Core routes are always present. Feature-module views are **lazily loaded**
// (separate chunks, EXT-4) and gated on the module being enabled (EXT-6).
const routes = [
  { path: '/', name: 'home', component: () => import('./views/HomeView.vue') },
  { path: '/about', name: 'about', component: () => import('./views/AboutView.vue') },

  // --- proceedings module ---
  {
    path: '/search', name: 'search', meta: { module: 'proceedings' },
    component: () => import('./modules/proceedings/SearchView.vue'),
  },
  {
    path: '/sessions', name: 'sessions', meta: { module: 'proceedings' },
    component: () => import('./modules/proceedings/SessionsView.vue'),
  },
  {
    path: '/sessions/:id', name: 'session', meta: { module: 'proceedings' },
    component: () => import('./modules/proceedings/SessionView.vue'), props: true,
  },
  {
    // Viewer — deep-linkable per speech and per sentence (VIE-5).
    path: '/proceedings/:uid', name: 'viewer', meta: { module: 'proceedings' },
    component: () => import('./modules/proceedings/ViewerView.vue'), props: true,
  },

  // --- representatives module ---
  {
    path: '/representatives', name: 'representatives', meta: { module: 'representatives' },
    component: () => import('./modules/representatives/RepListView.vue'),
  },
  {
    path: '/representatives/factions', name: 'factions', meta: { module: 'representatives' },
    component: () => import('./modules/representatives/FactionsView.vue'),
  },
  {
    // Nationality advocates (nemzetiségi szószólók, REP-9) — a **category chip**
    // of the Felszólalók page above, not a page of its own: they hold a different
    // mandate (no faction, no constituency, no vote), so the list still shows one
    // group at a time, but the chip row switches between them in place. Each chip
    // keeps its own URL so a chosen category stays linkable, citable and
    // separately indexable, and the same list component serves them all, reading
    // which mandate to show from the route name. Declared before
    // `/representatives/:id` so the static segment is never taken for a person id.
    path: '/representatives/advocates', name: 'advocates',
    meta: { module: 'representatives' },
    component: () => import('./modules/representatives/RepListView.vue'),
  },
  {
    // Other speakers (REP-12) — everyone who spoke in the House holding neither an
    // MP's mandate nor an advocacy: non-MP ministers and state secretaries, the
    // President of the Republic, invited guests. The third chip, same list
    // component again. Declared before `/representatives/:id`.
    path: '/representatives/speakers', name: 'speakers',
    meta: { module: 'representatives' },
    component: () => import('./modules/representatives/RepListView.vue'),
  },
  {
    // ...and the fourth chip: everyone above in one list, for a reader who does
    // not know which of the three a name belongs to. Declared before
    // `/representatives/:id` like the other static segments, so "all" is never
    // taken for a person id.
    path: '/representatives/all', name: 'allSpeakers',
    meta: { module: 'representatives' },
    component: () => import('./modules/representatives/RepListView.vue'),
  },
  {
    // Tisztségviselők (REP-11) — the parliament's own all-time office-holder
    // registry, listed term by term rather than person by person (a career runs
    // through several offices). Declared before `/representatives/:id`.
    path: '/representatives/officials', name: 'officials',
    meta: { module: 'representatives' },
    component: () => import('./modules/representatives/OfficialsView.vue'),
  },
  {
    // Tárcák (§6C) — the government side of the record. Its own module (so it
    // switches off on its own, EXT-6) but its pages live in the Representatives
    // section's tab bar, next to the office holders whose institution it is
    // (MIN-5). Declared before `/representatives/:id` like the other static
    // segments, so "portfolios" is never taken for a person id.
    path: '/representatives/portfolios', name: 'portfolios',
    meta: { module: 'portfolios' },
    component: () => import('./modules/portfolios/PortfolioListView.vue'),
  },
  {
    path: '/representatives/portfolios/:slug', name: 'portfolio',
    meta: { module: 'portfolios' },
    component: () => import('./modules/portfolios/PortfolioView.vue'), props: true,
  },
  {
    // Bizottságok (§6F) — the bodies the House does most of its work in. Its own
    // module (so it switches off on its own, EXT-6) but, like Tárcák, its pages
    // live in the Representatives section's tab bar: a committee is a group of
    // the same people the section is already about (BIZ-2). Declared before
    // `/representatives/:id` like the other static segments, so "committees" is
    // never taken for a person id.
    path: '/representatives/committees', name: 'committees',
    meta: { module: 'committees' },
    component: () => import('./modules/committees/CommitteeListView.vue'),
  },
  {
    // One sitting's minutes (BIZ-15). Addressed by the MEETING id, not nested
    // under the committee's: a jegyzőkönyv belongs to one sitting, the meeting
    // id is what every other reference to it already uses, and a path carrying
    // both ids would let the two disagree.
    path: '/representatives/committees/meetings/:meetingId', name: 'committee-minutes',
    meta: { module: 'committees' },
    component: () => import('./modules/committees/CommitteeMinutesView.vue'),
    props: true,
  },
  {
    path: '/representatives/committees/:id', name: 'committee',
    meta: { module: 'committees' },
    component: () => import('./modules/committees/CommitteeView.vue'), props: true,
  },
  {
    // "Who represents me?" — find your own constituency and its MP (REP-10). Not a
    // page of its own but the **place mode of the Felszólalók page**: the same
    // question ("which of these people is mine") keyed by a settlement instead of
    // a name, so the same component serves it and swaps its search card and list
    // for the lookup panel. It keeps this URL — the address it was published
    // under, with its own share card (og.py) — so existing links, and a resolved
    // answer's own address, stay valid. Declared before `/representatives/:id`
    // like the other static segments. The guard below leaves its `?cycle=` alone
    // like every other page, but the panel ignores the scope: it answers for the
    // cycle the constituency boundaries belong to (see the panel).
    path: '/representatives/lookup', name: 'lookup',
    meta: { module: 'representatives' },
    component: () => import('./modules/representatives/RepListView.vue'),
  },
  {
    // Összehasonlítás (REP-15) — two to four people in parallel columns, the
    // question a profile always raises next ("compared to whom?"). The people
    // compared live in `?ids=`, so a comparison is as shareable and citable as a
    // profile; with none it is the empty picker the reader fills in. Declared
    // before `/representatives/:id` like the other static segments, so "compare"
    // is never taken for a person id.
    path: '/representatives/compare', name: 'compare',
    meta: { module: 'representatives' },
    component: () => import('./modules/representatives/RepCompareView.vue'),
  },
  {
    path: '/representatives/:id', name: 'profile', meta: { module: 'representatives' },
    component: () => import('./modules/representatives/RepProfileView.vue'), props: true,
  },

  // --- where these pages used to live -------------------------------------
  // The three analyses were sub-tabs of Votes / Törvényjavaslatok / Felszólalók
  // before they were gathered into their own section. A direct hit on an old
  // address is answered by the server with a 301 (backend `app/redirects.py`),
  // which is what a crawler or an existing link gets; these mirror the same
  // moves for navigation that happens inside the running app, carrying the
  // query (`?cycle=`, `?tab=`) and hash across unchanged. Declared ahead of the
  // `/votes/:id` pattern that shares the `/votes/…` shape with one of them.
  { path: '/votes/cohesion', redirect: moved('cohesion') },
  { path: '/questions', redirect: moved('questions') },
  { path: '/settlements', redirect: moved('settlements') },
  { path: '/settlements/representatives', redirect: moved('settlementReps') },
  { path: '/settlements/:maz(\\d{2})/:taz(\\d{3})', redirect: moved('settlement') },

  // --- bills module ---
  {
    path: '/bills', name: 'bills', meta: { module: 'bills' },
    component: () => import('./modules/bills/BillsListView.vue'),
  },
  {
    // Kérdések (BILL-13) — the question-type irományok (kérdés, interpelláció,
    // azonnali kérdés) as their own browse page, the middle tab of the section.
    // They are ~90% of everything that is not a törvényjavaslat, so on the
    // all-irományok list they drown out every other type; here they get the
    // filters that only make sense for a question (was it answered, how, and did
    // the asker accept the reply). Same data layer and same detail view as the
    // other two tabs — an opened question is still a `/documents/:id`, which is
    // the canonical address for every non-törvényjavaslat (og.py).
    //
    // Not at `/questions`: that address is a shipped 301 to the Kérdések *Sankey*
    // in Elemzések (backend `redirects.py`, mirrored below), which is a different
    // page. Declared before `/bills/:id` so "questions" is never taken for an
    // iromány id — the same ordering the static representatives segments make.
    path: '/bills/questions', name: 'questionList', meta: { module: 'bills' },
    component: () => import('./modules/bills/QuestionListView.vue'),
  },
  {
    path: '/bills/:id', name: 'bill', meta: { module: 'bills' },
    component: () => import('./modules/bills/BillView.vue'), props: true,
  },

  // --- other irományok (part of the bills module: same data layer + detail
  // view, separate browse page for the non-törvényjavaslat document types) ---
  {
    path: '/documents', name: 'documents', meta: { module: 'bills' },
    component: () => import('./modules/documents/DocumentsListView.vue'),
  },
  {
    path: '/documents/:id', name: 'document', meta: { module: 'bills' },
    component: () => import('./modules/bills/BillView.vue'), props: true,
  },

  // --- votes module ---
  {
    path: '/votes', name: 'votes', meta: { module: 'votes' },
    component: () => import('./modules/votes/VotesListView.vue'),
  },
  {
    path: '/votes/:id', name: 'vote', meta: { module: 'votes' },
    component: () => import('./modules/votes/VoteView.vue'), props: true,
  },

  // --- elemzések (§4E) ---
  // A section of the site rather than a module: each page below reads the data
  // of the module named in its `meta`, and switches off with it (EXT-6). The
  // landing page has no module of its own — it lists whichever analyses are
  // available, and says so when none are.
  {
    path: '/analyses', name: 'analyses',
    component: () => import('./modules/analyses/AnalysesView.vue'),
  },
  {
    // Frakcióelemzés — the party co-voting charts (VOTE-8).
    path: '/analyses/faction-cohesion', name: 'cohesion', meta: { module: 'votes' },
    component: () => import('./modules/votes/CohesionView.vue'),
  },
  {
    // Kérdések — Sankey of who asked a question and who answered (BILL-11).
    path: '/analyses/questions', name: 'questions', meta: { module: 'bills' },
    component: () => import('./modules/bills/QuestionsView.vue'),
  },
  {
    // Témák (TOPIC-9) — the CAP topic mix of the floor and of the irományok.
    // Registered against proceedings, whose speeches the chart's first series is;
    // the document series comes from the bills module and is simply absent where
    // that is off (EXT-6).
    path: '/analyses/topics', name: 'topics', meta: { module: 'proceedings' },
    component: () => import('./modules/analyses/TopicsView.vue'),
  },
  {
    // Közbeszólások (§6E) — the directed graph of who heckles whom.
    path: '/analyses/interjections', name: 'interjections',
    meta: { module: 'interjections' },
    component: () => import('./modules/interjections/InterjectionsView.vue'),
  },
  {
    // Települések (§6D) — the settlement-mention map and list.
    path: '/analyses/settlements', name: 'settlements', meta: { module: 'settlements' },
    component: () => import('./modules/settlements/SettlementsView.vue'),
  },
  {
    // The own-constituency measures (TEL-9). **Not linked from anywhere for now** —
    // the route stays so the page is reachable and citable, but no tab advertises it
    // (see the registry in modules/analyses). Declared before the two-segment
    // settlement route so "representatives" is never taken for a county code.
    path: '/analyses/settlements/representatives', name: 'settlementReps',
    meta: { module: 'settlements' },
    component: () => import('./modules/settlements/SettlementRepsView.vue'),
  },
  {
    // A settlement is identified by the register's own "<maz>/<taz>" key, kept as
    // two path segments so the URL carries the same id the API does.
    path: '/analyses/settlements/:maz(\\d{2})/:taz(\\d{3})', name: 'settlement',
    meta: { module: 'settlements' },
    component: () => import('./modules/settlements/SettlementView.vue'), props: true,
  },

  // --- embeddable figures ---
  // Chrome-free single-chart views meant to be dropped into a third-party page
  // via <iframe> (see EmbedButton). `meta.embed` tells App.vue to render the bare
  // chart with no site header/nav/footer, and the guard below to leave the URL
  // untouched (the embed carries its own explicit `?cycle=` + chart params).
  {
    path: '/embed/:kind', name: 'embed', meta: { embed: true },
    component: () => import('./views/EmbedView.vue'), props: true,
  },

  { path: '/:pathMatch(.*)*', name: 'notfound', component: () => import('./views/NotFoundView.vue') },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
  // Coming back to a page puts the reader back where they left it (§4D) — both
  // on a browser Back and on the "‹ back to the sitting day" / "‹ Ülésnapok"
  // links, which are ordinary pushes with no saved position of their own. The
  // offset can't be applied straight away: the destination fetches its rows
  // after mounting, so at this point it is one spinner tall and any offset would
  // clamp to 0. Hence the promise — see lib/scrollMemory.js.
  scrollBehavior(to, from, saved) {
    // Advanced even for a hash target, so the trail stays a faithful record of
    // the pages the reader has walked through.
    const target = scrollTarget(to, saved)
    // An in-page choice that rewrote the address and scrolled to its own answer
    // (the lookup's constituency picker): leave the position it chose alone. Not
    // on a history pop, where the reader's own saved offset is the truth.
    if (claimsOwnScroll(to) && !saved) return false
    if (to.hash) return false // viewer manages its own scroll to a sentence
    if (!target || !target.top) return { top: 0 }
    const stale = () => router.currentRoute.value.fullPath !== to.fullPath
    return whenReachable(target.top, stale).then((ready) => {
      if (!ready) return false // reader moved on, or the content never came
      keepInPlace(target.top, stale)
      return target
    })
  },
})

// Note where the reader is *before* the incoming view replaces the DOM, so the
// offset recorded still belongs to the page being left (§4D).
router.beforeEach((to, from) => {
  rememberScroll(from)
  return true
})

// Block routes whose module is disabled in the backend manifest (EXT-6).
router.beforeEach(async (to) => {
  try {
    await loadMeta()
  } catch {
    /* meta failed; let the view render its own error state */
  }
  // Embed routes are self-contained (their own `?cycle=` + params) and must not
  // adopt the visitor's saved cycle or have their URL rewritten — the iframe
  // snippet has to stay byte-for-byte what the sharer copied. Let them through.
  if (to.meta.embed) return true
  // The constituency lookup (REP-10) sits inside an enabled module but depends on
  // an external source, so the backend can switch it off on its own — in which case
  // its endpoints 404 and so must its page, rather than rendering an error state.
  if (to.name === 'lookup' && store.loaded && !store.featureEnabled('constituency_lookup')) {
    return {
      name: 'notfound',
      params: { pathMatch: to.path.substring(1).split('/') },
      query: to.query,
    }
  }
  // The same for an analysis gated on a capability rather than on its module
  // (Témák, whose data needs a classification pass the deployment may never have
  // run): its card and tab are already gone, so its address must not be the one
  // place it still answers.
  const gate = ANALYSIS_BY_ROUTE[to.name]?.feature
  if (gate && store.loaded && !store.featureEnabled(gate)) {
    return {
      name: 'notfound',
      params: { pathMatch: to.path.substring(1).split('/') },
      query: to.query,
    }
  }
  if (to.meta.module && store.loaded && !store.moduleEnabled(to.meta.module)) {
    // Render the 404 view *at the requested URL* — a named catch-all resolved
    // without its repeatable param would rewrite the address bar to "/".
    return {
      name: 'notfound',
      params: { pathMatch: to.path.substring(1).split('/') },
      query: to.query,
      hash: to.hash,
    }
  }

  // Sync the global cycle scope with the URL. A valid `?cycle=` wins — adopt it
  // so a shared link overrides the visitor's saved default. Otherwise write the
  // current scope (set by loadMeta→initCycles from localStorage/latest) back
  // into the URL, **unless it is the default scope**, which stays implicit. The
  // in-guard redirect commits once (no extra history entry), and views that
  // rebuild the query on filter/pagination simply get the param re-added.
  //
  // The default-scope exemption is what keeps the site indexable (§SEO-2).
  // Appending `?cycle=` to *every* address meant that a crawler asking for the
  // clean URL got a JS redirect to a parameterised twin, whose server-rendered
  // <link rel="canonical"> points straight back at the clean URL — so Google
  // filed the clean URL as "Page with redirect", the twin as "Alternate page
  // with proper canonical tag", and indexed neither.
  if (store.loaded) {
    const periods = (store.meta && store.meta.periods) || []
    const fromUrl = parseCycles(to.query[CYCLE_QUERY], periods)
    if (fromUrl !== undefined) {
      const canonical = serializeCycles(fromUrl)
      if (serializeCycles(store.cycles) !== canonical) setCycles(fromUrl)
      // Rewrite a scope that parsed to something other than what it says — a
      // stale cycle number, or the cycles out of order ("43,42", "43,99") — so
      // the address always spells out the scope actually applied, and re-sharing
      // it can't drift. Settles in one redirect: the canonical form parses to
      // itself. A URL that *explicitly* spells out the default scope is left
      // alone: it may already be shared or indexed, and its canonical tag
      // consolidates it onto the clean URL anyway.
      if (to.query[CYCLE_QUERY] !== canonical) {
        return { path: to.path, query: { ...to.query, [CYCLE_QUERY]: canonical }, hash: to.hash }
      }
    } else {
      const desired = serializeCycles(store.cycles)
      if (desired === serializeCycles(defaultCycles(periods))) {
        // Default scope — the address stays parameter-free. Drop an
        // unparseable leftover (`?cycle=`, `?cycle=99`) so it does.
        if (to.query[CYCLE_QUERY] !== undefined) {
          const query = { ...to.query }
          delete query[CYCLE_QUERY]
          return { path: to.path, query, hash: to.hash }
        }
      } else if (to.query[CYCLE_QUERY] !== desired) {
        return { path: to.path, query: { ...to.query, [CYCLE_QUERY]: desired }, hash: to.hash }
      }
    }

    // Frakcióelemzés (cohesion) is a within-cycle analysis — co-voting pooled
    // across several Houses is meaningless, whether that is the whole corpus or
    // two terms with different memberships — so unless exactly one cycle is in
    // scope its sub-tab is hidden (App.vue) and its route is unreachable: bounce
    // to the section index, keeping the scope. Also fires when the user widens
    // the scope while already viewing the page.
    if (to.name === 'cohesion' && store.cycles.length !== 1) {
      return { name: 'analyses', query: to.query, hash: to.hash }
    }
  }
  return true
})
