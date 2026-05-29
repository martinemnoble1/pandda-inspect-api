import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The client talks ONLY to the REST contract. In dev we proxy /api to the
// Django server so the browser sees a single origin (and WASM cross-origin
// isolation headers are satisfied).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
    },
    headers: {
      // Required for Moorhen's WASM (SharedArrayBuffer).
      "Cross-Origin-Embedder-Policy": "require-corp",
      "Cross-Origin-Opener-Policy": "same-origin",
    },
  },
});
