// Front-end feature switches for pages that are built and working but kept off
// the public site for editorial reasons. Flipping the flag back to `true` is the
// only step needed to restore the page — the views, routes, API and i18n strings
// all stay in place.

// Frakcióelemzés (VOTE-8, /votes/cohesion + the `faction-cohesion` embed).
// Temporarily hidden: the co-voting numbers need explanatory context we don't
// present yet. While false the sub-tab is gone, the route 404s and the embed
// kind is refused; the /votes/cohesion API endpoint itself is untouched.
export const COHESION_ENABLED = false

// Beszédmetrikák (READ-1..7, §5.7): the per-speech readability (LIX) and lexical
// diversity (MATTR) chips on the sitting-day speech list and in the viewer.
// Temporarily hidden while false — the chips simply don't render.
//
// Nothing else is switched off: the loader still measures every speech, the API
// still serves `metrics` on each speech and the methodology on /meta, and the
// stored numbers stay current. So this is purely an editorial curtain, and
// flipping it back to `true` is the only step needed to show them again.
export const SPEECH_METRICS_ENABLED = false
