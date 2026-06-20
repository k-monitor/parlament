import { createRouter, createWebHistory } from 'vue-router'
import { loadMeta, store } from './store.js'

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
  if (to.meta.module && store.loaded && !store.moduleEnabled(to.meta.module)) {
    return { name: 'notfound' }
  }
  return true
})
