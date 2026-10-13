<script setup>
// One iromány as a list card: number, type, status, date and topic, the title,
// and who submitted it (and, for a question, who answered it). Shared by the
// all-irományok list (BILL-9) and the documents scope of the site search
// (SEA-14), so a document reads the same wherever it is found.
//
// Emits `open` when the reader follows the card to the document — the hook the
// lists hang their search-quality click on (SEA-12).
import { computed } from 'vue'
import { store } from '../store.js'
import { formatDate } from '../format.js'
import FactionBadge from './FactionBadge.vue'
import TopicBadge from './TopicBadge.vue'

defineProps({ bill: { type: Object, required: true } })
const emit = defineEmits(['open'])

// A committee-tabled iromány names the committee instead of a person (BIZ-28);
// linked here as on the iromány's own page, while that module is mounted (EXT-6).
const showCommittees = computed(() => store.moduleEnabled('committees'))
</script>

<template>
  <li class="card pad billcard">
    <div class="billhead">
      <router-link :to="{ name: 'document', params: { id: bill.id } }" class="billnum" @click="emit('open')">{{ bill.bill_number }}</router-link>
      <span class="badge" v-if="bill.type">{{ bill.type }}</span>
      <span class="badge status" v-if="bill.status">{{ bill.status }}</span>
      <span class="muted small" v-if="bill.submitted_date">{{ formatDate(bill.submitted_date) }}</span>
      <!-- Most classified irományok are kérdések and interpellációk, so
           this is the list where the topic earns its place (TOPIC-8). -->
      <TopicBadge :topic="bill.topic" kind="bill" compact />
    </div>
    <router-link :to="{ name: 'document', params: { id: bill.id } }" class="billtitle" @click="emit('open')">{{ bill.title }}</router-link>
    <div class="sponsors small" v-if="bill.sponsors.length || bill.responder">
      <template v-for="(s, i) in bill.sponsors" :key="i">
        <router-link v-if="s.person_id" :to="{ name: 'profile', params: { id: s.person_id } }">{{ s.name }}</router-link>
        <router-link
          v-else-if="s.committee && showCommittees"
          :to="{ name: 'committee', params: { id: s.committee.id } }"
        >{{ s.name }}</router-link>
        <span v-else>{{ s.name }}</span>
        <FactionBadge v-if="s.faction" :faction="s.faction" />
        <span v-if="i < bill.sponsors.length - 1" aria-hidden="true">·</span>
      </template>
      <!-- questions: submitter → whoever answered it in plenary -->
      <template v-if="bill.responder">
        <span class="arrow" aria-hidden="true">→</span>
        <span class="visually-hidden">{{ $t('documents.answeredBy') }}:</span>
        <router-link
          v-if="bill.responder.person_id" :title="bill.responder.office"
          :to="{ name: 'profile', params: { id: bill.responder.person_id } }"
        >{{ bill.responder.name }}</router-link>
        <span v-else :title="bill.responder.office">{{ bill.responder.name }}</span>
        <FactionBadge v-if="bill.responder.faction" :faction="bill.responder.faction" />
      </template>
    </div>
  </li>
</template>

<style scoped>
.billcard { display: flex; flex-direction: column; gap: .4rem; }
.billhead { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; }
.billnum { font-weight: 800; color: var(--accent); }
.billtitle { color: var(--ink); font-weight: 600; }
.billtitle:hover { color: var(--accent); }
.sponsors { display: flex; gap: .4rem; flex-wrap: wrap; align-items: center; }
.sponsors .arrow { color: var(--ink-soft); }
.badge.status { background: var(--accent-soft); }
</style>
