<script setup>
// Single bill detail (Bills module). Shows the bill's metadata, its sponsors
// (each MP linked to their profile, EXT-2), and a link to the official text on
// parlament.hu (LEGAL-1 source attribution).
import { ref, watch, onMounted } from 'vue'
import { api } from '../../api.js'
import { formatDate } from '../../format.js'
import StateBlock from '../../components/StateBlock.vue'
import FactionBadge from '../../components/FactionBadge.vue'

const props = defineProps({ id: String })

const bill = ref(null)
const loading = ref(false)
const error = ref(false)
// The embedded PDF is heavy, so it isn't loaded until the user asks: the <iframe>
// is only rendered (and its src only fetched) after clicking "show document".
const docRevealed = ref(false)

async function load() {
  loading.value = true; error.value = false; bill.value = null; docRevealed.value = false
  try { bill.value = await api.bill(props.id) } catch { error.value = true }
  finally { loading.value = false }
}
onMounted(load)
watch(() => props.id, load)
</script>

<template>
  <StateBlock :loading="loading" :error="error" @retry="load">
    <article v-if="bill" class="bill">
      <router-link :to="{ name: 'bills' }" class="small">‹ {{ $t('bills.title') }}</router-link>

      <header class="card pad bhead">
        <div class="row" style="gap:.6rem; align-items:center; flex-wrap:wrap;">
          <span class="bnum">{{ bill.bill_number }}</span>
          <span class="badge" v-if="bill.status">{{ bill.status }}</span>
          <span class="muted small" v-if="bill.type">{{ bill.type }}</span>
        </div>
        <h1>{{ bill.title }}</h1>
        <p class="muted small" v-if="bill.submitted_date">
          {{ $t('bills.submittedDate') }}: {{ formatDate(bill.submitted_date) }}
        </p>
      </header>

      <section v-if="bill.stages && bill.stages.length" class="card pad">
        <h2>{{ $t('bills.timeline') }}</h2>
        <p class="muted small timeline-legend">{{ $t('bills.timelineNote') }}</p>
        <ol class="timeline" :aria-label="$t('bills.timeline')">
          <li
            v-for="(s, i) in bill.stages" :key="i"
            class="tstep" :class="{ done: s.done, current: s.current, future: !s.done }"
            :aria-current="s.current ? 'step' : undefined"
          >
            <span class="tmarker" aria-hidden="true">{{ s.current ? '●' : (s.done ? '✓' : '') }}</span>
            <span class="tlabel">{{ s.label }}</span>
            <span class="visually-hidden">
              — {{ s.current ? $t('bills.stageCurrent') : (s.done ? $t('bills.stageDone') : $t('bills.stagePending')) }}
            </span>
          </li>
        </ol>
      </section>

      <section class="card pad">
        <h2>{{ $t('bills.submitters') }}</h2>
        <ul class="plain">
          <li v-for="(s, i) in bill.sponsors" :key="i" class="sponsor">
            <router-link v-if="s.person_id" :to="{ name: 'profile', params: { id: s.person_id } }">{{ s.name }}</router-link>
            <span v-else>{{ s.name }}</span>
            <FactionBadge v-if="s.faction" :faction="s.faction" />
          </li>
        </ul>
      </section>

      <section class="card pad">
        <h2>{{ $t('bills.source') }}</h2>
        <template v-if="bill.text_url">
          <div class="docbar">
            <button type="button" class="btn" :aria-expanded="docRevealed" @click="docRevealed = !docRevealed">
              {{ docRevealed ? $t('bills.hideDocument') : $t('bills.showDocument') }}
            </button>
            <a :href="bill.text_url" target="_blank" rel="noopener" class="small">↗ {{ $t('bills.openInNewTab') }}</a>
          </div>
          <!-- iframe (and thus the PDF fetch) only exists once revealed -->
          <div v-if="docRevealed" class="docframe">
            <iframe :src="bill.text_url" :title="bill.text_caption || bill.bill_number" loading="lazy"></iframe>
          </div>
        </template>
        <p v-else class="muted small">{{ $t('bills.noText') }}</p>
        <p class="small">
          <a :href="bill.source_url" target="_blank" rel="noopener">{{ $t('bills.viewOnParlament') }}</a>
        </p>
      </section>
    </article>
  </StateBlock>
</template>

<style scoped>
.bill { display: flex; flex-direction: column; gap: 1rem; }
.bhead { margin-top: .6rem; }
.bhead h1 { margin: .5rem 0 .3rem; font-size: 1.3rem; }
.bnum { font-weight: 800; color: var(--accent); font-size: 1.1rem; }
.plain { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .5rem; }
.sponsor { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; }
/* Vertical past→future legislative-stage timeline. Each step's connector (the
   line above its dot) is solid when reached, dashed when still upcoming. */
.timeline-legend { margin: 0 0 1rem; }
.timeline { list-style: none; margin: 0; padding: 0; }
.tstep {
  position: relative; display: flex; align-items: center; gap: .8rem;
  padding: .55rem 0 .55rem 2.2rem; min-height: 1.4rem;
}
.tmarker {
  position: absolute; left: 0; width: 1.7rem; height: 1.7rem; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: .85rem; font-weight: 800; z-index: 1; background: var(--surface);
  border: 2px solid var(--line); color: var(--ink-faint);
}
/* connector line running up from each dot to the previous one */
.tstep::before {
  content: ''; position: absolute; left: calc(0.85rem - 1px); top: -0.55rem;
  width: 2px; height: 0.55rem; background: var(--line);
}
.tstep:first-child::before { display: none; }
.tstep.done .tmarker { background: var(--accent); border-color: var(--accent); color: var(--accent-ink, #fff); }
.tstep.done::before { background: var(--accent); }
.tstep.current .tmarker { box-shadow: 0 0 0 3px var(--accent-soft); }
.tstep.future .tmarker { background: var(--surface); }
/* the segment leading into a not-yet-reached step is dashed/muted */
.tstep.future::before {
  background: none;
  border-left: 2px dashed var(--line); width: 0; left: calc(0.85rem - 1px);
}
.tlabel { font-size: .95rem; }
.tstep.future .tlabel { color: var(--ink-faint); }
.tstep.current .tlabel { font-weight: 700; }
.docbar { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; }
.btn {
  background: var(--accent); color: var(--accent-ink, #fff); border: none;
  border-radius: 8px; padding: .5rem .9rem; font-weight: 700; cursor: pointer; font-size: .95rem;
}
.btn:hover { filter: brightness(1.05); }
.docframe { margin-top: .9rem; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.docframe iframe { display: block; width: 100%; height: 80vh; border: 0; }
</style>
