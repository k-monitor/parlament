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
  if (!seconds) return '0p'
  const lang = i18n.global.locale.value
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

// A top-level "(…)" parenthetical (no nesting expected in the transcripts).
const PARENTHETICAL = /\(([^()]+)\)/g

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

// Push a spoken (non-interjection) segment onto `out`, returning the pushed
// paragraph (or `prev` unchanged when the segment is empty). If the segment
// leads with orphaned punctuation (see LEADING_PUNCT) and a previous spoken
// paragraph exists, that punctuation is lifted onto the end of `prev` so the
// break reads "…dobják ki," / "arra adtak…" instead of "…dobják ki" / ", arra
// adtak…".
function pushSpoken(out, raw, prev) {
  let text = raw.trim()
  if (prev) {
    const lead = text.match(LEADING_PUNCT)
    if (lead) {
      prev.text += lead[0]
      text = text.slice(lead[0].length).trimStart()
    }
  }
  if (!text) return prev
  const para = { text, interjection: false }
  out.push(para)
  return para
}

// Split ONE block of text (a source paragraph, or a single karaoke sentence)
// into ordered segments: `[{ text, interjection, speaker }]`. Parentheticals
// become `interjection: true` segments (stage directions / named heckles, with
// `speaker` set when a "Name:" prefix was recognised); numeric & date references
// stay inline in the spoken text; punctuation orphaned after a dropped
// parenthetical is reattached to the preceding spoken segment. Shared by the
// flowing spoiler (per source paragraph) and the karaoke viewer (per sentence).
export function splitSegments(block) {
  const out = []
  let last = 0, m
  // Last spoken segment in THIS block, so orphaned leading punctuation on a
  // post-interjection continuation reattaches to it — never across a block/
  // sentence boundary.
  let prevSpoken = null
  PARENTHETICAL.lastIndex = 0
  while ((m = PARENTHETICAL.exec(block)) !== null) {
    // A reference like "(2)" or "(V. 9.)" is part of the speech, not a heckle:
    // skip the match so it stays in the surrounding spoken text (`last` is left
    // untouched, so the next slice swallows it).
    const inner = m[1].trim()
    if (/\d/.test(inner) && REFERENCE_PAREN.test(inner)) continue
    prevSpoken = pushSpoken(out, block.slice(last, m.index), prevSpoken)
    for (const part of m[1].split(INTERJECTION_SEP)) {
      const t = part.trim()
      if (!t) continue
      const { speaker, text } = splitInterjectionSpeaker(t)
      out.push({ text, interjection: true, speaker })
    }
    last = m.index + m[0].length
  }
  pushSpoken(out, block.slice(last), prevSpoken)
  return out
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
