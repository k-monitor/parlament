<script setup>
// One sitting day (use case 2): transcript segmented agenda item → speech, each
// speech links into the viewer.
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api.js'
import { agendaLabel, formatDate, formatDuration, formatSpeakingTime } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import SpeakerLink from '../../components/SpeakerLink.vue'
import TimingBadge from '../../components/TimingBadge.vue'
import WordCloud from '../../components/WordCloud.vue'

const props = defineProps({ id: String })
const router = useRouter()
const data = ref(null)
const loading = ref(false)
const error = ref(false)
// Word cloud (WCLOUD-1): a separate, non-blocking request so it never slows the
// transcript load (WCLOUD-5). A failure here leaves the transcript untouched.
const cloud = ref(null)
// Speaker toplist (TOPSPK-1): likewise a separate, non-blocking request (TOPSPK-5).
const topSpeakers = ref(null)
// Longest single speaking time, for sizing the (decorative) bars (TOPSPK-4).
const topMax = computed(() => Math.max(1, ...(topSpeakers.value?.speakers || []).map((s) => s.seconds || 0)))

async function load() {
  loading.value = true; error.value = false; cloud.value = null; topSpeakers.value = null
  try { data.value = await api.session(props.id) } catch { error.value = true } finally { loading.value = false }
  api.sessionWordcloud(props.id).then((c) => { cloud.value = c }).catch(() => {})
  api.sessionTopSpeakers(props.id).then((t) => { topSpeakers.value = t }).catch(() => {})
}
onMounted(load)
watch(() => props.id, load)

// Clicking a word opens proceedings search scoped to this sitting day (WCLOUD-4).
function searchWord(word) {
  const date = data.value?.session?.date
  router.push({ name: 'search', query: { q: word, date_from: date, date_to: date } })
}
</script>

<template>
  <StateBlock :loading="loading" :error="error" @retry="load">
    <div v-if="data">
      <router-link :to="{ name: 'sessions' }" class="small">‹ {{ $t('sessions.title') }}</router-link>
      <h1>{{ formatDate(data.session.date) }} · {{ data.session.sitting }}. {{ $t('sessions.sitting').toLowerCase() }}</h1>
      <p class="muted small">
        <a v-if="data.session.video_playseq || data.session.source_page" :href="data.session.video_playseq || data.session.source_page" target="_blank" rel="noopener">↗ {{ $t('viewer.viewOnParlament') }}</a>
      </p>

      <section v-if="cloud && cloud.words.length" class="card pad wcloud">
        <h2 class="wcloud-title">{{ $t('sessions.wordcloud') }}</h2>
        <WordCloud :words="cloud.words" :caption="$t('sessions.wordcloudCaption')" @pick="searchWord" />
      </section>

      <section v-if="topSpeakers && topSpeakers.speakers.length" class="card pad toplist">
        <h2 class="wcloud-title">{{ $t('sessions.topSpeakers') }}</h2>
        <p class="small soft" style="margin:0 0 .6rem;">{{ $t('sessions.topSpeakersCaption') }}</p>
        <ol class="top-rows">
          <li v-for="sp in topSpeakers.speakers" :key="sp.person_id" class="top-row">
            <SpeakerLink :speaker="sp" size="sm" />
            <FactionBadge :faction="sp.faction" />
            <span class="bar-track" aria-hidden="true">
              <span class="bar-fill" :style="{ width: ((sp.seconds / topMax) * 100) + '%', background: sp.faction && sp.faction.color || 'var(--accent)' }"></span>
            </span>
            <span class="top-time">⏱ {{ formatSpeakingTime(sp.seconds) }}</span>
            <span class="muted small top-count">{{ sp.speeches }} {{ $t('sessions.speeches') }}</span>
          </li>
        </ol>
      </section>

      <section v-for="a in data.agenda" :key="a.id" class="agenda card">
        <h2 class="pad agenda-title">
          {{ a.title }}
          <span class="badge" v-if="a.type">{{ agendaLabel(a.type) }}</span>
        </h2>
        <ul class="speeches">
          <li v-for="sp in a.speeches" :key="sp.uid">
            <router-link :to="{ name: 'viewer', params: { uid: sp.uid } }" class="speech-row" :class="{ procedural: sp.procedural }">
              <SpeakerLink :speaker="sp.speaker" />
              <FactionBadge :faction="sp.faction" />
              <span class="badge subtle" v-if="sp.speech_type">{{ sp.speech_type }}</span>
              <span class="grow"></span>
              <span class="muted small" v-if="!sp.has_text">📼 {{ $t('viewer.videoOnly') }}</span>
              <TimingBadge :timing="sp.timing" />
              <span class="muted small" v-if="sp.duration">⏱ {{ formatDuration(sp.duration) }}</span>
              <span class="play-affordance" aria-hidden="true">▶</span>
            </router-link>
          </li>
        </ul>
      </section>
    </div>
  </StateBlock>
</template>

<style scoped>
.wcloud { margin-bottom: 1rem; }
.wcloud-title { font-size: 1.05rem; margin: 0 0 .6rem; }
.toplist { margin-bottom: 1rem; }
.top-rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
.top-row { display: grid; grid-template-columns: minmax(130px, 1.5fr) auto 1fr auto auto; gap: .55rem; align-items: center; padding: .12rem 0; font-size: .9rem; }
.top-row :deep(.row span) { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.top-row .bar-track { background: #eceae4; border-radius: 5px; height: 9px; overflow: hidden; }
.top-row .bar-fill { display: block; height: 100%; border-radius: 5px; min-width: 2px; }
.top-time { font-size: .82rem; font-variant-numeric: tabular-nums; color: var(--ink); white-space: nowrap; }
.top-count { white-space: nowrap; }
@media (max-width: 560px) {
  .top-row { grid-template-columns: 1fr auto; column-gap: .5rem; }
  .top-row .bar-track { grid-column: 1 / -1; }
}
.agenda { margin-bottom: 1rem; }
.agenda-title { font-size: 1.05rem; margin: 0; border-bottom: 1px solid var(--line); display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; }
.speeches { list-style: none; margin: 0; padding: .3rem; }
.speech-row { display: flex; align-items: center; gap: .8rem; padding: .5rem .7rem; border-radius: 8px; color: var(--ink); }
.speech-row:hover { background: var(--accent-soft); text-decoration: none; }
.speech-row.procedural { opacity: .72; }
.badge.subtle { background: var(--line); color: var(--muted); font-weight: 400; }
.grow { flex: 1; }
.play-affordance { color: var(--accent); }
</style>
