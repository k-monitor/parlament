<script setup>
// Micro-donation panel — a Vue port of K-Monitor's Voksmonitor donate card.
// One of three placements (footer link, About page, search value-moment); this
// component backs the latter two. The user picks a suggested amount (or none for
// a free amount) which feeds the paypal.me link; a second link leads to
// K-Monitor's full donation page (bank transfer, 1% tax offering, …).
//
// `dismissible` adds a close button; when set with a `storageKey` the dismissal
// persists in localStorage so a visitor who closed it once at a value-moment
// (e.g. after a search) isn't asked again. The About-page instance is permanent
// (no close button), so it never touches storage.
import { ref, computed } from 'vue'

const props = defineProps({
  dismissible: { type: Boolean, default: false },
  // localStorage key remembering a dismissal; only used when `dismissible`.
  storageKey: { type: String, default: '' },
})

const PAYPAL_BASE = 'https://www.paypal.me/kmonitor'
const MORE_OPTIONS_URL = 'https://tamogatas.k-monitor.hu/?utm_source=parlamonitor'

// Suggested micro-amounts (HUF), laid out as a 2×2 grid. Labels are literal
// money, not translated.
const AMOUNTS = [
  { value: '1000', emoji: '👍', label: '1 000 Ft' },
  { value: '2000', emoji: '🙏', label: '2 000 Ft' },
  { value: '5000', emoji: '❤️', label: '5 000 Ft' },
  { value: '10000', emoji: '🤩', label: '10 000 Ft' },
]

const selected = ref('1000')
function pick(value) { selected.value = selected.value === value ? null : value }

// paypal.me/kmonitor/1000HUF pre-fills the amount; with none selected it opens
// the plain profile so the donor can type a free amount.
const paypalUrl = computed(() =>
  selected.value ? `${PAYPAL_BASE}/${selected.value}HUF` : PAYPAL_BASE)

function readDismissed() {
  if (!props.dismissible || !props.storageKey) return false
  try { return localStorage.getItem(props.storageKey) === '1' } catch { return false }
}
const visible = ref(!readDismissed())
function dismiss() {
  visible.value = false
  if (props.storageKey) { try { localStorage.setItem(props.storageKey, '1') } catch { /* storage blocked */ } }
}
</script>

<template>
  <section
    v-if="visible" class="donate card" :class="{ 'donate--dismissible': dismissible }"
    :aria-label="$t('donate.title')"
  >
    <button
      v-if="dismissible" type="button" class="donate__close"
      :title="$t('donate.close')" :aria-label="$t('donate.close')" @click="dismiss"
    >
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"
           stroke-width="2" stroke-linecap="round" aria-hidden="true">
        <path d="M6 6l12 12M18 6L6 18" />
      </svg>
    </button>

    <div class="donate__text">
      <h3 class="donate__title">{{ $t('donate.title') }}</h3>
      <p class="donate__desc">{{ $t('donate.description') }}</p>
    </div>

    <div class="donate__amounts" role="group" :aria-label="$t('donate.amountsLabel')">
      <button
        v-for="a in AMOUNTS" :key="a.value" type="button"
        class="donate__amount" :class="{ active: selected === a.value }"
        :aria-pressed="selected === a.value" @click="pick(a.value)"
      >
        <span aria-hidden="true">{{ a.emoji }}</span> {{ a.label }}
      </button>
    </div>

    <div class="donate__actions">
      <a class="btn" :href="paypalUrl" target="_blank" rel="noopener noreferrer">
        {{ $t('donate.paypalButton') }}
      </a>
      <a class="btn secondary" :href="MORE_OPTIONS_URL" target="_blank" rel="noopener noreferrer">
        {{ $t('donate.moreOptionsButton') }}
      </a>
    </div>
  </section>
</template>

<style scoped>
/* Horizontal layout: description text on the left, the 2×2 amount grid to its
   right, and the two action buttons stacked at the right end. Flex-wrap lets it
   collapse to a single column at the narrower placements (About page, mobile). */
.donate {
  position: relative;
  background: var(--accent-soft);
  border-color: #f0cfc9;
  padding: 1.1rem 1.2rem 1.2rem;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 1rem 1.5rem;
}
.donate__close {
  position: absolute; top: .6rem; right: .6rem;
  display: inline-flex; align-items: center; justify-content: center;
  width: 30px; height: 30px; padding: 0; cursor: pointer;
  border: 0; border-radius: 8px; background: transparent; color: var(--ink-faint);
}
.donate__close:hover, .donate__close:focus-visible { background: rgba(178,40,23,.12); color: var(--accent); outline: none; }

.donate__text { flex: 1 1 18rem; min-width: 0; }
.donate__title { margin: 0 0 .3rem; font-size: 1.2rem; color: var(--ink); }
.donate__desc { margin: 0; color: var(--ink-soft); max-width: 62ch; }

.donate__amounts {
  flex: 0 0 auto;
  display: grid;
  grid-template-columns: repeat(2, minmax(7rem, 1fr));
  gap: .6rem;
}
/* Styled to match the action buttons below (.btn / .btn.secondary) so the two
   amount rows line up with the two stacked buttons; the selected amount fills
   with the accent, echoing the primary donate button. */
.donate__amount {
  display: inline-flex; align-items: center; justify-content: center; gap: .4rem;
  font: inherit; font-size: 1rem; font-weight: 600; cursor: pointer; white-space: nowrap;
  padding: .55rem 1rem; border-radius: var(--radius);
  border: 1px solid var(--line); background: var(--surface); color: var(--ink);
  transition: background .12s, border-color .12s, color .12s;
}
.donate__amount:hover, .donate__amount:focus-visible { background: #efeee9; outline: none; }
.donate__amount.active { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
.donate__amount.active:hover { background: #8e2012; }

.donate__actions { flex: 0 0 auto; display: flex; flex-direction: column; gap: .6rem; min-width: 13rem; }
.donate__actions .btn { width: 100%; text-align: center; white-space: nowrap; }

/* Keep the stacked buttons clear of the absolutely-positioned close button. */
.donate--dismissible .donate__actions { margin-top: .4rem; }
</style>
