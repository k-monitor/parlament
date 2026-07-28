<script setup>
import DonateCard from '../components/DonateCard.vue'
</script>

<template>
  <!-- Body copy lives in the locale files as static markup (inline links and
       emphasis), so every paragraph is rendered with v-html. -->
  <article class="card pad prose" style="max-width:75ch;">
    <h1>{{ $t('about.title') }}</h1>
    <p v-html="$t('about.body1')"></p>
    <p v-html="$t('about.body2')"></p>
    <p v-html="$t('about.body3')"></p>
    <p v-html="$t('about.body4')"></p>
    <p v-html="$t('about.figyusz')"></p>

    <h2>{{ $t('about.dataTitle') }}</h2>
    <p v-html="$t('about.dataBody1')"></p>
    <p v-html="$t('about.dataBody2')"></p>
    <p v-html="$t('about.dataBody3')"></p>
  </article>

  <DonateCard class="about-donate" />
</template>

<style scoped>
.about-donate { max-width: 75ch; margin-top: 1.5rem; }

/* Long-form copy: roomier lines and visibly underlined inline links (the
   global link style drops the underline, which reads poorly mid-sentence).
   :deep() is required because the anchors come from v-html. */
.prose p { line-height: 1.65; margin: 0 0 .9rem; }
.prose :deep(a) { text-decoration: underline; text-underline-offset: 2px; }
</style>
