<script setup>
// "Szám a parlamentből" — one number about the cycles in scope, and the sentence
// that says what it counts, taking turns with the others (lib/rotation.js).
//
// Every fact is read off an endpoint a page of the site already serves, so the
// number here is the number the linked page shows, and each fact is gated on the
// module (or feature) behind it: a deployment without the interjections module
// simply has one fact fewer (EXT-6). A fact whose answer is empty in this scope —
// no interjections recorded, no topics classified — is left out rather than
// shown as a zero, since "0 %" of an unmeasured thing is not a fact at all.
//
// The facts are neutral superlatives (who spoke the longest, which ministry was
// asked the most), never scores: the site presents, it does not rank anyone's
// worth (§1.2).
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../api.js'
import { store, currentCycleLabel } from '../store.js'
import { useRotation } from '../lib/rotation.js'
import { topicName } from '../lib/topics.js'
import RotationControls from './RotationControls.vue'

const { t, te } = useI18n()

const num = (n) => (n ?? 0).toLocaleString('hu-HU')
const pct = (share) => `${Math.round((share || 0) * 100)}%`
function hoursMinutes(seconds) {
  const m = Math.round((seconds || 0) / 60)
  const h = Math.floor(m / 60)
  const rest = m % 60
  if (!h) return t('home.facts.minutes', { m: rest })
  return rest ? t('home.facts.hoursMinutes', { h, m: rest }) : t('home.facts.hours', { h })
}
const person = (p) => ({ text: p.label, to: { name: 'profile', params: { id: p.person_id } } })

// Each fact: `load(period)` → the fact, or null when there is nothing to say.
// A fact is { value, slots: { name: { text, to?, color? } }, link } — its sentence
// and link label are `home.facts.<id>.*`. Every placeholder in the sentence is a
// slot, so a name can be a link and a number can be set in bold alike. Anything
// worded (a duration, a topic's name) is a function, read at render time, so a
// language switch re-words the fact without asking the API again.
const FACTS = [
  {
    id: 'speakingTime',
    on: () => store.moduleEnabled('representatives'),
    async load(period) {
      const res = await api.representatives({ period, sort: 'speaking_time', limit: 1 })
      const top = (res.representatives || [])[0]
      if (!top || !top.speaking_seconds) return null
      return {
        value: () => hoursMinutes(top.speaking_seconds),
        slots: { name: person(top) },
        link: { name: 'representatives' },
      }
    },
  },
  {
    id: 'interjections',
    on: () => store.moduleEnabled('interjections'),
    async load(period) {
      const res = await api.interjectionGraph(period, 2, 'made')
      const top = [...(res.nodes || [])].sort((a, b) => (b.out || 0) - (a.out || 0))[0]
      if (!top || !top.out) return null
      return {
        value: num(top.out),
        slots: { name: person(top) },
        link: { name: 'interjections', query: { from: top.person_id } },
      }
    },
  },
  {
    id: 'blindSpots',
    on: () => store.moduleEnabled('settlements'),
    async load(period) {
      const res = await api.settlementSummary(period)
      if (!res.settlements || !res.mentions) return null
      return {
        value: num(res.blind),
        slots: {
          total: { text: num(res.settlements) },
          pct: { text: pct(res.blind / res.settlements) },
        },
        link: { name: 'settlements', query: { mode: 'blind' } },
      }
    },
  },
  {
    id: 'topTopic',
    on: () => store.moduleEnabled('proceedings') && store.featureEnabled('speech_topics'),
    async load(period) {
      const res = await api.topicMix(period)
      const top = (res.topics || [])[0]
      if (!top || !top.share) return null
      return {
        value: pct(top.share),
        slots: { topic: { text: () => topicName(top.label, t, te),
                          to: { name: 'topics', query: { topic: top.label } } } },
        link: { name: 'topics' },
      }
    },
  },
  {
    id: 'factionShare',
    on: () => store.moduleEnabled('representatives'),
    async load(period) {
      const res = await api.factions(period)
      const rows = (res.factions || []).filter((f) => f.speaking_seconds > 0)
      const total = rows.reduce((s, f) => s + f.speaking_seconds, 0)
      const top = [...rows].sort((a, b) => b.speaking_seconds - a.speaking_seconds)[0]
      if (!top || !total) return null
      return {
        value: pct(top.speaking_seconds / total),
        slots: { faction: { text: top.label, color: top.color } },
        link: { name: 'factions' },
      }
    },
  },
  {
    id: 'questions',
    on: () => store.moduleEnabled('portfolios'),
    async load(period) {
      const res = await api.portfolios({ period })
      const top = [...(res.portfolios || [])]
        .filter((p) => p.kind === 'ministry' && p.answered > 0)
        .sort((a, b) => b.answered - a.answered)[0]
      if (!top) return null
      return {
        value: num(top.answered),
        slots: { portfolio: { text: top.name,
                              to: { name: 'portfolio', params: { slug: top.slug } } } },
        link: { name: 'portfolios' },
      }
    },
  },
]

const facts = ref([])
const loading = ref(true)
let seq = 0
async function load() {
  const mine = ++seq
  loading.value = true
  const period = store.cycles
  const enabled = FACTS.filter((f) => f.on())
  const results = await Promise.allSettled(enabled.map((f) => f.load(period)))
  if (mine !== seq) return
  facts.value = results
    .map((r, i) => (r.status === 'fulfilled' && r.value ? { id: enabled[i].id, ...r.value } : null))
    .filter(Boolean)
  loading.value = false
}
// The facts are about the cycles in scope, so a scope switch asks again. The
// manifest must be in first: it says which modules the facts may ask.
watch(() => [store.loaded, store.cycles.join(',')], () => { if (store.loaded) load() },
      { immediate: true })

const rot = useRotation(computed(() => facts.value.length), { interval: 9000 })
const fact = computed(() => facts.value[rot.index.value] || null)
const names = computed(() => facts.value.map((f) => t(`home.facts.${f.id}.name`)))

const scopeLabel = computed(() => {
  const c = currentCycleLabel()
  return c ? t('cycle.scope', { cycle: c }) : t('cycle.scopeAll')
})
const show = (v) => (typeof v === 'function' ? v() : v)
// A case ending the language glues onto the number (hu "15%-ot"), since the
// number stands on its own line above the sentence that continues it.
const suffix = (id) => (te(`home.facts.${id}.valueSuffix`) ? t(`home.facts.${id}.valueSuffix`) : '')
</script>

<template>
  <section
    class="card pad facts" :aria-label="$t('home.facts.title')"
    aria-roledescription="carousel" v-bind="rot.holdHandlers"
  >
    <div class="facts__head">
      <span class="facts__scope">{{ scopeLabel }}</span>
    </div>

    <div class="facts__body" :aria-live="rot.running.value ? 'off' : 'polite'">
      <div v-if="loading && !facts.length" class="facts__skeleton" aria-hidden="true">
        <span></span><span></span><span></span>
      </div>
      <p v-else-if="!facts.length" class="soft">{{ $t('home.facts.empty') }}</p>
      <Transition v-else name="facts-fade" mode="out-in">
        <div v-if="fact" :key="fact.id" class="fact">
          <p class="fact__value">{{ show(fact.value) }}{{ suffix(fact.id) }}</p>
          <i18n-t :keypath="`home.facts.${fact.id}.text`" tag="p" class="fact__text" scope="global">
            <template v-for="(slot, name) in fact.slots" :key="name" #[name]>
              <router-link v-if="slot.to" :to="slot.to" class="fact__ref">{{ show(slot.text) }}</router-link>
              <strong v-else class="fact__ref">
                <span v-if="slot.color" class="fact__dot" :style="{ background: slot.color }" aria-hidden="true"></span>{{ show(slot.text) }}
              </strong>
            </template>
          </i18n-t>
          <router-link :to="fact.link" class="fact__more">
            {{ $t(`home.facts.${fact.id}.link`) }} <span aria-hidden="true">→</span>
          </router-link>
        </div>
      </Transition>
    </div>

    <RotationControls
      :count="facts.length" :index="rot.index.value" :running="rot.running.value"
      :stopped="rot.stopped.value" :turn="rot.turn.value" :interval="rot.interval"
      :names="names" :label="$t('home.facts.title')"
      @go="rot.go" @toggle="rot.toggle"
    />
  </section>
</template>

<style scoped>
.facts { display: flex; flex-direction: column; min-width: 0; }
.facts__head { display: flex; justify-content: flex-end; }
.facts__scope { font-size: .78rem; color: var(--ink-faint); text-align: right; }
.facts__body { flex: 1; display: flex; flex-direction: column; justify-content: center; padding: .9rem 0 1rem; }
.fact__value {
  margin: 0; font-size: clamp(2.2rem, 5vw, 3.2rem); line-height: 1.05; font-weight: 800;
  color: var(--accent); letter-spacing: -.01em; font-variant-numeric: tabular-nums;
}
.fact__text { margin: .6rem 0 0; font-size: 1.05rem; line-height: 1.45; color: var(--ink-soft); max-width: 36ch; }
.fact__ref { color: var(--ink); font-weight: 700; }
a.fact__ref:hover { color: var(--accent); }
.fact__dot {
  display: inline-block; width: .65rem; height: .65rem; border-radius: 50%;
  margin-right: .3rem; vertical-align: .02em; border: 1px solid rgba(0,0,0,.15);
}
.fact__more { display: inline-block; margin-top: .8rem; font-weight: 700; font-size: .92rem; }

.facts__skeleton { display: grid; gap: .6rem; }
.facts__skeleton span { display: block; height: 1rem; border-radius: 6px; background: #f0eee8; }
.facts__skeleton span:first-child { height: 3rem; width: 70%; }
.facts__skeleton span:last-child { width: 55%; }

.facts-fade-enter-active, .facts-fade-leave-active { transition: opacity .25s ease, transform .25s ease; }
.facts-fade-enter-from { opacity: 0; transform: translateY(6px); }
.facts-fade-leave-to { opacity: 0; transform: translateY(-6px); }
@media (prefers-reduced-motion: reduce) {
  .facts-fade-enter-active, .facts-fade-leave-active { transition: none; }
}
</style>
