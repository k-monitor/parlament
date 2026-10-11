<script>
// What a day has to show: `waiting` — held (or announced) but nothing published
// yet; `partial` — browsable, still arriving (SIT-2's own `pending`); `ready`.
// Exported so the week digest can say how many of its days are which.
export function dayState(d) {
  if (d.status === 'scheduled' || d.status === 'awaiting_media' || !d.speeches) return 'waiting'
  return d.processing === 'pending' ? 'partial' : 'ready'
}
</script>

<script setup>
// A row of sitting-day cards for the week digest (HOME-4): one per day, each
// linking to its sitting-day page, whatever its publication state — a day with
// nothing in it yet reads as not-yet, the sittings list's own dashed look.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatSpeakingTime } from '../format.js'

defineProps({ days: { type: Array, required: true } })

const { locale } = useI18n()
const intlLocale = computed(() => (locale.value === 'en' ? 'en-US' : 'hu-HU'))
function dayLabel(iso) {
  try {
    return new Intl.DateTimeFormat(intlLocale.value,
      { month: 'long', day: 'numeric', weekday: 'long' }).format(new Date(`${iso}T12:00:00`))
  } catch { return iso }
}
const num = (n) => (n ?? 0).toLocaleString('hu-HU')
</script>

<template>
  <ol class="wdays">
    <li v-for="d in days" :key="d.id">
      <router-link
        :to="{ name: 'session', params: { id: d.id } }" class="wday"
        :class="'is-' + dayState(d)"
      >
        <span class="wday__date">
          <strong>{{ dayLabel(d.date) }}</strong>
          <span class="muted small">{{ d.sitting }}. {{ $t('sessions.sitting').toLowerCase() }}</span>
        </span>
        <span v-if="dayState(d) === 'waiting'" class="badge wday__badge"
              :title="$t('lastWeek.waitingNote')">
          ⏳ {{ $t('lastWeek.waiting') }}
        </span>
        <span v-else class="wday__stat">
          <span>{{ $t('sessions.speechesCount', { count: num(d.speeches) }) }}</span>
          <span>{{ formatSpeakingTime(d.seconds) }}</span>
          <span v-if="dayState(d) === 'partial'" class="badge wday__badge"
                :title="$t('sessions.partialNote')">⏳ {{ $t('sessions.partial') }}</span>
        </span>
      </router-link>
    </li>
  </ol>
</template>

<style scoped>
.wdays {
  list-style: none; margin: 0; padding: 0; display: grid; gap: .5rem;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 250px), 1fr));
}
.wday {
  display: flex; align-items: center; justify-content: space-between; gap: .4rem 1rem; flex-wrap: wrap;
  height: 100%; padding: .6rem .8rem; border: 1px solid var(--line); border-radius: 8px; color: var(--ink);
  transition: border-color .15s ease, background .15s ease;
}
.wday:hover { text-decoration: none; border-color: var(--accent); background: #fffaf9; }
.wday.is-waiting { border-style: dashed; background: #fbfaf7; }
.wday__date { display: flex; flex-direction: column; line-height: 1.3; }
.wday__date strong { font-size: .95rem; }
.wday__stat { display: flex; align-items: center; flex-wrap: wrap; gap: .2rem .8rem; font-size: .85rem; color: var(--ink-soft); }
.wday__badge { background: #fdf3e3; color: #8a5a00; cursor: help; }
@media (prefers-reduced-motion: reduce) { .wday { transition: none; } }
</style>
