<script setup>
import DonateCard from '../components/DonateCard.vue'
</script>

<template>
  <!-- Body copy lives in the locale files as static markup (inline links and
       emphasis), so every paragraph is rendered with v-html. -->
  <article class="card pad prose">
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

    <h2>{{ $t('about.sourcesTitle') }}</h2>
    <p v-html="$t('about.sourcesBody1')"></p>
    <p v-html="$t('about.sourcesCrow')"></p>
    <p v-html="$t('about.sourcesBody2')"></p>
    <p v-html="$t('about.sourcesBody3')"></p>
    <p>{{ $t('about.sourcesModelsIntro') }}</p>
    <ul>
      <li>{{ $t('about.sourcesModel1') }}</li>
      <li>{{ $t('about.sourcesModel2') }}</li>
      <li>{{ $t('about.sourcesModel3') }}</li>
      <li>{{ $t('about.sourcesModel4') }}</li>
      <li>{{ $t('about.sourcesModel5') }}</li>
    </ul>
    <p v-html="$t('about.sourcesParlaCap')"></p>
    <p v-html="$t('about.sourcesBody4')"></p>
    <p v-html="$t('about.sourcesBody5')"></p>
    <p v-html="$t('about.sourcesBody6')"></p>
    <p v-html="$t('about.sourcesBody7')"></p>
    <p v-html="$t('about.sourcesBody8')"></p>
    <p v-html="$t('about.sourcesBody9')"></p>
  </article>

  <DonateCard class="about-donate" />
</template>

<style scoped>
.about-donate { margin-top: 1.5rem; }

/* Long-form copy: roomier lines and visibly underlined inline links (the
   global link style drops the underline, which reads poorly mid-sentence).
   :deep() is required because the anchors come from v-html. */
.prose p { line-height: 1.65; margin: 0 0 .9rem; }
.prose ul { line-height: 1.65; margin: -.4rem 0 .9rem; padding-left: 1.25rem; }
.prose :deep(a) { text-decoration: underline; text-underline-offset: 2px; }
</style>
