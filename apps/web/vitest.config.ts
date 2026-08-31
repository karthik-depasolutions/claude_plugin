// Kept separate from vite.config.ts because vitest bundles its own (older)
// copy of vite, and importing `defineConfig` from "vitest/config" into the
// vite@8 config trips a type-version mismatch. This file is not in any
// tsconfig `include`, so `tsc -b` never typechecks it; vitest transpiles it
// with esbuild at runtime.
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
