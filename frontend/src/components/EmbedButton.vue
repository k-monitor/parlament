<script setup>
// "Embed this figure" control, pinned to the bottom-right corner of a chart. It
// opens a popover with a ready-to-paste <iframe> snippet pointing at the
// chrome-free `/embed/:kind` view (EmbedView). The snippet always carries the
// **currently selected electoral cycle scope** (store.cycles) plus the chart-specific
// params the host passes, so what a reader embeds matches what they are looking
// at. The host must give its chart container `position: relative` — this button
// positions itself absolutely within it.
//
// The popover reuses ShareButton's teleport-to-body + fixed-position machinery so
// it is never clipped by a chart card's overflow.
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { store, currentCycleLabel, serializeCycles } from '../store.js'

const props = defineProps({
  // Which embed view to point at: 'search-trend' | 'faction-speaking' |
  // 'questions-sankey' | 'faction-cohesion' | 'vote-participation'.
  kind: { type: String, required: true },
  // Chart-specific query params (q, id, filters). Empty/nullish values dropped.
  params: { type: Object, default: () => ({}) },
  // Human title of the figure — used as the iframe's title attribute (a11y).
  title: { type: String, default: '' },
  // Default iframe height in px (charts differ: a pie is short, a sankey tall).
  height: { type: Number, default: 420 },
  // Sensible max width for the responsive iframe (wide diagrams pass more).
  maxWidth: { type: Number, default: 680 },
})

const { t, locale } = useI18n()
const open = ref(false)
const copied = ref(false)
const btnRef = ref(null)
const menuRef = ref(null)
const menuStyle = ref({})

// The scope to bake into the embed URL: 'all' or the period numbers ("43,44").
const cycleValue = computed(() => serializeCycles(store.cycles))
const cycleLabel = computed(() => currentCycleLabel() || t('cycle.all'))

// Absolute URL of the chrome-free chart, carrying the current cycle + params (+
// the active language, so an English page embeds the English chart).
const embedUrl = computed(() => {
  const url = new URL(`/embed/${props.kind}`, location.origin)
  url.searchParams.set('cycle', cycleValue.value)
  for (const [k, v] of Object.entries(props.params || {})) {
    if (v === undefined || v === null || v === '') continue
    url.searchParams.set(k, v)
  }
  if (locale.value && locale.value !== 'hu') url.searchParams.set('lang', locale.value)
  return url.toString()
})

// The paste-ready iframe. Responsive (width:100% up to a max), no border chrome
// beyond a subtle frame, and lazily loaded so it never blocks the host page.
const snippet = computed(() => {
  const titleAttr = (props.title || 'Parlamonitor').replace(/"/g, '&quot;')
  return `<iframe src="${embedUrl.value}" title="${titleAttr}" width="${props.maxWidth}" height="${props.height}" style="width:100%;max-width:${props.maxWidth}px;border:1px solid #e5e2db;border-radius:12px" loading="lazy"></iframe>`
})

async function toggle() {
  if (open.value) { close(); return }
  const r = btnRef.value.getBoundingClientRect()
  // Embed menus align to the button's right edge (it sits in the bottom-right).
  menuStyle.value = {
    position: 'fixed',
    top: `${r.bottom + 6}px`,
    right: `${window.innerWidth - r.right}px`,
  }
  open.value = true
  await nextTick()
  reposition()
}
function close() { open.value = false }

// Keep the popover glued to its button, flipping above / clamping on-screen.
function reposition() {
  raf = 0
  if (!open.value || !btnRef.value || !menuRef.value) return
  const r = btnRef.value.getBoundingClientRect()
  if (r.bottom < 0 || r.top > window.innerHeight) { close(); return }
  const { width, height } = menuRef.value.getBoundingClientRect()
  let left = r.right - width
  let top = r.bottom + 6
  if (top + height > window.innerHeight - 8 && r.top - 6 - height >= 8) top = r.top - 6 - height
  left = Math.min(Math.max(8, left), Math.max(8, window.innerWidth - width - 8))
  top = Math.min(Math.max(8, top), Math.max(8, window.innerHeight - height - 8))
  menuStyle.value = { position: 'fixed', top: `${top}px`, left: `${left}px` }
}

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

async function copyCode() {
  const code = snippet.value
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(code)
    } else {
      const ta = document.createElement('textarea')
      ta.value = code
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    copied.value = true
    setTimeout(() => { copied.value = false }, 1800)
  } catch {
    /* clipboard blocked — the textarea is selectable, so leave the menu open */
  }
}
</script>

<template>
  <span class="embed-fab" @keydown.esc="close">
    <button
      ref="btnRef" type="button" class="embed-trigger"
      :aria-expanded="open" aria-haspopup="dialog"
      :title="$t('embed.label')" :aria-label="$t('embed.label')" @click="toggle"
    >
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
           stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" />
      </svg>
      <span class="embed-txt">{{ $t('embed.label') }}</span>
    </button>

    <Teleport to="body">
      <div v-if="open" ref="menuRef" class="embed-menu" :style="menuStyle"
           role="dialog" :aria-label="$t('embed.title')" @keydown.esc="close">
        <p class="embed-h">{{ $t('embed.title') }}</p>
        <p class="embed-note">{{ $t('embed.cycleNote', { cycle: cycleLabel }) }}</p>
        <textarea class="embed-code" readonly rows="4" :value="snippet"
                  @focus="$event.target.select()"></textarea>
        <div class="embed-actions">
          <button type="button" class="embed-copy" @click="copyCode">
            {{ copied ? $t('embed.copied') : $t('embed.copy') }}
          </button>
          <a :href="embedUrl" target="_blank" rel="noopener" class="embed-preview">{{ $t('embed.preview') }}</a>
        </div>
      </div>
    </Teleport>
  </span>
</template>

<style scoped>
/* A normal inline control; the host places it in a bottom-right footer row
   (`.fig-foot`) so it sits flush in the chart card's corner without overlapping
   the chart content. */
.embed-fab { display: inline-flex; }
.embed-trigger {
  display: inline-flex; align-items: center; gap: .3rem; line-height: 1;
  font: inherit; font-size: .75rem; font-weight: 600; cursor: pointer;
  padding: .32rem .6rem; border-radius: 999px;
  border: 1px solid var(--line); background: var(--surface); color: var(--ink-soft);
  transition: border-color .12s, color .12s, background .12s;
}
.embed-trigger:hover, .embed-trigger:focus-visible, .embed-trigger[aria-expanded="true"] {
  border-color: var(--accent); color: var(--accent); outline: none;
}

.embed-menu {
  z-index: 1000; width: min(92vw, 360px);
  background: var(--surface); border: 1px solid var(--line);
  border-radius: 10px; box-shadow: 0 6px 20px rgba(0, 0, 0, .16);
  padding: .7rem .75rem; text-align: left;
}
.embed-h { margin: 0 0 .15rem; font-weight: 700; font-size: .92rem; color: var(--ink); }
.embed-note { margin: 0 0 .5rem; font-size: .76rem; color: var(--ink-soft); }
.embed-code {
  width: 100%; box-sizing: border-box; resize: vertical;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: .72rem; line-height: 1.4; color: var(--ink);
  background: var(--bg); border: 1px solid var(--line); border-radius: 7px; padding: .5rem .6rem;
}
.embed-actions { display: flex; align-items: center; gap: .8rem; margin-top: .55rem; }
.embed-copy {
  font: inherit; font-size: .82rem; font-weight: 700; cursor: pointer;
  padding: .4rem .85rem; border-radius: 7px; border: 0;
  background: var(--accent); color: var(--accent-ink, #fff);
}
.embed-copy:hover, .embed-copy:focus-visible { filter: brightness(1.05); outline: none; }
.embed-preview { font-size: .82rem; font-weight: 600; color: var(--ink-soft); }
.embed-preview:hover { color: var(--accent); }

@media (max-width: 560px) {
  /* Keep the corner button unobtrusive on small charts — icon only. */
  .embed-txt { display: none; }
}
</style>
