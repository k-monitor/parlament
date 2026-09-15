<script setup>
// The order paper for the sitting that is coming (NR-5).
//
// Everything else on the site is a record of what the House has done; this is
// the one block that says what it is about to do, read off the napirend PDF the
// Aktuális page publishes. That difference is the whole design problem here: an
// order paper is a PLAN, and the House drops, reorders and re-times items
// between issuing it and holding the sitting. So every rendering of it carries
// the document it came from and the moment that document was issued — the
// "…órai állapot szerint" stamp the napirend prints on itself (TRUST-1).
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../api.js'
import StateBlock from './StateBlock.vue'

const { locale } = useI18n()
const data = ref(null)
const loading = ref(true)
const failed = ref(false)
const expanded = ref(false)

// How many items of each day are shown before the reader asks for the rest.
// A sitting runs to two or three dozen items; the home page is an invitation,
// not the order paper itself, so it shows the head of each day and links out.
const PREVIEW_ITEMS = 4

const agenda = computed(() => data.value && data.value.agenda)
const days = computed(() => (agenda.value && agenda.value.days) || [])
const hiddenCount = computed(() =>
  days.value.reduce((n, d) => n + Math.max(0, d.items.length - PREVIEW_ITEMS), 0))

function shownItems(day) {
  return expanded.value ? day.items : day.items.slice(0, PREVIEW_ITEMS)
}

// The listing prints the weekday in Hungarian capitals ("HÉTFŐ"), which is the
// source's voice, not ours — so the heading is built from the ISO date in the
// reader's own locale instead.
function dayLabel(iso) {
  if (!iso) return ''
  const d = new Date(iso + 'T12:00:00')
  if (Number.isNaN(d.getTime())) return iso
  try {
    return new Intl.DateTimeFormat(locale.value === 'en' ? 'en-US' : 'hu-HU', {
      year: 'numeric', month: 'long', day: 'numeric', weekday: 'long',
    }).format(d)
  } catch { return iso }
}

// "2026-09-14T15:45" — the stamp the document itself carries. Rendered short,
// since it sits inline in the provenance line.
function stampLabel(stamp) {
  if (!stamp) return ''
  const d = new Date(stamp)
  if (Number.isNaN(d.getTime())) return stamp
  try {
    return new Intl.DateTimeFormat(locale.value === 'en' ? 'en-US' : 'hu-HU', {
      year: 'numeric', month: 'long', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    }).format(d)
  } catch { return stamp }
}

// The House Committee's next meeting as one line. Built here rather than in the
// template because Vue collapses the whitespace between interpolations, and the
// parts are optional: the page states a venue and a time, either of which can be
// missing.
const houseCommittee = computed(() => {
  const hc = data.value && data.value.houseCommittee
  if (!hc || !hc.date) return null
  return [dayLabel(hc.date), hc.time].filter(Boolean).join(' ')
    + (hc.place ? ' · ' + hc.place : '')
})

async function load() {
  loading.value = true
  failed.value = false
  try {
    data.value = await api.upcomingAgenda()
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<template>
  <section class="card pad upcoming">
    <div class="upcoming__head">
      <h2>{{ $t('upcoming.title') }}</h2>
      <span v-if="agenda && agenda.extraordinary" class="badge">
        {{ $t('upcoming.extraordinary') }}
      </span>
    </div>
    <StateBlock
      :loading="loading" :error="failed" :empty="!loading && !failed && !agenda"
      :empty-text="$t('upcoming.empty')" @retry="load"
    >
      <p class="upcoming__source soft">
        <a :href="agenda.url" target="_blank" rel="noopener">
          {{ agenda.label || $t('upcoming.sourceDoc') }}
          <span aria-hidden="true">↗</span>
        </a>
        <template v-if="agenda.statusLabel"> · {{ agenda.statusLabel }}</template>
        <template v-if="agenda.statusAt">
          · {{ $t('upcoming.asOf', { at: stampLabel(agenda.statusAt) }) }}
        </template>
      </p>

      <!-- The napirend is linked but holds no days: either the PDF could not be
           read (the link above is then still the useful thing on this card), or
           the House published one with nothing scheduled on it yet. -->
      <p v-if="!days.length" class="soft">{{ $t('upcoming.noItems') }}</p>

      <div v-for="day in days" :key="day.date || day.weekday" class="uday">
        <h3 class="uday__date">
          <time :datetime="day.date">{{ dayLabel(day.date) }}</time>
        </h3>
        <p class="uday__times soft">
          <span v-if="day.startsAt">{{ $t('upcoming.startsAt') }}: {{ day.startsAt }}</span>
          <span v-if="day.decisionsFrom && day.decisionsFrom.length">
            {{ $t('upcoming.decisionsFrom') }}: {{ day.decisionsFrom.join(', ') }}
          </span>
          <span v-if="day.endsNote">{{ $t('upcoming.endsAt') }}: {{ day.endsNote }}</span>
        </p>

        <ol class="uitems">
          <li v-for="(item, i) in shownItems(day)" :key="i" class="uitem">
            <span class="uitem__ord" aria-hidden="true">
              {{ item.ordinal != null ? item.ordinal + '.' : '·' }}
            </span>
            <div class="uitem__body">
              <p class="uitem__title">
                <!-- The order paper prints the iromány number and nothing else;
                     it links to the bill page only when that number resolves to
                     a bill we actually hold. -->
                <router-link
                  v-if="item.billId" class="uitem__code"
                  :to="{ name: 'bill', params: { id: item.billId } }"
                >{{ item.billCode }}</router-link>
                <span v-else-if="item.billCode" class="uitem__code plain">{{ item.billCode }}</span>
                {{ item.title }}
              </p>
              <p class="uitem__meta soft">
                <span v-if="item.stage">{{ item.stage }}</span>
                <span v-if="item.timeWindow">{{ item.timeWindow }}</span>
                <span v-if="item.submitter" class="uitem__who">{{ item.submitter }}</span>
              </p>
              <p v-if="item.flags && item.flags.length" class="uitem__flags">
                <span v-for="f in item.flags" :key="f" class="badge">
                  {{ $t('upcoming.flags.' + f) }}
                </span>
              </p>
            </div>
          </li>
        </ol>
      </div>

      <button
        v-if="hiddenCount" class="btn secondary upcoming__more"
        :aria-expanded="expanded" @click="expanded = !expanded"
      >
        {{ expanded ? $t('upcoming.showLess')
                    : $t('upcoming.showAll', { n: hiddenCount }) }}
      </button>

      <p v-if="houseCommittee" class="upcoming__hc soft">
        <strong>{{ $t('upcoming.houseCommittee') }}:</strong>
        <time :datetime="data.houseCommittee.date">{{ houseCommittee }}</time>
      </p>

      <p v-if="data.documents && data.documents.length" class="upcoming__docs soft">
        <span class="upcoming__docs-label">{{ $t('upcoming.documents') }}:</span>
        <a
          v-for="d in data.documents" :key="d.slug" :href="d.url"
          target="_blank" rel="noopener"
        >{{ d.label }}<span aria-hidden="true">↗</span></a>
      </p>
    </StateBlock>
  </section>
</template>

<style scoped>
.upcoming__head { display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; }
.upcoming h2 { margin: 0; font-size: 1.15rem; }
.upcoming__source { margin: .35rem 0 .9rem; font-size: .82rem; }
.upcoming__source a { color: var(--focus); }

.uday + .uday { margin-top: 1.1rem; }
/* No text-transform: Intl already casing the date the way the language wants is
   the point — Hungarian writes "2026. szeptember 14., hétfő" in lower case, and
   capitalising it here would be us overruling the locale. */
.uday__date {
  margin: 0; font-size: .95rem;
  border-bottom: 1px solid var(--line); padding-bottom: .3rem;
}
.uday__times {
  display: flex; flex-wrap: wrap; gap: .2rem .9rem;
  margin: .35rem 0 .5rem; font-size: .8rem;
}

.uitems { list-style: none; margin: 0; padding: 0; }
.uitem { display: flex; gap: .55rem; padding: .45rem 0; }
.uitem + .uitem { border-top: 1px solid var(--line); }
.uitem__ord {
  flex: 0 0 1.9rem; text-align: right; font-variant-numeric: tabular-nums;
  color: var(--ink-faint); font-size: .85rem; padding-top: .1rem;
}
.uitem__body { min-width: 0; }
.uitem__title { margin: 0; font-size: .9rem; line-height: 1.4; }
.uitem__code { font-weight: 700; margin-right: .35rem; color: var(--accent); }
.uitem__code.plain { color: var(--ink-faint); }
.uitem__meta {
  display: flex; flex-wrap: wrap; gap: .1rem .7rem; margin: .15rem 0 0;
  font-size: .78rem;
}
.uitem__who { font-style: italic; }
.uitem__flags { display: flex; flex-wrap: wrap; gap: .3rem; margin: .3rem 0 0; }

.upcoming__more { margin-top: .9rem; }
.upcoming__hc { margin: 1rem 0 0; font-size: .83rem; }
.upcoming__hc strong { margin-right: .3rem; }
.upcoming__docs-label { margin-right: -.4rem; }
.upcoming__docs {
  display: flex; flex-wrap: wrap; gap: .2rem .8rem;
  margin: .5rem 0 0; font-size: .8rem;
}
.upcoming__docs a { color: var(--focus); }

@media (max-width: 480px) {
  .uitem__ord { flex-basis: 1.4rem; }
}
</style>
