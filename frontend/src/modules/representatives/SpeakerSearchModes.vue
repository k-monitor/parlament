<script setup>
// The two ways of finding a representative on the *Felszólalók* page: by **name**
// (the browsable list, REP-1) and by **place** ("Ki a képviselőm?" — the
// constituency lookup, REP-10). It is one question with two keys, so they belong
// in the same search card: a reader who does not know their MP's name cannot
// start from the list, and a reader who does should not have to find out that a
// separate page exists for the other case.
//
// Each mode is a **route**, not a local toggle. The lookup keeps the
// `/representatives/lookup` address it was published under — its share card
// (og.py), its inbound links and a resolved answer's URL all stay valid — and
// Back moves between the modes as it moves between pages.
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { store } from '../../store.js'

const props = defineProps({
  // 'name' — the list; 'place' — the constituency lookup.
  mode: { type: String, required: true },
})
const route = useRoute()

// The lookup's data comes from outside parlament.hu, so the backend can switch it
// off on its own (REP-10); with nowhere to switch to, a one-way switch is noise.
const enabled = computed(() => store.featureEnabled('constituency_lookup'))

// Coming back to the list lands on the Képviselők chip with a clean query: both
// modes search on `q`, but there it means a person's name and here a settlement,
// so nothing can be carried across. Staying in the name mode keeps the reader's
// own list (their chip, filters and sort) exactly as it is.
const nameTo = computed(() => (props.mode === 'name'
  ? { name: route.name, query: route.query }
  : { name: 'representatives' }))
</script>

<template>
  <nav v-if="enabled" class="modes" :aria-label="$t('reps.searchMode')">
    <router-link
      :to="nameTo" class="mode" :class="{ active: mode === 'name' }"
      :aria-current="mode === 'name' ? 'page' : undefined"
    >{{ $t('reps.modeName') }}</router-link>
    <router-link
      :to="{ name: 'lookup' }" class="mode" :class="{ active: mode === 'place' }"
      :aria-current="mode === 'place' ? 'page' : undefined"
    ><span aria-hidden="true">📍</span> {{ $t('lookup.title') }}</router-link>
  </nav>
</template>

<style scoped>
/* A segmented control rather than another row of pills. The page already has a
   pill row one line below — the category chips, which filter the list — and these
   two switch what the card itself asks for, a bigger move than a filter. So they
   borrow the *form control* language instead: the same rounded-rectangle corner as
   the search field and the Szűrők button beside it, joined in one track, which
   reads as "either/or" where separate pills would read as one more filter. */
.modes {
  display: flex; flex-wrap: wrap; gap: .2rem; width: fit-content; max-width: 100%;
  margin: 0 0 .8rem; padding: .2rem; border: 1px solid var(--line);
  border-radius: var(--radius); background: var(--bg);
}
.mode {
  display: inline-flex; align-items: center; gap: .3rem; padding: .38rem .9rem;
  border-radius: calc(var(--radius) - 3px); font-size: .9rem; font-weight: 600;
  line-height: 1.4; color: var(--ink-soft);
}
.mode:hover { color: var(--accent); background: var(--accent-soft); text-decoration: none; }
/* The selected mode is filled, and `aria-current` says so independently of the
   fill (A11Y-1). */
.mode.active, .mode.active:hover {
  background: var(--accent); color: var(--accent-ink); box-shadow: var(--shadow);
}
</style>
