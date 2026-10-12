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

// Remuneration (REP-17, §6B): the published monthly fee panel on an MP profile,
// with the §104(1) reading of it. Which profiles carry it:
//
// * `'off'` — none. The data stays live either way (the registry sync stores
//   every published month and the profile API still serves `remuneration`).
// * `'declarants'` — only the profiles of MPs who have filed a declaration in
//   the House's electronic system (EVNYR, REP-18). There the fee is shown with
//   the other income they declared listed under it (REP-19), so a reader sees
//   the parliamentary fee as one source among the ones the MP states, never
//   as a bare figure on its own.
// * `'all'` — every MP with a published month, declaration or not.
//
// 'declarants' since 2026-10-08 (it had been 'off' since 2026-09-17).
export const REMUNERATION_SCOPE = 'declarants'

// Asset-declaration contents (REP-18): the itemised public part of a declaration
// filed in the House's electronic system (properties, vehicles, savings, debts,
// income, interests), shown under the declaration on a profile.
//
// ON. The House publishes this data for free reuse ("szabadon feldolgozhatja"),
// and every load replaces it with the system's current daily snapshot, so a
// correction upstream is never masked by an older copy here. It is still a step
// beyond linking the PDF, which is why it has a switch of its own: `false` keeps
// every declaration listed and linked (page and PDF) and only drops the
// expandable contents — and with them the declared-income list on the income
// card (REP-19), which is the same contents read into numbers. The API serves
// `content` and `declared_income` either way.
export const ASSET_DECLARATION_DETAILS_ENABLED = true
