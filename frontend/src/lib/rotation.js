// A set of home-page panels that take turns in one place (the example search, the
// numbers, the figures).
//
// Content that changes by itself is only acceptable if the reader can stop it
// (WCAG 2.2.2), and it must never move away from under someone who is using it.
// So the rotation has two kinds of pause:
//
// - a **hold**, while the pointer is over the panel or focus is inside it. It
//   lifts by itself, and the turn resumes where it left off rather than starting
//   over, so the progress bar and the timer never disagree;
// - a **stop**, which is the reader's decision: the pause button, or taking hold
//   of an interactive figure (`stop()` — nobody wants the map they are panning to
//   turn into a different figure). Only the play button undoes it.
//
// A reader who asked their system for reduced motion starts stopped: the panel
// shows its first item and the controls, and turning is theirs to start. A tab in
// the background is held too, so a page left open does not keep fetching.
import { computed, onBeforeUnmount, onMounted, ref, unref, watch } from 'vue'

function prefersReducedMotion() {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

export function useRotation(count, { interval = 8000 } = {}) {
  const index = ref(0)
  const stopped = ref(prefersReducedMotion())
  const hovering = ref(false)
  const focused = ref(false)
  const hidden = ref(typeof document !== 'undefined' && document.hidden)
  // Bumped whenever a turn starts afresh, so a progress bar keyed on it restarts.
  const turn = ref(0)

  const size = computed(() => Number(unref(count)) || 0)
  const running = computed(() => size.value > 1 && !stopped.value
    && !hovering.value && !focused.value && !hidden.value)

  let timer = 0
  let remaining = interval
  let startedAt = 0

  function arm() {
    clearTimeout(timer)
    timer = 0
    if (!running.value) return
    startedAt = Date.now()
    timer = setTimeout(() => go(index.value + 1), remaining)
  }
  // Keep what is left of the turn, so a hold resumes it instead of restarting it.
  function disarm() {
    if (!timer) return
    clearTimeout(timer)
    timer = 0
    remaining = Math.max(0, remaining - (Date.now() - startedAt))
  }

  function go(i) {
    const n = size.value
    if (!n) return
    clearTimeout(timer)
    timer = 0
    index.value = ((i % n) + n) % n
    remaining = interval
    turn.value++
    arm()
  }
  const next = () => go(index.value + 1)
  const prev = () => go(index.value - 1)

  function stop() { stopped.value = true }
  function toggle() {
    stopped.value = !stopped.value
    // Pressing play is asking for movement: the next item comes after a whole
    // turn, not after whatever was left of the one that was interrupted.
    if (!stopped.value) { remaining = interval; turn.value++ }
  }

  watch(running, (on) => (on ? arm() : disarm()))
  watch(size, (n) => {
    if (index.value >= n) index.value = 0
    remaining = interval
    turn.value++
    arm()
  })

  // Spread onto the panel's root element.
  const holdHandlers = {
    onMouseenter: () => { hovering.value = true },
    onMouseleave: () => { hovering.value = false },
    onFocusin: () => { focused.value = true },
    onFocusout: (e) => {
      if (!e.currentTarget.contains(e.relatedTarget)) focused.value = false
    },
  }

  function onVisibility() { hidden.value = document.hidden }
  onMounted(() => {
    document.addEventListener('visibilitychange', onVisibility)
    arm()
  })
  onBeforeUnmount(() => {
    document.removeEventListener('visibilitychange', onVisibility)
    clearTimeout(timer)
  })

  return { index, running, stopped, turn, interval, go, next, prev, stop, toggle, holdHandlers }
}
