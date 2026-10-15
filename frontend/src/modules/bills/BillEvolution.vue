<script setup>
// How the bill changed on its way through the House (BILL-15): the text as
// submitted against the egységes javaslat — Parliament's own consolidation of
// the bill with the Törvényalkotási Bizottság's összegző módosító javaslat —
// one of the bill's own sections at a time. Only what differs is listed.
//
// The first response carries only the first few sections (an omnibus bill's
// consolidation differs in over a hundred, each a whole §); the rest are
// fetched when the reader asks for all of them. Each section starts closed,
// its diff shown when the reader opens it.
import { computed, reactive, ref } from 'vue'
import { api } from '../../api.js'
import DiffText from '../../components/DiffText.vue'

const props = defineProps({
  billId: { type: String, required: true },
  evolution: { type: Object, required: true },
})

const SHOWN = 6
const expanded = ref(false)
const all = ref(null)
const loading = ref(false)
const total = computed(() => props.evolution.total ?? (props.evolution.provisions || []).length)
const provisions = computed(() => all.value || props.evolution.provisions || [])
const shown = computed(() => (expanded.value ? provisions.value : provisions.value.slice(0, SHOWN)))
const open = reactive({})

async function toggle() {
  expanded.value = !expanded.value
  if (expanded.value && !all.value && provisions.value.length < total.value) {
    loading.value = true
    try {
      all.value = (await api.billEvolution(props.billId)).evolution?.provisions || null
    } catch { /* the head stays; the button says nothing more is coming */ } finally {
      loading.value = false
    }
  }
}
</script>

<template>
  <section class="card pad evolution">
    <h2>{{ $t('evolution.title') }}</h2>
    <p class="muted small note">
      <i18n-t keypath="evolution.note" tag="span" scope="global">
        <template #from>
          <a :href="evolution.from.url" target="_blank" rel="noopener">{{ evolution.from.number }}</a>
        </template>
        <template #to>
          <a :href="evolution.to.url" target="_blank" rel="noopener">{{ evolution.to.number }}</a>
        </template>
      </i18n-t>
      <template v-if="evolution.unchanged">{{ ' ' + $t('evolution.unchanged', { n: evolution.unchanged }) }}</template>
    </p>
    <p v-if="!provisions.length" class="muted small">{{ $t('evolution.none') }}</p>
    <ol class="plain plist">
      <li v-for="p in shown" :key="p.provision" class="prov">
        <button type="button" class="phead" :aria-expanded="!!open[p.provision]"
                @click="open[p.provision] = !open[p.provision]">
          <span class="caret" aria-hidden="true">{{ open[p.provision] ? '▾' : '▸' }}</span>
          <strong>{{ p.provision === 'cím' ? $t('evolution.titlePart') : p.provision }}</strong>
          <span v-if="p.kind !== 'modified'" class="kind" :class="p.kind">{{ $t('lawChanges.kind.' + p.kind) }}</span>
        </button>
        <div v-if="open[p.provision]" class="pbody">
          <DiffText :segments="p.diff" :context="200" />
        </div>
      </li>
    </ol>
    <p v-if="loading" class="muted small">{{ $t('lawChanges.loading') }}</p>
    <button v-if="total > SHOWN" type="button" class="btn small secondary more"
            :aria-expanded="expanded" @click="toggle">
      {{ expanded ? $t('bills.showLess') : $t('evolution.showAll', { n: total }) }}
    </button>
  </section>
</template>

<style scoped>
/* Same section heading as the rest of the bill page (BillView's scoped
   `.bill section > h2` does not reach into this component). */
section > h2 {
  display: flex; align-items: center; gap: .55rem;
  margin: 0 0 .8rem; font-size: 1.15rem;
}
section > h2::before {
  content: ''; flex: none; width: .28rem; height: 1.05rem;
  background: var(--accent); border-radius: 2px;
}
.note { margin: 0 0 .8rem; }
.plain { list-style: none; padding: 0; margin: 0; }
.plist { display: flex; flex-direction: column; }
.prov { border-top: 1px solid var(--line); }
.prov:first-child { border-top: 0; }
.phead {
  display: flex; width: 100%; gap: .5rem; align-items: baseline; text-align: left;
  padding: .45rem .3rem; background: none; border: 0; cursor: pointer;
  font: inherit; color: var(--ink);
}
.phead:hover { background: var(--accent-soft); border-radius: 6px; }
.caret { color: var(--ink-faint); flex: none; width: .8rem; }
.pbody { padding: 0 .3rem .7rem 1.6rem; }
.kind {
  font-size: .72rem; font-weight: 700; padding: 0 .45rem; border-radius: 999px;
  line-height: 1.5; white-space: nowrap;
}
.kind.new { background: #dcefdc; color: #1d4d20; }
.kind.repealed { background: #f9dcd8; color: #7a1c10; }
.more { margin-top: .9rem; }
</style>
