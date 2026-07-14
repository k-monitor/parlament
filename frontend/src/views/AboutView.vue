<script setup>
import { store } from '../store.js'
import DonateCard from '../components/DonateCard.vue'
</script>

<template>
  <article class="card pad" style="max-width:75ch;">
    <h1>{{ $t('about.title') }}</h1>
    <p>{{ $t('about.body') }}</p>

    <h2>{{ $t('about.dataTitle') }}</h2>
    <p>{{ $t('about.dataBody') }}</p>

    <h2>{{ $t('about.privacyTitle') }}</h2>
    <p>{{ $t('about.privacyBody') }}</p>

    <h2>{{ $t('about.apiTitle') }}</h2>
    <p>{{ $t('about.apiBody') }} <a href="/api/docs" target="_blank" rel="noopener">{{ $t('about.apiLink') }}</a>.</p>

    <h2>{{ $t('app.source') }}</h2>
    <p v-if="store.meta">
      <a :href="store.meta.source_attribution.url" target="_blank" rel="noopener">{{ store.meta.source_attribution.name }}</a>
      —
      <a :href="store.meta.source_attribution.license_url" target="_blank" rel="noopener">{{ $t('viewer.license') }}</a>
    </p>
  </article>

  <DonateCard class="about-donate" />
</template>

<style scoped>
.about-donate { max-width: 75ch; margin-top: 1.5rem; }
</style>
