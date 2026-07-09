<script setup>
// Faction label with its consistent colour dot (REP-4). Colour is decorative;
// the label carries the meaning, so it stays readable without colour (A11Y).
// With `link` set (and the faction carrying an id), the chip becomes a filter:
// clicking it opens the representative list scoped to that faction — the same
// target as the Frakciók page's "members" link.
defineProps({
  faction: { type: Object, default: null },
  link: { type: Boolean, default: false },
})
</script>

<template>
  <router-link
    v-if="faction && faction.label && link && faction.id != null"
    class="chip chip-link"
    :to="{ name: 'representatives', query: { faction_id: faction.id } }"
    :title="$t('reps.filterByFaction', { faction: faction.label })"
  >
    <span class="dot" :style="{ background: faction.color || '#999' }" aria-hidden="true"></span>
    {{ faction.label }}
  </router-link>
  <span v-else-if="faction && faction.label" class="chip">
    <span class="dot" :style="{ background: faction.color || '#999' }" aria-hidden="true"></span>
    {{ faction.label }}
  </span>
</template>
