# 2026-08-24 — MN-24/23/22/21 batch: status autostart, Linux autostart --now, install PATH, disk-space check

Four tickets run through the standard plan→implement→test/review→commit cycle in one sitting, all landed on a single generic branch `fix` (not per-ticket branches — explicit user request: more unrelated fixes were expected to land on the same branch before a PR).

## Commits (in order, all on `fix`, off `master` at `44d0916`)

1. `8b8e06c` — webui CSS nesting fix (unrelated pre-existing small ask, landed first).
2. `48eed59` — MN-24: `mnemo status` no longer autostarts a stopped service.
3. `490ec2c` — MN-23: Linux autostart enable/disable no longer kills the live process.
4. `ab8c541` — MN-22: `install.sh` registers the `mnemo` wrapper in every rc file the login shell actually reads.
5. `a341881` — MN-21: disk-space check before install/update/model-download.

## MN-24 — `mnemo status` autostart bug

Root cause: `_cmd_status()` → `_run_api()` → `_client()` built a `Client(...)` with `autostart=True` by default; `Client._request()` calls `ensure_backend()` (self-spawn) on any connection error, with no exemption for `status`.
Fix: `_client()`/`_run_api()` gained a keyword-only `autostart: bool = True`, `_cmd_status()` passes `autostart=False`.
Trivial, single-file (`src/cli.py`), no architecture impact.
Both a tester and a reviewer independently confirmed the fix and found no other call site wrongly affected.

## MN-23 — Linux autostart toggle kills the live service

Root cause: `_linux_enable()`/`_linux_disable()` (`src/autostart.py`) called `systemctl --user enable/disable --now mnemo.service`.
`--now` doesn't just register the logon entry, it immediately starts/stops the unit — and `mnemo.service` is the same unit the live backend runs under, so toggling autostart on Linux genuinely killed the running process; the next API call (webui poll, or MN-24's own lazy-autostart) silently respawned it, reading as "the service restarted" (new pid, reset uptime).
Fix: drop `--now` from both calls — `install.sh` already starts the service via a separate explicit step (`autostart enable` at step 7, `service start` at step 9), so nothing relies on the old side effect.
Verified by static code reading only (no Linux machine in-session, by explicit user instruction — "поки тільки по коду... сам туди не лізь", i.e. do not touch a remote Linux box for this).

## MN-22 — `mnemo` not on PATH after Linux/macOS install

Root cause: `install.sh` wrote the `mnemo()` shell function into a single `PROFILE_FILE` (`~/.profile`, or `~/.bashrc` as fallback if `.profile` doesn't exist).
`.profile` is login-shell-only — a plain new terminal (GNOME Terminal, VS Code, etc.) opens a non-login bash and never reads it. zsh (macOS default) wasn't covered at all.

User explicitly widened scope mid-grooming ("Треба фікс під обидві системи: і під macOS, і під Linux... просто в два файли записати") — not just a bash non-login/login fix, but real zsh + macOS coverage too, done by detecting `$SHELL` and writing to the right set of files rather than treating it as a bigger/riskier feature.

Fix: replaced the single `PROFILE_FILE` with a `PROFILE_FILES` array chosen by `basename "$SHELL"` (the user's real login shell, not whatever shell is running the installer): bash/unknown → `.bashrc` + `.profile`; zsh → `.zshrc` + `.zprofile`.
The existing idempotent fenced-block `awk`-replace logic just got wrapped in a loop over the array, no logic duplicated.

**Real bug caught by review, fixed inline (not delegated back out — small enough)**: the first implementation's zsh fallback was `.zprofile` if it exists, else `.profile` — but zsh never reads `.profile` (only `.zshenv`/`.zprofile`/`.zshrc`/`.zlogin`), so that fallback silently wrote to a file the shell would never source.
Fixed to always create `.zprofile` directly (nothing to clobber, it didn't exist) instead of falling back to a dead file.
Didn't violate the ticket's stated AC either way (`.zshrc` alone already satisfies "new terminal sees mnemo"), but was a real correctness gap in what the code claimed to do.

## MN-21 — disk-space check before install/update/model-download (new feature, largest of the four)

Went through `planner` first (not straight to implementation) because it spans 3 files across 2 languages (bash, PowerShell, Python) with a real architectural choice to make.

**Architecture decision (user-confirmed, not the lead's call alone — flagged as a genuine fork)**: three independent native implementations (bash `df`, PowerShell `(Get-Item).PSDrive.Free`, Python `shutil.disk_usage`), constants duplicated in each, rather than one shared Python helper that `install.sh`/`install.ps1` would shell out to.
This matches an existing repo precedent — `human()` (install.sh) / `Format-Bytes` (install.ps1) / `human_bytes()` (diagnostics.py) are already three independent "format bytes" implementations for the exact same reason: `install.sh`/`install.ps1` run *before* any venv exists, so shelling to a Python helper would be an added subprocess round-trip for the sake of 3 constants.
Risk (constant drift across 3 files) is mitigated by an explicit sync test, not by structural sharing.

**Numbers (user-confirmed)**: `ENGINE_VERSION_SIZE_BYTES = 300_000_000` (≈234 MiB measured + rounding), `MODEL_DOWNLOAD_SIZE_BYTES = 2_200_000_000` (matches the "~2.2 GB" text already shown in install prompts, not the more precise 2.15 GB measured on disk — chosen for UX consistency with what the user already sees), `INSTALL_DISK_BUFFER_BYTES = 500_000_000`.
`--no-model`/`-NoModel` excludes the model size from the requirement entirely (planner's own addition, user-confirmed — not in the original ticket text).

**Scope widened mid-implementation (user, while 2 agents were already running)**: also check disk space before *downloading the model specifically*, not just install/self-update — the webui "Завантажити модель" button and `mnemo warmup`.
Investigated and found both triggers converge on one choke point: `POST /api/embed/download` (api.py) doesn't call `warmup()` in-process, it spawns `mnemo warmup --force` as a subprocess.
So the check only needed adding once, in `_cmd_warmup()` (cli.py), after the existing "nothing to download for this provider" early-return and before the actual `warmup()` call — reusing the same `engine_update.check_disk_space()`, with new `include_version_size: bool` / `target: Path | None` parameters so the warmup call can ask for "just the model budget, at `model-cache/`" instead of the self-update shape ("version + model, at `versions_dir()`").

**Two real bugs caught by review (first pass), sent back and fixed**:
1. `install.ps1`'s new `Test-DiskSpace` failure path used a bare `exit 1` — the *only* direct `exit` inside `Invoke-Install` besides the top-level catch block.
   Every other failure in that function `throw`s, and the top-level `try { Invoke-Install } catch { ...; if ($PSCommandPath) { exit 1 } }` guard exists specifically so a bare `exit` is never reached when the script runs via `iex (irm ...)` with no real file backing it — `exit` there kills the whole calling PowerShell process/terminal, not just the script.
   There's a recorded prior live incident of install.ps1 touching a real `~/.mnemo` unexpectedly (2026-08-22 log) tied to this exact class of risk.
   Fixed: `Test-DiskSpace` throws instead of printing+returning `$false`, routing through the same centrally-caught guarded path as every other install.ps1 error.
2. The ticket's own stated AC ("constants duplicated, sync covered by a test") wasn't actually met — no test existed anywhere for the new bash/PowerShell functions, despite both being explicitly split into small testable units with comments saying so.
   Fixed: added `test_disk_space_check()` to both `tests/test_install_posix.py` and `tests/test_install_windows.py` (dot-source the real functions, stub only the "available bytes" primitive), plus `check_disk_space_constants_agree()` in both files (regex-extracts the 3 constants from `install.sh`, `install.ps1`, `src/config.py`, asserts equality) — duplicated in both test files on purpose, since each self-skips on the "wrong" OS and the sync check needs to run regardless of which OS the CI happens to be.

A second review round confirmed both fixes: `test_install_windows.py` real run — 41/41 passed (35 pre-existing + 6 new disk-space tests).
`test_engine_update.py` — 128/128.
`test_platform.py` — 316/316.

## Process notes (for future batches like this)

- Running 2+ implementation agents in parallel on genuinely disjoint file sets (service-dev on `config.py`/`engine_update.py`/`diagnostics.py`/`cli.py`, platform-dev on `install.sh`/`install.ps1`) worked cleanly with zero merge conflicts — worth doing again when a feature naturally splits by file ownership.
- Mid-flight scope additions (the MN-22 zsh widening, the MN-21 warmup-download widening) were absorbed by messaging the *already-running* agent that owned the relevant file, rather than spinning up a new one — cheaper and kept context/decisions in one place.
- Two `nul` / scratch-diff files (`nul`, `mn21.diff.txt`) turned up as stray untracked artifacts from agent tool use (Windows `> nul` redirect misfire; a reviewer's own working diff dump) — neither was part of the actual change, both deleted before commit.
  Worth an eyeball on `git status` before every commit in a multi-agent batch like this, not just a blind `git add`.
