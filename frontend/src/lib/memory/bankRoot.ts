import type { FsDirsResult } from "@/lib/api/memory";

/** `<project>/.claude/memory` -> `<project>`, or null when `path` isn't
 *  shaped that way. Mirrors `_project_root_from_bank` in `src/api.py`. */
export function projectRootForBankPath(path: string | null | undefined): string | null {
  if (!path) return null;
  const norm = path.replace(/\\/g, "/").replace(/\/+$/, "");
  const suffix = ".claude/memory";
  if (!norm.endsWith("/" + suffix)) return null;
  const root = norm.slice(0, norm.length - suffix.length - 1);
  return root || null;
}

/** The root a bank gets registered at once "create structure" is checked —
 *  the server's own `memory_dir` for the currently loaded directory, never
 *  naive string concatenation (a path already ending in `.claude` needs
 *  only `memory` appended). */
export function effectiveBankRoot(path: string, createStructure: boolean, data: FsDirsResult | null): string {
  if (!createStructure) return path;
  if (data?.memory_dir) return data.memory_dir;
  return path.replace(/\/+$/, "") + "/.claude/memory";
}
