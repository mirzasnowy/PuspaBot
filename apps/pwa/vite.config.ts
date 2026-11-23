import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/sse": "http://127.0.0.1:8000",
      "/static": "http://127.0.0.1:8000",
      "/manifest.json": "http://127.0.0.1:8000",
    },
  },
  // Screenshots run against the production bundle (vite preview),
  // not vite dev: dev serves every module separately and the
  // browser exhausts its per-host connections after a few full
  // navigations (ERR_INSUFFICIENT_RESOURCES, blank pages). The
  // preview proxy mirrors the dev proxy so the app talks to the
  // same shot backend on :8000.
  preview: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/sse": "http://127.0.0.1:8000",
      "/static": "http://127.0.0.1:8000",
      "/manifest.json": "http://127.0.0.1:8000",
    },
  },
});
