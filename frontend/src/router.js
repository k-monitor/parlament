import { createRouter, createWebHistory } from 'vue-router'
import { loadMeta, setCycle, store } from './store.js'
import { COHESION_ENABLED } from './features.js'

// The global electoral cycle is carried in a `?cycle=` query param so that a
// shared link reproduces the exact scope the sharer was viewing (a period
// number, or `all` for the "all cycles" scope — matching the localStorage
// sentinel in store.js). The guard below keeps it in sync in both directions.
const CYCLE_QUERY = 'cycle'

// Parse a `?cycle=` value into a store cycle, validating against known periods.
// `undefined` = absent or invalid (caller falls back to the current cycle),
// `null` = the explicit "all cycles" sentinel, otherwise the period number.
function cycleFromQuery(raw, periods) {
  if (raw === undefined || raw === null) return undefined
  if (raw === 'all') return null
  const n = Number(raw)
  return Number.isFinite(n) && periods.some((p) => p.number === n) ? n : undefined
}

// Serialise a store cycle (null = all) into its `?cycle=` query value.
function cycleToQuery(cycle) {
  return cycle === null ? 'all' : String(cycle)
}

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
  scrollBehavior(to, from, saved) {
    if (to.hash) return false // viewer manages its own scroll to a sentence
    return saved || { top: 0 }
  },
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

  // Sync the global cycle with the URL. A valid `?cycle=` wins — adopt it so a
  // shared link overrides the visitor's saved default. Otherwise write the
  // current cycle (set by loadMeta→initCycle from localStorage/latest) back
  // into the URL, so every address carries an explicit, shareable scope. The
  // in-guard redirect commits once (no extra history entry), and views that
  // rebuild the query on filter/pagination simply get the param re-added.
  if (store.loaded) {
    const periods = (store.meta && store.meta.periods) || []
    const fromUrl = cycleFromQuery(to.query[CYCLE_QUERY], periods)
    if (fromUrl !== undefined) {
      if (store.cycle !== fromUrl) setCycle(fromUrl)
    } else {
      const desired = cycleToQuery(store.cycle)
      if (to.query[CYCLE_QUERY] !== desired) {
        return { path: to.path, query: { ...to.query, [CYCLE_QUERY]: desired }, hash: to.hash }
      }
    }

    // Frakcióelemzés (cohesion) is a single-cycle analysis — co-voting compared
    // across different cycles is meaningless — so under the "all cycles" scope
    // its sub-tab is hidden (App.vue) and its route is unreachable: bounce to the
    // vote list, keeping the scope. Also fires when the user switches to "all
    // cycles" while already viewing the page.
    if (to.name === 'cohesion' && store.cycle === null) {
      return { name: 'votes', query: to.query, hash: to.hash }
    }
  }
  return true
})
