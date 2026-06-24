// Build orchestrator for multi-page React dashboard UI.
// Runs vite build once per page by setting TM_PAGE before each
// invocation, so every output dir is regenerated. Works cross-platform without
// extra deps (avoids cross-env).
import { spawn } from "node:child_process";

const target = process.argv[2]; // optional: "start" | "digest" | "health" | "quality" | "settings" | "agent-tools" | "canvas"
const pages = target ? [target] : ["start", "digest", "health", "quality", "settings", "agent-tools", "canvas"];

for (const page of pages) {
  console.log(`\n=== building ${page} ===`);
  await new Promise((resolve, reject) => {
    const child = spawn("npx", ["vite", "build"], {
      stdio: "inherit",
      env: { ...process.env, TM_PAGE: page },
      shell: process.platform === "win32",
    });
    child.on("close", (code) =>
      code === 0 ? resolve(undefined) : reject(new Error(`vite build (${page}) exited ${code}`)),
    );
    child.on("error", reject);
  });
}
console.log("\n=== build complete ===");
