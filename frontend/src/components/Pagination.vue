<script setup>
// Reusable page navigation for the filterable list views (bills, documents,
// votes). Page state lives in the URL (`offset`), matching the proceedings
// search pager, so a given page is deep-linkable. The parent owns the URL push
// (it also owns the other filters); this component only reports the target page.
defineProps({
  page: { type: Number, required: true },        // 0-based current page
  totalPages: { type: Number, required: true },
})
const emit = defineEmits(['goto'])
</script>

<template>
  <nav v-if="totalPages > 1" class="pager" :aria-label="$t('app.pager.nav')">
    <button class="btn secondary" :disabled="page === 0"
            :aria-label="$t('app.pager.prev')" @click="emit('goto', page - 1)">‹</button>
    <span class="muted small">{{ $t('app.pager.status', { page: page + 1, total: totalPages }) }}</span>
    <button class="btn secondary" :disabled="page + 1 >= totalPages"
            :aria-label="$t('app.pager.next')" @click="emit('goto', page + 1)">›</button>
  </nav>
</template>

<style scoped>
.pager { display: flex; align-items: center; justify-content: center; gap: 1rem; margin: 1.5rem 0; }
</style>
