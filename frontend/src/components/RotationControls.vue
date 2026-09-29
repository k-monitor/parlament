<script setup>
// The foot of a rotating home-page panel (lib/rotation.js), the same on every one
// of them: the panel's name on the left, and on the right one dot per item and a
// pause/play button. The current dot fills up over the item's turn,
// so the reader can see that the panel will change, and roughly when, before it
// does. It stands still whenever the rotation does: a held panel is not counting.
defineProps({
  count: { type: Number, required: true },
  index: { type: Number, required: true },
  running: { type: Boolean, default: false },
  stopped: { type: Boolean, default: false },
  turn: { type: Number, default: 0 },
  interval: { type: Number, default: 8000 },
  // What each dot shows, for its accessible name ("2 / 5: Közbeszólások").
  names: { type: Array, default: () => [] },
  // The panel's name: shown on the left, and the controls' accessible name.
  label: { type: String, default: '' },
  // For a narrow card with many items: previous / next around a single progress
  // bar, instead of a dot per item.
  compact: { type: Boolean, default: false },
})
defineEmits(['go', 'toggle'])
</script>

<template>
  <div class="rot-foot">
    <span class="rot-foot__name">{{ label }}</span>
    <div v-if="count > 1" class="rot" role="group" :aria-label="label">
      <button
        type="button" class="rot__toggle"
        :aria-label="stopped ? $t('home.rotation.play') : $t('home.rotation.pause')"
        :title="stopped ? $t('home.rotation.play') : $t('home.rotation.pause')"
        @click="$emit('toggle')"
      >
        <svg v-if="stopped" viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3.2v9.6L12.6 8z" fill="currentColor" /></svg>
        <svg v-else viewBox="0 0 16 16" aria-hidden="true">
          <rect x="4" y="3.5" width="2.6" height="9" rx=".6" fill="currentColor" />
          <rect x="9.4" y="3.5" width="2.6" height="9" rx=".6" fill="currentColor" />
        </svg>
      </button>
      <template v-if="compact">
        <button type="button" class="rot__step" :aria-label="$t('home.rotation.prev')"
                :title="$t('home.rotation.prev')" @click="$emit('go', index - 1)">‹</button>
        <span class="rot__dot on" role="img" :aria-label="`${index + 1} / ${count}`">
          <span
            v-if="!stopped" :key="turn" class="rot__fill" aria-hidden="true"
            :style="{ animationDuration: interval + 'ms', animationPlayState: running ? 'running' : 'paused' }"
          ></span>
        </span>
        <button type="button" class="rot__step" :aria-label="$t('home.rotation.next')"
                :title="$t('home.rotation.next')" @click="$emit('go', index + 1)">›</button>
      </template>
      <template v-else>
        <button
          v-for="i in count" :key="i" type="button" class="rot__dot"
          :class="{ on: i - 1 === index }"
          :aria-label="`${i} / ${count}${names[i - 1] ? ': ' + names[i - 1] : ''}`"
          :aria-current="i - 1 === index ? 'true' : undefined"
          @click="$emit('go', i - 1)"
        >
          <span
            v-if="i - 1 === index && !stopped" :key="turn" class="rot__fill" aria-hidden="true"
            :style="{ animationDuration: interval + 'ms', animationPlayState: running ? 'running' : 'paused' }"
          ></span>
        </button>
      </template>
    </div>
  </div>
</template>

<style scoped>
/* Wraps on a narrow card, the controls keeping to the right. */
.rot-foot {
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
  gap: 0 .5rem; min-height: 2.2rem; padding-top: .3rem; border-top: 1px solid var(--line);
}
.rot-foot__name {
  font-size: .72rem; font-weight: 700; color: var(--ink-faint);
  text-transform: uppercase; letter-spacing: .04em;
}
.rot { display: inline-flex; align-items: center; gap: .15rem; margin-left: auto; }
.rot__toggle {
  display: inline-flex; align-items: center; justify-content: center;
  width: 1.75rem; height: 1.75rem; margin-right: .2rem; padding: 0;
  border: 1px solid var(--line); border-radius: 999px; background: var(--surface);
  color: var(--ink-soft); cursor: pointer;
}
.rot__toggle:hover { color: var(--accent); border-color: var(--accent); }
.rot__step {
  width: 1.5rem; height: 1.75rem; padding: 0 0 .15rem; border: 0; background: none;
  color: var(--ink-soft); font: inherit; font-size: 1.2rem; line-height: 1; cursor: pointer;
}
.rot__step:hover { color: var(--accent); }
.rot__toggle svg { width: .85rem; height: .85rem; }
/* The hit area is the whole button; the dot is drawn inside it, so a small dot
   is still a finger-sized target. */
.rot__dot {
  position: relative; display: inline-flex; align-items: center; justify-content: center;
  width: 1.4rem; height: 1.75rem; padding: 0; border: 0; background: none; cursor: pointer;
}
.rot__dot::before {
  content: ''; width: .5rem; height: .5rem; border-radius: 999px;
  background: #d6d3cb; transition: width .2s ease, background .2s ease;
}
.rot__dot:hover::before { background: var(--ink-faint); }
span.rot__dot { cursor: default; }
.rot__dot.on { width: 2rem; }
.rot__dot.on::before { width: 1.5rem; background: var(--accent-soft); box-shadow: inset 0 0 0 1px #ecc9c3; }
.rot__fill {
  position: absolute; left: .25rem; top: 50%; height: .5rem; margin-top: -.25rem;
  width: 1.5rem; border-radius: 999px; background: var(--accent);
  transform-origin: left center; animation: rot-fill linear forwards;
}
/* Stopped (paused by the reader, or reduced motion): the current dot is simply full. */
.rot__dot.on:not(:has(.rot__fill))::before { background: var(--accent); box-shadow: none; }
@keyframes rot-fill { from { transform: scaleX(0); } to { transform: scaleX(1); } }
@media (prefers-reduced-motion: reduce) {
  .rot__dot::before { transition: none; }
}
</style>
