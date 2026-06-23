import path from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Multi-page build: each React dashboard page is a separate entry with its own
// output directory. Select the target via the TM_PAGE env var (default: start).
//   TM_PAGE=start   ->  src/index.html            ->  static/react/start/
//   TM_PAGE=digest  ->  src/digest/index.html     ->  static/react/digest/
// The base path must match the FastAPI static mount so hashed assets resolve.
const page = process.env.TM_PAGE === "digest" ? "digest" : "start";

const entry =
  page === "digest"
    ? path.resolve(__dirname, "digest.html")
    : path.resolve(__dirname, "index.html");

const outDir = path.resolve(
  __dirname,
  `../tigermemory-dashboard/src/tigermemory_dashboard/static/react/${page}`,
);

export default defineConfig({
  base: `/static/react/${page}/`,
  plugins: [react(), tailwindcss()],
  build: {
    outDir,
    emptyOutDir: true,
    manifest: true,
    sourcemap: false,
    rollupOptions: {
      input: entry,
      output: {
        assetFileNames: "assets/[name]-[hash][extname]",
        chunkFileNames: "assets/[name]-[hash].js",
        entryFileNames: "assets/[name]-[hash].js",
      },
    },
  },
});
