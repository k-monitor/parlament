<script setup>
// The speaker suggestion dropdown (SEA-7). Purely presentational: the main query
// box and the speaker filter show the very same rows, so the markup and its
// chrome live here once. Each row is a real <button> so the list is reachable by
// keyboard (Tab through the options, Enter to pick) without a roving tabindex.
defineProps({
  speakers: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  // Said above the rows where a name means something other than what the reader
  // typed it for — in the main search box a picked name becomes a *filter*, not
  // a search term, and that has to be visible before the click.
  heading: { type: String, default: '' },
  // "No such speaker" is only worth saying where the typed text is meant to be a
  // name (the filter field). In the main box the text is usually a search term,
  // and telling the reader that "költségvetés" is nobody's name is noise.
  emptyText: { type: String, default: '' },
  // Overlays the content below it instead of pushing it down — for the main
  // search box, where the list appears while the reader is still typing. The
  // parent supplies the positioning context.
  floating: { type: Boolean, default: false },
})
defineEmits(['pick'])

const PLACEHOLDER =
  'data:image/svg+xml;utf8,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48"><rect width="48" height="48" fill="#e7e5df"/><circle cx="24" cy="19" r="9" fill="#bdb9af"/><rect x="9" y="32" width="30" height="18" rx="9" fill="#bdb9af"/></svg>')
function onErr(e) { e.target.src = PLACEHOLDER }
</script>

<template>
  <div
    v-if="speakers.length || loading || emptyText"
    class="suggestbox" :class="{ floating }"
  >
    <p v-if="heading && speakers.length" class="muted small sg-head">{{ heading }}</p>
    <ul v-if="speakers.length" class="suggestions">
      <li v-for="s in speakers" :key="s.person_id">
        <button type="button" class="suggestion" @click="$emit('pick', s)">
          <img class="avatar sm" :src="s.photo_uri || PLACEHOLDER" @error="onErr" alt="" loading="lazy" />
          <span class="s-name">{{ s.label }}</span>
          <span class="muted small s-meta">
            {{ $t('search.speakerSpeeches', { n: (s.speeches || 0).toLocaleString('hu-HU') }) }}
          </span>
        </button>
      </li>
    </ul>
    <p v-else-if="loading" class="muted small sg-note">{{ $t('app.loading') }}</p>
    <p v-else-if="emptyText" class="muted small sg-note">{{ emptyText }}</p>
  </div>
</template>

<style scoped>
.suggestbox {
  margin-top: .4rem; padding: .35rem;
  background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius);
}
/* Over the page, not shoving it: while the reader types in the main search box
   the hint, the filter panel and the results below must stay where they are. */
.suggestbox.floating {
  position: absolute; top: 100%; left: 0; right: 0; z-index: 30;
  box-shadow: var(--shadow);
}
.sg-head { margin: .15rem .5rem .35rem; }
.sg-note { margin: .35rem .5rem; }
.suggestions {
  list-style: none; margin: 0; padding: 0; display: grid; gap: .15rem;
  /* A four-letter fragment can still match dozens of people (and the House has
     many a "Nagy"): scroll the list rather than run it off the screen. */
  max-height: 19rem; overflow-y: auto;
}
.suggestion {
  display: flex; align-items: center; gap: .55rem; width: 100%;
  text-align: left; cursor: pointer; padding: .35rem .5rem; border-radius: 8px;
  border: 1px solid transparent; background: transparent; color: inherit; font: inherit;
}
.suggestion:hover, .suggestion:focus-visible { background: var(--accent-soft); border-color: #f0cfc9; }
.s-name { font-weight: 600; }
/* The activity figure sits right, away from the name it disambiguates. */
.s-meta { margin-left: auto; padding-left: .5rem; white-space: nowrap; }
</style>
