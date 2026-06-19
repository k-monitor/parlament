<script setup>
// Reusable loading / error / empty state with an accessible live region.
defineProps({ loading: Boolean, error: Boolean, empty: Boolean, emptyText: String })
const emit = defineEmits(['retry'])
</script>

<template>
  <div v-if="loading" class="state" role="status" aria-live="polite">
    <div class="spinner" aria-hidden="true"></div>
    {{ $t('app.loading') }}
  </div>
  <div v-else-if="error" class="state" role="alert">
    <p>{{ $t('app.error') }}</p>
    <button class="btn secondary" @click="emit('retry')">{{ $t('app.retry') }}</button>
  </div>
  <div v-else-if="empty" class="state">{{ emptyText }}</div>
  <slot v-else />
</template>
