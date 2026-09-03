// Front-end feature switches for pages that are built and working but kept off
// the public site for editorial reasons. Flipping the flag back to `true` is the
// only step needed to restore the page — the views, routes, API and i18n strings
// all stay in place.

// Beszédmetrikák (READ-1..7, §5.7): the per-speech readability (LIX) and lexical
// diversity (MATTR) chips on the sitting-day speech list and in the viewer.
//
// ON, but **hidden until the reader asks for it**: this is an annotation most
// readers did not come for, so neither surface shows it by default. One persisted
// opt-in (`store.showSpeechMetrics`) governs both, flipped by the "beszédmetrikák"
// toggle on a sitting day — the only control, and therefore the feature's single
// point of discovery. Once on, the day list carries the short wording on every row
// and the viewer the full phrase in its speech header.
//
// So this flag and the reader's toggle answer different questions: this one is
// whether the feature exists at all on the site, that one whether a reader who
// knows about it wants to see it. Turning this back to `false` also removes the
// toggle (and with it any way in), and nothing else changes: the
// loader still measures every speech, the API still serves `metrics` on each
// speech and the methodology on /meta, and the stored numbers stay current.
export const SPEECH_METRICS_ENABLED = true
