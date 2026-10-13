<script setup>
// One scope's hits on the search page (SEA-14), drawn the way that scope's
// records are drawn everywhere else: a transcript excerpt for the plenary, the
// minutes' snippet for a committee sitting, and the list pages' own cards for
// irományok, votes and people. The page uses it for its single full result
// list and for each section of a search across several scopes alike.
//
// Emits `hit` with the 0-based index of a result the reader opens — the page
// counts it onto the scope's own search-quality row (SEA-12).
import { computed } from 'vue'
import { formatDate, formatDateLocal, searchExcerptLines } from '../../format.js'
import FactionBadge from '../../components/FactionBadge.vue'
import SpeakerLink from '../../components/SpeakerLink.vue'
import TimingBadge from '../../components/TimingBadge.vue'
import DocumentCard from '../../components/DocumentCard.vue'
import VoteCard from '../../components/VoteCard.vue'
import PersonCard from '../../components/PersonCard.vue'

const props = defineProps({
  scope: { type: String, required: true },
  results: { type: Array, required: true },
  // The votes scope under a speaker filter is that person's roll call, and
  // every card then says how they voted.
  personScoped: { type: Boolean, default: false },
})
const emit = defineEmits(['hit'])

// Each plenary result rendered as a transcript excerpt (SEA-4): the matched
// sentence plus its surrounding context, parsed exactly like the sitting-day
// transcript — speaker label stripped, parenthetical stage directions / heckles
// lifted out as italic asides, and a change of speaker in the spilled-in context
// attributed. See searchExcerptLines() in format.js.
const plenary = computed(() => (props.scope === 'plenary' ? props.results : [])
  .map((r) => ({ ...r, lines: searchExcerptLines(r) })))

function viewerLink(r) {
  return { name: 'viewer', params: { uid: r.speech_uid }, query: { s: r.sentence_ord } }
}

// A committee hit opens the jegyzőkönyv at the speech (BIZ-29's `#sp-<ord>`):
// the minutes are one document, and the speech is the finest place in it.
function minutesLink(r) {
  return { name: 'committee-minutes', params: { meetingId: r.meetingId }, hash: `#sp-${r.ord}` }
}

// Who said it, as the minutes page draws a speech: a face and a faction chip
// for a member, the office and body for a guest. The minutes' faction column
// also holds a guest's ministry, which only reads as a party when it has a colour.
function committeeSpeaker(r) {
  return { person_id: r.personId, label: r.name, photo_uri: r.photoUri }
}
function committeeFaction(r) {
  return r.factionColor ? { label: r.faction, color: r.factionColor } : null
}
function committeeRole(r) {
  return r.factionColor ? '' : [r.role, r.org].filter(Boolean).join(', ')
}
</script>

<template>
  <ol v-if="scope === 'plenary'" class="results">
    <li v-for="(r, i) in plenary" :key="r.sentence_id" class="card pad result">
      <div class="result-meta row">
        <SpeakerLink v-if="r.speaker" :speaker="r.speaker" />
        <FactionBadge :faction="r.faction" />
        <span class="muted small">{{ formatDate(r.date) }}</span>
        <span class="muted small" v-if="r.agenda_title">{{ $t('search.on') }} {{ r.agenda_title }}</span>
        <TimingBadge :timing="r.timing" />
      </div>
      <router-link :to="viewerLink(r)" class="result-excerpt" @click="emit('hit', i)">
        <span v-for="(ln, j) in r.lines" :key="j" class="excerpt-line"
              :class="{ aside: ln.interjection }">
          <span v-if="ln.speakerLabel" class="ctx-speaker">{{ ln.speakerLabel }}: </span><span v-html="ln.html"></span>
        </span>
      </router-link>
      <div class="result-actions">
        <router-link :to="viewerLink(r)" class="btn secondary small watch" @click="emit('hit', i)">▶ {{ $t('search.watch') }}</router-link>
      </div>
    </li>
  </ol>

  <!-- Committee hits: one per speech, opened at its anchor in the jegyzőkönyv.
       The excerpt is the server's snippet, already escaped with only its
       <mark>s left as markup, like the plenary's. -->
  <ol v-else-if="scope === 'committee'" class="results">
    <li v-for="(r, i) in results" :key="r.id" class="card pad result">
      <div class="result-meta row">
        <SpeakerLink :speaker="committeeSpeaker(r)" />
        <FactionBadge :faction="committeeFaction(r)" />
        <span v-if="committeeRole(r)" class="muted small">{{ committeeRole(r) }}</span>
        <span class="muted small">{{ r.heldOn ? formatDate(r.heldOn) : formatDateLocal(r.heldAt) }}</span>
        <router-link :to="{ name: 'committee', params: { id: r.committeeId } }" class="small">
          {{ r.committeeName }}
        </router-link>
      </div>
      <p v-if="r.section" class="muted small result-section">{{ r.section }}</p>
      <router-link :to="minutesLink(r)" class="result-excerpt" @click="emit('hit', i)">
        <span v-html="r.excerpt"></span>
      </router-link>
      <div class="result-actions">
        <router-link :to="minutesLink(r)" class="btn secondary small" @click="emit('hit', i)">
          {{ $t('search.openMinutes') }}
        </router-link>
      </div>
    </li>
  </ol>

  <ul v-else-if="scope === 'documents'" class="results">
    <DocumentCard v-for="(b, i) in results" :key="b.id" :bill="b" @open="emit('hit', i)" />
  </ul>

  <ul v-else-if="scope === 'votes'" class="results">
    <VoteCard
      v-for="(v, i) in results" :key="v.id" :vote="v"
      :person-scoped="personScoped" @open="emit('hit', i)"
    />
  </ul>

  <ul v-else-if="scope === 'people'" class="results-grid grid">
    <PersonCard v-for="(p, i) in results" :key="p.person_id" :person="p" @open="emit('hit', i)" />
  </ul>
</template>

<style scoped>
.results { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .7rem; }
.results-grid { list-style: none; padding: 0; margin: 0; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); }
/* Card reads top-to-bottom: speaker/meta header → context around the match →
   the watch action. A column gap spaces every part uniformly. */
.result { display: flex; flex-direction: column; gap: .45rem; }
/* The matched sentence + its surrounding context, read as a transcript excerpt
   (SEA-4): each parsed line is its own block, the whole excerpt links to the
   viewer at the match, and the highlighted term stays prominent via <mark>. */
.result-excerpt { display: block; color: var(--ink); line-height: 1.6; }
.result-excerpt:hover { text-decoration: none; }
.result-excerpt:hover :deep(mark) { outline: 2px solid var(--accent-soft); }
.excerpt-line { display: block; }
.excerpt-line + .excerpt-line { margin-top: .4em; }
/* Stage directions / heckles lifted out of the parentheses: italic, in a softer
   tone so they read as asides, not speech (matches the sitting-day transcript). */
.excerpt-line.aside { font-style: italic; color: var(--ink-soft); }
.ctx-speaker { font-weight: 600; color: var(--ink); font-style: normal; }
.result-meta { gap: .8rem; row-gap: .4rem; }
.result-section { margin: 0; }
.result-actions { display: flex; justify-content: flex-end; }
</style>
