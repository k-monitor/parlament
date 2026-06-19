<script setup></script>
<template>
  <div class="state">
    <h1>404</h1>
    <p>{{ $t('app.notFound') }}</p>
    <router-link class="btn" :to="{ name: 'home' }">{{ $t('app.backHome') }}</router-link>
  </div>
</template>
