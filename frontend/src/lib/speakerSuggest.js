// The plumbing behind the search page's speaker suggestions (SEA-7): the
// keystroke debounce, the minimum query length, and the request sequencing that
// stops a slow older response from overwriting a newer one. Two boxes show the
// same suggestions — the main query input and the speaker filter — and they
// differ only in markup, so the fetching lives here once.
import { ref } from 'vue'
import { api } from '../api.js'

// The floor for the MAIN search box, where most of what is typed is an ordinary
// search term: below four characters a name fragment stops being an answer
// (`/suggest` matches anywhere inside the label, accent-folded per §4B, so three
// characters pull in hundreds of people), and a dropdown opening on the second
// letter of every query would be in the way. A box that exists only to name a
// person has no such problem — it passes its own `minChars`.
export const SUGGEST_MIN_CHARS = 4

export function useSpeakerSuggest({ minChars = SUGGEST_MIN_CHARS, delay = 200 } = {}) {
  const speakers = ref([])
  const loading = ref(false)
  // Monotonic request id: keystrokes overlap in flight and the responses can
  // land out of order — only the latest one may write the list.
  let seq = 0
  let timer = null

  // Also the teardown: the debounce outlives the component, so a view that
  // shows suggestions calls this on unmount.
  function clear() {
    clearTimeout(timer)
    seq++                       // orphan anything already in flight
    speakers.value = []
    loading.value = false
  }

  function ask(term) {
    const q = (term || '').trim()
    clearTimeout(timer)
    if (q.length < minChars) { clear(); return }
    timer = setTimeout(() => run(q), delay)
  }

  async function run(q) {
    const mine = ++seq
    loading.value = true
    try {
      const res = await api.suggest(q)
      if (mine === seq) speakers.value = res.speakers || []
    } catch {
      // A suggestion that fails to arrive is not an error the reader needs: the
      // search itself still works, so the list simply stays empty.
      if (mine === seq) speakers.value = []
    } finally {
      if (mine === seq) loading.value = false
    }
  }

  return { speakers, loading, ask, clear }
}
