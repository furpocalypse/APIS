import path from "node:path";
import { fileURLToPath } from "node:url";

import { devtools as tanstackDevtools } from "@tanstack/devtools-vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import devtools from "solid-devtools/vite";
import { defineConfig } from "vite";
import solid from "vite-plugin-solid";
import wasm from "vite-plugin-wasm";
import tsconfigPaths from "vite-tsconfig-paths";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  base: "/static/bundler/",
  plugins: [
    tsconfigPaths(),
    tanstackDevtools(),
    devtools({
      autoname: true,
    }),
    tanstackRouter({
      target: "solid",
      autoCodeSplitting: true,
    }),
    solid(),
    wasm(),
  ],
  build: {
    manifest: "manifest.json",
    emptyOutDir: true,
    // Resolve relative to this config file so `vite build` works the same
    // regardless of cwd (CI / Docker / Makefile all run from different
    // directories).
    outDir: path.resolve(__dirname, "../static/bundler/"),
    rollupOptions: {
      input: "src/index.tsx",
      // `@tanstack/hotkeys-devtools` ships a top-level "use client" directive
      // (React RSC convention) that has no meaning in our Solid bundle; Rollup
      // otherwise logs it once per build.
      onwarn(warning, defaultHandler) {
        if (
          warning.code === "MODULE_LEVEL_DIRECTIVE" &&
          warning.message.includes("@tanstack/hotkeys-devtools")
        ) {
          return;
        }
        defaultHandler(warning);
      },
    },
  },
  css: {
    preprocessorOptions: {
      scss: {
        quietDeps: true,
        silenceDeprecations: ["color-functions", "import"],
      },
    },
  },
});
