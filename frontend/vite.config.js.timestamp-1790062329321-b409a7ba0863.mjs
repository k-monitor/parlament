// vite.config.js
import { defineConfig } from "file:///home/boa/parlament/frontend/node_modules/vite/dist/node/index.js";
import vue from "file:///home/boa/parlament/frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs";
var vite_config_default = defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // Use 127.0.0.1 (not "localhost"): on dual-stack hosts Node resolves
      // localhost to IPv6 ::1 first, but uvicorn binds IPv4 127.0.0.1 — the
      // proxy would then get ECONNREFUSED on every /api call.
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/media": { target: "http://127.0.0.1:8000", changeOrigin: true }
    }
  },
  build: {
    // Split each lazily-loaded feature module into its own chunk (EXT-4).
    chunkSizeWarningLimit: 900
  },
  // hls.js is only reached through the lazily-loaded viewer chunk, so Vite would
  // otherwise discover it late and re-optimize deps mid-session — which 504s any
  // in-flight dynamic import (surfacing as a bogus "application/json MIME" error
  // on module loads). Pre-bundling it up front makes dev startup deterministic.
  optimizeDeps: {
    include: ["hls.js"],
    // @ffmpeg/ffmpeg spawns its worker via `new Worker(new URL('./worker.js',
    // import.meta.url), {type:'module'})`. esbuild's dep pre-bundling mangles
    // that pattern, breaking the worker; excluding it lets Vite/Rollup's worker
    // plugin resolve it correctly. Only reached through the lazily-imported
    // clip-exporter chunk (VIE-10), so excluding it costs normal viewing nothing.
    exclude: ["@ffmpeg/ffmpeg", "@ffmpeg/util"]
  }
});
export {
  vite_config_default as default
};
//# sourceMappingURL=data:application/json;base64,ewogICJ2ZXJzaW9uIjogMywKICAic291cmNlcyI6IFsidml0ZS5jb25maWcuanMiXSwKICAic291cmNlc0NvbnRlbnQiOiBbImNvbnN0IF9fdml0ZV9pbmplY3RlZF9vcmlnaW5hbF9kaXJuYW1lID0gXCIvaG9tZS9ib2EvcGFybGFtZW50L2Zyb250ZW5kXCI7Y29uc3QgX192aXRlX2luamVjdGVkX29yaWdpbmFsX2ZpbGVuYW1lID0gXCIvaG9tZS9ib2EvcGFybGFtZW50L2Zyb250ZW5kL3ZpdGUuY29uZmlnLmpzXCI7Y29uc3QgX192aXRlX2luamVjdGVkX29yaWdpbmFsX2ltcG9ydF9tZXRhX3VybCA9IFwiZmlsZTovLy9ob21lL2JvYS9wYXJsYW1lbnQvZnJvbnRlbmQvdml0ZS5jb25maWcuanNcIjtpbXBvcnQgeyBkZWZpbmVDb25maWcgfSBmcm9tICd2aXRlJ1xuaW1wb3J0IHZ1ZSBmcm9tICdAdml0ZWpzL3BsdWdpbi12dWUnXG5cbi8vIEluIGRldiB0aGUgU1BBIHJ1bnMgdW5kZXIgVml0ZSBhbmQgcHJveGllcyBBUEkgKyBtZWRpYSB0byB0aGUgRmFzdEFQSSBiYWNrZW5kXG4vLyAoTkZSLTE6IHNlcGFyYXRlIGJhY2tlbmQpLiBJbiBwcm9kIGBucG0gcnVuIGJ1aWxkYCBlbWl0cyBhIHN0YXRpYyBidW5kbGUgdGhhdFxuLy8gdGhlIGJhY2tlbmQgY2FuIHNlcnZlIGRpcmVjdGx5IChQQVJMQU1PTklUT1JfRlJPTlRFTkRfRElTVCkgb3IgYW55IHN0YXRpYyBob3N0LlxuZXhwb3J0IGRlZmF1bHQgZGVmaW5lQ29uZmlnKHtcbiAgcGx1Z2luczogW3Z1ZSgpXSxcbiAgc2VydmVyOiB7XG4gICAgcG9ydDogNTE3MyxcbiAgICBwcm94eToge1xuICAgICAgLy8gVXNlIDEyNy4wLjAuMSAobm90IFwibG9jYWxob3N0XCIpOiBvbiBkdWFsLXN0YWNrIGhvc3RzIE5vZGUgcmVzb2x2ZXNcbiAgICAgIC8vIGxvY2FsaG9zdCB0byBJUHY2IDo6MSBmaXJzdCwgYnV0IHV2aWNvcm4gYmluZHMgSVB2NCAxMjcuMC4wLjEgXHUyMDE0IHRoZVxuICAgICAgLy8gcHJveHkgd291bGQgdGhlbiBnZXQgRUNPTk5SRUZVU0VEIG9uIGV2ZXJ5IC9hcGkgY2FsbC5cbiAgICAgICcvYXBpJzogeyB0YXJnZXQ6ICdodHRwOi8vMTI3LjAuMC4xOjgwMDAnLCBjaGFuZ2VPcmlnaW46IHRydWUgfSxcbiAgICAgICcvbWVkaWEnOiB7IHRhcmdldDogJ2h0dHA6Ly8xMjcuMC4wLjE6ODAwMCcsIGNoYW5nZU9yaWdpbjogdHJ1ZSB9LFxuICAgIH0sXG4gIH0sXG4gIGJ1aWxkOiB7XG4gICAgLy8gU3BsaXQgZWFjaCBsYXppbHktbG9hZGVkIGZlYXR1cmUgbW9kdWxlIGludG8gaXRzIG93biBjaHVuayAoRVhULTQpLlxuICAgIGNodW5rU2l6ZVdhcm5pbmdMaW1pdDogOTAwLFxuICB9LFxuICAvLyBobHMuanMgaXMgb25seSByZWFjaGVkIHRocm91Z2ggdGhlIGxhemlseS1sb2FkZWQgdmlld2VyIGNodW5rLCBzbyBWaXRlIHdvdWxkXG4gIC8vIG90aGVyd2lzZSBkaXNjb3ZlciBpdCBsYXRlIGFuZCByZS1vcHRpbWl6ZSBkZXBzIG1pZC1zZXNzaW9uIFx1MjAxNCB3aGljaCA1MDRzIGFueVxuICAvLyBpbi1mbGlnaHQgZHluYW1pYyBpbXBvcnQgKHN1cmZhY2luZyBhcyBhIGJvZ3VzIFwiYXBwbGljYXRpb24vanNvbiBNSU1FXCIgZXJyb3JcbiAgLy8gb24gbW9kdWxlIGxvYWRzKS4gUHJlLWJ1bmRsaW5nIGl0IHVwIGZyb250IG1ha2VzIGRldiBzdGFydHVwIGRldGVybWluaXN0aWMuXG4gIG9wdGltaXplRGVwczoge1xuICAgIGluY2x1ZGU6IFsnaGxzLmpzJ10sXG4gICAgLy8gQGZmbXBlZy9mZm1wZWcgc3Bhd25zIGl0cyB3b3JrZXIgdmlhIGBuZXcgV29ya2VyKG5ldyBVUkwoJy4vd29ya2VyLmpzJyxcbiAgICAvLyBpbXBvcnQubWV0YS51cmwpLCB7dHlwZTonbW9kdWxlJ30pYC4gZXNidWlsZCdzIGRlcCBwcmUtYnVuZGxpbmcgbWFuZ2xlc1xuICAgIC8vIHRoYXQgcGF0dGVybiwgYnJlYWtpbmcgdGhlIHdvcmtlcjsgZXhjbHVkaW5nIGl0IGxldHMgVml0ZS9Sb2xsdXAncyB3b3JrZXJcbiAgICAvLyBwbHVnaW4gcmVzb2x2ZSBpdCBjb3JyZWN0bHkuIE9ubHkgcmVhY2hlZCB0aHJvdWdoIHRoZSBsYXppbHktaW1wb3J0ZWRcbiAgICAvLyBjbGlwLWV4cG9ydGVyIGNodW5rIChWSUUtMTApLCBzbyBleGNsdWRpbmcgaXQgY29zdHMgbm9ybWFsIHZpZXdpbmcgbm90aGluZy5cbiAgICBleGNsdWRlOiBbJ0BmZm1wZWcvZmZtcGVnJywgJ0BmZm1wZWcvdXRpbCddLFxuICB9LFxufSlcbiJdLAogICJtYXBwaW5ncyI6ICI7QUFBc1EsU0FBUyxvQkFBb0I7QUFDblMsT0FBTyxTQUFTO0FBS2hCLElBQU8sc0JBQVEsYUFBYTtBQUFBLEVBQzFCLFNBQVMsQ0FBQyxJQUFJLENBQUM7QUFBQSxFQUNmLFFBQVE7QUFBQSxJQUNOLE1BQU07QUFBQSxJQUNOLE9BQU87QUFBQTtBQUFBO0FBQUE7QUFBQSxNQUlMLFFBQVEsRUFBRSxRQUFRLHlCQUF5QixjQUFjLEtBQUs7QUFBQSxNQUM5RCxVQUFVLEVBQUUsUUFBUSx5QkFBeUIsY0FBYyxLQUFLO0FBQUEsSUFDbEU7QUFBQSxFQUNGO0FBQUEsRUFDQSxPQUFPO0FBQUE7QUFBQSxJQUVMLHVCQUF1QjtBQUFBLEVBQ3pCO0FBQUE7QUFBQTtBQUFBO0FBQUE7QUFBQSxFQUtBLGNBQWM7QUFBQSxJQUNaLFNBQVMsQ0FBQyxRQUFRO0FBQUE7QUFBQTtBQUFBO0FBQUE7QUFBQTtBQUFBLElBTWxCLFNBQVMsQ0FBQyxrQkFBa0IsY0FBYztBQUFBLEVBQzVDO0FBQ0YsQ0FBQzsiLAogICJuYW1lcyI6IFtdCn0K
