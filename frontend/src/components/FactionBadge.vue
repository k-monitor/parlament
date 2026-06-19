<script setup>
// Faction label with its consistent colour dot (REP-4). Colour is decorative;
// the label carries the meaning, so it stays readable without colour (A11Y).
defineProps({ faction: { type: Object, default: null } })
</script>

<template>
  <span v-if="faction && faction.label" class="chip">
    <span class="dot" :style="{ background: faction.color || '#999' }" aria-hidden="true"></span>
    {{ faction.label }}
  </span>
</template>
