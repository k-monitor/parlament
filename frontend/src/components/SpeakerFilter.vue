<script setup>
// The search page's speaker filter (SEA-3): pick a person and the result list,
// the popularity chart and the breakdown are all scoped to what *they* said.
//
// The person is picked from suggestions rather than typed free-hand: the filter
// travels in the URL as an opaque upstream `person_id`, and a typed name would
// have to be resolved to one anyway — ambiguously, since the House has several
// people to a surname. So the control has two states: an input that suggests
// (SEA-7), and the chosen speaker as a removable chip.
import { ref, onUnmounted } from 'vue'
import { useSpeakerSuggest } from '../lib/speakerSuggest.js'
import SpeakerSuggestList from './SpeakerSuggestList.vue'

defineProps({
  // The active filter: a speaker object (`person_id` + `label`, and a photo when
  // one is known), or null for "anyone". The page owns it — it is URL state.
  modelValue: { type: Object, default: null },
  inputId: { type: String, default: 'f-speaker' },
})
const emit = defineEmits(['update:modelValue'])

const term = ref('')
// From the first character: this box takes nothing but a name, so there is no
// ordinary search term for a short fragment to get in the way of — and a single
// letter is exactly how someone looks for a person whose spelling they are
// unsure of. (The main search box keeps its floor; see speakerSuggest.js.)
const { speakers, loading, ask, clear } = useSpeakerSuggest({ minChars: 1 })
// Distinguishes "nothing typed yet" from "typed a name, and nobody matches it".
const asked = ref(false)

// The chip deliberately does NOT link to the profile: inside a form a link out
// of the page is a trap — the reader is here to narrow a search, not to leave it.
const PLACEHOLDER =
  'data:image/svg+xml;utf8,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48"><rect width="48" height="48" fill="#e7e5df"/><circle cx="24" cy="19" r="9" fill="#bdb9af"/><rect x="9" y="32" width="30" height="18" rx="9" fill="#bdb9af"/></svg>')
function onErr(e) { e.target.src = PLACEHOLDER }

function onInput() {
  asked.value = term.value.trim().length > 0
  ask(term.value)
}

function pick(s) {
  term.value = ''
  asked.value = false
  clear()
  emit('update:modelValue', { person_id: s.person_id, label: s.label, photo_uri: s.photo_uri })
}

// Escape abandons the half-typed name without touching the active filter.
function onEsc() {
  term.value = ''
  asked.value = false
  clear()
}

onUnmounted(clear)   // the debounce outlives the component
</script>

<template>
  <div class="speakerfilter">
    <label :for="inputId">{{ $t('search.speaker') }}</label>

    <!-- Chosen: the filter itself, with the one action it still needs. -->
    <div v-if="modelValue" class="picked">
      <img class="avatar sm" :src="modelValue.photo_uri || PLACEHOLDER" @error="onErr" alt="" />
      <span class="pickedname">{{ modelValue.label }}</span>
      <button
        type="button" class="drop" :aria-label="$t('search.speakerClear')"
        @click="emit('update:modelValue', null)"
      >×</button>
    </div>

    <!-- Not chosen: type a name, pick a person. -->
    <div v-else class="pickfield">
      <input
        :id="inputId" v-model="term" type="search" autocomplete="off"
        :placeholder="$t('search.speakerPlaceholder')"
        @input="onInput" @keydown.esc.stop.prevent="onEsc"
      />
      <SpeakerSuggestList
        :speakers="speakers" :loading="loading"
        :empty-text="asked && !loading && !speakers.length ? $t('search.speakerNoMatch') : ''"
        @pick="pick"
      />
      <p v-if="!asked && !speakers.length" class="muted small hint">
        {{ $t('search.speakerHint') }}
      </p>
    </div>
  </div>
</template>

<style scoped>
.speakerfilter label { display: block; font-weight: 600; font-size: .9rem; margin-bottom: .3rem; }
.pickfield { position: relative; }
.picked {
  display: flex; align-items: center; gap: .5rem;
  border: 1px solid var(--line); border-radius: 8px; padding: .25rem .35rem .25rem .4rem;
  background: var(--surface);
}
.pickedname { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.drop {
  margin-left: auto; border: 0; background: none; cursor: pointer; font-size: 1.2rem;
  line-height: 1; color: var(--ink-faint); padding: .1rem .35rem; border-radius: 6px;
}
.drop:hover, .drop:focus-visible { color: var(--ink); background: var(--accent-soft); }
.hint { margin: .3rem 0 0; }
</style>
