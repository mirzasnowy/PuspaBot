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
});
