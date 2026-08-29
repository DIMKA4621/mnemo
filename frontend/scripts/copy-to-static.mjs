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
import { cpSync, existsSync, mkdirSync, readdirSync, rmSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = join(here, "..");
const repoRoot = join(frontendRoot, "..");
const outDir = join(frontendRoot, "out");
const staticDir = join(repoRoot, "src", "webui", "static");

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
console.log(`copied ${outDir} -> ${staticDir}`);
