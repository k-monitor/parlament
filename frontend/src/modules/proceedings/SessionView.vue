<script setup>
// One sitting day (use case 2): transcript segmented agenda item → speech, each
// speech links into the viewer.
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api.js'
import { agendaLabel, formatDate, formatSpeakingTime } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import FactionBadge from '../../components/FactionBadge.vue'
import SpeakerLink from '../../components/SpeakerLink.vue'
import WordCloud from '../../components/WordCloud.vue'
import HelpTip from '../../components/HelpTip.vue'
import SpeechRow from './SpeechRow.vue'

const props = defineProps({ id: String })
const router = useRouter()
const data = ref(null)
const loading = ref(false)
const error = ref(false)
// Word cloud (WCLOUD-1): a separate, non-blocking request so it never slows the
// transcript load (WCLOUD-5). A failure here leaves the transcript untouched.
const cloud = ref(null)
// New words (NEW-1): words that debuted on this day, never said before in
// parliament (previous cycles included). Again a separate, non-blocking request.
const newWords = ref(null)
// Speaker toplist (TOPSPK-1): likewise a separate, non-blocking request (TOPSPK-5).
const topSpeakers = ref(null)
// Longest single speaking time, for sizing the (decorative) bars (TOPSPK-4).
const topMax = computed(() => Math.max(1, ...(topSpeakers.value?.speakers || []).map((s) => s.seconds || 0)))

async function load() {
  loading.value = true; error.value = false; cloud.value = null; newWords.value = null; topSpeakers.value = null
  try { data.value = await api.session(props.id) } catch { error.value = true } finally { loading.value = false }
  api.sessionWordcloud(props.id).then((c) => { cloud.value = c }).catch(() => {})
  api.sessionNewWords(props.id).then((n) => { newWords.value = n }).catch(() => {})
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
        <a v-if="data.session.source_page" :href="data.session.source_page" target="_blank" rel="noopener">↗ {{ $t('viewer.viewOnParlament') }}</a>
      </p>

      <section v-if="cloud && cloud.words.length" class="card pad wcloud">
        <div class="sechead">
          <h2 class="wcloud-title">{{ $t('sessions.wordcloud') }}</h2>
          <HelpTip :label="$t('sessions.wordcloud')"><p>{{ $t('sessions.wordcloudCaption') }}</p></HelpTip>
        </div>
        <WordCloud :words="cloud.words" :caption="$t('sessions.wordcloudCaption')" :show-caption="false" @pick="searchWord" />
      </section>

      <section v-if="newWords && newWords.words.length" class="card pad newwords">
        <div class="sechead">
          <h2 class="wcloud-title">{{ $t('sessions.newWords') }}</h2>
          <HelpTip :label="$t('sessions.newWords')"><p>{{ $t('sessions.newWordsCaption') }}</p></HelpTip>
        </div>
        <ul class="chips">
          <li v-for="w in newWords.words" :key="w.text">
            <button
              type="button" class="chip" :title="`${w.text}: ${w.count}`"
              @click="searchWord(w.text)"
            >{{ w.text }}<span class="chip-count" v-if="w.count > 1">{{ w.count }}</span></button>
          </li>
        </ul>
      </section>

      <section v-if="topSpeakers && topSpeakers.speakers.length" class="card pad toplist">
        <div class="sechead">
          <h2 class="wcloud-title">{{ $t('sessions.topSpeakers') }}</h2>
          <HelpTip :label="$t('sessions.topSpeakers')"><p>{{ $t('sessions.topSpeakersCaption') }}</p></HelpTip>
        </div>
        <ol class="top-rows">
          <li v-for="sp in topSpeakers.speakers" :key="sp.person_id" class="top-row">
            <SpeakerLink :speaker="sp" size="sm" />
            <FactionBadge v-if="sp.faction" :faction="sp.faction" />
            <span v-else aria-hidden="true"></span>
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
          <SpeechRow v-for="sp in a.speeches" :key="sp.uid" :speech="sp" />
        </ul>
      </section>
    </div>
  </StateBlock>
</template>

<style scoped>
.wcloud { margin-bottom: 1rem; }
.sechead { display: flex; align-items: center; gap: .35rem; margin-bottom: .6rem; }
.sechead .wcloud-title { margin: 0; }
.wcloud-title { font-size: 1.05rem; margin: 0 0 .6rem; }
.newwords { margin-bottom: 1rem; }
.chips { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: .4rem; }
.chip {
  display: inline-flex; align-items: baseline; gap: .32rem;
  font: inherit; font-size: .9rem; cursor: pointer;
  padding: .2rem .55rem; border-radius: 999px;
  border: 1px solid var(--line); background: var(--accent-soft); color: var(--ink);
  transition: border-color .12s, background .12s;
}
.chip:hover, .chip:focus-visible { border-color: var(--accent); outline: none; }
.chip-count { font-size: .72rem; color: var(--muted); font-variant-numeric: tabular-nums; }
.toplist { margin-bottom: 1rem; }
.top-rows { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: minmax(130px, 1.5fr) auto 1fr auto auto; row-gap: .24rem; }
.top-row { display: grid; grid-template-columns: subgrid; grid-column: 1 / -1; column-gap: .55rem; align-items: center; padding: .12rem 0; font-size: .9rem; }
.top-row :deep(.row span) { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.top-row .bar-track { background: #eceae4; border-radius: 5px; height: 9px; overflow: hidden; }
.top-row .bar-fill { display: block; height: 100%; border-radius: 5px; min-width: 2px; }
.top-time { font-size: .82rem; font-variant-numeric: tabular-nums; color: var(--ink); white-space: nowrap; }
.top-count { white-space: nowrap; }
@media (max-width: 560px) {
  .top-rows { grid-template-columns: 1fr auto; column-gap: .5rem; }
  .top-row .bar-track { grid-column: 1 / -1; }
}
.agenda { margin-bottom: 1rem; }
.agenda-title { font-size: 1.05rem; margin: 0; border-bottom: 1px solid var(--line); display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; }
.speeches { list-style: none; margin: 0; padding: .3rem; }
</style>
