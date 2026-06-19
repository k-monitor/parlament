// Minimal global store (Vue reactive) holding the site manifest from /api/v1/meta.
// The enabled-module list drives which nav entries show and which module routes
// are reachable (EXT-4/EXT-6) — a module disabled in the backend simply vanishes.
import { reactive } from 'vue'
import { api } from './api.js'

export const store = reactive({
  meta: null,
  loaded: false,
  failed: false,
  moduleEnabled(name) {
    if (!this.meta) return true // optimistic before load
    return this.meta.modules.some((m) => m.name === name)
  },
})

let inflight = null
export function loadMeta() {
  if (store.loaded) return Promise.resolve(store.meta)
  if (inflight) return inflight
  inflight = api
    .meta()
    .then((m) => {
      store.meta = m
      store.loaded = true
      return m
    })
    .catch((e) => {
      store.failed = true
      throw e
    })
  return inflight
}
