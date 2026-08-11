import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// In dev, proxy API paths straight to FastAPI (uvicorn on :8420) so the app
// can always call relative paths like fetch("/runs"). In production the
// built assets are served separately and VITE_API_BASE_URL (baked in at
// build time, see docker-compose.yml) is used instead - see src/lib/api.ts.
//
// Ports 8420/5420 (instead of the more common 8000/5173) are deliberate:
// on a dev machine running several other projects' Docker containers, a
// collision on 8000/5173 doesn't fail loudly - uvicorn/vite just silently
// bind to (or proxy to) whatever *else* is already listening there, and
// every API call 404s against the wrong app. `strictPort` turns that into
// a loud, immediate error instead.
const API_TARGET = process.env.VITE_API_BASE_URL || "http://localhost:8420";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5420,
    strictPort: true,
    proxy: {
      "/runs": { target: API_TARGET, changeOrigin: true },
      "/packs": { target: API_TARGET, changeOrigin: true },
      "/health": { target: API_TARGET, changeOrigin: true },
    },
  },
});
