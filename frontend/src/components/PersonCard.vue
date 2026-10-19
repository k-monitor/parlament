<script setup>
// One person as a list card: face and name, faction (or, for someone without
// one, the nationality or office they spoke for), constituency, whether their
// mandate ended early, and how much they spoke. Shared by the Felszólalók
// lists (REP-1) and the people scope of the site search (SEA-14).
//
// Emits `open` when the reader follows the card to the profile (SEA-12).
import { useI18n } from 'vue-i18n'
import { formatDateLocal, formatSpeakingTime } from '../format.js'
import FactionBadge from './FactionBadge.vue'
import SpeakerLink from './SpeakerLink.vue'

defineProps({ person: { type: Object, required: true } })
const emit = defineEmits(['open'])
const { t } = useI18n()

// "megszűnt mandátum · 2024-09-30" — a card must say that a seat was given up,
// or a former MP reads exactly like a sitting one (REP-14).
function mandateEndedLabel(m) {
  const end = formatDateLocal(m && m.end)
  return end ? `${t('reps.mandateEnded')} · ${end}` : t('reps.mandateEnded')
}
</script>

<template>
  <li class="card pad repcard">
    <SpeakerLink
      :speaker="{ person_id: person.person_id, label: person.label, photo_uri: person.photo_uri }"
      @click="emit('open')"
    />
    <div class="repmeta">
      <FactionBadge :faction="person.faction" link />
      <!-- An advocate has no faction/constituency; the nationality they
           speak for takes that slot (REP-9). The chip shows the bare
           nationality (the tab already says these are advocates), but
           carries the full phrase for assistive tech. -->
      <span
        v-if="person.is_advocate && person.nationality" class="chip"
        :aria-label="$t('reps.advocateFor', { nationality: person.nationality })"
      >{{ person.nationality }}</span>
      <!-- A non-MP speaker has neither faction nor constituency either: the
           office they spoke in is what identifies them (REP-2/REP-12), so it
           takes the same slot. Scoped to the selected cycle by the API. -->
      <span v-if="person.office" class="chip office">{{ person.office }}</span>
      <span v-if="person.constituency" class="muted small">📍 {{ person.constituency }}</span>
      <!-- A mandate given up before the term ended (REP-14). Marked on the
           card, with the date, so a former MP is never listed as if they
           were still sitting. Text, not colour, carries it (A11Y-1). -->
      <span v-if="person.mandate && person.mandate.terminated" class="chip ended"
            :title="person.mandate.end_reason || ''">{{ mandateEndedLabel(person.mandate) }}</span>
    </div>
    <div class="repstats small muted">
      <span>{{ person.speech_count }} {{ $t('reps.speeches') }}</span>
      <span>·</span>
      <span>{{ formatSpeakingTime(person.speaking_seconds) }}</span>
    </div>
  </li>
</template>

<style scoped>
.repcard { display: flex; flex-direction: column; gap: .5rem; }
.repmeta { display: flex; gap: .6rem; flex-wrap: wrap; align-items: center; }
/* Office titles are long ("Külgazdasági és Külügyminisztérium államtitkára") and
   the cards are narrow, so this chip wraps instead of stretching the grid. */
.chip.office { font-weight: 600; line-height: 1.25; }
/* A terminated mandate reads as a quiet annotation, not an alarm — it is a fact
   about the seat, not a judgement about the person. */
.chip.ended { font-size: .8rem; opacity: .85; }
.repstats { display: flex; gap: .4rem; }
</style>
