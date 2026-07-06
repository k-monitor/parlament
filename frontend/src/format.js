// Shared formatting helpers (durations, dates, agenda labels).
import { i18n } from './i18n.js'

export function formatDuration(seconds) {
  if (seconds == null) return '—'
  const s = Math.round(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
  return `${m}:${String(sec).padStart(2, '0')}`
}

// Compact "Xó Yp" / "Xh Ym" speaking-time label.
export function formatSpeakingTime(seconds) {
  const lang = i18n.global.locale.value
  if (!seconds) return lang === 'en' ? '0m' : '0p'
  const m = Math.round(seconds / 60)
  if (m < 60) return lang === 'en' ? `${m}m` : `${m}p`
  const h = Math.floor(m / 60)
  const rem = m % 60
  return lang === 'en' ? `${h}h ${rem}m` : `${h}ó ${rem}p`
}

export function formatDate(iso) {
  if (!iso) return ''
  return iso.slice(0, 10)
}

// Date + HH:MM for events/votes that carry a time-of-day (e.g. "2026-05-27 08:34").
export function formatDateTime(iso) {
  if (!iso) return ''
  const d = iso.slice(0, 10)
  const t = iso.slice(11, 16)
  return t ? `${d} ${t}` : d
}

export function agendaLabel(type) {
  if (!type) return ''
  const key = `agendaTypes.${type}`
  const translated = i18n.global.t(key)
  return translated === key ? type : translated
}

// HH:MM:SS for a video offset, as a deep-link-friendly seconds string too.
export function timeLabel(seconds) {
  return formatDuration(seconds)
}

// --- Transcript rendering -------------------------------------------------
//
// Raw proceedings text carries two things a reader shouldn't see as plain body:
//   1. a leading speaker label — "TUZSON BENCE (Fidesz):", "ELNÖK:",
//      "DR. ÁDER JÁNOS köztársasági elnök:" — redundant because the row already
//      shows the speaker, so it is stripped;
//   2. stage-direction / heckle parentheticals — "(Taps a kormánypártok
//      soraiból.)", "(Közbeszólás: …)" — which read better lifted out of the
//      speech into their own italic lines. One parenthetical can bundle several
//      interjections separated by a spaced dash (U+2011); each becomes its own
//      italic paragraph.

// A token whose letters are all uppercase (a shouted surname / "DR.") — the
// signature of a speaker name. Empty of letters ⇒ not a name token.
const isCapsToken = (t) => /\p{L}/u.test(t) && !/\p{Ll}/u.test(t)

// Strip the redundant leading speaker attribution from the start of a speech —
// "TUZSON BENCE (Fidesz):", "ELNÖK:", "DR. ÁDER JÁNOS köztársasági elnök:". The
// label always opens the speech and ends at its first colon; dropping up to that
// colon also swallows the faction/role trailing the name ("(Momentum)",
// "korjegyző", "…képviselőcsoportja részéről"). It is recognised structurally so
// a genuine sentence is never mutilated: a `!`/`?` before the colon disqualifies
// it (real speech, not a label), and the label must be either the lone chair
// token ("ELNÖK…") or open with two ALL-CAPS name tokens — so "EU-csúcs volt:…"
// or "MSZP frakcióvezetője …:" (acronym-led sentences) are left intact.
export function stripSpeakerLabel(text) {
  const stop = text.search(/[:!?\n]/)
  if (stop < 0 || text[stop] !== ':' || stop > 140) return text
  const tokens = text.slice(0, stop).trim().split(/\s+/)
  const isLabel =
    (tokens.length === 1 && /^ELNÖK/u.test(tokens[0])) ||
    (tokens.length >= 2 && isCapsToken(tokens[0]) && isCapsToken(tokens[1]))
  return isLabel ? text.slice(stop + 1).replace(/^[ \t]+/, '') : text
}

// An interjection separator inside a parenthetical: a dash flanked by whitespace.
// The surrounding-space requirement keeps hyphenated names (Ruszin-Szendi,
// Turi-Kovács) and ranges (2028-ig) intact — only " ‑ " style separators split.
// The class covers the dash block U+2010–U+2015, the minus sign U+2212, and a
// plain hyphen.
const INTERJECTION_SEP = /\s+[‐-―−-]\s+/

// Some parentheticals are references the speaker dictated, not stage directions
// or heckles, and must stay INLINE (not be lifted into their own italic line):
//   • a bare number — "A Házszabály 9. § (2) bekezdése", "(3) pont";
//   • a Hungarian legal date — Roman-numeral month + day — as in
//     "17/2026. (V. 9.) OGY-határozat" or "H/170. … (II. 24.)".
// Recognised structurally: the content is made up only of digits, Roman-numeral
// letters, dots and whitespace, and contains at least one digit (so a real
// heckle — which has lowercase words — never matches).
const REFERENCE_PAREN = /^[IVXLCDM\d.\s]+$/

// An interjection often opens with the heckler's name — "Vitályos Eszter:
// Végrehajtod vagy nem?". Split that "Name:" attribution off so the caller can
// resolve the name to a representative (face + profile link) and set the remark
// apart as a small aside. The name is title-case tokens (optionally led by an
// honorific like "Dr."), two to four of them — Hungarian names are surname +
// given — so single-word cues ("Közbeszólás:", "Taps:") and descriptions with
// lowercase words ("Moraj a kormánypárti oldalon:") are NOT taken for a name.
// The name still has to match a real MP downstream, so a false positive like
// "Az Elnök:" simply fails to resolve and renders as plain text.
const HONORIFIC = /^(?:dr|prof|ifj|id|özv)\.?$/i
// A name token: a capitalised word (accents/apostrophes/hyphens allowed, e.g.
// "Ruszin-Szendi") OR a bare initial ("Z." in "Z. Kárpát Dániel").
const NAME_TOKEN = /^\p{Lu}(?:[\p{L}'’-]*|\.)$/u

// Returns `{ speaker, text }`: `speaker` is the attributed name (null if none),
// `text` the remark with any "Name:" prefix removed.
function splitInterjectionSpeaker(raw) {
  const colon = raw.indexOf(':')
  if (colon < 1 || colon > 60) return { speaker: null, text: raw }
  const text = raw.slice(colon + 1).trim()
  if (!text) return { speaker: null, text: raw }
  const tokens = raw.slice(0, colon).trim().split(/\s+/)
  let i = 0
  while (i < tokens.length && HONORIFIC.test(tokens[i])) i++
  const nameTokens = tokens.slice(i)
  if (nameTokens.length < 2 || nameTokens.length > 4) return { speaker: null, text: raw }
  if (!nameTokens.every((t) => NAME_TOKEN.test(t))) return { speaker: null, text: raw }
  return { speaker: nameTokens.join(' '), text }
}

// Punctuation that ends up orphaned at the START of a continuation when a
// parenthetical is dropped mid-sentence ("…dobják ki" | "(heckle)" | ", arra
// adtak…"). It closed the interrupted clause, so it belongs on the paragraph
// before the interjection, not dangling on the one after it.
const LEADING_PUNCT = /^[,.;:!?…]+/
// A pause-dash the speaker used to bracket the interjected clause — "…úr ‑ de
// hát… (Derültség.) ‑, hogy…" leaves a stray "‑" opening the continuation once
// the parenthetical is lifted out. Unlike the clause punctuation it carries no
// meaning on its own, so it is simply DROPPED (not reattached). Same dash class
// as INTERJECTION_SEP (U+2010–U+2015, U+2212, hyphen).
const LEADING_DASH = /^[\s‐-―−-]+/

// Push a spoken (non-interjection) segment onto `out`, returning the pushed
// paragraph (or `prev` unchanged when the segment is empty). When a previous
// spoken paragraph exists and this segment opens with the debris of a lifted
// parenthetical — stray pause-dashes and/or the punctuation that closed the
// interrupted clause — the dashes are dropped and the clause punctuation is
// lifted onto the end of `prev`, so the break reads "…dobják ki," / "arra
// adtak…" instead of "…dobják ki" / ", arra adtak…" (and "‑, hogy…" never
// dangles as its own "▶ ‑," row in the karaoke viewer).
function pushSpoken(out, raw, prev) {
  let text = raw.trim()
  if (prev) {
    // Peel the lifted-parenthetical debris off the front, alternating between
    // stray dashes (dropped) and clause punctuation (lifted onto `prev`), until
    // real words remain.
    for (;;) {
      const dash = text.match(LEADING_DASH)
      if (dash) text = text.slice(dash[0].length)
      const punct = text.match(LEADING_PUNCT)
      if (punct) { prev.text += punct[0]; text = text.slice(punct[0].length).trimStart() }
      if (!dash && !punct) break
    }
  }
  if (!text) return prev
  const para = { text, interjection: false }
  out.push(para)
  return para
}

// Push a parenthetical's inner content as one or more interjection segments —
// one per interjection bundled inside (they split on a spaced dash), each with
// its "Name:" attribution lifted off (see splitInterjectionSpeaker).
function pushAsides(out, inner) {
  for (const part of inner.split(INTERJECTION_SEP)) {
    // Trim surrounding separator-dashes too: when an interjection bundle is cut
    // across sentence boundaries the " ‑ " separator loses one flanking space,
    // so INTERJECTION_SEP no longer splits it and a stray leading/trailing dash
    // clings to the part ("Nyilvános idegenvezetés van. ‑").
    const t = part.replace(LEADING_DASH, '').replace(/[\s‐-―−-]+$/, '').trim()
    if (!t) continue
    const { speaker, text } = splitInterjectionSpeaker(t)
    out.push({ text, interjection: true, speaker })
  }
}

// Split ONE block of text into ordered segments `[{ text, interjection, speaker }]`,
// carrying open-parenthesis state IN and OUT so a parenthetical can span the
// block boundary. This matters for the karaoke viewer, which splits per SENTENCE:
// a stage direction with an internal full stop — "(A miniszterek felállnak. A
// patkóban… gratulál az esküt tett minisztereknek.)" — is cut into several
// sentences, so no single sentence holds a balanced "(…)". `inParen` (from the
// previous sentence) tells us the sentence opens inside a still-running aside;
// the returned `inParen` tells the next one the same. No nesting is expected in
// the transcripts, so the state is a simple boolean.
//
// Parentheticals become `interjection: true` segments (stage directions / named
// heckles); numeric & date references — "(2)", "(V. 9.)" — stay inline in the
// spoken text; punctuation orphaned after a dropped parenthetical is reattached
// to the preceding spoken segment.
export function splitSegmentsCarry(block, inParen = false, prevSpoken = null) {
  const out = []
  // `prevSpoken` is the last spoken segment seen so far — it may live in an
  // EARLIER sentence (passed in by segmentSentences) so orphaned leading
  // punctuation on a post-interjection continuation reattaches even when the
  // parenthetical spanned a sentence boundary. It is threaded back out as
  // `lastSpoken` for the next sentence.

  // Continuation of an aside opened in an earlier sentence: everything up to the
  // closing ")" (or the whole block, if it never closes) is still the aside.
  if (inParen) {
    const close = block.indexOf(')')
    if (close < 0) { pushAsides(out, block); return { segments: out, inParen: true, lastSpoken: prevSpoken } }
    pushAsides(out, block.slice(0, close))
    block = block.slice(close + 1)
    inParen = false
  }

  let last = 0, idx = 0
  while (idx < block.length) {
    const open = block.indexOf('(', idx)
    if (open < 0) break
    const close = block.indexOf(')', open + 1)
    if (close < 0) {
      // Unclosed "(": opens an aside that runs on into the next sentence.
      prevSpoken = pushSpoken(out, block.slice(last, open), prevSpoken)
      pushAsides(out, block.slice(open + 1))
      return { segments: out, inParen: true, lastSpoken: prevSpoken }
    }
    const inner = block.slice(open + 1, close).trim()
    // A reference like "(2)" or "(V. 9.)" is part of the speech, not a heckle:
    // leave it inline (advance past it without moving `last`, so the next spoken
    // slice swallows it).
    if (/\d/.test(inner) && REFERENCE_PAREN.test(inner)) { idx = close + 1; continue }
    prevSpoken = pushSpoken(out, block.slice(last, open), prevSpoken)
    pushAsides(out, block.slice(open + 1, close))
    last = close + 1
    idx = close + 1
  }
  prevSpoken = pushSpoken(out, block.slice(last), prevSpoken)
  return { segments: out, inParen: false, lastSpoken: prevSpoken }
}

// Convenience wrapper for callers that pass a self-contained block (a full source
// paragraph, as the flowing spoiler does): returns just the segments, dropping
// the carry state. A paragraph's parentheticals are balanced within it.
export function splitSegments(block) {
  return splitSegmentsCarry(block).segments
}

// Segment a speech's flat karaoke sentence list, threading open-parenthesis state
// from each sentence into the next so a stage direction split across sentence
// boundaries is lifted out as italic asides (not left as spoken text with stray
// "(" / ")"). Returns each sentence with a `.segments` array; the ord/time_start/
// time_end fields are preserved for seeking and karaoke highlight. The redundant
// leading speaker label is stripped from the opening sentence.
export function segmentSentences(sentences) {
  let inParen = false
  // Thread the last spoken segment across sentences so a comma orphaned by a
  // parenthetical that closed in a LATER sentence ("…szégyellték (heckle" /
  // "…soraiból.)," / "nem értem…") reattaches to its clause instead of showing
  // as its own "▶ ," row. Reset at a paragraph boundary so punctuation is never
  // lifted across a <p> break.
  let prevSpoken = null
  let prevPara
  return (sentences || []).map((s, i) => {
    if (i > 0 && s.paragraph !== prevPara) prevSpoken = null
    prevPara = s.paragraph
    const text = i === 0 ? stripSpeakerLabel(s.text) : s.text
    const res = splitSegmentsCarry(text, inParen, prevSpoken)
    inParen = res.inParen
    prevSpoken = res.lastSpoken
    return { ...s, segments: res.segments }
  })
}

// Turn a speech's flat sentence list into rendered paragraphs. Returns
// `[{ text, interjection, speaker }]`: `interjection: true` marks a stage-
// direction/heckle paragraph the caller should render in italics; `speaker`
// (when non-null) is the name a heckle was attributed to ("Name: …"), for the
// caller to resolve to a representative — see splitInterjectionSpeaker().
export function transcriptParagraphs(sentences) {
  // Re-group the flat sentence list into the source <p> paragraphs: sentences
  // sharing a `paragraph` index belong together. A null index (pre-migration
  // speech) collapses the whole speech to one block — the previous behaviour.
  const blocks = []
  let key
  for (const s of sentences || []) {
    if (blocks.length === 0 || s.paragraph !== key) {
      blocks.push(s.text)
      key = s.paragraph
    } else {
      blocks[blocks.length - 1] += ' ' + s.text
    }
  }
  // The speaker label only ever opens the speech, so strip it from the first
  // block alone.
  if (blocks.length) blocks[0] = stripSpeakerLabel(blocks[0])

  const out = []
  for (const block of blocks) out.push(...splitSegments(block))
  return out
}
