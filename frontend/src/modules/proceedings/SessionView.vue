<script setup>
// One sitting day (use case 2): transcript segmented agenda item → speech, each
// speech links into the viewer.
import { ref, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api.js'
import { agendaLabel, formatDate, formatDuration } from '../../format.js'
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

async function load() {
  loading.value = true; error.value = false; cloud.value = null
  try { data.value = await api.session(props.id) } catch { error.value = true } finally { loading.value = false }
  api.sessionWordcloud(props.id).then((c) => { cloud.value = c }).catch(() => {})
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
