<script setup>
// A text with its changes marked (BILL-14..16): `segments` is the API's
// [[op, text], …] with op `eq` / `ins` / `del`, so the eq+del parts read as the
// old text and the eq+ins parts as the new one.
//
// Insertions and deletions are real <ins>/<del> elements, each opened by a
// visually hidden word, so a screen reader announces the change rather than
// running the old and new words together. A long provision whose change sits in
// one clause is shown around the change, with the rest folded into "…" that
// opens on click (`context` characters kept on each side; 0 = show all).
import { computed, ref } from 'vue'

const props = defineProps({
  segments: { type: Array, default: () => [] },
  context: { type: Number, default: 0 },
})

const expanded = ref(false)

// Split long unchanged runs into kept head/tail and a folded middle.
const parts = computed(() => {
  const segs = props.segments || []
  if (!props.context || expanded.value) return segs.map(([op, text]) => ({ op, text }))
  const out = []
  segs.forEach(([op, text], i) => {
    if (op !== 'eq' || text.length <= props.context * 2 + 40) {
      out.push({ op, text })
      return
    }
    const first = i === 0
    const last = i === segs.length - 1
    const head = first ? '' : text.slice(0, props.context)
    const tail = last ? '' : text.slice(text.length - props.context)
    const cutHead = head ? head.replace(/\s+\S*$/, ' ') : ''
    const cutTail = tail ? tail.replace(/^\S*\s+/, ' ') : ''
    if (cutHead) out.push({ op: 'eq', text: cutHead })
    out.push({ op: 'fold' })
    if (cutTail) out.push({ op: 'eq', text: cutTail })
  })
  return out
})
</script>

<template>
  <p class="difftext">
    <template v-for="(p, i) in parts" :key="i">
      <ins v-if="p.op === 'ins'"><span class="visually-hidden">{{ $t('diff.inserted') }} </span>{{ p.text }}</ins>
      <del v-else-if="p.op === 'del'"><span class="visually-hidden">{{ $t('diff.deleted') }} </span>{{ p.text }}</del>
      <button v-else-if="p.op === 'fold'" type="button" class="fold"
              :title="$t('diff.expand')" @click="expanded = true">…</button>
      <template v-else>{{ p.text }}</template>
    </template>
    <button v-if="expanded && context" type="button" class="fold less" @click="expanded = false">
      {{ $t('diff.collapse') }}
    </button>
  </p>
</template>

<style scoped>
.difftext { margin: 0; white-space: pre-line; line-height: 1.6; overflow-wrap: anywhere; }
ins {
  background: var(--diff-ins-bg, #dcefdc); color: var(--diff-ins-ink, #1d4d20);
  text-decoration: none; border-radius: 3px; padding: 0 .1em;
  box-decoration-break: clone; -webkit-box-decoration-break: clone;
}
del {
  background: var(--diff-del-bg, #f9dcd8); color: var(--diff-del-ink, #7a1c10);
  text-decoration: line-through; text-decoration-thickness: 1px;
  border-radius: 3px; padding: 0 .1em;
  box-decoration-break: clone; -webkit-box-decoration-break: clone;
}
.fold {
  display: inline; border: 1px solid var(--line); background: var(--bg);
  color: var(--ink-soft); border-radius: 999px; padding: 0 .45em; margin: 0 .15em;
  font: inherit; font-size: .85em; line-height: 1.3; cursor: pointer;
}
.fold:hover, .fold:focus-visible { border-color: var(--accent); color: var(--accent); }
.fold.less { margin-left: .5em; }
</style>
