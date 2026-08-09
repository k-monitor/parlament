import { createRouter, createWebHistory } from 'vue-router'
import { defaultCycles, loadMeta, parseCycles, serializeCycles, setCycles, store } from './store.js'
import { COHESION_ENABLED } from './features.js'
import { keepInPlace, rememberScroll, scrollTarget, whenReachable } from './lib/scrollMemory.js'

// The global electoral-cycle scope is carried in a `?cycle=` query param so that
// a shared link reproduces the exact scope the sharer was viewing: one or more
// comma-separated period numbers ("43" / "43,44"), or `all` for the "all cycles"
// scope — matching the localStorage sentinel in store.js. The guard below keeps
// it in sync in both directions.
const CYCLE_QUERY = 'cycle'

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
    // Nationality advocates (nemzetiségi szószólók, REP-9) — a sibling page of the
    // MP list rather than a filter of it: they hold a different mandate (no
    // faction, no constituency, no vote). Same list component, which reads which
    // mandate to show from the route name. Declared before `/representatives/:id`
    // so the static segment is never taken for a person id.
    path: '/representatives/advocates', name: 'advocates',
    meta: { module: 'representatives' },
    component: () => import('./modules/representatives/RepListView.vue'),
  },
  {
    // Other speakers (REP-12) — everyone who spoke in the House holding neither an
    // MP's mandate nor an advocacy: non-MP ministers and state secretaries, the
    // President of the Republic, invited guests. Same list component again, keyed
    // off the route name. Declared before `/representatives/:id`.
    path: '/representatives/speakers', name: 'speakers',
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
    // "Who represents me?" — find your own constituency and its MP (REP-10). Also
    // declared before `/representatives/:id`. The guard below leaves its `?cycle=`
    // alone like every other page, but the view ignores the scope: it answers for
    // the cycle the constituency boundaries belong to (see the view).
    path: '/representatives/lookup', name: 'lookup',
    meta: { module: 'representatives' },
    component: () => import('./modules/representatives/ConstituencyLookupView.vue'),
  },
  {
    path: '/representatives/:id', name: 'profile', meta: { module: 'representatives' },
    component: () => import('./modules/representatives/RepProfileView.vue'), props: true,
  },

  // --- bills module ---
  {
    path: '/bills', name: 'bills', meta: { module: 'bills' },
    component: () => import('./modules/bills/BillsListView.vue'),
  },
  {
    // Kérdések — Sankey of who asked a question and who answered (BILL-11).
    // Part of the bills module; declared before /bills/:id so it isn't shadowed.
    path: '/questions', name: 'questions', meta: { module: 'bills' },
    component: () => import('./modules/bills/QuestionsView.vue'),
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
  // Frakcióelemzés — the party co-voting charts (VOTE-8), a sub-tab of Votes.
  // Declared before /votes/:id so "cohesion" isn't captured as a vote id. Behind
  // COHESION_ENABLED (features.js): while off, the same path serves the 404 view
  // rather than disappearing into /votes/:id.
  ...(COHESION_ENABLED ? [{
    path: '/votes/cohesion', name: 'cohesion', meta: { module: 'votes' },
    component: () => import('./modules/votes/CohesionView.vue'),
  }] : [{
    path: '/votes/cohesion', component: () => import('./views/NotFoundView.vue'),
  }]),
  {
    path: '/votes/:id', name: 'vote', meta: { module: 'votes' },
    component: () => import('./modules/votes/VoteView.vue'), props: true,
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
  // snippet has to stay byte-for-byte what the sharer copied. Let them through —
  // except the figure of a page that is currently off the public site, which is
  // shown as a 404 like its host page (previously copied iframes go blank).
  if (to.meta.embed) {
    if (!COHESION_ENABLED && to.params.kind === 'faction-cohesion') {
      return { name: 'notfound', params: { pathMatch: to.path.substring(1).split('/') }, query: to.query }
    }
    return true
  }
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

    // Frakcióelemzés (cohesion) is a within-cycle analysis — co-voting compared
    // across a whole multi-decade corpus is meaningless — so under the "all
    // cycles" scope its sub-tab is hidden (App.vue) and its route is unreachable:
    // bounce to the vote list, keeping the scope. Also fires when the user
    // switches to "all cycles" while already viewing the page.
    if (to.name === 'cohesion' && !store.cycles.length) {
      return { name: 'votes', query: to.query, hash: to.hash }
    }
  }
  return true
})
