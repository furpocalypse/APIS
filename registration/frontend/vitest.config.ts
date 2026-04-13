import solid from "vite-plugin-solid";
import tsconfigPaths from "vite-tsconfig-paths";
import { defineConfig } from "vitest/config";

// Solid needs its own plugin chain rather than `vite.config.ts`'s — the router
// plugin runs a code-mod pass that fights jsdom, and the tanstack devtools
// plugin requires a running devtools server we don't want in tests. Keep this
// config minimal and independent.
export default defineConfig({
  plugins: [tsconfigPaths(), solid()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["node_modules", "dist", "../static/bundler"],
    server: {
      deps: {
        inline: [/solid-js/, /@solidjs\/testing-library/],
      },
    },
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/*.{test,spec}.{ts,tsx}",
        "src/test-setup.ts",
        "src/routeTree.gen.ts",
        "src/**/*.d.ts",
      ],
    },
  },
  resolve: {
    conditions: ["development", "browser"],
  },
});
