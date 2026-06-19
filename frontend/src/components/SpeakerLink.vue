<script setup>
// Avatar + name linking to an MP profile (or plain text for non-MP speakers
// like the President of the Republic, who have no profile page).
import { computed } from 'vue'
defineProps({
  speaker: { type: Object, required: true },
  size: { type: String, default: '' },
})
const PLACEHOLDER =
  'data:image/svg+xml;utf8,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48"><rect width="48" height="48" fill="%23e7e5df"/><circle cx="24" cy="19" r="9" fill="%23bdb9af"/><rect x="9" y="32" width="30" height="18" rx="9" fill="%23bdb9af"/></svg>'
  )
function onErr(e) { e.target.src = PLACEHOLDER }
</script>

<template>
  <component
    :is="speaker.person_id ? 'router-link' : 'span'"
    :to="speaker.person_id ? { name: 'profile', params: { id: speaker.person_id } } : undefined"
    class="row"
    style="gap:.6rem; align-items:center;"
  >
    <img
      :class="['avatar', size]"
      :src="speaker.photo_uri || PLACEHOLDER"
      @error="onErr"
      alt=""
      loading="lazy"
    />
    <span><slot>{{ speaker.label }}</slot></span>
  </component>
</template>
