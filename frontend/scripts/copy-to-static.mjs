#!/usr/bin/env node
/**
 * `next build` (with `output: 'export'`) writes to `frontend/out/`; this
 * copies that tree into `src/webui/static/`, replacing its contents. Run
 * via `npm run release` (build + copy) from `frontend/`. The result is
 * committed to git by the team lead — this script never touches git itself.
 *
 * `src/webui/__init__.py`'s `STATIC_DIR`/mount contract is unchanged: this
 * script only ever writes files under that same directory, never the
 * mount/serving code.
 */
import { cpSync, existsSync, mkdirSync, readdirSync, renameSync, rmSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, relative, sep } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = join(here, "..");
const repoRoot = join(frontendRoot, "..");
const outDir = join(frontendRoot, "out");
const staticDir = join(repoRoot, "src", "webui", "static");

// Next's export names each segment-prefetch file by replacing "/" with "."
// in a path built with `path.relative`, which on Windows yields "\" — so a
// Windows build writes `memory/__next.memory/__PAGE__.txt` where Linux writes
// (and the client router requests) `memory/__next.memory.__PAGE__.txt`.
// Unfixed upstream as of next 16.5.0-canary.32 (`dist/export/index.js`,
// `collectSegmentPathsImpl`). Folding the stray directories back makes the
// output identical on every OS.
function flattenSegmentDirs(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const path = join(dir, entry.name);
    if (!entry.name.startsWith("__next.")) {
      flattenSegmentDirs(path);
      continue;
    }
    for (const file of readdirSync(path, { recursive: true, withFileTypes: true })) {
      if (file.isDirectory()) continue;
      const nested = relative(path, join(file.parentPath, file.name));
      renameSync(join(path, nested), join(dir, `${entry.name}.${nested.split(sep).join(".")}`));
    }
    rmSync(path, { recursive: true });
  }
}

if (!existsSync(outDir)) {
  console.error(`error: ${outDir} does not exist — run "next build" first`);
  process.exit(1);
}

if (existsSync(staticDir)) {
  for (const entry of readdirSync(staticDir)) {
    rmSync(join(staticDir, entry), { recursive: true, force: true });
  }
} else {
  mkdirSync(staticDir, { recursive: true });
}

cpSync(outDir, staticDir, { recursive: true });
flattenSegmentDirs(staticDir);
console.log(`copied ${outDir} -> ${staticDir}`);
