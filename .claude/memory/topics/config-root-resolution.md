# Project-root resolution in `config.resolve()` — `CLAUDE_PROJECT_DIR` dropped (2026-09-04)

**Update, same day, after the fix below was already drafted:** the initial diagnosis here (a leaked `$CLAUDE_PROJECT_DIR`) turned out to be the wrong root cause.
The real bug — much bigger — is `mnemo_bootstrap.py`'s launcher dispatch forcing `cwd=` to the engine's own install directory on *every* invocation of the installed `mnemo`/ `mnemo.exe`.
See "The real root cause" below; that section is authoritative.
Dropping `CLAUDE_PROJECT_DIR` from `config.resolve()` was still done and is still correct (its own justification — an auto-inject hook — is independently gone), but it did **not** fix the reported problem by itself; live-reproduced (`mnemo tree`/`mnemo search` run from this very repo's root, via the installed launcher, both silently queried the engine's own near-empty bank instead of the mnemo project bank) *before* the launcher fix landed, confirming `Path.cwd()` was wrong regardless of any env var.

**Second update, same day, after the PR was already open:** the first attempt at "the real root cause" fix (bare removal of `cwd=str(version_root)`, described below in "The real root cause" / "Fix") was **itself wrong** — it fixed the reported bug but broke a different, real, previously-tested guarantee: Windows CI's `test_install_windows.py` "project-local src shadowing" case failed immediately (2 failing Windows checks on the PR, macOS/Linux unaffected — this dispatcher only runs on Windows).
See "The shadowing regression, and the actual fix" below for the corrected design (`$MNEMO_INVOKED_CWD` + restored `cwd=`), which is what actually shipped.

## Incident

Running `mnemo init` from `E:\work_projects\python\voice-agent` (a plain interactive PowerShell window) printed `mnemo init: project = C:\Users\dima\.mnemo\versions\v3.2.0` — it wired a bank and wrote `.claude/memory`, `.mcp.json.template`, `mcp-setup.ps1/.sh` etc. **inside the installed engine's own versioned copy**, not into `voice-agent`, even though `mnemo init -h` said `--root ROOT  Project root (default: cwd)`.

Root cause: that PowerShell session had `$env:CLAUDE_PROJECT_DIR` (or `$env:MNEMO_ROOT`) set to the engine's version directory, leaked in from some other Claude-Code-spawned process tree.
`config.resolve()`'s precedence was `explicit > $MNEMO_ROOT > $CLAUDE_PROJECT_DIR > cwd` — the env var silently outranked the terminal's actual cwd, and nothing in `--help` mentioned that either override existed.

Cleanup performed: removed the stray `v3.2.0` bank (`mnemo banks remove v3.2.0`, deleted its index) and deleted the files `init` had written under `~/.mnemo/versions/v3.2.0/` (`.claude/`, `.mcp.json*`, `.mcp.env*`, `mcp-setup.*`, `.gitignore`).

## Why `CLAUDE_PROJECT_DIR` was in the chain at all

Added deliberately in an earlier phase (`docs/Memory-contracts-v3.md` §15.8, decision #12) for a very specific reason: mnemo used to ship an **auto-inject hook** that read the project root to decide which bank to query.
A hook receives `cwd` unreliably (or a subdirectory of the real project root), while Claude Code passes `CLAUDE_PROJECT_DIR` to every hook/MCP invocation explicitly — so the hook was made to send that instead of `cwd`, and `config.resolve()` grew the matching env-var branch to honor it.

## Why it was safe to remove

That auto-inject hook **no longer exists**.
Per `CLAUDE.md`: "No hook targets any more beyond the `hook-postedit` no-op shim: the discipline lives in the rule, not in an injection." The entire justification for the `CLAUDE_PROJECT_DIR` branch evaporated when the hook did — nobody removed the now-pointless branch when the hook was retired.

Checked every live caller of `config.resolve()` before removing it:

- MCP (`/mcp?token=...`) resolves the bank from the **token**, never from cwd/env.
- `mnemo search`/`reindex`/`tree` (the CLI) resolve the bank through the **registry**, via `cli._bank_ref()` — matches `cwd` against registered bank roots in both directions, no env vars involved at all.
- `index.py::reindex()` / `pending_embeddings()` also call `config.resolve()`, but have **zero callers anywhere in the codebase** — dead code left over from before the service/workqueue architecture (the live watcher/workqueue path calls lower-level `index.index_file()`/`index.prune()` directly with a `bank.root` from the registry).

So `config.resolve()`'s env-var fallback mattered, in practice, only for one live caller: `scaffold.py::init_project()` — i.e. `mnemo init`.
That is exactly where the stale `CLAUDE_PROJECT_DIR` became a footgun instead of doing anything useful.

## The real root cause: `mnemo_bootstrap.py` forced `cwd=str(version_root)`

`mnemo.exe` (the installed launcher) is a thin dispatcher: `mnemo_bootstrap.py` resolves the active engine version, then does `subprocess.run([python, "-m", "src.cli", *sys.argv[1:]], cwd=str(version_root), ...)`.
That `cwd=str(version_root)` **unconditionally** points the child at the engine's own `~/.mnemo/versions/<tag>/` — never at wherever the terminal actually was.
Introduced by `3cf33e7` (self-update engine: versioned `.venv`, launcher dispatch, 2026-08-20), whose comment explains the *intent* (so `-m src.cli` can still import the `src` package even if something changes directory) but the same commit already added `env["PYTHONPATH"] = str(version_root) + ...` as a "belt-and-braces" second mechanism for exactly that — making the `cwd=` override redundant for its own stated purpose, while silently breaking every consumer of `Path.cwd()` downstream.

Verified live (2026-09-04, before touching `mnemo_bootstrap.py`): running `mnemo tree` and `mnemo search "..."` from `E:\work_projects\other\mnemo` (the real mnemo repo, containing 890+ indexed chunks) via the installed launcher both silently resolved the **engine's own** bank (`~/.mnemo/versions/v3.2.0l/.claude/memory`, 1 chunk) instead of the mnemo project's bank — with no error, no warning, just a plausible-looking answer from the wrong data.
Confirmed the fix by simulating the corrected dispatch directly (`PYTHONPATH=<version_root>` + real cwd, no `cwd=` override, invoking the versioned venv's `python.exe -m src.cli` by hand) — `src` imported fine and `tree` correctly saw the real project's 74 files.

**Audited the entire blast radius before changing anything:** `Path.cwd()` / `os.getcwd()` appear in exactly two places in the whole `src/` tree — `config.resolve()` (root of `mnemo init`; also two dead functions in `index.py` with zero live callers) and `cli._bank_ref()` (`search`/`tree`/`reindex`/`ingest`'s default `--bank`).
Nothing else — service, watcher, workqueue, API, MCP, registry — reads cwd at all; all of it uses absolute paths off `MNEMO_HOME`/the bank registry.
Confirmed separately that the persistent service is unaffected because `service_ctl.start()` / `spawn_detached()` bypass `mnemo_bootstrap.py` entirely (they invoke the versioned venv's `python.exe -m src.cli serve` directly with their own explicitly-computed `cwd`, never inheriting the CLI dispatcher's).
MCP is HTTP inside the already-running service, no per-request spawn.
`hook-postedit` does nothing but `return EXIT_OK`.
So the fix's effect is precisely scoped: plain human/script invocations of the installed `mnemo` CLI (`init`/`search`/`tree`/`reindex`/`ingest` without an explicit `--root`/`--bank`) now see the real caller's directory, as `--help` always claimed.

**Fix:** removed the `cwd=str(version_root)` argument from `subprocess.run(...)` in `mnemo_bootstrap.py` (`src/`-repo copy — same file installer mirrors into every engine version) — the child now inherits the real invoking process's cwd, same as any normal subprocess call with no `cwd=` override.
`PYTHONPATH` (unchanged, already set unconditionally) remains fully sufficient for `-m src.cli` to import `src` regardless of cwd.
Re-verified end to end after rebuilding the local engine (`install.ps1`): `mnemo tree` from the mnemo repo now correctly shows the real bank; `mnemo init` in a fresh empty directory — with a bogus `$CLAUDE_PROJECT_DIR` deliberately set to prove it's no longer consulted — correctly registered the bank at the real cwd, not at the bogus path or the engine directory.

## The shadowing regression, and the actual fix

The bare removal of `cwd=str(version_root)` (described above) was diagnosed correctly but fixed the wrong way.
Python's `-m` flag **always** prepends the process's own `os.getcwd()` to `sys.path`, ahead of `PYTHONPATH` — this is documented CPython behaviour, not a mnemo quirk.
So once the dispatched subprocess's cwd became the caller's real project directory, any project with its own top-level `src/` package (an extremely common Python layout — src-layout is a widely recommended convention) would have that `src` win the import over mnemo's own `src` at `version_root`, because cwd now sat ahead of `PYTHONPATH` in `sys.path`.
`PYTHONPATH` was never "belt-and-braces redundant with `cwd=`" as the first fix assumed — it was the *secondary* defence; `cwd=` was the *primary* one, and removing it removed the actual protection.

This is exactly what `tests/test_install_windows.py`'s "project-local src shadowing" case exists to catch: it runs `mnemo.exe --help` with `cwd` set to a directory containing a hostile `src/__init__.py` + `src/cli.py` (`raise RuntimeError("project src shadowed mnemo")`) and asserts `--help` still succeeds.
It caught the regression immediately in CI (Windows-only jobs failed; macOS/Linux passed, since this dispatcher and its `-m`-cwd-prepend behaviour is Windows-specific to this exe path) — the PR was already open when this surfaced.

**The actual fix keeps both properties, because they don't actually conflict — they were solved by conflating two different things (a subprocess's OS-level `cwd`, and "the directory `config.resolve()`/`cli._bank_ref()` should treat as the project root") into one mechanism (`cwd=`) that could only serve one master at a time:**

1. `mnemo_bootstrap.py` captures `os.getcwd()` **before** anything else, into a local `invoked_cwd` — this is the one and only place in the whole call chain that ever sees the real caller's directory as `Path.cwd()` would report it natively.
2. That value is passed to the child via a new env var, `env["MNEMO_INVOKED_CWD"]`.
3. `subprocess.run(..., cwd=str(version_root), ...)` is **restored** exactly as it was before this incident — so the dispatched process's own `Path.cwd()` stays the engine directory, `-m src.cli`'s `sys.path` prepend stays harmless (`version_root`, the correct `src`), and the shadowing test passes again.
4. `config.py` gains a public `invoked_cwd() -> Path` helper: reads `$MNEMO_INVOKED_CWD` if set, else falls back to a real `Path.cwd()` (so direct `python -m src.cli` invocations — dev shells, the test suite, anything with no launcher in front of it — keep working exactly as before, since for those the env var is simply absent and native `Path.cwd()` is already correct).
5. `config.resolve()` and `cli._bank_ref()` — the only two places in `src/` that ever read `Path.cwd()`/`os.getcwd()` — both now call `config.invoked_cwd()` instead of `Path.cwd()` directly.

**Why this new env var isn't "just another `CLAUDE_PROJECT_DIR`" — the exact failure mode that killed `CLAUDE_PROJECT_DIR`, computed staleness/leakage across unrelated processes, cannot happen here: `$MNEMO_INVOKED_CWD` is set fresh, by `mnemo_bootstrap.py` itself, on every single invocation, directly from `os.getcwd()` at the moment THAT invocation starts — it is never inherited from a parent shell, never carried over from an unrelated session, and nothing outside this one dispatch call ever sets it.**

Re-verified live after rebuilding the engine with both fixes together: (a) `mnemo --help` run with cwd inside a directory containing a hostile `src/cli.py` succeeds (shadowing guarded), and (b) `mnemo tree` run from this repo's own root correctly shows the real mnemo bank, and `mnemo init` in a fresh empty directory with a bogus `$CLAUDE_PROJECT_DIR` set still resolves to the real cwd — both properties hold simultaneously.

Files touched by this second round: `mnemo_bootstrap.py` (restored `cwd=`, added `MNEMO_INVOKED_CWD` capture), `src/config.py` (new `invoked_cwd()`, `resolve()` uses it), `src/cli.py` (`_bank_ref()` uses it too), `tests/test_platform.py` (`test_project_resolution` extended to cover `MNEMO_INVOKED_CWD` precedence and the no-launcher fallback).

## Decision (2026-09-04)

`config.resolve()` precedence is now: **explicit `--root` > `$MNEMO_ROOT` > cwd.** `$CLAUDE_PROJECT_DIR` is no longer consulted anywhere in mnemo.

`$MNEMO_ROOT` stays — it is not the same kind of risk.
It is a real, documented, **deliberately user-set** override for non-interactive/containerized deployments (`docs/containers/README.md`, the docker-compose examples): a container's cwd is not meaningfully "the project," so pinning the root via `environment: MNEMO_ROOT=...` is the correct, intended mechanism there.
Nobody's shell acquires `$MNEMO_ROOT` by accident the way it acquires `$CLAUDE_PROJECT_DIR` from a Claude-Code-spawned parent process — that asymmetry is the entire reason one stays and the other goes.

Files touched: `mnemo_bootstrap.py` (dropped `cwd=str(version_root)` — **this is the fix that actually matters**), `src/config.py` (`resolve()` + docstring — drops `CLAUDE_PROJECT_DIR`, independently correct but not the reported bug's cause), `src/cli.py` (`init`'s `--root` help text now names `$MNEMO_ROOT` explicitly), `tests/test_platform.py` (`test_project_resolution` — replaced the "CLAUDE_PROJECT_DIR fallback" assertion with one proving it's now ignored), `docs/Memory-contracts-v3.md` §15.8 and decision-table row 12 (marked superseded, reasoning kept for history), `docs/Setup-design.md`, `.claude/skills/mnemo-adopt/references/mnemo.md`.

**Lesson for next time:** the first plausible-sounding explanation (a leaked env var) was believable, testable-sounding, and wrong — it was never actually confirmed (nobody checked `$env:CLAUDE_PROJECT_DIR` in the offending shell before committing to that theory and starting the fix).
What broke the theory open was reproducing the bug directly against a known-good bank (this repo's own, 890 chunks) instead of trusting the narrative.
Reproduce before diagnosing, even when the first theory fits neatly.

**Second lesson, from the same incident's second round:** a code comment that calls one mechanism "belt-and-braces redundant" with another is a claim, not a fact — it went unverified here until CI (not local testing) proved it false.
`python -m pkg.mod` prepending the process's own `cwd` to `sys.path` **ahead of** `PYTHONPATH` is documented, general CPython behaviour worth remembering on its own: any dispatcher that both (a) runs a package via `-m` and (b) wants to control the child's *working* directory independently of *import* resolution needs two separate mechanisms (here: `cwd=` for imports, an explicit env var for the logical "real directory") — collapsing them into one `cwd=` value can only serve one of the two purposes at a time.
Also: this local push had already been verified by hand before opening the PR, and it still took CI's Windows-specific job to surface the regression — a reminder that "I tested it locally and it worked" is not the same claim as "full CI is green," especially for anything OS-specific that the local dev machine's manual checks don't naturally exercise the same way a clean CI runner does.
