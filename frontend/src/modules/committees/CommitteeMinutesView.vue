<script setup>
// One committee sitting's jegyzőkönyv (§6F / BIZ-15), built to read like a
// plenary sitting day (SessionView) — because it *is* one: a dated sitting,
// with an agenda, speakers and a record. The two pages therefore share their
// furniture, and differ only where the underlying records genuinely differ:
//
//   same   back link + titlebar + share, a top-speaker block, the transcript cut
//          into agenda sections, a sticky agenda outline on wide screens, and
//          prev/next navigation at the foot
//   differ committee minutes carry **no timings** — no per-speech clip, no
//          speaking time — so the toplist ranks by what was said rather than for
//          how long, and no speech is playable. And the whole document arrives
//          in one response, so the filters narrow it in place instead of paging.
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../../api.js'
import { loadMeta } from '../../store.js'
import { formatDate, formatSpeakingTime } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import SpeakerLink from '../../components/SpeakerLink.vue'
import ShareButton from '../../components/ShareButton.vue'
import HelpTip from '../../components/HelpTip.vue'

const props = defineProps({ meetingId: { type: String, required: true } })
const { t } = useI18n()

const data = ref(null)
const loading = ref(false)
const error = ref(false)
const speaker = ref('')
const query = ref('')

const shareTitle = computed(() => {
  const d = data.value
  return d ? `${d.committeeName} · ${formatDate(d.heldOn || d.heldAt)}` : ''
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
// camelCase throughout. Mapped here rather than bending either convention.
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

function speakerLabel(s) {
  const bits = []
  if (s.faction) bits.push(s.faction)
  if (s.role) bits.push(s.role)
  if (s.org) bits.push(s.org)
  return bits.join(' · ')
}

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
  const els = rootEl.value ? rootEl.value.querySelectorAll('section.agenda') : []
  if (!els.length) { activeSection.value = null; return }
  let current = els[0].dataset.sectionOrd
  for (const el of els) {
    if (el.getBoundingClientRect().top - 80 <= 0) current = el.dataset.sectionOrd
    else break
  }
  activeSection.value = current
}
function onScroll() { if (!spyRaf) spyRaf = requestAnimationFrame(updateActive) }

function goToSection(ord) {
  const el = document.getElementById('sec-' + ord)
  if (!el) return
  const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches
  el.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' })
  activeSection.value = String(ord)
}

onMounted(() => window.addEventListener('scroll', onScroll, { passive: true }))
onUnmounted(() => {
  window.removeEventListener('scroll', onScroll)
  cancelAnimationFrame(spyRaf)
})

let seq = 0
async function load() {
  const mine = ++seq
  loading.value = true; error.value = false
  try {
    const res = await api.committeeMinutes(props.meetingId)
    if (mine === seq) data.value = res
  } catch {
    if (mine === seq) error.value = true
  } finally {
    if (mine === seq) loading.value = false
  }
  if (mine === seq) nextTick(updateActive)
}

onMounted(async () => {
  await loadMeta().catch(() => {})
  load()
})
watch(() => props.meetingId, () => {
  speaker.value = ''; query.value = ''
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
        <RouterLink :to="{ name: 'committee', params: { id: data.committeeId },
                           query: { tab: 'meetings' } }">
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

      <div class="session-body">
        <div class="session-main">
          <div v-if="data.videos && data.videos.length" class="vidrow">
            <a v-for="v in data.videos" :key="v.videoId" class="card pad vid"
               :href="v.url" target="_blank" rel="noopener">
              <img v-if="v.thumbnail" :src="v.thumbnail" alt="" loading="lazy" />
              <span class="vidmeta">
                <strong>▶ {{ $t('committees.watch') }} ↗</strong>
                <span class="small muted">
                  <template v-if="v.continued">{{ $t('committees.videoPart') }} · </template>
                  <template v-if="v.durationS">{{ formatSpeakingTime(v.durationS) }}</template>
                </span>
              </span>
            </a>
          </div>

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
            <div class="speeches">
              <p v-if="sec.preamble" class="stage small muted">{{ sec.preamble }}</p>
              <article v-for="s in sec.speeches" :key="s.ord" class="speech">
                <!-- A `continued` speech is the same person carrying on past the
                     heading without their name being printed again; repeating
                     the name would read as someone taking the floor. Under a
                     filter the name is always shown, because the speech it
                     continues may not be on screen. -->
                <p v-if="!s.continued || filtered" class="who">
                  <RouterLink
                    v-if="s.personId"
                    :to="{ name: 'profile', params: { id: s.personId } }"
                  >{{ s.name }}</RouterLink>
                  <span v-else>{{ s.name }}</span>
                  <span v-if="speakerLabel(s)" class="small muted">
                    · {{ speakerLabel(s) }}
                  </span>
                </p>
                <p v-for="(para, i) in paragraphs(s.text)" :key="i" class="para">
                  {{ para }}
                </p>
              </article>
            </div>
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

        <aside v-if="showToc" class="session-toc">
          <nav class="toc-inner" :aria-label="$t('sessions.agenda')">
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
  .session-toc { display: block; }
}
@media (min-width: 1650px) {
  .session-body { margin-right: calc(-1 * (var(--toc-w) + var(--toc-gap))); }
}
.session-toc { align-self: stretch; }
.toc-inner {
  position: sticky; top: 72px;
  max-height: calc(100vh - 72px - 1rem);
  overflow-y: auto; overscroll-behavior: contain;
  background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius); box-shadow: var(--shadow);
  padding: .85rem 1rem 1rem;
}
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
.chip {
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
.vidrow { display: flex; flex-wrap: wrap; gap: .5rem; margin-bottom: 1rem; }
.vid {
  display: flex; gap: .6rem; align-items: center; text-decoration: none;
  border: 1px solid var(--line); color: inherit;
}
.vid:hover { border-color: var(--accent); }
.vid img { width: 8rem; height: auto; border-radius: .3rem; display: block; }
.vidmeta { display: flex; flex-direction: column; gap: .1rem; }
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
.speeches { padding: .3rem 1rem 1rem; }
.stage { margin: .6rem 0; font-style: italic; }
.speech { margin: 0 0 1rem; }
.who { margin: .6rem 0 .2rem; font-weight: 600; }
.para { margin: 0 0 .5rem; overflow-wrap: anywhere; }
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
@media (max-width: 720px) {
  .vid img { width: 5.5rem; }
}
</style>
