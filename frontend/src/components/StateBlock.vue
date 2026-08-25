<script setup>
// Reusable loading / error / empty state with an accessible live region.
// `errorText` overrides the generic failure message when the caller knows what
// actually went wrong; `retryable` (default true) hides the retry button for
// failures repeating the request cannot fix.
defineProps({
  loading: Boolean,
  error: Boolean,
  empty: Boolean,
  emptyText: String,
  errorText: String,
  retryable: { type: Boolean, default: true },
})
const emit = defineEmits(['retry'])
</script>

<template>
  <div v-if="loading" class="state" role="status" aria-live="polite">
    <div class="spinner" aria-hidden="true"></div>
    {{ $t('app.loading') }}
  </div>
  <div v-else-if="error" class="state" role="alert">
    <p>{{ errorText || $t('app.error') }}</p>
    <button v-if="retryable" class="btn secondary" @click="emit('retry')">{{ $t('app.retry') }}</button>
  </div>
  <div v-else-if="empty" class="state">{{ emptyText }}</div>
  <slot v-else />
</template>
