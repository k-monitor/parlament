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

// Suggested micro-amounts (HUF). Labels are literal money, not translated.
const AMOUNTS = [
  { value: '1000', emoji: '👍', label: '1 000 Ft' },
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
  <section v-if="visible" class="donate card" :aria-label="$t('donate.title')">
    <button
      v-if="dismissible" type="button" class="donate__close"
      :title="$t('donate.close')" :aria-label="$t('donate.close')" @click="dismiss"
    >
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"
           stroke-width="2" stroke-linecap="round" aria-hidden="true">
        <path d="M6 6l12 12M18 6L6 18" />
      </svg>
    </button>

    <div class="donate__brand">
      <img class="donate__mark" src="/parlamonitor.png" alt="" aria-hidden="true" />
      <span class="donate__name">Parlamonitor</span>
    </div>

    <h3 class="donate__title">{{ $t('donate.title') }}</h3>
    <p class="donate__desc">{{ $t('donate.description') }}</p>

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
.donate {
  position: relative;
  background: var(--accent-soft);
  border-color: #f0cfc9;
  padding: 1.1rem 1.2rem 1.2rem;
}
.donate__close {
  position: absolute; top: .6rem; right: .6rem;
  display: inline-flex; align-items: center; justify-content: center;
  width: 30px; height: 30px; padding: 0; cursor: pointer;
  border: 0; border-radius: 8px; background: transparent; color: var(--ink-faint);
}
.donate__close:hover, .donate__close:focus-visible { background: rgba(178,40,23,.12); color: var(--accent); outline: none; }

.donate__brand { display: inline-flex; align-items: center; gap: .45rem; margin-bottom: .6rem; }
.donate__mark { width: 1.6rem; height: 1.6rem; object-fit: contain; border-radius: .35rem; background: #fff; padding: .15rem; box-sizing: border-box; }
.donate__name { font-weight: 800; color: var(--accent); font-size: .95rem; }

.donate__title { margin: 0 0 .3rem; font-size: 1.2rem; color: var(--ink); }
.donate__desc { margin: 0 0 .9rem; color: var(--ink-soft); max-width: 62ch; }

.donate__amounts { display: flex; flex-wrap: wrap; gap: .5rem; margin-bottom: .9rem; }
.donate__amount {
  font: inherit; font-size: .9rem; font-weight: 600; cursor: pointer;
  padding: .45rem .8rem; border-radius: 999px;
  border: 1px solid var(--line); background: var(--surface); color: var(--ink-soft);
  transition: border-color .12s, color .12s, box-shadow .12s;
}
.donate__amount:hover, .donate__amount:focus-visible { border-color: var(--accent); color: var(--accent); outline: none; }
.donate__amount.active {
  border-color: var(--accent); color: var(--accent);
  box-shadow: inset 0 0 0 1px var(--accent);
}

.donate__actions { display: flex; flex-wrap: wrap; gap: .6rem; }
.donate__actions .btn { flex: 1 1 auto; text-align: center; min-width: 12rem; }
@media (max-width: 480px) {
  .donate__actions .btn { flex-basis: 100%; min-width: 0; }
}
</style>
