import { defineConfig, Plugin } from "vite";
import react from "@vitejs/plugin-react";

// Public base path. Empty/"/" (the default, and what the desktop build uses)
// serves at the origin root. A path-mounted deploy (Reinspect under
// `/reinspect/` on Materia's domain) sets VITE_BASE=/reinspect at build time so
// every emitted asset/route URL is prefixed and a single proxy rule
// (`/reinspect/* -> Reinspect, strip /reinspect`) suffices. See
// docs/CLOUD_DEPLOYMENT.md (Ingress).
const rawBase = process.env.VITE_BASE || "/";
const base = rawBase.endsWith("/") ? rawBase : rawBase + "/";

// Vite rewrites absolute `src`/`href` in index.html to include `base`, and
// `import.meta.env.BASE_URL` is base in app code — but it does NOT touch string
// literals inside the inline (classic) bootstrap script, where Moorhen's WASM
// is loaded via `loadScript("/MoorhenAssets/...")`. Prefix those at build time.
// No-op when base is "/", so the desktop build's index.html is byte-identical.
function prefixInlineAssets(): Plugin {
  return {
    name: "prefix-inline-moorhen-assets",
    transformIndexHtml(html) {
      if (base === "/") return html;
      const p = base.replace(/\/$/, ""); // "/reinspect"
      return html.replaceAll('"/MoorhenAssets/', `"${p}/MoorhenAssets/`);
    },
  };
}

// The client talks ONLY to the REST contract. In dev we proxy /api to the
// Django server so the browser sees a single origin (and WASM cross-origin
// isolation headers are satisfied).
export default defineConfig({
  base,
  plugins: [react(), prefixInlineAssets()],
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
