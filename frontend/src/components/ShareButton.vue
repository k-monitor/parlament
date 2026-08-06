<script setup>
// Reusable "Share this" control. Opens a small popover menu offering the
// platforms requested: Facebook, X, Bluesky and copy-to-clipboard, plus the
// native Web Share sheet when the browser/device supports it (mobile). The URL
// defaults to the current page (these are all deep-linkable views); the `title`
// prop is used as the pre-filled post text on X/Bluesky and the share sheet.
//
// The menu is teleported to <body> and positioned with fixed coordinates so it
// is never clipped when the button lives inside a scroll container (e.g. the
// viewer's transcript, which is `overflow-y: auto`).
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { copyText } from '../lib/clipboard.js'

const props = defineProps({
  // Human title of the thing being shared. Used as post text.
  title: { type: String, default: '' },
  // Explicit URL to share; defaults to the live page URL at click time.
  url: { type: String, default: '' },
  // Which edge the menu aligns to — 'right' opens it leftward from the button.
  align: { type: String, default: 'left' },
  // Icon-only trigger, for dense rows (speech rows, transcript sentences).
  compact: { type: Boolean, default: false },
})

const { t } = useI18n()
const open = ref(false)
const copied = ref(false)
const btnRef = ref(null)
const menuRef = ref(null)
const menuStyle = ref({})

// navigator.share only exists in a secure context and on supporting (mostly
// mobile) browsers; feature-detect once so the extra item only shows where it
// actually works.
const canNativeShare = ref(false)
onMounted(() => {
  canNativeShare.value = typeof navigator !== 'undefined' && typeof navigator.share === 'function'
})

function currentUrl() {
  return props.url || window.location.href
}
const shareText = computed(() => props.title || (typeof document !== 'undefined' ? document.title : ''))

async function toggle() {
  if (open.value) { close(); return }
  // Position synchronously from the button's rect so the menu never flashes at
  // the wrong spot, then measure once mounted to flip up / clamp on-screen.
  const r = btnRef.value.getBoundingClientRect()
  menuStyle.value = {
    position: 'fixed',
    top: `${r.bottom + 6}px`,
    ...(props.align === 'right' ? { right: `${window.innerWidth - r.right}px` } : { left: `${r.left}px` }),
  }
  open.value = true
  await nextTick()
  reposition()
}
function close() { open.value = false }

// Recompute the menu's fixed position from the button's current rect: flip above
// when it would overflow the bottom, and clamp on-screen. Called on open and on
// scroll/resize so the menu stays glued to its button instead of closing (a
// click can itself scroll the button into view, which would otherwise close the
// menu the instant it opened).
function reposition() {
  raf = 0
  if (!open.value || !btnRef.value || !menuRef.value) return
  const r = btnRef.value.getBoundingClientRect()
  // Button scrolled out of view entirely → nothing to anchor to; dismiss.
  if (r.bottom < 0 || r.top > window.innerHeight) { close(); return }
  const { width, height } = menuRef.value.getBoundingClientRect()
  let left = props.align === 'right' ? r.right - width : r.left
  let top = r.bottom + 6
  if (top + height > window.innerHeight - 8 && r.top - 6 - height >= 8) top = r.top - 6 - height
  left = Math.min(Math.max(8, left), Math.max(8, window.innerWidth - width - 8))
  top = Math.min(Math.max(8, top), Math.max(8, window.innerHeight - height - 8))
  menuStyle.value = { position: 'fixed', top: `${top}px`, left: `${left}px` }
}

// Close on outside interaction. The menu is teleported out of `root`, so both
// the trigger and the menu must be treated as "inside".
function onDocPointer(e) {
  if (!open.value) return
  const target = e.target
  if (btnRef.value?.contains(target) || menuRef.value?.contains(target)) return
  close()
}
let raf = 0
function onScrollOrResize() { if (open.value && !raf) raf = requestAnimationFrame(reposition) }
onMounted(() => {
  document.addEventListener('pointerdown', onDocPointer)
  window.addEventListener('scroll', onScrollOrResize, true)
  window.addEventListener('resize', onScrollOrResize)
})
onUnmounted(() => {
  document.removeEventListener('pointerdown', onDocPointer)
  window.removeEventListener('scroll', onScrollOrResize, true)
  window.removeEventListener('resize', onScrollOrResize)
  if (raf) cancelAnimationFrame(raf)
})

function intentUrl(target) {
  const url = currentUrl()
  const text = shareText.value
  const u = encodeURIComponent(url)
  switch (target) {
    case 'facebook':
      return `https://www.facebook.com/sharer/sharer.php?u=${u}`
    case 'x':
      return `https://twitter.com/intent/tweet?url=${u}&text=${encodeURIComponent(text)}`
    case 'bluesky':
      // Bluesky's compose intent takes a single `text` field, so fold the URL in.
      return `https://bsky.app/intent/compose?text=${encodeURIComponent(text ? `${text} ${url}` : url)}`
    default:
      return url
  }
}

function openTarget(target) {
  window.open(intentUrl(target), '_blank', 'noopener,noreferrer,width=620,height=560')
  close()
}

async function nativeShare() {
  close()
  try {
    await navigator.share({ title: shareText.value, url: currentUrl() })
  } catch {
    /* user dismissed the sheet, or it's unavailable — nothing to do */
  }
}

async function copyLink() {
  // Clipboard blocked → no confirmation, and the menu stays open so the user can
  // try another target.
  if (await copyText(currentUrl())) {
    copied.value = true
    setTimeout(() => { copied.value = false }, 1800)
  }
}
</script>

<template>
  <span class="share" @keydown.esc="close">
    <button
      ref="btnRef" type="button" class="share-btn" :class="{ compact }"
      :aria-expanded="open" aria-haspopup="menu"
      :title="$t('share.label')" :aria-label="$t('share.label')" @click="toggle"
    >
      <svg class="share-ic" viewBox="0 0 24 24" :width="compact ? 16 : 16" :height="compact ? 16 : 16" fill="none"
           stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <circle cx="18" cy="5" r="3" /><circle cx="6" cy="12" r="3" /><circle cx="18" cy="19" r="3" />
        <line x1="8.6" y1="10.5" x2="15.4" y2="6.5" /><line x1="8.6" y1="13.5" x2="15.4" y2="17.5" />
      </svg>
      <span v-if="!compact" class="share-txt">{{ $t('share.label') }}</span>
    </button>

    <Teleport to="body">
      <div v-if="open" ref="menuRef" class="share-menu" :style="menuStyle"
           role="menu" :aria-label="$t('share.menu')" @keydown.esc="close">
        <button v-if="canNativeShare" type="button" class="share-item" role="menuitem" @click="nativeShare">
          <span class="ic" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M4 12v7a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-7" /><polyline points="8 6 12 2 16 6" /><line x1="12" y1="2" x2="12" y2="15" />
            </svg>
          </span>
          {{ $t('share.native') }}
        </button>

        <button type="button" class="share-item" role="menuitem" @click="openTarget('facebook')">
          <span class="ic" aria-hidden="true">
            <svg viewBox="0 0 24 24"><path fill="#1877F2" d="M24 12.07C24 5.4 18.63 0 12 0S0 5.4 0 12.07C0 18.1 4.39 23.1 10.13 24v-8.44H7.08v-3.49h3.05V9.41c0-3.02 1.79-4.69 4.53-4.69 1.31 0 2.68.24 2.68.24v2.97h-1.51c-1.49 0-1.95.93-1.95 1.88v2.26h3.32l-.53 3.49h-2.79V24C19.61 23.1 24 18.1 24 12.07z" /></svg>
          </span>
          {{ $t('share.facebook') }}
        </button>

        <button type="button" class="share-item" role="menuitem" @click="openTarget('x')">
          <span class="ic ic-x" aria-hidden="true">
            <svg viewBox="0 0 24 24"><path fill="currentColor" d="M18.9 1.15h3.68l-8.04 9.19L24 22.85h-7.41l-5.8-7.58-6.64 7.58H.46l8.6-9.83L0 1.15h7.6l5.24 6.93 6.06-6.93zm-1.29 19.5h2.04L6.48 3.24H4.29l13.32 17.41z" /></svg>
          </span>
          {{ $t('share.x') }}
        </button>

        <button type="button" class="share-item" role="menuitem" @click="openTarget('bluesky')">
          <span class="ic" aria-hidden="true">
            <svg viewBox="0 0 600 530"><path fill="#1185FE" d="M135.7 44C202.6 94.3 274.5 195.9 301 250.6c26.5-54.7 98.4-156.3 165.3-206.6C514.6 7.8 593-30.5 593 58.9c0 17.9-10.2 149.9-16.2 171.3-20.9 74.5-96.9 93.5-164.5 82 118.1 20.1 148.2 86.7 83.3 153.3-123.2 126.5-177-31.7-190.8-72.2-2.6-7.4-3.8-10.9-3.8-7.9 0-3-1.2.5-3.8 7.9-13.8 40.5-67.6 198.7-190.8 72.2-64.9-66.6-34.8-133.2 83.3-153.3-67.6 11.5-143.6-7.5-164.5-82C10.2 208.8 0 76.8 0 58.9 0-30.5 78.4 7.8 135.7 44z" /></svg>
          </span>
          {{ $t('share.bluesky') }}
        </button>

        <button type="button" class="share-item" role="menuitem" @click="copyLink">
          <span class="ic" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M10 13a5 5 0 0 0 7.07 0l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" /><path d="M14 11a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
            </svg>
          </span>
          {{ copied ? $t('share.copied') : $t('share.copy') }}
        </button>
      </div>
    </Teleport>
  </span>
</template>

<style scoped>
.share { position: relative; display: inline-flex; vertical-align: middle; }
.share-btn {
  display: inline-flex; align-items: center; gap: .4rem;
  font: inherit; font-size: .85rem; font-weight: 600; cursor: pointer;
  padding: .35rem .7rem; border-radius: 999px;
  border: 1px solid var(--line); background: var(--surface); color: var(--ink-soft);
  transition: border-color .12s, color .12s, background .12s;
}
.share-btn:hover, .share-btn:focus-visible, .share-btn[aria-expanded="true"] {
  border-color: var(--accent); color: var(--accent); outline: none;
}
/* Icon-only variant for dense rows — matches the neighbouring icon buttons. */
.share-btn.compact {
  gap: 0; padding: .4rem; border: 0; border-radius: 6px;
  background: transparent; color: var(--muted); font-weight: 400;
}
.share-btn.compact:hover, .share-btn.compact:focus-visible, .share-btn.compact[aria-expanded="true"] {
  background: var(--line); color: var(--accent);
}
.share-ic { flex: none; }

.share-menu {
  z-index: 1000; min-width: 210px;
  background: var(--surface); border: 1px solid var(--line);
  border-radius: 10px; box-shadow: 0 6px 20px rgba(0, 0, 0, .16);
  padding: .35rem; display: flex; flex-direction: column; text-align: left;
}
.share-item {
  display: flex; align-items: center; gap: .65rem;
  font: inherit; font-size: .9rem; font-weight: 500; text-align: left; cursor: pointer;
  padding: .5rem .6rem; border: 0; border-radius: 7px; background: none; color: var(--ink);
  width: 100%;
}
.share-item:hover, .share-item:focus-visible { background: var(--bg); outline: none; }
.share-item .ic { flex: none; width: 18px; height: 18px; display: inline-flex; }
.share-item .ic svg { width: 18px; height: 18px; display: block; }
/* The X mark is monochrome — tint it with the current ink colour. */
.share-item .ic-x { color: var(--ink); }
</style>
