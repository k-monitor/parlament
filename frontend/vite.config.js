import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// In dev the SPA runs under Vite and proxies API + media to the FastAPI backend
// (NFR-1: separate backend). In prod `npm run build` emits a static bundle that
// the backend can serve directly (PARLAMONITOR_FRONTEND_DIST) or any static host.
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // Use 127.0.0.1 (not "localhost"): on dual-stack hosts Node resolves
      // localhost to IPv6 ::1 first, but uvicorn binds IPv4 127.0.0.1 — the
      // proxy would then get ECONNREFUSED on every /api call.
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/media': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: {
    // Split each lazily-loaded feature module into its own chunk (EXT-4).
    chunkSizeWarningLimit: 900,
  },
  // hls.js is only reached through the lazily-loaded viewer chunk, so Vite would
  // otherwise discover it late and re-optimize deps mid-session — which 504s any
  // in-flight dynamic import (surfacing as a bogus "application/json MIME" error
  // on module loads). Pre-bundling it up front makes dev startup deterministic.
  optimizeDeps: {
    include: ['hls.js'],
  },
})
