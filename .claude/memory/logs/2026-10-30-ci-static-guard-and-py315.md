# 2026-10-30 — CI on PR #22: static guard and the Windows Python 3.15 failure

## `frontend-static-guard` — a real Next.js-on-Windows bug, not hash drift

The suspicion carried over from 2026-10-29 (Windows vs Linux chunk hashes differ) was wrong: CI's Linux rebuild produced byte-identical content, and `git diff` only showed six *deleted* files.
Next's static export names each segment-prefetch file by replacing `/` with `.` in a path built with `path.relative` (`next/dist/export/index.js`, `collectSegmentPathsImpl`), which on Windows contains `\`, so a Windows build writes `memory/__next.memory/__PAGE__.txt` where Linux writes `memory/__next.memory.__PAGE__.txt`.
Linux is right: the client router (`segment-cache/cache.js`) requests the dotted name, so the committed Windows build served 404s for segment prefetches — the guard caught a real defect.
Unfixed upstream as of next 16.5.0-canary.32.
Fix: `frontend/scripts/copy-to-static.mjs`'s `flattenSegmentDirs()` folds `__next.*` directories into dotted file names after copying; a no-op on Linux; the six files are pure renames (identical blobs).
Weakness noted, not changed: the guard's `git diff --exit-code` cannot see untracked files — that is why the Linux-named files were invisible in the CI diff; `git status --porcelain -- src/webui/static` would be stricter.

## `platform (windows-2022, 3.12)` — `py -3` picks Python 3.15

`test_stage_release_real_pipeline` fails with `Build-EngineVersion failed … Failed to install Python dependencies`; pip's own output is lost because `_build_engine_version` raises with `stderr or stdout`.
Not caused by the branch: the same test passed on runner image `20260824`; image `win22/20261025` ships Python 3.15, and `Resolve-PythonCommand ''` tries `py -3` first, i.e. the newest registered Python.
On 3.15 `onnxruntime` and `mmh3` (via fastembed) have no wheels (`pip install --dry-run --python-version 3.15 … → ResolutionImpossible`); 3.12 and 3.14 resolve.
A real product bug: a machine with 3.15 next to 3.12 breaks both self-update and `install.ps1` without `-Python`.
User's decision: one unified Python version check across every install path (install scripts, get one-liners, self-update), with an explicit supported range and an upper bound, instead of trusting whatever `py -3` returns.
