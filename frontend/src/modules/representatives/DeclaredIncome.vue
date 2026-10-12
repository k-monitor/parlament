<script setup>
// The income a person lists in their newest EVNYR declaration (REP-19), entry by
// entry, on the profile's income card under the parliamentary fee (REP-17).
//
// The amounts are the declarant's own "havi átlagos jövedelem" texts, which the
// API reads into numbers (backend/app/declared_income.py). What this view adds is
// only how they read:
//
// * **No total.** The entries mix gross and net, months and years, now and the
//   three years before, so a sum would be a figure nobody declared (TRUST-1).
// * **Gross or net only where the declarant said so.** The published fee above
//   is gross; a bare declared figure may be either, and labelling it would invent
//   the comparison the reader is most likely to make.
// * **The text stays in sight** wherever it says more than the number does (a
//   year, "kb.", a range), and is shown *instead* of a number when the API could
//   not read one unambiguously (several amounts in one field, words only).
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatDate } from '../../format.js'

const props = defineProps({
  income: { type: Object, required: true },
  // Whether the card also carries the published fee: the heading then says
  // "other income", and an entry that is the mandate itself points up to it.
  withFee: { type: Boolean, default: false },
})
const { t, te, locale } = useI18n()

const numLocale = computed(() => (locale.value === 'hu' ? 'hu-HU' : 'en-GB'))
const fmt = (n) => Number(n).toLocaleString(numLocale.value, { maximumFractionDigits: 2 })
// Forints read as "Ft" in Hungarian and "HUF" in English; anything else by its code.
const cur = (c) => (c === 'HUF' ? t('income.huf') : c)

const decl = computed(() => props.income.declaration || {})
const declTitle = computed(() => {
  const type = decl.value.type
  if (!type) return t('profile.declarationTitleUntyped')
  const key = type.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
  return t('profile.declarationTitle', {
    type: te(`profile.declarationTypes.${key}`) ? t(`profile.declarationTypes.${key}`) : type,
  })
})
const groups = computed(() => (props.income.groups || []).map((g) => {
  const key = `declaration.blocks.${g.path.replace(/\./g, '_')}`
  return { ...g, title: te(key) ? t(key) : g.path }
}))

// "kb. nettó 180 000 Ft": the qualifiers the declarant wrote, in the order
// Hungarian writes them, then the figure.
function amount(r) {
  const span = r.amount_max != null ? `${fmt(r.amount)}–${fmt(r.amount_max)}` : fmt(r.amount)
  return [r.approx ? t('income.approx') : '', r.basis ? t(`income.basis.${r.basis}`) : '',
    `${span} ${cur(r.currency)}`].filter(Boolean).join(' ')
}
// The period goes beside the amount only when it is not the field's own month:
// "/ hó" on every row would be noise, "/ év" on one row is the thing to see.
const period = (r) => (r.period_stated && r.period !== 'month' ? t(`income.per.${r.period}`) : '')

// What an amount says beyond its number, currency and basis: when anything is
// left over ("(2025-ben)", "kb.", "-tól -ig"), the text goes under the figure.
const PLAIN = /^[\s\d.,'\u00a0\u202f-]*(?:ft\.?|huf|forint|eur|euró|€|usd|\$|gbp|£|chf|pln|zł|ron|czk)?[\s.,-]*$/i
function showRaw(r) {
  return r?.status === 'ok' && !PLAIN.test(r.raw.replace(/nett[óo]|brutt[óo]/gi, ''))
}

// The small print under an entry: who pays, the second statement of the same
// income (net beside gross, the forints beside a foreign amount), the
// declarant's exchange rate.
function meta(item) {
  const r = item.income
  const out = []
  if (item.payer) out.push(item.payer)
  if (r?.status === 'ok') {
    if (r.alt) {
      const alt = [r.alt.basis ? t(`income.basis.${r.alt.basis}`) : '',
        `${fmt(r.alt.amount)} ${cur(r.alt.currency || r.currency)}`,
        r.alt.period && r.alt.period !== 'month' ? t(`income.per.${r.alt.period}`) : '']
      out.push(alt.filter(Boolean).join(' '))
    }
  }
  if (item.rate) out.push(t('income.rate', { rate: item.rate }))
  return out.join(' · ')
}
</script>

<template>
  <div class="dinc">
    <h3 class="dinc-head">{{ withFee ? $t('income.otherTitle') : $t('income.title') }}</h3>
    <p class="small muted dinc-src">
      <a :href="decl.url" target="_blank" rel="noopener">{{ declTitle }}</a>
      <template v-if="decl.finalizedAt">, {{ $t('profile.declarationFinalized', { date: formatDate(decl.finalizedAt) }) }}</template>
    </p>

    <p v-if="!groups.length" class="small">{{ $t('income.empty') }}</p>
    <div v-for="g in groups" :key="g.path" class="dinc-group" :class="{ previous: g.when === 'previous' }">
      <h4>{{ g.title }}</h4>
      <ul class="plain">
        <li v-for="(it, i) in g.items" :key="i" class="small dinc-item">
          <div class="dinc-row">
            <span class="dinc-name">
              {{ it.name || $t('income.unnamed') }}<span v-if="it.detail" class="muted"> · {{ it.detail }}</span>
            </span>
            <span class="dinc-amt">
              <template v-if="!it.income"><span class="muted">{{ $t('income.notGiven') }}</span></template>
              <template v-else-if="it.income.status === 'ok'">
                <strong>{{ amount(it.income) }}</strong> <span v-if="period(it.income)" class="muted">{{ period(it.income) }}</span>
              </template>
              <template v-else-if="it.income.status === 'none'"><span class="muted">{{ $t('income.noPay') }}</span></template>
              <!-- Several amounts, or words only: the text as filed, never a pick. -->
              <q v-else class="dinc-asfiled">{{ it.income.raw }}</q>
            </span>
          </div>
          <p v-if="meta(it)" class="muted dinc-meta">{{ meta(it) }}</p>
          <p v-if="showRaw(it.income)" class="muted dinc-meta">{{ $t('income.asFiled') }}: <q>{{ it.income.raw }}</q></p>
          <p v-if="it.income && it.income.status === 'multiple'" class="muted dinc-meta">{{ $t('income.multiple') }}</p>
          <p v-else-if="it.income && it.income.status === 'unparsed'" class="muted dinc-meta">{{ $t('income.unparsed') }}</p>
          <p v-if="withFee && it.parliamentary" class="muted dinc-meta">{{ $t('income.parliamentary') }}</p>
        </li>
      </ul>
    </div>

    <ul class="plain dinc-notes">
      <li v-if="income.unpaid" class="small muted">{{ $t('income.unpaid', { n: income.unpaid }, income.unpaid) }}</li>
      <li class="small muted">{{ $t('income.caveat') }}</li>
    </ul>
  </div>
</template>

<style scoped>
.dinc-group ul, .dinc-notes { list-style: none; margin: 0; padding: 0; }
.dinc-head { margin: 0 0 .1rem; font-size: .95rem; }
.dinc-src { margin: 0 0 .5rem; }
.dinc-group + .dinc-group { margin-top: .6rem; }
.dinc-group h4 { margin: 0 0 .25rem; font-size: .8rem; font-weight: 600; color: var(--ink-soft); }
.dinc-item + .dinc-item { margin-top: .35rem; }
/* Name left, amount right, as in the fee's month list above; a long name wraps
   rather than pushing the amount off the card. */
.dinc-row { display: flex; align-items: baseline; gap: .6rem; }
.dinc-name { min-width: 0; overflow-wrap: anywhere; }
.dinc-amt { margin-left: auto; text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.dinc-amt strong { font-weight: 600; }
.dinc-asfiled { white-space: normal; font-style: italic; overflow-wrap: anywhere; }
.dinc-meta { margin: .05rem 0 0; font-size: .74rem; overflow-wrap: anywhere; }
/* The three years before: listed, but quieter than what is earned now. */
.previous .dinc-amt strong { font-weight: 500; }
/* The small print, under a rule as the fee's caveats are. */
.dinc-notes { margin-top: .6rem; padding-top: .5rem; border-top: 1px solid var(--line); display: grid; gap: .3rem; }
@media (max-width: 520px) {
  .dinc-row { flex-wrap: wrap; }
  .dinc-amt { white-space: normal; }
}
</style>
