<script setup>
// Discloses that video timing is an estimate (VIE-6 / TIM-3 / TRUST-1).
defineProps({ timing: { type: Object, default: null } })
</script>

<template>
  <span
    v-if="timing && timing.estimated"
    class="badge warn"
    :title="$t('viewer.estimatedTimingTip')"
  >
    ⚠ {{ $t('viewer.estimatedTiming') }}
    <span class="visually-hidden">— {{ $t('viewer.estimatedTimingTip') }}</span>
  </span>
</template>
