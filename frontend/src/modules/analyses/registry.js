import { store } from '../../store.js'

// The Elemzések section's contents (§4E), in the order they are shown.
//
// This list is the **only** place the section is described: the landing page
// builds its cards from it, and App.vue builds the section's sub-tab bar and
// its route-matching set from it too. Adding an analysis is therefore one entry
// here plus its route in router.js and its two i18n strings — no navigation
// code to touch.
//
// Each entry:
//   key      — i18n key under `analyses.cards.*` (title / desc / source)
//   route    — the route name the card and the tab link to; also the `nav.*`
//              key the tab is labelled with
//   module   — the backend module the page's data comes from. The analyses are
//              *views onto other modules' data*, not a module of their own, so
//              each one switches off with the module that feeds it (EXT-6).
//   detail   — routes that are detail pages of this analysis: they keep its tab
//              active and count as being "inside" the section.
//   cycleScoped — the analysis only means something within one electoral cycle,
//              so it is unavailable unless exactly one cycle is in scope (§4A).
//   icon     — SVG path `d` strings, drawn stroked in a 24×24 box.
export const ANALYSES = [
  {
    key: 'cohesion',
    route: 'cohesion',
    module: 'votes',
    cycleScoped: true,
    // Four cells of an agreement matrix — the analysis's signature chart.
    icon: ['M3.5 3.5h7v7h-7z', 'M13.5 3.5h7v7h-7z', 'M3.5 13.5h7v7h-7z', 'M13.5 13.5h7v7h-7z'],
  },
  {
    key: 'questions',
    route: 'questions',
    module: 'bills',
    // One column's flow bending into another — the Sankey.
    icon: ['M3 5.5h3.5a4 4 0 0 1 4 4v5a4 4 0 0 0 4 4H20', 'm16.5 15 3.5 3.5-3.5 3.5'],
  },
  {
    key: 'settlements',
    route: 'settlements',
    module: 'settlements',
    detail: ['settlement', 'settlementReps'],
    // A map pin.
    icon: ['M12 21.5s7-5.7 7-11.5a7 7 0 1 0-14 0c0 5.8 7 11.5 7 11.5z',
           'M12 12.6a2.6 2.6 0 1 0 0-5.2 2.6 2.6 0 0 0 0 5.2z'],
  },
]

// Route names that belong to the section — the analyses themselves, their
// detail pages, and the landing page.
export const ANALYSIS_ROUTES = [
  'analyses', ...ANALYSES.flatMap((a) => [a.route, ...(a.detail || [])]),
]

export const ANALYSIS_BY_ROUTE = Object.fromEntries(ANALYSES.map((a) => [a.route, a]))

// Whether an analysis is on the site at all: its module is mounted (EXT-6).
// This is what decides the section's top-bar entry, so the entry does not come
// and go as the reader changes the cycle scope.
export function analysisEnabled(a) { return store.moduleEnabled(a.module) }

// Whether it can be *opened right now*: enabled, and — for a within-cycle
// analysis — with exactly one cycle in scope (§4A). Neither "all cycles" nor a
// multi-cycle selection will do: the numbers describe one House, and pooling
// several terms' roll calls would compare factions that never sat together,
// under memberships that changed in between.
export function analysisAvailable(a) {
  return analysisEnabled(a) && (!a.cycleScoped || store.cycles.length === 1)
}
