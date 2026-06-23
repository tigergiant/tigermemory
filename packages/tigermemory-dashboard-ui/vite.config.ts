import path from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  base: "/static/react/start/",
  plugins: [react(), tailwindcss()],
  build: {
    outDir: path.resolve(
      __dirname,
      "../tigermemory-dashboard/src/tigermemory_dashboard/static/react/start",
    ),
    emptyOutDir: true,
    manifest: true,
    sourcemap: false,
    rollupOptions: {
      output: {
        assetFileNames: "assets/[name]-[hash][extname]",
        chunkFileNames: "assets/[name]-[hash].js",
        entryFileNames: "assets/[name]-[hash].js",
      },
    },
  },
});
