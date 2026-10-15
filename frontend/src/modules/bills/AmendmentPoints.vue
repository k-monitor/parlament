<script setup>
// One módosító javaslat as a diff of the bill (BILL-16): each point names the
// provision it changes and reprints it with the proposed insertions and
// deletions — read off the PDF's own formatting (bold [deletions], underlined
// insertions), so what is shown is what the submitter marked. Loaded when the
// reader opens it, from GET /bills/:id/amendments?number=.
import { onMounted, ref } from 'vue'
import { api } from '../../api.js'
import DiffText from '../../components/DiffText.vue'

const props = defineProps({
  billId: { type: String, required: true },
  number: { type: String, required: true },
})

const data = ref(null)
const loading = ref(true)
const failed = ref(false)

onMounted(async () => {
  try {
    data.value = await api.billAmendment(props.billId, props.number)
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="amend">
    <p v-if="loading" class="muted small">{{ $t('lawChanges.loading') }}</p>
    <p v-else-if="failed" class="muted small">{{ $t('lawChanges.loadFailed') }}</p>
    <template v-else-if="data">
      <p v-if="data.scan" class="muted small">{{ $t('amendment.scan') }}</p>
      <p v-else-if="!data.points.length" class="muted small">{{ $t('amendment.noPoints') }}</p>
      <template v-else>
        <p class="muted small legend">
          <ins>{{ $t('diff.legendIns') }}</ins> <del>{{ $t('diff.legendDel') }}</del>
          <template v-if="!data.marked"> · {{ $t('amendment.unmarked') }}</template>
        </p>
        <ol class="plain points">
          <li v-for="p in data.points" :key="p.point" class="point">
            <div class="phead small">
              <strong>{{ $t('amendment.point', { n: p.point }) }}</strong>
              <span v-if="p.target" class="target">{{ p.target }}</span>
              <span v-if="p.kind" class="muted">· {{ p.kind }}</span>
            </div>
            <DiffText :segments="p.segments" :context="220" />
          </li>
        </ol>
      </template>
    </template>
  </div>
</template>

<style scoped>
.amend { margin-top: .7rem; padding: .7rem .8rem; border: 1px solid var(--line); border-radius: 8px; background: var(--bg); }
.plain { list-style: none; padding: 0; margin: 0; }
.legend { margin: 0 0 .6rem; }
.legend ins { background: #dcefdc; color: #1d4d20; text-decoration: none; padding: 0 .25em; border-radius: 3px; }
.legend del { background: #f9dcd8; color: #7a1c10; padding: 0 .25em; border-radius: 3px; }
.points { display: flex; flex-direction: column; gap: .8rem; }
.point { border-top: 1px solid var(--line); padding-top: .6rem; }
.point:first-child { border-top: 0; padding-top: 0; }
.phead { display: flex; gap: .45rem; flex-wrap: wrap; align-items: baseline; margin-bottom: .3rem; }
.target { color: var(--ink-soft); overflow-wrap: anywhere; }
</style>
