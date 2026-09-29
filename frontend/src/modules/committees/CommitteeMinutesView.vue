<script setup>
// One committee sitting's jegyzőkönyv (§6F / BIZ-15), built to read like a
// plenary sitting day (SessionView) — because it *is* one: a dated sitting,
// with an agenda, speakers and a record. The two pages therefore share their
// furniture, and differ only where the underlying records genuinely differ:
//
//   same   back link + titlebar + share, a top-speaker block, the transcript cut
//          into agenda sections of one row per speech (face, faction chip,
//          share), a sticky agenda outline on wide screens, and prev/next
//          navigation at the foot
//   differ committee minutes carry **no timings** of their own — no per-speech
//          clip, no speaking time — so the toplist ranks by what was said rather
//          than for how long. With no viewer page to link, a speech's address is
//          an anchor on this one (`#sp-<ord>`, BIZ-29), and its text is shown
//          rather than folded away: it is the whole record. And the whole
//          document arrives in one response, so the filters narrow it in place
//          instead of paging.
//
// Where the sitting was streamed, its recording plays **on this page** (BIZ-30),
// beside the record as the proceedings viewer's player sits beside its
// transcript: docked in the side column above the agenda outline on a wide
// screen, pinned under the site header once started on a narrow one. Where the
// recording has also been aligned to the record, it is the viewer's karaoke as
// well — click a sentence to hear it, and the sentence being spoken is
// highlighted and followed down the page (VIE-3/VIE-4). A sentence the alignment
// could not place is shown as plain text, not playable.
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { loadMeta } from '../../store.js'
import { formatDate, formatDuration, segmentSentences } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import YouTubePlayer from '../../components/YouTubePlayer.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import SpeakerLink from '../../components/SpeakerLink.vue'
import ShareButton from '../../components/ShareButton.vue'
import HelpTip from '../../components/HelpTip.vue'
import { clipTitle, titleDate, usePageTitle } from '../../lib/pageTitle.js'

const props = defineProps({ meetingId: { type: String, required: true } })
const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const data = ref(null)
const loading = ref(false)
const error = ref(false)
const speaker = ref('')
const query = ref('')

const shareTitle = computed(() => {
  const d = data.value
  return d ? `${d.committeeName} · ${formatDate(d.heldOn || d.heldAt)}` : ''
})

// Tab title: the record — or, where it has a recording, the sitting — and its
// date, as the server's card has it (og.py `share_committee_minutes`).
usePageTitle(() => {
  const d = data.value
  if (!d?.committeeName) return ''
  const title = t(d.videos?.length ? 'pageTitle.meeting' : 'pageTitle.minutes', { committee: d.committeeName })
  const date = titleDate(d.heldOn || d.heldAt)
  return clipTitle(date ? `${title}, ${date}` : title)
})

// A sitting served for its **recording alone** (BIZ-27): the House had not
// published the jegyzőkönyv yet (or we have not read it), but the stream is up.
// The page is then its cover and the video — every record-derived block simply
// has nothing in it, so the same page renders both states without a second code
// path. `hasMinutes` is absent on an older API, where every sitting served here
// had a record; that reads as "has one", which it did.
const recordingOnly = computed(() => data.value?.hasMinutes === false)

// The attendance block is the one part of the record that is a heading with a
// list under it rather than a list on its own, so it needs telling when it is
// empty — an open <details> promising "who was present" and holding nothing
// reads as a fault rather than as an unpublished record.
const hasParticipants = computed(() => Object.values(
  (data.value && data.value.participants) || {}).some((l) => l.length))

// The toplist ranks by how much was said, so the bars are sized against the
// most — the same decorative scale the sitting day's speaking-time bars use.
// Words, not characters: it is what the row prints, and a bar measuring one
// thing beside a number stating another reads as a sorting bug.
const topMax = computed(
  () => Math.max(1, ...(data.value?.topSpeakers || []).map((s) => s.words || 0)))

// SpeakerLink speaks the proceedings module's snake_case shape; this module is
// camelCase throughout. Mapped here rather than bending either convention. Takes
// a toplist row or a transcript speech alike: both carry the same three fields.
function asSpeaker(s) {
  return { person_id: s.personId, label: s.name, photo_uri: s.photoUri }
}

// The speakers of this sitting, most prolific first — the filter's options.
// Built from the transcript rather than from the attendance list: a sitting's
// guests routinely speak without being listed, and the point of the filter is
// to find what someone *said*.
const speakers = computed(() => {
  const counts = new Map()
  for (const s of (data.value && data.value.transcript) || []) {
    const key = s.name || ''
    if (!key) continue
    const held = counts.get(key) || { name: key, n: 0 }
    held.n += 1
    counts.set(key, held)
  }
  return [...counts.values()].sort((a, b) => b.n - a.n || a.name.localeCompare(b.name))
})

// Accent- and case-insensitive, because that is how a Hungarian reader types a
// search: "kolcsegvetes" has to find "költségvetés".
function fold(s) {
  return (s || '').normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase()
}

const shown = computed(() => {
  const all = (data.value && data.value.transcript) || []
  const q = fold(query.value.trim())
  return all.filter((s) => (!speaker.value || s.name === speaker.value)
    && (!q || fold(s.text).includes(q) || fold(s.name).includes(q)))
})

const filtered = computed(() => !!speaker.value || !!query.value.trim())

// The transcript cut into its sections, so the record reads as the sitting ran:
// an agenda heading, then what was said under it. A section whose every speech
// the filter removed is dropped with them — an empty heading tells the reader
// nothing and, unfiltered, there are none.
const sections = computed(() => {
  if (!data.value) return []
  const bySection = new Map()
  for (const s of shown.value) {
    const key = s.section == null ? -1 : s.section
    if (!bySection.has(key)) bySection.set(key, [])
    bySection.get(key).push(s)
  }
  // A handful of sittings in the corpus print **no speaker names at all** — the
  // clerk ran the chair's words straight under each heading. There is nothing
  // to attribute, so the parser keeps that text as the section's preamble; it
  // is still the record of the sitting, and dropping it because nobody was
  // named would leave the page blank. So a section with a preamble is kept even
  // when it holds no speeches. Never under a filter: the preamble is unsearched
  // text, and it would be the one thing on screen not matching what was typed.
  if (!filtered.value) {
    for (const meta of data.value.sections || []) {
      if (meta.preamble && !bySection.has(meta.ord)) bySection.set(meta.ord, [])
    }
  }
  const out = []
  for (const [ord, speeches] of [...bySection.entries()].sort((a, b) => a[0] - b[0])) {
    const meta = (data.value.sections || []).find((x) => x.ord === ord)
    out.push({
      ord,
      title: meta ? meta.title : null,
      level: meta ? meta.level : 0,
      preamble: !filtered.value && meta ? meta.preamble : null,
      speeches,
    })
  }
  return out
})

// Paragraphs are stored as blank-line-separated text, which is what the PDF's
// own indentation meant; splitting here keeps the markup out of the database.
function paragraphs(text) {
  return (text || '').split(/\n{2,}/).filter((p) => p.trim())
}

// A speech's text as the lines it is read in, as a sitting day's transcript
// reads (SpeechRow): its paragraphs, with the parentheticals the clerk wrote
// into them — "(Szavazás.)", "(Jelzésre:)", "(Derültség.)", "(Gőgös Zoltán: Ez
// nem igaz!)" — lifted out onto italic lines of their own, and a heckle's
// "Name:" split off so it can carry the heckler's face. References and tags stay
// in the sentence ("a 46. § (2a) bekezdése", "Szabó Timea (Párbeszéd)"): the
// segmenter in format.js tells them apart. Each line is either an aside
// `{ aside: true, text, speaker }` or spoken text `{ pieces: [{ text, sep, row }] }`.
//
// A speech's label is never stripped here, as a plenary one is: the parser has
// already taken the speaker off, and a label still at the start is a speaker it
// missed — the one sign on the page of who was really talking.
function plainLines(text) {
  const paras = paragraphs(text).map((t, i) => ({ text: t, paragraph: i }))
  return segmentSentences(paras, { stripLabel: false }).flatMap((p) => p.segments.map((g) =>
    (g.interjection ? { aside: true, text: g.text, speaker: g.speaker } : { pieces: [{ text: g.text }] })))
}

// The same for a timed speech (BIZ-30), whose lines are built from its
// sentences so each piece of spoken text stays a seek into the recording. A
// sentence can hold an aside ("Ki nem? (Szavazás. - Nincs ilyen.)") and an aside
// can run over several, so the parenthesis state is threaded sentence to
// sentence and a line breaks wherever an aside is lifted out or a paragraph
// ends. `anchor` marks the first thing each sentence puts on the page — the
// element the karaoke follows down it.
function timedLines(paras) {
  const rows = paras.flatMap((p) => p.rows)
  const lines = []
  let line = null
  segmentSentences(rows.map((r) => ({ ...r, paragraph: r.para })), { stripLabel: false })
    .forEach((r, i) => {
      if (i && r.para !== rows[i - 1].para) line = null
      r.segments.forEach((g, j) => {
        const anchor = j === 0
        if (g.interjection) {
          lines.push({ aside: true, text: g.text, speaker: g.speaker, row: rows[i], anchor })
          line = null
          return
        }
        const sep = line ? (anchor ? r.sep : ' ') : ''
        if (!line) { line = { pieces: [] }; lines.push(line) }
        line.pieces.push({ text: g.text, sep, row: rows[i], anchor })
      })
    })
  return lines
}

// --- the recording and its karaoke (BIZ-30) --------------------------------
const hasVideos = computed(() => !!data.value?.videos?.length)
const playerRef = ref(null)
const sideEl = ref(null)
const playerEngaged = ref(false)
const playerPlaying = ref(false)
// The sentence being spoken, as `<speech ord>-<sentence index>` — also the
// suffix of its element id, so following it down the page is one lookup.
const activeKey = ref(null)
const activeOrd = computed(() =>
  activeKey.value == null ? null : Number(activeKey.value.split('-')[0]))

// A timed speech's sentences grouped back into its paragraphs, built once per
// sitting rather than in the template (which re-renders on every playback tick).
const sentenceParas = computed(() => {
  const out = new Map()
  for (const s of data.value?.transcript || []) {
    if (!s.sentences?.length) continue
    const paras = []
    s.sentences.forEach((sent, i) => {
      const row = { ...sent, key: `${s.ord}-${i}` }
      const last = paras[paras.length - 1]
      if (last && last.para === sent.para) last.rows.push(row)
      else paras.push({ para: sent.para, rows: [row] })
    })
    out.set(s.ord, paras)
  }
  return out
})
// Every playable sentence in document order — which is also time order, the
// alignment keeping the sentences' times non-decreasing across the sitting.
const timedList = computed(() => {
  const out = []
  for (const s of data.value?.transcript || []) {
    for (const p of sentenceParas.value.get(s.ord) || []) {
      for (const r of p.rows) {
        if (r.timeStart != null) {
          out.push({ key: r.key, ord: s.ord, videoId: r.videoId,
                     start: r.timeStart, end: r.timeEnd ?? r.timeStart })
        }
      }
    }
  }
  return out
})
// Every speech's lines, built once per sitting for the same reason.
const bodies = computed(() => {
  const out = new Map()
  for (const s of data.value?.transcript || []) {
    const paras = sentenceParas.value.get(s.ord)
    out.set(s.ord, paras ? timedLines(paras) : plainLines(s.text))
  }
  return out
})

// name -> { person_id, label, photo_uri } for the heckles whose "Name:" is a
// representative's: those get a face and a link, the rest stay text.
// Best-effort, as on a sitting day: a failure leaves every heckle as text.
const hecklers = ref({})
async function resolveHecklers(mine) {
  hecklers.value = {}
  const names = [...bodies.value.values()].flat().filter((l) => l.speaker).map((l) => l.speaker)
  if (!names.length) return
  try {
    const resolved = await api.resolveSpeakers(names)
    if (mine === seq) hecklers.value = resolved
  } catch { /* keep plain */ }
}

function firstTimed(s) {
  return timedList.value.find((x) => x.ord === s.ord) || null
}
// Where the play button starts: the speech the address names, if it is timed
// (the reader followed a link to that speech), else the top of the recording.
const startAt = computed(() => {
  if (targetOrd.value == null) return null
  const x = timedList.value.find((y) => y.ord === targetOrd.value)
  return x ? { videoId: x.videoId, t: x.start } : null
})

// A short pause between two sentences keeps the last one lit rather than
// blinking the highlight off and on at every breath.
const HOLD_S = 2
function onTime({ videoId, t }) {
  let key = null
  for (const x of timedList.value) {
    if (x.videoId !== videoId) continue
    if (x.start > t) break
    if (t < x.end + HOLD_S) key = x.key
  }
  if (key !== activeKey.value) {
    activeKey.value = key
    if (key && autoFollow) scrollToActive()
  }
}

function playSentence(sent) {
  if (sent.timeStart == null) return
  autoFollow = true
  activeKey.value = sent.key
  playerRef.value?.seek(sent.videoId, sent.timeStart, true)
}
function playSpeech(s) {
  const x = firstTimed(s)
  if (!x) return
  autoFollow = true
  activeKey.value = x.key
  playerRef.value?.seek(x.videoId, x.start, true)
}

// Karaoke follow, as the viewer does it: a smooth scroll fires scroll events of
// its own, which must not read as the reader taking over — so they are ignored
// for a moment after each programmatic scroll, and a real scroll pauses
// following for a few seconds.
let autoFollow = true
let suppressScrollUntil = 0
let followTimer = null
// Centred in what is left of the window below the site header — and below the
// player, where a narrow screen has it pinned over the top of the record.
function scrollToActive() {
  const el = document.getElementById('cs-' + activeKey.value)
  if (!el) return
  let top = headerH.value
  if (sideEl.value && playerEngaged.value
      && window.matchMedia('(max-width: 1099.98px)').matches) {
    top = Math.max(top, sideEl.value.getBoundingClientRect().bottom)
  }
  const rect = el.getBoundingClientRect()
  const room = window.innerHeight - top
  const y = window.scrollY + rect.top - top - Math.max(0, (room - rect.height) / 2)
  suppressScrollUntil = performance.now() + 900
  const motion = !window.matchMedia('(prefers-reduced-motion: reduce)').matches
  window.scrollTo({ top: Math.max(0, y), behavior: motion ? 'smooth' : 'auto' })
}
function onUserScroll() {
  if (performance.now() < suppressScrollUntil) return
  autoFollow = false
  clearTimeout(followTimer)
  followTimer = setTimeout(() => { autoFollow = true }, 4000)
}

// The docked player sits right under the site header — under what is *visible*
// of it, measured as the page scrolls: its height moves with the width (the nav
// wraps on a phone), and the header does not stay pinned past the first screen
// (`#app` is one viewport tall, styles.css), so neither a fixed offset nor its
// height would be right once the reader is down in the record.
const headerH = ref(0)
function measureHeader() {
  const el = document.querySelector('.site-top')
  headerH.value = el ? Math.max(0, Math.round(el.getBoundingClientRect().bottom)) : 0
}

// The line beside a speaker's name. A faction the shared table knows is drawn as
// its chip instead (`factionColor` set); anything else in that column — a
// guest's ministry, mostly — stays words here.
function speakerLabel(s) {
  const bits = []
  if (s.faction && !s.factionColor) bits.push(s.faction)
  if (s.role) bits.push(s.role)
  if (s.org) bits.push(s.org)
  return bits.join(' · ')
}

// --- a speech's own address (BIZ-29) ---------------------------------------
// A plenary speech is linked by its viewer page; a committee speech has none (no
// clip to play), so it is an anchor on this page — `#sp-<ord>`, the speech's
// place in the document. The router leaves a hash target's scrolling to the page
// (router.js scrollBehavior), so the page brings the speech into view itself,
// once the record has arrived, and marks it for as long as the address names it.
const targetOrd = computed(() => {
  const m = /^#sp-(\d+)$/.exec(route.hash || '')
  return m ? Number(m[1]) : null
})

function speechLocation(s) {
  return { name: 'committee-minutes', params: { meetingId: props.meetingId },
           query: route.query, hash: '#sp-' + s.ord }
}
// Resolved once rather than per speech: a long sitting runs to 200 of them.
const pageUrl = computed(() => location.origin + router.resolve(
  { name: 'committee-minutes', params: { meetingId: props.meetingId }, query: route.query }).href)
function speechUrl(s) { return pageUrl.value + '#sp-' + s.ord }
function speechShareTitle(s) { return `${s.name} · ${shareTitle.value}` }

function revealTarget(smooth) {
  const ord = targetOrd.value
  const d = data.value
  if (ord == null || !d || d.meetingId !== props.meetingId) return
  if (!d.transcript.some((s) => s.ord === ord)) return
  // A filter that hides the linked speech gives way to it: the reader followed a
  // link to that speech, not to what they had typed before.
  if (!shown.value.some((s) => s.ord === ord)) { speaker.value = ''; query.value = '' }
  nextTick(() => {
    const el = document.getElementById('sp-' + ord)
    if (!el) return
    const motion = smooth && !window.matchMedia('(prefers-reduced-motion: reduce)').matches
    el.scrollIntoView({ behavior: motion ? 'smooth' : 'auto', block: 'start' })
  })
}
watch(targetOrd, () => revealTarget(true))

// --- the sticky agenda outline (TOC-1, as on a sitting day) ----------------
// Only the level-0 headings: the sub-entries ("Határozathozatalok") are stages
// within a point, and listing them doubles the outline's length without adding
// a destination anyone is looking for.
const rootEl = ref(null)
const activeSection = ref(null)
const tocEntries = computed(() =>
  (data.value?.sections || []).filter((s) => s.title && !s.level))
const showToc = computed(() => tocEntries.value.length > 1 && !filtered.value)

let spyRaf = 0
function updateActive() {
  spyRaf = 0
  measureHeader()
  const els = rootEl.value ? rootEl.value.querySelectorAll('section.agenda') : []
  if (!els.length) { activeSection.value = null; return }
  let current = els[0].dataset.sectionOrd
  for (const el of els) {
    if (el.getBoundingClientRect().top - 80 <= 0) current = el.dataset.sectionOrd
    else break
  }
  activeSection.value = current
}
function onScroll() {
  if (!spyRaf) spyRaf = requestAnimationFrame(updateActive)
  onUserScroll()
}

function goToSection(ord) {
  const el = document.getElementById('sec-' + ord)
  if (!el) return
  const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches
  el.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' })
  activeSection.value = String(ord)
}

onMounted(() => {
  window.addEventListener('scroll', onScroll, { passive: true })
  window.addEventListener('resize', measureHeader, { passive: true })
  measureHeader()
})
onUnmounted(() => {
  window.removeEventListener('scroll', onScroll)
  window.removeEventListener('resize', measureHeader)
  cancelAnimationFrame(spyRaf)
  clearTimeout(followTimer)
})

let seq = 0
async function load() {
  const mine = ++seq
  loading.value = true; error.value = false
  try {
    const res = await api.committeeMinutes(props.meetingId)
    if (mine === seq) { data.value = res; resolveHecklers(mine) }
  } catch {
    if (mine === seq) error.value = true
  } finally {
    if (mine === seq) loading.value = false
  }
  if (mine === seq) nextTick(() => { revealTarget(false); updateActive() })
}

onMounted(async () => {
  await loadMeta().catch(() => {})
  load()
})
watch(() => props.meetingId, () => {
  speaker.value = ''; query.value = ''
  // The player resets itself on a new recording, but one that is unmounted
  // (a sitting with no video) cannot say so.
  activeKey.value = null; playerEngaged.value = false; playerPlaying.value = false
  window.scrollTo({ top: 0 })
  load()
})
</script>

<template>
  <StateBlock :loading="loading" :error="error" @retry="load">
    <div v-if="data" ref="rootEl">
      <p class="small">
        <RouterLink :to="{ name: 'sessions', query: { tab: 'committees' } }">
          ‹ {{ $t('sessions.tabCommittees') }}
        </RouterLink>
        <span aria-hidden="true"> · </span>
        <RouterLink :to="{ name: 'committee', params: { id: data.committeeId } }">
          {{ data.committeeName }}
        </RouterLink>
      </p>

      <div class="titlebar">
        <h1>
          {{ formatDate(data.heldOn || data.heldAt) }}
          <template v-if="data.numberInYear"> · {{ data.numberInYear }}</template>
          <span class="badge upcoming" v-if="data.closedSession"
                :title="$t('committees.closedSession')">🔒</span>
        </h1>
        <ShareButton :title="shareTitle" align="right" />
      </div>
      <p class="muted small sub">
        {{ data.committeeName }}
        <template v-if="data.venue">
          <span aria-hidden="true"> · </span>{{ data.venue }}
        </template>
        <template v-if="data.openedAt">
          <span aria-hidden="true"> · </span>
          {{ data.openedAt }}<template v-if="data.closedAt">–{{ data.closedAt }}</template>
        </template>
      </p>
      <p class="muted small" v-if="data.url">
        <a :href="data.url" target="_blank" rel="noopener">
          ↗ {{ $t('committees.minutesSource') }}
        </a>
      </p>

      <!-- A partly closed sitting publishes only its open points, so a reader
           counting speeches has to know they are not counting the meeting. -->
      <p v-if="data.closedSession" class="notice small">
        {{ $t('committees.closedSession') }}
      </p>
      <!-- Only the recording exists. Said before it rather than left to the
           absence of everything else: the reader came for the sitting, and
           "there is no record of this yet" is the page's main fact. -->
      <p v-if="recordingOnly" class="notice small">
        {{ $t('committees.recordingOnly') }}
      </p>
      <!-- The document was fetched but could not be read (a scan, a damaged
           file). Said plainly rather than shown as an empty sitting: "we could
           not read this" and "nothing was said" are different findings. -->
      <p v-if="data.error" class="notice small">
        {{ $t('committees.minutesUnavailable') }}
        <span class="muted">({{ data.error }})</span>
      </p>

      <div class="session-body"
           :class="{ 'has-player': hasVideos, engaged: playerEngaged,
                     'player-leads': hasVideos && !data.transcript.length }"
           :style="{ '--header-h': headerH + 'px' }">
        <div class="session-main">
          <!-- Who did the talking — the sitting day's toplist, asked of a record
               that has no timings. -->
          <section v-if="data.topSpeakers && data.topSpeakers.length"
                   class="card pad toplist">
            <div class="sechead">
              <h2 class="wcloud-title">{{ $t('committees.topSpeakers') }}</h2>
              <HelpTip :label="$t('committees.topSpeakers')">
                <p>{{ $t('committees.topSpeakersCaption') }}</p>
              </HelpTip>
            </div>
            <ol class="top-rows">
              <li v-for="sp in data.topSpeakers" :key="(sp.personId || sp.name)"
                  class="top-row">
                <SpeakerLink :speaker="asSpeaker(sp)" size="sm" />
                <span class="small muted fac">{{ sp.faction || '' }}</span>
                <span class="bar-track" aria-hidden="true">
                  <span class="bar-fill"
                        :style="{ width: ((sp.words / topMax) * 100) + '%' }"></span>
                </span>
                <!-- The bar is sized by how much was said, so the figure beside
                     it has to be that too — a speech count there would read as
                     the bar's own scale and contradict it on every row (the
                     chair takes the floor oftenest and says least). -->
                <span class="top-words">{{ $t('committees.words', { count: sp.words }) }}</span>
                <span class="muted small top-count">
                  {{ $t('sessions.speechesCount', { count: sp.speeches }) }}
                </span>
              </li>
            </ol>
          </section>

          <section v-if="data.agenda.length" class="card pad block">
            <h2 class="wcloud-title">{{ $t('committees.agenda') }}</h2>
            <ol class="agenda-list">
              <li v-for="(a, i) in data.agenda" :key="i">
                <span class="atitle">{{ a.title }}</span>
                <!-- Linked only where this deployment holds the document;
                     otherwise the number stands as a plain label (BIZ-11). -->
                <RouterLink
                  v-if="a.held" class="chip"
                  :to="{ name: 'document', params: { id: a.billId } }"
                >{{ a.billNumber }}</RouterLink>
                <span v-else-if="a.billNumber" class="chip muted">{{ a.billNumber }}</span>
                <span v-if="a.notes.length" class="small muted notes">
                  {{ a.notes.join(' · ') }}
                </span>
              </li>
            </ol>
          </section>

          <details v-if="hasParticipants" class="card pad block">
            <summary><strong>{{ $t('committees.participants') }}</strong></summary>
            <div class="pgrid">
              <div v-for="g in ['chair', 'present', 'proxy', 'staff', 'guest']" :key="g">
                <template v-if="data.participants[g] && data.participants[g].length">
                  <h3 class="small muted">{{ $t('committees.' + {
                    chair: 'chairs', present: 'present', proxy: 'proxies',
                    staff: 'staff', guest: 'guests' }[g]) }}</h3>
                  <ul>
                    <li v-for="(p, i) in data.participants[g]" :key="i">
                      <RouterLink
                        v-if="p.personId"
                        :to="{ name: 'profile', params: { id: p.personId } }"
                      >{{ p.name }}</RouterLink>
                      <span v-else>{{ p.name }}</span>
                      <span class="small muted">
                        <template v-if="p.faction"> ({{ p.faction }})</template>
                        <template v-if="p.title"> · {{ p.title }}</template>
                        <template v-if="p.org"> · {{ p.org }}</template>
                        <template v-if="p.proxyName">
                          · {{ $t('committees.proxyHeldBy', { name: p.proxyName }) }}
                        </template>
                      </span>
                    </li>
                  </ul>
                </template>
              </div>
            </div>
          </details>

          <div v-if="data.transcript.length" class="filters">
            <input
              v-model="query" type="search" class="input"
              :placeholder="$t('committees.searchInMinutes')"
              :aria-label="$t('committees.searchInMinutes')"
            />
            <select v-model="speaker" class="input"
                    :aria-label="$t('committees.filterSpeaker')">
              <option value="">{{ $t('committees.allSpeakers') }}</option>
              <option v-for="s in speakers" :key="s.name" :value="s.name">
                {{ s.name }} ({{ s.n }})
              </option>
            </select>
            <span v-if="filtered" class="small muted count">
              {{ $t('committees.matches', { count: shown.length }) }}
            </span>
          </div>

          <!-- Why there is nothing to read. Skipped whole for a sitting we
               hold only a recording of (BIZ-27): that was said above the video,
               and each of these would be a remark about a document this page
               never claimed to have. -->
          <template v-if="!recordingOnly">
            <section v-if="!data.transcript.length && !sections.length"
                     class="card pad empty-day">
              <p>{{ $t('committees.minutesNotParsed') }}</p>
            </section>
            <!-- Text with nobody named against it. Said plainly rather than
                 left to look like an omission: the document really is written
                 that way, and inventing a speaker would be a guess. -->
            <p v-else-if="!data.transcript.length" class="small muted note">
              {{ $t('committees.noSpeakersNamed') }}
            </p>
            <p v-else-if="!shown.length" class="small muted">
              {{ $t('committees.noMatch') }}
            </p>
          </template>

          <section
            v-for="sec in sections" :key="sec.ord" class="agenda card"
            :id="'sec-' + sec.ord" :data-section-ord="sec.ord"
          >
            <h2 v-if="sec.title" class="pad agenda-title" :class="{ sub: sec.level > 0 }">
              {{ sec.title }}
            </h2>
            <p v-if="sec.preamble" class="stage small muted">{{ sec.preamble }}</p>
            <!-- One bubble per speech, headed as a sitting day's speech row is:
                 face, name, faction chip, then the speech's own link and share
                 (BIZ-29). -->
            <ul v-if="sec.speeches.length" class="speeches">
              <li
                v-for="s in sec.speeches" :key="s.ord" :id="'sp-' + s.ord"
                class="speech"
                :class="{ targeted: targetOrd === s.ord, speaking: activeOrd === s.ord }"
              >
                <div class="speech-row">
                  <div class="speech-main">
                    <SpeakerLink :speaker="asSpeaker(s)" />
                    <FactionBadge v-if="s.factionColor"
                                  :faction="{ label: s.faction, color: s.factionColor }" />
                    <span v-if="speakerLabel(s)" class="small muted role">{{ speakerLabel(s) }}</span>
                    <!-- The same person carrying on past a heading without the
                         clerk printing the name again. Said, so the bubble does
                         not read as them taking the floor a second time. -->
                    <span v-if="s.continued" class="badge subtle"
                          :title="$t('committees.speechContinuedNote')">
                      {{ $t('committees.speechContinued') }}
                    </span>
                  </div>
                  <div class="speech-actions">
                    <!-- Play the recording from this speech (BIZ-30). -->
                    <button v-if="sentenceParas.has(s.ord) && firstTimed(s)"
                            type="button" class="permalink play-speech"
                            :title="$t('committees.playFromHere')"
                            :aria-label="$t('committees.playFromHere')"
                            @click="playSpeech(s)">
                      <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"
                           fill="currentColor"><path d="M8 5.5v13l11-6.5z" /></svg>
                    </button>
                    <!-- `replace`: marking one speech after another should not
                         turn Back into a walk through them. -->
                    <RouterLink
                      :to="speechLocation(s)" replace class="permalink"
                      :title="$t('committees.speechLink')"
                      :aria-label="$t('committees.speechLink')"
                    >
                      <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"
                           fill="none" stroke="currentColor" stroke-width="2"
                           stroke-linecap="round" stroke-linejoin="round">
                        <path d="M10 13a5 5 0 0 0 7.07 0l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
                        <path d="M14 11a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
                      </svg>
                    </RouterLink>
                    <ShareButton :title="speechShareTitle(s)" :url="speechUrl(s)"
                                 align="right" compact />
                  </div>
                </div>
                <!-- The speech's lines (see plainLines): spoken text, and the
                     stage directions and heckles lifted out of it in italics — a
                     heckle by a representative with their face. In a timed
                     speech each sentence of the spoken text is a seek into the
                     recording (VIE-3), lit while it is spoken (VIE-4). -->
                <div class="speech-body">
                  <template v-for="(ln, i) in bodies.get(s.ord)" :key="i">
                    <p v-if="ln.aside && ln.speaker && hecklers[ln.speaker]"
                       class="para aside heckle"
                       :id="ln.anchor ? 'cs-' + ln.row.key : undefined">
                      <SpeakerLink :speaker="hecklers[ln.speaker]" size="xs" class="heckle-who" />
                      <span>{{ ln.text }}</span>
                    </p>
                    <p v-else-if="ln.aside" class="para aside"
                       :id="ln.anchor ? 'cs-' + ln.row.key : undefined">
                      {{ ln.speaker ? `${ln.speaker}: ${ln.text}` : ln.text }}
                    </p>
                    <p v-else class="para"><template v-for="(pc, j) in ln.pieces" :key="j">{{ pc.sep }}<span
                            v-if="pc.row && pc.row.timeStart != null"
                            :id="pc.anchor ? 'cs-' + pc.row.key : undefined"
                            class="sent" :class="{ active: activeKey === pc.row.key }"
                            role="button" tabindex="0"
                            :title="formatDuration(pc.row.timeStart)"
                            @click="playSentence(pc.row)"
                            @keydown.enter.prevent="playSentence(pc.row)"
                            @keydown.space.prevent="playSentence(pc.row)">{{ pc.text }}</span><span
                            v-else-if="pc.row" class="sent untimed">{{ pc.text }}</span><template
                            v-else>{{ pc.text }}</template></template></p>
                  </template>
                </div>
              </li>
            </ul>
          </section>

          <!-- Move to the committee's adjacent sitting, as a sitting day moves
               to the next sitting day. Only sittings whose minutes we have read
               are offered — the link goes to this page. -->
          <nav
            v-if="data.neighbours && (data.neighbours.prev || data.neighbours.next)"
            class="day-nav" :aria-label="$t('sessions.sittingNav')"
          >
            <RouterLink
              v-if="data.neighbours.prev" class="day-nav-btn prev"
              :to="{ name: 'committee-minutes',
                     params: { meetingId: data.neighbours.prev.meetingId } }"
            >
              <span class="day-nav-dir">‹ {{ $t('sessions.prevSitting') }}</span>
              <span class="day-nav-date">
                {{ formatDate(data.neighbours.prev.heldAt) }}
                <template v-if="data.neighbours.prev.numberInYear">
                  · {{ data.neighbours.prev.numberInYear }}
                </template>
              </span>
            </RouterLink>
            <span v-else aria-hidden="true"></span>
            <RouterLink
              v-if="data.neighbours.next" class="day-nav-btn next"
              :to="{ name: 'committee-minutes',
                     params: { meetingId: data.neighbours.next.meetingId } }"
            >
              <span class="day-nav-dir">{{ $t('sessions.nextSitting') }} ›</span>
              <span class="day-nav-date">
                {{ formatDate(data.neighbours.next.heldAt) }}
                <template v-if="data.neighbours.next.numberInYear">
                  · {{ data.neighbours.next.numberInYear }}
                </template>
              </span>
            </RouterLink>
            <span v-else aria-hidden="true"></span>
          </nav>

          <details class="methodology">
            <summary>{{ $t('factions.methodology') }}</summary>
            <p class="small muted">{{ $t('committees.methodology') }}</p>
          </details>
        </div>

        <aside v-if="showToc || hasVideos" ref="sideEl" class="session-toc">
          <div class="side-inner">
          <!-- The recording (BIZ-30), above the outline so both stay in reach
               while the record scrolls past. -->
          <section v-if="hasVideos" class="side-player" :aria-label="$t('committees.videos')">
            <YouTubePlayer ref="playerRef" :videos="data.videos" :start-at="startAt"
                           :label="data.committeeName"
                           @time="onTime" @engaged="playerEngaged = $event"
                           @playing="playerPlaying = $event" />
            <p v-if="data.timing" class="small muted sync-note">
              {{ $t('committees.syncNote') }}
            </p>
          </section>
          <nav v-if="showToc" class="toc-inner" :aria-label="$t('sessions.agenda')">
            <p class="toc-title">{{ $t('sessions.agenda') }}</p>
            <ol class="toc-list">
              <li v-for="(s, i) in tocEntries" :key="s.ord">
                <a
                  :href="'#sec-' + s.ord" class="toc-link"
                  :class="{ active: activeSection === String(s.ord) }"
                  :aria-current="activeSection === String(s.ord) ? 'true' : undefined"
                  @click.prevent="goToSection(s.ord)"
                >
                  <span class="toc-num" aria-hidden="true">{{ i + 1 }}</span>
                  <span class="toc-text">{{ s.title }}</span>
                </a>
              </li>
            </ol>
          </nav>
          </div>
        </aside>
      </div>
    </div>
  </StateBlock>
</template>

<style scoped>
/* The two-column shell of a sitting day: transcript + sticky agenda outline.
   Single column by default; the outline appears once there is room, and on very
   wide screens it spills into the right gutter so the transcript keeps its full
   width and stays aligned with the header. */
.session-body { display: block; }
.session-toc { display: none; }
@media (min-width: 1100px) {
  .session-body {
    --toc-w: 240px;
    --toc-gap: 2rem;
    display: grid;
    grid-template-columns: minmax(0, 1fr) var(--toc-w);
    gap: var(--toc-gap);
    align-items: start;
  }
  /* A player needs a column wide enough to watch in (BIZ-30). */
  .session-body.has-player { --toc-w: 360px; }
  .session-toc { display: block; }
}
@media (min-width: 1650px) {
  .session-body:not(.has-player) { margin-right: calc(-1 * (var(--toc-w) + var(--toc-gap))); }
}
/* The wider column only fits in the right gutter on a wider screen. */
@media (min-width: 1900px) {
  .session-body.has-player { margin-right: calc(-1 * (var(--toc-w) + var(--toc-gap))); }
}
.session-toc { align-self: stretch; }
/* The side column's content — player, then outline — sticks as one, so the
   outline scrolls inside whatever height the player leaves it. */
.side-inner {
  position: sticky; top: calc(var(--header-h, 0px) + 1rem);
  max-height: calc(100vh - var(--header-h, 0px) - 2rem);
  display: flex; flex-direction: column; gap: .8rem;
}
.side-player { flex: none; }
.sync-note { margin: .35rem 0 0; }
.toc-inner {
  flex: 0 1 auto; min-height: 0;
  overflow-y: auto; overscroll-behavior: contain;
  background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius); box-shadow: var(--shadow);
  padding: .85rem 1rem 1rem;
}
/* Narrow screens: no outline, and the player leads the page instead — pinned
   under the site header once the reader has started it, so it stays in view
   while the record scrolls beneath it (the viewer's mobile pane, VIE-4). Its
   width is capped so a pinned player never takes more than ~40% of the height. */
@media (max-width: 1099.98px) {
  .session-body.has-player { display: flex; flex-direction: column; }
  .session-body.has-player .session-toc { display: block; order: -1; margin-bottom: 1rem; }
  .session-body.has-player .toc-inner { display: none; }
  .session-body.has-player .side-inner { position: static; max-height: none; }
  .session-body.has-player .side-player { width: 100%; max-width: calc(40vh * 16 / 9); }
  .session-body.has-player.engaged:not(.player-leads) .session-toc {
    position: sticky; top: var(--header-h, 0px); z-index: 10;
    margin: 0 calc(-1 * var(--gutter)) 1rem; padding: .5rem var(--gutter);
    background: var(--bg); box-shadow: 0 6px 8px -8px rgba(0, 0, 0, .35);
  }
  .session-body.has-player.engaged .sync-note { display: none; }
}
/* A sitting with a recording and no record to read beside it (BIZ-27): the
   player is the page, so it leads the main column at a watchable size rather
   than sitting in the side column next to an empty one. */
.session-body.player-leads { display: flex; flex-direction: column; margin-right: 0; }
.session-body.player-leads .session-toc { display: block; order: -1; margin-bottom: 1rem; }
.session-body.player-leads .side-inner { position: static; max-height: none; }
.session-body.player-leads .side-player { width: 100%; max-width: 760px; }
.toc-title {
  margin: 0 0 .5rem; padding-bottom: .5rem; border-bottom: 1px solid var(--line);
  font-size: .72rem; font-weight: 700; letter-spacing: .06em;
  text-transform: uppercase; color: var(--ink-faint);
}
.toc-list { list-style: none; margin: 0; padding: 0; }
.toc-link {
  display: flex; gap: .55rem; align-items: baseline;
  margin: 0 -.5rem; padding: .32rem .5rem;
  color: var(--ink-soft); font-size: .86rem; line-height: 1.35;
  border-radius: 6px; border-left: 2px solid transparent;
  transition: background .12s, color .12s, border-color .12s;
}
.toc-link:hover { background: var(--bg); color: var(--ink); text-decoration: none; }
.toc-link.active {
  background: var(--accent-soft); color: var(--accent); font-weight: 600;
  border-left-color: var(--accent);
}
.toc-num {
  flex: none; color: var(--ink-faint); font-variant-numeric: tabular-nums;
  font-size: .78rem; min-width: 1.3em; text-align: right;
}
.toc-link.active .toc-num { color: var(--accent); }
.toc-text { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

.titlebar { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.titlebar h1 { margin-bottom: 0; }
.sub { margin: .2rem 0 .1rem; }
.badge.upcoming {
  font-size: .72rem; font-weight: 600; vertical-align: middle;
  padding: .15rem .55rem; border-radius: 999px;
  background: var(--accent-soft); color: var(--ink-soft);
}
.notice {
  border-left: 3px solid var(--accent); padding: .35rem .6rem; margin: .5rem 0;
}
.block { margin-bottom: 1rem; }
.block summary { cursor: pointer; }
.sechead { display: flex; align-items: center; gap: .35rem; margin-bottom: .6rem; }
.sechead .wcloud-title { margin: 0; }
.wcloud-title { font-size: 1.05rem; margin: 0 0 .6rem; }
.toplist { margin-bottom: 1rem; }
.top-rows { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: minmax(130px, 1.5fr) auto 1fr auto auto; row-gap: .24rem; }
.top-row { display: grid; grid-template-columns: subgrid; grid-column: 1 / -1; column-gap: .55rem; align-items: center; padding: .12rem 0; font-size: .9rem; }
.top-row :deep(.row span) { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.top-row .bar-track { background: #eceae4; border-radius: 5px; height: 9px; overflow: hidden; }
.top-row .bar-fill { display: block; height: 100%; border-radius: 5px; min-width: 2px; background: var(--accent); }
.top-row .fac { white-space: nowrap; }
.top-words { font-size: .82rem; font-variant-numeric: tabular-nums; color: var(--ink); white-space: nowrap; }
.top-count { white-space: nowrap; }
@media (max-width: 560px) {
  .top-rows { grid-template-columns: 1fr auto; column-gap: .5rem; }
  .top-row .bar-track { grid-column: 1 / -1; }
}
.agenda-list { margin: 0; padding-left: 1.2rem; display: grid; gap: .5rem; }
.agenda-list .atitle { overflow-wrap: anywhere; }
.agenda-list .notes { display: block; }
/* Scoped to the agenda on purpose: an unqualified `.chip` also matches the root
   of the FactionBadge child component (scoped CSS reaches a child's root), which
   squashed every speech's faction chip into this bordered pill — the same trap
   SessionView's `.chips .chip` notes. */
.agenda-list .chip {
  display: inline-block; margin-left: .4rem; font-size: .75rem;
  border: 1px solid var(--line); border-radius: .6rem; padding: 0 .4rem;
}
.pgrid {
  display: grid; gap: .8rem; margin-top: .6rem;
  grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
}
.pgrid h3 { margin: 0 0 .2rem; font-size: .8rem; text-transform: lowercase; }
.pgrid ul { list-style: none; padding: 0; margin: 0; display: grid; gap: .15rem; }
.filters { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; margin-bottom: 1rem; }
.filters .input { flex: 1 1 14rem; min-width: 0; }
.filters .count { flex: 0 0 auto; }
.empty-day { color: var(--ink-soft); text-align: center; }
/* scroll-margin keeps a jumped-to section clear of the sticky site header. */
.agenda { margin-bottom: 1rem; scroll-margin-top: 72px; }
.agenda-title {
  font-size: 1.05rem; margin: 0; border-bottom: 1px solid var(--line);
  display: flex; gap: .6rem; align-items: center; flex-wrap: wrap;
}
/* A stage within an agenda point ("Határozathozatalok"), not a point of its
   own — quieter, and absent from the outline. */
.agenda-title.sub { font-size: .92rem; color: var(--ink-soft); }
.stage { margin: .8rem 1rem; font-style: italic; }
/* Each speech a bubble of its own, its head laid out as SpeechRow's row is so
   the two chambers' transcripts read alike. */
.speeches { list-style: none; margin: 0; padding: .6rem; display: grid; gap: .5rem; }
.speech {
  border: 1px solid var(--line); border-radius: var(--radius);
  scroll-margin-top: 72px;
  transition: border-color .12s, box-shadow .12s;
}
.speech-row { display: flex; align-items: center; gap: .8rem; padding: .5rem .7rem; border-radius: var(--radius) var(--radius) 0 0; }
.speech-main { flex: 1 1 auto; min-width: 0; display: flex; align-items: center; flex-wrap: wrap; gap: .4rem .8rem; }
.speech-main .role { overflow-wrap: anywhere; }
/* The face and the name stay one unit: a long name wraps beside the avatar
   rather than dropping below it (SpeakerLink's `.row` wraps by default). */
.speech-main .row { flex-wrap: nowrap; min-width: 0; }
.speech-actions { flex: 0 0 auto; display: flex; align-items: center; gap: .15rem; }
.permalink {
  display: inline-flex; align-items: center; justify-content: center;
  width: 1.9rem; height: 1.9rem; border-radius: 6px; color: var(--ink-faint);
}
.permalink:hover, .permalink:focus-visible { background: var(--line); color: var(--accent); text-decoration: none; }
.badge.subtle { background: var(--line); color: var(--ink-faint); font-weight: 400; cursor: help; }
/* The text sits under the speaker's name, past the avatar, as a sitting day's
   opened transcript does. */
.speech-body { padding: 0 1rem .75rem calc(.7rem + 48px + .6rem); }
.para { margin: 0 0 .6rem; line-height: 1.6; overflow-wrap: anywhere; }
.para:last-child { margin-bottom: 0; }
/* The stage directions and heckles lifted out of the record's parentheses:
   italic, in the softer (still AA) tone, so they read as asides rather than as
   what was said — as a sitting day's transcript sets them (SpeechRow). */
.para.aside { font-style: italic; color: var(--ink-soft); }
/* A heckle by a known representative: a small face and linked name before the
   remark, kept smaller than the speech so it never reads as one. */
.para.heckle { display: flex; align-items: center; gap: .45rem; font-size: .9em; }
.heckle-who { flex: none; flex-wrap: nowrap; font-style: normal; font-weight: 600; gap: .35rem !important; }
/* A timed sentence (BIZ-30): a seek into the recording, lit while it is spoken.
   Inline, so the paragraph still reads as prose. */
.sent { border-radius: 4px; transition: background .12s; }
.sent[role="button"] { cursor: pointer; }
.sent[role="button"]:hover { background: #f0eee8; }
.sent[role="button"]:focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }
.sent.active { background: var(--accent-soft); color: #5a121a; box-decoration-break: clone; -webkit-box-decoration-break: clone; }
.speech.speaking { border-color: var(--accent); }
.play-speech { border: 0; background: transparent; cursor: pointer; padding: 0; }
/* The speech the address names (BIZ-29). */
.speech.targeted { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.speech.targeted .speech-row { background: var(--accent-soft); }
/* On a phone the avatar indent would cost a fifth of the line; the text takes
   the bubble's full width instead, and the actions stay pinned top-right beside
   a name that wraps. */
@media (max-width: 560px) {
  .speeches { padding: .4rem; }
  .speech-row { align-items: flex-start; gap: .5rem; padding: .5rem .6rem; }
  .speech-body { padding: .1rem .75rem .7rem; }
}
.day-nav { display: flex; justify-content: space-between; gap: 1rem; margin-top: 1.5rem; }
.day-nav-btn {
  display: flex; flex-direction: column; gap: .12rem; max-width: 47%;
  padding: .55rem .85rem; border: 1px solid var(--line); border-radius: var(--radius);
  background: var(--surface); box-shadow: var(--shadow); text-decoration: none;
  transition: border-color .12s, background .12s;
}
.day-nav-btn:hover, .day-nav-btn:focus-visible { border-color: var(--accent); text-decoration: none; }
.day-nav-btn.next { text-align: right; align-items: flex-end; }
.day-nav-dir { font-size: .78rem; font-weight: 600; color: var(--accent); }
.day-nav-date { font-size: .92rem; color: var(--ink); }
.methodology { margin-top: 1.5rem; }
.methodology summary { cursor: pointer; font-weight: 600; color: var(--ink-soft); font-size: .85rem; }
</style>
