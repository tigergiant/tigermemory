import path from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Multi-page build: each React dashboard page is a separate entry with its own
// output directory. Select the target via the TM_PAGE env var (default: start).
//   TM_PAGE=start   ->  src/index.html            ->  static/react/start/
//   TM_PAGE=digest  ->  src/digest/index.html     ->  static/react/digest/
//   TM_PAGE=ledger  ->  ledger.html               ->  static/react/ledger/
//   TM_PAGE=health  ->  src/health/index.html     ->  static/react/health/
//   TM_PAGE=quality ->  src/quality/index.html    ->  static/react/quality/
//   TM_PAGE=settings -> src/settings/index.html    ->  static/react/settings/
//   TM_PAGE=agent-tools -> agent-tools.html           ->  static/react/agent-tools/
//   TM_PAGE=canvas -> canvas.html                 ->  static/react/canvas/
//   TM_PAGE=self-evolution -> self-evolution.html ->  static/react/self-evolution/
// The base path must match the FastAPI static mount so hashed assets resolve.
const pages = new Set(["start", "digest", "ledger", "health", "quality", "settings", "agent-tools", "canvas", "self-evolution"]);
const requestedPage = process.env.TM_PAGE || "start";
const page = pages.has(requestedPage) ? requestedPage : "start";

const entry =
  page === "digest"
    ? path.resolve(__dirname, "digest.html")
    : page === "ledger"
      ? path.resolve(__dirname, "ledger.html")
      : page === "health"
        ? path.resolve(__dirname, "health.html")
        : page === "quality"
          ? path.resolve(__dirname, "quality.html")
          : page === "settings"
            ? path.resolve(__dirname, "settings.html")
            : page === "agent-tools"
              ? path.resolve(__dirname, "agent-tools.html")
              : page === "canvas"
                ? path.resolve(__dirname, "canvas.html")
                : page === "self-evolution"
                  ? path.resolve(__dirname, "self-evolution.html")
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
