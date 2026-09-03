<script setup>
// Elemzések (§4E) — the section's front door: one card per analysis, built from
// the registry so the page grows by itself as analyses are added.
//
// The section is a *navigation* grouping, not a module: each analysis still
// belongs to the module whose data it reads (votes, bills, settlements), so a
// disabled module takes its card with it (EXT-6) and nothing here needs to know
// which. A within-cycle analysis under the "all cycles" scope stays on the page
// but is shown as unavailable, with the reason — silently dropping it would
// leave the reader wondering where it went.
import { computed } from 'vue'
import { store } from '../../store.js'
import { ANALYSES, analysisAvailable, analysisEnabled } from './registry.js'

const cards = computed(() => ANALYSES.filter(analysisEnabled).map((a) => ({
  ...a, available: analysisAvailable(a),
})))
</script>

<template>
  <!-- No heading here: the section masthead above the page (App.vue) carries the
       section's name and lead on every page of Elemzések, and is this page's own
       `h1`. Repeating them would say the same thing twice, a screen reader
       included. -->
  <section v-if="cards.length" class="grid explore-grid">
    <component
      :is="c.available ? 'router-link' : 'div'" v-for="c in cards" :key="c.key"
      :to="c.available ? { name: c.route } : undefined"
      class="card feature" :class="{ 'feature--off': !c.available }"
    >
      <span class="feature__icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round" stroke-linejoin="round">
          <path v-for="(d, i) in c.icon" :key="i" :d="d" />
        </svg>
      </span>
      <span class="feature__source">{{ $t('analyses.cards.' + c.key + '.source') }}</span>
      <h2>{{ $t('analyses.cards.' + c.key + '.title') }}</h2>
      <p class="soft">{{ $t('analyses.cards.' + c.key + '.desc') }}</p>
      <span v-if="c.available" class="feature__cta" aria-hidden="true">→</span>
      <span v-else class="feature__note small">{{ $t('analyses.needsCycle') }}</span>
    </component>
  </section>
  <p v-else class="muted">{{ $t('analyses.empty') }}</p>

  <p v-if="cards.length" class="small soft note">{{ $t('analyses.note') }}</p>
</template>

<style scoped>
/* The cards themselves are the site-wide `.feature` entry card (styles.css),
   shared with the home page so the two read as one system. Only what is
   particular to this page lives here. */
.note { max-width: 70ch; margin-top: 1.2rem; }
/* The source of the numbers, above the title: these are views onto another
   part of the corpus, and saying which is half of what makes them readable. */
.feature__source {
  font-size: .72rem; font-weight: 700; letter-spacing: .04em; text-transform: uppercase;
  color: var(--ink-faint); margin-bottom: .3rem;
}
/* An analysis that exists but can't be opened under the current cycle scope:
   the card stays in place, greyed, with the reason where its arrow would be. */
.feature--off { opacity: .68; }
.feature--off:hover { transform: none; box-shadow: var(--shadow); border-color: var(--line); }
.feature__note { color: var(--ink-faint); margin-top: 1rem; }
</style>
