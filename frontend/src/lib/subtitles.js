// Subtitle generation for the clip exporter (VIE-10).
//
// The cue text is always the OFFICIAL transcript (the sentence records — never
// an ASR hypothesis, cf. TIM-1); the cue times are the sentences' stored
// day-absolute `time_start`/`time_end` rebased to the clip's t=0 (the export
// window start). Only sentences that overlap the window are emitted.
//
// A whole sentence can be long (parliamentary sentences routinely run 200+
// chars), which as a single cue fills half the frame. So each sentence is broken
// into short caption chunks (~2 lines each), with the sentence's own time span
// distributed across them by character length — captions that track the speech
// but stay small on screen.
import { segmentSentences } from '../format.js'

// Roughly two lines at the caption font size. A chunk may be flushed earlier at a
// clause boundary once it has passed SOFT_BREAK, so breaks land on punctuation.
const MAX_CUE_CHARS = 74
const SOFT_BREAK = 36
const CLAUSE_END = /[,;:—–]$/

// day-absolute seconds -> "HH:MM:SS,mmm" (SRT) or "HH:MM:SS.mmm" (VTT).
function stamp(seconds, sep) {
  const ms = Math.max(0, Math.round(seconds * 1000))
  const h = Math.floor(ms / 3600000)
  const m = Math.floor((ms % 3600000) / 60000)
  const s = Math.floor((ms % 60000) / 1000)
  const f = ms % 1000
  const p2 = (n) => String(n).padStart(2, '0')
  return `${p2(h)}:${p2(m)}:${p2(s)}${sep}${String(f).padStart(3, '0')}`
}

// Break one sentence's text into short chunks on word boundaries, preferring to
// end a chunk at a clause boundary (comma/dash) once it is reasonably full.
function chunkText(text) {
  const words = text.split(/\s+/).filter(Boolean)
  const chunks = []
  let cur = ''
  for (const w of words) {
    const tentative = cur ? `${cur} ${w}` : w
    if (cur && tentative.length > MAX_CUE_CHARS) { chunks.push(cur); cur = w }
    else { cur = tentative }
    // Flush at a clause boundary once the chunk carries enough to be worth a cue.
    if (cur.length >= SOFT_BREAK && CLAUSE_END.test(cur)) { chunks.push(cur); cur = '' }
  }
  if (cur) chunks.push(cur)
  return chunks.length ? chunks : ['']
}

// Split a sentence [start,end] into timed sub-cues, its duration shared across
// the chunks in proportion to their length (longer chunk → more screen time).
function sentenceCues(text, start, end) {
  const chunks = chunkText(text)
  if (chunks.length === 1) return [{ start, end, text: chunks[0] }]
  const totalChars = chunks.reduce((a, c) => a + c.length, 0) || 1
  const span = end - start
  const out = []
  let t = start
  for (const c of chunks) {
    const cd = span * (c.length / totalChars)
    out.push({ start: t, end: t + cd, text: c })
    t += cd
  }
  return out
}

// The caption cues within [windowStart, windowEnd], each rebased to the clip's
// t=0 and clamped to the clip. Returns [{ start, end, text }].
export function cuesForWindow(sentences, windowStart, windowEnd) {
  const dur = windowEnd - windowStart
  const cues = []
  // Parse with the SAME logic the viewer uses (format.js): the speaker label is
  // stripped on sentence 0, parenthetical stage directions / heckles ("(Taps.)",
  // "(Zaj. ‑ …)") are lifted out as asides — even when a parenthetical SPANS
  // sentence boundaries (carry state threaded across the list) — while numeric/
  // date references like "(2)" stay inline. Captions then carry only the spoken
  // words. Run over ALL sentences so the cross-sentence paren carry is correct
  // even when the window starts mid-speech.
  const segmented = segmentSentences(sentences || [])
  for (const s of segmented) {
    if (s.time_start == null) continue
    const end = s.time_end != null ? s.time_end : s.time_start + 4
    if (end <= windowStart || s.time_start >= windowEnd) continue
    const text = (s.segments || [])
      .filter((g) => !g.interjection)
      .map((g) => g.text)
      .join(' ')
      .replace(/\s+/g, ' ')
      .replace(/^[\s‑–—-]+|[\s‑–—-]+$/g, '')   // drop stray edge dashes (heckle separators)
      .trim()
    // Skip fragments with no actual words (e.g. a lone „ opening a quote whose
    // body fell into the next sentence) — they'd just flash on screen.
    if (!/[\p{L}\p{N}]/u.test(text)) continue
    for (const c of sentenceCues(text, s.time_start, end)) {
      // Keep only sub-cues that actually fall inside the export window.
      if (c.end <= windowStart || c.start >= windowEnd) continue
      cues.push({
        start: Math.max(0, c.start - windowStart),
        end: Math.min(dur, c.end - windowStart),
        text: c.text,
      })
    }
  }
  return cues
}

export function buildSrt(sentences, windowStart, windowEnd) {
  const cues = cuesForWindow(sentences, windowStart, windowEnd)
  return cues
    .map((c, i) => `${i + 1}\n${stamp(c.start, ',')} --> ${stamp(c.end, ',')}\n${c.text}\n`)
    .join('\n')
}

export function buildVtt(sentences, windowStart, windowEnd) {
  const cues = cuesForWindow(sentences, windowStart, windowEnd)
  const body = cues
    .map((c) => `${stamp(c.start, '.')} --> ${stamp(c.end, '.')}\n${c.text}`)
    .join('\n\n')
  return `WEBVTT\n\n${body}\n`
}
