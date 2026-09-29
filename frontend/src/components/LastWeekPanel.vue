<script setup>
// "A múlt héten" — the sitting week that has just gone, at a glance
// (`GET /proceedings/week`): its days, what they add up to, who spoke the most,
// what the week was distinctively about, and how many votes it held.
//
// The home page's other perishable block, the order paper, says what the House
// is about to do; this one says what it has just done. Its hard part is the same
// honesty problem the sittings list has (SIT-2): parlament.hu publishes a held
// sitting in instalments, so a day from last Thursday can still have nothing in
// it, or only half. Every day is therefore listed whatever its state, a day with
// nothing yet is marked as such instead of being counted as a quiet one, and the
// totals say when they cover only part of the week.
//
// When the House did not sit last week the endpoint hands back the latest week it
// did sit in, and the heading says "the latest sitting days" instead — a recess
// must not leave the home page claiming that last week was empty of *record*.
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../api.js'
import { store } from '../store.js'
import { formatSpeakingTime } from '../format.js'
import StateBlock from './StateBlock.vue'

const { t, locale } = useI18n()
const data = ref(null)
const loading = ref(true)
const failed = ref(false)
const votes = ref(null)   // { total, adopted } — only with the votes module

const week = computed(() => data.value && data.value.week)
const days = computed(() => (data.value && data.value.days) || [])
const totals = computed(() => (data.value && data.value.totals) || {})

// What a day has to show: `waiting` — held (or announced) but nothing published
// yet; `partial` — browsable, still arriving (SIT-2's own `pending`); `ready`.
function dayState(d) {
  if (d.status === 'scheduled' || d.status === 'awaiting_media' || !d.speeches) return 'waiting'
  return d.processing === 'pending' ? 'partial' : 'ready'
}
const waitingDays = computed(() => days.value.filter((d) => dayState(d) === 'waiting').length)
const partialDays = computed(() => days.value.filter((d) => dayState(d) === 'partial').length)

const intlLocale = computed(() => (locale.value === 'en' ? 'en-US' : 'hu-HU'))
const asDate = (iso) => new Date(`${iso}T12:00:00`)
function dayLabel(iso) {
  try {
    return new Intl.DateTimeFormat(intlLocale.value,
      { month: 'long', day: 'numeric', weekday: 'long' }).format(asDate(iso))
  } catch { return iso }
}
const rangeLabel = computed(() => {
  if (!week.value) return ''
  const fmt = new Intl.DateTimeFormat(intlLocale.value,
    { year: 'numeric', month: 'long', day: 'numeric' })
  try {
    return fmt.formatRange(asDate(week.value.start), asDate(week.value.end))
  } catch {
    return `${week.value.start} – ${week.value.end}`
  }
})

const num = (n) => (n ?? 0).toLocaleString('hu-HU')
const searchWord = (w) => ({ name: 'search',
  query: { q: w, date_from: week.value.start, date_to: week.value.end } })
const votesLink = computed(() => week.value && ({ name: 'votes',
  query: { date_from: week.value.start, date_to: week.value.end } }))

async function loadVotes(w) {
  if (!store.moduleEnabled('votes')) return
  const range = { date_from: w.start, date_to: w.end, limit: 1 }
  try {
    // Quorum calls are divisions too, but not decisions: the count is what the
    // House decided, adopted or rejected.
    const [yes, no] = await Promise.all([
      api.votes({ ...range, result: 'Elfogadva' }),
      api.votes({ ...range, result: 'Elutasítva' }),
    ])
    votes.value = { total: (yes.total || 0) + (no.total || 0), adopted: yes.total || 0 }
  } catch {
    votes.value = null
  }
}

async function load() {
  loading.value = true
  failed.value = false
  try {
    data.value = await api.sittingWeek()
    if (data.value && data.value.week) loadVotes(data.value.week)
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<template>
  <section v-if="loading || failed || week" class="card pad lastweek" aria-labelledby="lastweek-title">
    <div class="lw__head">
      <div>
        <h2 id="lastweek-title">
          {{ !week || week.last_week ? $t('lastWeek.title') : $t('lastWeek.titleEarlier') }}
        </h2>
        <p v-if="week" class="lw__range soft">{{ rangeLabel }}</p>
      </div>
      <router-link :to="{ name: 'sessions' }" class="lw__all">
        {{ $t('lastWeek.allSittings') }} <span aria-hidden="true">→</span>
      </router-link>
    </div>

    <StateBlock :loading="loading" :error="failed" @retry="load">
      <template v-if="week">
        <dl v-if="totals.speeches" class="lw__stats">
          <div><dt>{{ num(totals.days) }}</dt><dd>{{ $t('lastWeek.stats.days') }}</dd></div>
          <div><dt>{{ num(totals.speeches) }}</dt><dd>{{ $t('lastWeek.stats.speeches') }}</dd></div>
          <div><dt>{{ formatSpeakingTime(totals.seconds) }}</dt><dd>{{ $t('lastWeek.stats.time') }}</dd></div>
          <div v-if="votes && votes.total">
            <dt><router-link :to="votesLink">{{ num(votes.total) }}</router-link></dt>
            <dd>{{ $t('lastWeek.stats.votes', { adopted: num(votes.adopted) }) }}</dd>
          </div>
        </dl>

        <!-- The totals above cover only what is in; say so, and say what is not. -->
        <p v-if="waitingDays" class="lw__note">
          <span aria-hidden="true">⏳</span>
          {{ totals.speeches
              ? $t('lastWeek.someWaiting', { n: waitingDays }, waitingDays)
              : $t('lastWeek.allWaiting', {}, days.length) }}
        </p>
        <p v-else-if="partialDays" class="lw__note">
          <span aria-hidden="true">⏳</span> {{ $t('lastWeek.somePartial', {}, partialDays) }}
        </p>

        <div class="lw__grid" :class="{ solo: !data.top_speakers.length && !data.words.length }">
          <ol class="lw__days">
            <li v-for="d in days" :key="d.id">
              <router-link
                :to="{ name: 'session', params: { id: d.id } }" class="lw__day"
                :class="'is-' + dayState(d)"
              >
                <span class="lw__date">
                  <strong>{{ dayLabel(d.date) }}</strong>
                  <span class="muted small">{{ d.sitting }}. {{ $t('sessions.sitting').toLowerCase() }}</span>
                </span>
                <span v-if="dayState(d) === 'waiting'" class="badge lw__badge"
                      :title="$t('lastWeek.waitingNote')">
                  ⏳ {{ $t('lastWeek.waiting') }}
                </span>
                <span v-else class="lw__daystat">
                  <span>{{ $t('sessions.speechesCount', { count: num(d.speeches) }) }}</span>
                  <span>{{ formatSpeakingTime(d.seconds) }}</span>
                  <span v-if="dayState(d) === 'partial'" class="badge lw__badge"
                        :title="$t('sessions.partialNote')">⏳ {{ $t('sessions.partial') }}</span>
                </span>
              </router-link>
            </li>
          </ol>

          <div v-if="data.top_speakers.length || data.words.length" class="lw__side">
            <template v-if="data.top_speakers.length">
              <h3 class="lw__subhead">{{ $t('lastWeek.topSpeakers') }}</h3>
              <ol class="lw__speakers">
                <li v-for="s in data.top_speakers" :key="s.person_id">
                  <img v-if="s.photo_uri" :src="s.photo_uri" alt="" class="avatar xs" loading="lazy" />
                  <span v-else class="avatar xs" aria-hidden="true"></span>
                  <router-link :to="{ name: 'profile', params: { id: s.person_id } }" class="lw__who">
                    {{ s.label }}
                  </router-link>
                  <span v-if="s.faction" class="chip">
                    <span class="dot" :style="{ background: s.faction.color }" aria-hidden="true"></span>
                    {{ s.faction.label }}
                  </span>
                  <span class="lw__time">{{ formatSpeakingTime(s.seconds) }}</span>
                </li>
              </ol>
            </template>
            <template v-if="data.words.length">
              <h3 class="lw__subhead">{{ $t('lastWeek.words') }}</h3>
              <p class="lw__words">
                <router-link
                  v-for="w in data.words" :key="w.text" :to="searchWord(w.text)"
                  class="lw__word" :class="{ 'is-entity': w.kind === 'entity' }"
                  :title="$t('lastWeek.wordTitle', { n: num(w.count) })"
                >{{ w.text }}</router-link>
              </p>
            </template>
          </div>
        </div>
      </template>
    </StateBlock>
  </section>
</template>

<style scoped>
.lw__head { display: flex; align-items: flex-start; justify-content: space-between; gap: .6rem 1rem; flex-wrap: wrap; }
.lw__head h2 { margin: 0; font-size: 1.15rem; }
.lw__range { margin: .15rem 0 0; font-size: .85rem; }
.lw__all { font-weight: 700; font-size: .9rem; white-space: nowrap; }

/* The same four-column rhythm as the corpus figures used to have on this page. */
.lw__stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 1rem 0 0; max-width: 680px; }
.lw__stats div { margin: 0; padding: 0 1.4rem; border-left: 1px solid var(--line); }
.lw__stats div:first-child { padding-left: 0; border-left: 0; }
.lw__stats dt { font-size: 1.45rem; font-weight: 800; color: var(--accent); white-space: nowrap; }
.lw__stats dt a { color: inherit; }
.lw__stats dd { margin: 0; color: var(--ink-faint); font-size: .85rem; }
@media (max-width: 620px) {
  .lw__stats { grid-template-columns: repeat(2, minmax(0, 1fr)); row-gap: .9rem; }
  .lw__stats div:nth-child(odd) { padding-left: 0; border-left: 0; }
}

.lw__note {
  margin: .9rem 0 0; padding: .5rem .75rem; border-radius: 8px;
  background: #fdf3e3; color: #6b4600; font-size: .88rem;
}

.lw__grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 1.2rem 2rem; margin-top: 1rem; }
.lw__grid.solo { grid-template-columns: minmax(0, 1fr); }
@media (max-width: 760px) { .lw__grid { grid-template-columns: minmax(0, 1fr); } }

.lw__days { list-style: none; margin: 0; padding: 0; display: grid; gap: .5rem; align-content: start; }
.lw__day {
  display: flex; align-items: center; justify-content: space-between; gap: .4rem 1rem; flex-wrap: wrap;
  padding: .6rem .8rem; border: 1px solid var(--line); border-radius: 8px; color: var(--ink);
  transition: border-color .15s ease, background .15s ease;
}
.lw__day:hover { text-decoration: none; border-color: var(--accent); background: #fffaf9; }
/* A day with nothing in it yet reads as not-yet, like the sittings list's own. */
.lw__day.is-waiting { border-style: dashed; background: #fbfaf7; }
.lw__date { display: flex; flex-direction: column; line-height: 1.3; }
.lw__date strong { font-size: .95rem; }
.lw__daystat { display: flex; align-items: center; flex-wrap: wrap; gap: .2rem .8rem; font-size: .85rem; color: var(--ink-soft); }
.lw__badge { background: #fdf3e3; color: #8a5a00; cursor: help; }

.lw__subhead { margin: 0 0 .45rem; font-size: .8rem; font-weight: 700; color: var(--ink-faint); text-transform: uppercase; letter-spacing: .04em; }
.lw__side .lw__subhead + * + .lw__subhead { margin-top: 1rem; }
.lw__speakers { list-style: none; margin: 0; padding: 0; display: grid; gap: .35rem; }
.lw__speakers li { display: flex; align-items: center; gap: .5rem; min-width: 0; font-size: .9rem; }
.lw__who { color: var(--ink); font-weight: 600; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lw__who:hover { color: var(--accent); }
.lw__speakers .chip { font-weight: 500; white-space: nowrap; }
.lw__time { margin-left: auto; color: var(--ink-soft); font-variant-numeric: tabular-nums; white-space: nowrap; }
.lw__words { display: flex; flex-wrap: wrap; gap: .35rem; margin: 0; }
.lw__word {
  padding: .15rem .6rem; border-radius: 999px; background: #f0eee8;
  color: var(--ink); font-size: .85rem; font-weight: 600;
}
.lw__word.is-entity { background: var(--accent-soft); color: var(--accent); }
.lw__word:hover { text-decoration: none; background: var(--accent); color: var(--accent-ink); }
@media (prefers-reduced-motion: reduce) { .lw__day { transition: none; } }
</style>
