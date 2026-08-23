# 2026-08-22 — real-machine incident: self-update UI staleness + console-window regression

User hit this on two real machines after the v3.0.1 release: `current` genuinely switched (confirmed via `mnemo doctor`/Maintenance page showing the right Python path), but the console kept lying — a stuck yellow "очікує підтвердження" badge that did nothing on click, a footer stuck on "v 3.0.0" forever, a brand-new laptop claiming "update available 3.0.1" immediately after installing exactly that version, a red 3-minute timeout card, and — twice, on a fresh install AND a live self-update — a blank console window that popped up and hung for the duration of `pip install`.

Diagnosed live against the actual source (not guessed) before touching anything, per user's explicit "diagnose first" instruction.
Five distinct, independently-confirmed root causes, all fixed same session.
Plan file (for the record, plan mode was used given the cross-cutting scope): `majestic-stirring-pnueli.md` (superseded a stale cabinet-rename plan from earlier in this session).

## Root cause 1 — `current` unknown until the first self-update ever runs

`install.ps1`/`install.sh` build `versions/<tag>/` from a release archive but never call `engine_update.record_installed(...)` (confirmed: zero matches).
`engine_version.json` doesn't exist after a fresh install → `current` is `None` → every comparison against `latest_known.tag` reads as "update available", even for the version just installed.

**Fix — self-detect instead of requiring the installer to remember a step.** `engine_update._detect_own_tag()`: reads the name of this process's own `versions/<tag>/` directory from `Path(__file__)` (`None` outside the versioned layout — devserver, tests, a checkout run directly).
`effective_current_tag(state)`: `state["current"]` once the registry has ever recorded a switch, else the self-detected tag.
Self-heals if the state file is ever missing, deleted, or hand-edited — not just a one-time installer fix.
Used at every site that used to read the raw `state.get("current")`: `engine_update.record_check()`'s `update_available` formula (the one that matters most — fixes the backend's own `auto_eligible_tag()` decision, not just display, so auto-update can't re-offer the version it's already on), `api.py`'s `api_update_status()` / `api_update_check()`, `diagnostics.py`'s `doctor` self_update section.
`record_installed()` itself untouched — already correct once a real switch happens; this only covers the gap *before* that first write exists.

A "local" dev build self-detects as `"local"`, which will always look "behind" a real release tag — left as-is, a developer running from source knows it, not worth a special case.

## Root cause 2 — `SERVICE_VERSION` is a hardcoded literal nobody bumps

`config.py`'s `SERVICE_VERSION = "3.0.0"`, unchanged since inception — completely disconnected from the self-update tag-tracking system.
Fed the sidebar footer (`shell.js`'s `renderService()`) via `/api/status`, which is exactly why the footer said "v 3.0.0" on a machine genuinely running v3.0.1.

**Fix:** `api.py`'s `SERVICE_VERSION` now derives from the same `engine_update.effective_current_tag(engine_update.read_state())`, falling back to the literal only for devserver/tests outside the versioned layout.
One source of truth instead of two.

## Root cause 3 — `Invoke-CheckedWithHeartbeat`'s `Start-Job` pops a console

This session's own new code (heartbeat progress indicators, landed as `installer-progress-heartbeat.md`) ran `pip install`/`warmup` inside `Start-Job`.
A classic PowerShell background job spawns its OWN hosting `powershell.exe` through the job engine — entirely outside `service_ctl.spawn_detached`'s `CREATE_NO_WINDOW` discipline (that guard only covers mnemo's own Python-side `subprocess`/`Popen` calls).
When the caller has no console of its own — exactly self-update's `stage_release()` dot-sourcing a downloaded release's `install.ps1` from a `subprocess.run` — Windows gives the job host a brand-new, VISIBLE console.
It shows nothing (a job writes to a pipe) and stays open for as long as the step takes.
Matches both reported sightings — fresh install AND live self-update — since both funnel through this one shared `Build-EngineVersion` function.

**Fix:** replaced `Start-Job` with a directly-controlled `System.Diagnostics.Process` (`CreateNoWindow=$true`, `UseShellExecute=$false`) — same explicit creation-flags discipline `service_ctl.py` already applies Python-side, expressed in .NET.

**Two more real bugs caught live, before ever touching the real `install.ps1`**, testing an isolated copy of the rewritten function first:
- `ProcessStartInfo.ArgumentList` came back **`$null`** on this machine's PowerShell/.NET — not a "property not found" error, not a pre-populated empty collection either, just `$null` — so `.Add()` on it threw "cannot call a method on a null-valued expression" for every argument, silently (non-terminating in that context), and the child process ended up launched with NO arguments at all — a bare interactive `powershell.exe` that then hung forever waiting on stdin, produced the exact "dots forever, never exits" symptom this whole fix was for.
  Do not trust `ArgumentList`'s documented ".NET Framework 4.6.1+" availability without checking on the actual target runtime — fixed by hand-quoting each argument (wrap in `"..."` only if it contains whitespace/a quote) and joining into `.Arguments` (a single string, re-split by the OS the normal Win32 way) — the same fix `-ArgumentList`'s naive space-join needed, just without depending on a property this runtime doesn't support.
- `[System.Collections.Generic.List[string]]::Synchronized(...)` doesn't exist — `Synchronized` is an `ArrayList` static method, not `List<T>`'s.
  Fixed by using `[System.Collections.ArrayList]::Synchronized(...)` instead for the captured-output sink.

Verified live afterward: success path (dots print, no throw), failure path (nonzero exit throws with captured output dumped), and — the actual point of the whole fix — an argument containing spaces (`-InstallHome "C:\Program Files\Fake Install Home"`) arrives at the child as ONE argv element (`ARGC=3`, path intact in `ARG[1]`), not silently mis-split.
Also dot-sourced the REAL `install.ps1` (it already has a `$MyInvocation.InvocationName -eq "."` guard for exactly this) and called the real function directly — same result, not just the isolated copy.

`install.sh`'s `run_with_heartbeat` (background `&` + `kill -0`) has no console-window concept on POSIX — untouched, this bug was Windows-only.

## Root cause 4 — `resyncAll()` never re-fetches `/api/update/status`

Self-update deliberately kills the WS mid-switch; the reconnect contract is `hello` → `resyncAll()` re-reads everything over REST (`shell.js`).
But `resyncAll()` only called `loadBanks/loadStatus/loadLogs/loadTree` — never `update.js`'s `refreshUpdateStatus()`, which used to run exactly once, at `boot()`.
A tab that wasn't actively watching an open progress modal when an update happened (auto-apply firing server-side, or another tab/CLI triggering it) was left with permanently stale `state.update` — the stuck badge, the stale tag comparison — until a manual full reload.

**Fix:** one added line, `refreshUpdateStatus().catch(() => {})`, in `resyncAll()`.

## Root cause 5 — poll timeout, per explicit user request

`UPDATE_POLL_TIMEOUT_MS`: 3 → 5 minutes.
Possibly related to the red timeout card seen mid-incident (a slower connection's `pip install` genuinely running long, worsened by root cause 3's hanging console) — more margin either way, user asked for it directly rather than it being a diagnosed root cause on its own.

## Root cause 1, round two — self-detection must win over a STALE registry, not just a missing one

Caught live, redeploying this exact fix to the real machine to verify it: running `install.ps1` directly against a machine that had previously done a real self-update repoints `current` to `versions/local/` — a local rebuild is not a self-update, so it never calls `record_installed()`.
`engine_version.json`'s `current` was left holding the OLD real tag (`"v3.0.1"`) while the engine actually running was `"local"`.
My first cut of `effective_current_tag()` (`state.get("current") or _detect_own_tag()`) got the priority backwards: it trusted the stale registry entry over the correct, freshly-true self-detected value, so `doctor`/`/api/update/status` kept reporting `"v3.0.1"` right after `current -> local` printed on the same run.
Reproduced and confirmed via `mnemo doctor` before touching the code again.

**Fix:** flipped the priority — `_detect_own_tag() or state.get("current")`.
Self-detection (what folder this process is actually running from) is ground truth for "what is running right now" the moment anything reassigns `current` outside the self-update path; the registry is the fallback only for when self-detection genuinely cannot answer (devserver, tests, a checkout run outside `~/.mnemo` entirely).
Zero downside for the normal self-update case — after a real switch, both sources already agree.

Verified: new test case `effective_current_tag({"current": "v3.0.1"})` with `_detect_own_tag` patched to `"v9.9.9"` now resolves to `"v9.9.9"`, not the stale `"v3.0.1"` — **86/86**.
Redeployed for real (`install.ps1` on this machine's actual `~/.mnemo`, twice — once with the bug, once with the fix): `doctor` now correctly reports `self-update      current local, v3.0.1 available` and `/health`'s `version` reads `"local"`, matching what is genuinely running.

## Follow-up feature, same day: plain `install.ps1`/`install.sh` can now report a real tag instead of always "local"

User's own follow-up request, after seeing this machine's redeploy report `self-update current local, v3.0.1 available`: shouldn't a plain install still be able to say what version it actually is, either by pulling from GitHub's release info or by checking whether the local checkout is at a real tag?
Both, implemented:

1. **get.ps1/get.sh -> install.ps1/install.sh, `$env:MNEMO_INSTALL_TAG` / `$MNEMO_INSTALL_TAG`.** GitHub's release archive carries no `.git` directory (confirmed earlier, `engine_update.py`'s own `stage_release()` docstring), so a downloaded bootstrap install could never self-detect a tag via git -- it would say "local" forever and nag to "update" to the exact release it just installed.
   `get.ps1`/`get.sh` already know the exact tag they resolved (`Resolve-MnemoArchiveUrl`'s `Tag` field / `$TAG` in the `releases/latest` branch) -- now passed through as an env var, set ONLY for a confirmed release (left unset for the master fallback, a custom archive override, or an explicit `MNEMO_GET_REF` branch override -- none of those name an installable version).
2. **`Get-LocalCheckoutTag`/`get_local_checkout_tag`** (install.ps1/ install.sh): for a manual `git clone && git checkout <tag> && ./install.ps1` workflow.
   Requires the working tree to be genuinely CLEAN (`git status --porcelain` empty) AND HEAD to sit exactly on a tag (`git describe --tags --exact-match`) -- a checkout at v3.0.1 with local edits on top is not actually v3.0.1, so a dirty tree always falls back to "local" regardless of what tag HEAD is near.
   No git / not a repo / detached-untagged HEAD all resolve the same way.

Priority in `Invoke-Install`/the POSIX equivalent: `$env:MNEMO_INSTALL_TAG` (get.ps1's confirmed-release info) > `Get-LocalCheckoutTag` (git-based) > the fixed `"local"` fallback (unchanged default).

Verified live: `Get-LocalCheckoutTag` against a throwaway repo — clean+tag -> the tag, dirty+same tag -> `$null` (correct, does not claim it), against this actual dirty/untagged `feat/v3` checkout -> `$null` (correct, falls back to "local", matching what redeploying to this machine actually showed).
`Resolve-MnemoArchiveUrl`'s `.Tag` field checked against the REAL repo for all three branches: real release -> `v3.0.1` (matches the actual latest tag), explicit ref override -> empty, custom archive -> empty.
Full priority chain (env var wins over git detection) confirmed via an isolated dot-sourced test.
`tests/test_install_windows.py` (29/29) and `tests/test_get_bootstrap.py` (15/15) both still green — neither currently asserts the new `MNEMO_INSTALL_TAG` passthrough specifically (get.ps1's `MNEMO_GET_ARCHIVE_URL` test seam structurally cannot reach the confirmed- release branch that sets it, and there is no seam to fake a successful codeload download while still going through the real tag-resolution path) -- covered by direct manual verification instead, flagged here rather than overclaimed as automated coverage.

## User-decided scheme, same day: `l` suffix instead of bare "local", plus a hard error on a failed release lookup

Two more decisions from the user, refining the follow-up feature above:

1. **`get.ps1`/`get.sh`: a failed/empty release lookup is now a HARD installation error, not a silent fallback to `master`.** Reversed the original 2026-08-21 soft-fallback design on purpose: a one-liner that silently hands someone unreleased `master` when it meant to hand them the latest release makes the installed version depend on which GitHub API call happened to work that day.
   The error message names the likely cause (network vs. genuinely no releases) and points at the manual-clone fallback (`git clone ...; cd mnemo; ./install.ps1`).
   Explicit overrides (`MNEMO_GET_REF`, `MNEMO_GET_ARCHIVE_URL`) are deliberate, not errors — unchanged.
   Required moving `Resolve-MnemoArchiveUrl`'s call inside get.ps1's own try block (it was called BEFORE the try before), so the new `throw` still goes through the existing `$PSCommandPath`-aware exit handling that keeps a failure from closing the caller's shell when run via `irm | iex`.
2. **`Get-LocalCheckoutVersionTag`/`get_local_checkout_version_tag` (renamed from `Get-LocalCheckoutTag`): a real base version instead of bare `"local"`.** Clean tree + HEAD exactly on a tag → that tag verbatim ("v3.0.1").
   Anything else with a *reachable* tag (commits on top of it, or uncommitted changes, or both) → nearest ancestor tag (`git describe --tags --abbrev=0`, no assumption about exact position) + lowercase `"l"` appended ("v3.0.1l") — lowercase specifically because that is the established convention for alpha/beta-style suffixes, per the user.
   Only truly no-tag-reachable-at-all (no git, not a repo, no tags in history) still falls back to the bare `"local"` sentinel.
   UI: the sidebar footer marks an `l`-suffixed version in the existing `--warn-fg` amber (same token already used for "indexing"/in-progress states) — `shell.js`'s `LOCAL_BUILD_VERSION_RE = /\dl$/` detects it, no new visual vocabulary introduced.

### Real bug caught live, before ever shipping this

`install.ps1` sets `$ErrorActionPreference = "Stop"` at file scope.
Under that setting, a failing native command's STDERR becomes a **terminating error even when redirected with `2>$null`** — confirmed directly and reproducibly on this machine: a plain, entirely expected "fatal: no tag exactly matches ..." from `git describe --tags --exact-match` (on a clean checkout that is simply not on a tag) threw instead of just setting a nonzero `$LASTEXITCODE`.
Because the function only calls that exact-match check inside `if ($clean) { ... }`, the exception silently skipped the `--abbrev=0` fallback entirely — collapsing straight to `$null`/`"local"` for exactly the case this whole feature exists for: a clean checkout ahead of the last tag.
The ORIGINAL exact-match-only `Get-LocalCheckoutTag` never hit this, because a dirty tree short-circuited before ever reaching the exact-match call — reorganizing it to run conditionally on `$clean` is what newly exposed the gotcha.

Caught by testing against a throwaway repo with "clean tree, one commit past a tag" (the real repo's own dirty tree happened to mask the bug by skipping the exact-match branch entirely, which is why the very first live check against this actual repo looked correct by accident).
**Fix:** shadow `$ErrorActionPreference = "SilentlyContinue"` as a local variable at the top of `Get-LocalCheckoutVersionTag` — PowerShell scoping means this only affects calls made from within this one function; the caller's `"Stop"` is restored the instant it returns.
No other line needed to change once `$LASTEXITCODE` checks could actually see a nonzero exit code instead of an exception eating it.

Verified live, all scenarios, after the fix: clean+exact-tag → tag verbatim; clean+one-commit-past-tag → tag+`l` (previously the broken case); dirty+same-tagged-commit → tag+`l`; no tags anywhere → nothing (→ `"local"`).
New automated coverage added to `tests/test_get_bootstrap.py`: `test_get_local_checkout_version_tag_scheme` (4 checks, real throwaway git repos, dot-sources the real `install.ps1` and calls the real function — this exact regression is now locked in) and `test_get_ps1_errors_when_no_release_found` (3 checks: nonzero exit, no download attempted, error message names the manual-clone fallback).
Full suite: **22/22** (`test_get_bootstrap.py`), **29/29** (`test_install_windows.py`) unaffected.

Redeployed live to this machine's real `~/.mnemo`: `current -> v3.0.0l` (this repo's own dirty `feat/v3` checkout, correctly based on the nearest reachable tag `v3.0.0` — `v3.0.1` exists on `master` but isn't yet merged into this branch, so it is NOT reachable here, which is exactly correct git-history-accurate behavior, not a bug), `doctor` reporting `self-update current v3.0.0l, v3.0.1 available`, `/health`'s `version` reading `"v3.0.0l"`.
This is the first real, live self-update comparison this session that both sides of the code now agree on and is factually accurate end to end.

## Incident, same day: redeploying the local fixed build twice self-updated BACK to the old, still-buggy real v3.0.1

Nothing from this whole day's work is committed, tagged, or released yet — it only exists in this local uncommitted working tree.
`auto_update` is `true` by default.
The moment the local fixed build (`v3.0.0l`) was redeployed to this machine, the background checker correctly saw `v3.0.1 available` (true — v3.0.1 really is newer than v3.0.0l) and armed an unattended auto-apply countdown, which fired and switched `current` to the REAL, already-published GitHub `v3.0.1` — which predates this entire session and still has every bug fixed today: the `Start-Job` console-window regression, the hardcoded `SERVICE_VERSION` literal, the pre-fix `effective_current_tag` priority.
**This is not a new bug** — it is today's OWN fixes not being live anywhere except this one uncommitted checkout, so any real self-update necessarily reintroduces the unfixed code.
Confirmed live, twice in a row: redeploying `v3.0.0l` → auto-update fires within roughly a minute → `current` becomes `v3.0.1` (old code) → console shown, footer shows stale "3.0.0" (exactly what the user saw and reported, screenshots, real machine).

**Fix applied:** `PUT /api/settings {"auto_update": false}` via the local `/api` (token from `$env:MNEMO_API_TOKEN`) — stops the unattended countdown from re-arming.
One redeploy had ALREADY re-armed and fired before the setting took effect (the disable does not retroactively cancel an apply already past its settle point) — waited for it to reach a terminal state (never interrupted mid-build, per this session's own established discipline), then redeployed the local fixed build a third time.
Final state confirmed stable: `current: v3.0.0l`, `auto_update: false` (persisted to `state/settings.json`, source `"file"` not `"default"`), passive "Доступна нова версія v3.0.1" banner shown (correct — it genuinely is newer by tag comparison) but nothing fires unattended.

**Standing consequence, not yet resolved:** as long as `auto_update` stays off and nothing is released, this machine will keep passively showing "v3.0.1 available" forever (correct, not a bug) without ever actually applying it (also correct, given the real v3.0.1 is a downgrade in terms of fixes right now).
The real fix is committing and cutting an actual new release (e.g. v3.0.2) containing today's work — until then, local `install.ps1` re-runs are this machine's only way to carry today's fixes, and each one requires re-checking whether auto_update needs disabling again first.

## Cosmetic regression, same incident: "v v3.0.0l" (doubled prefix)

`effective_current_tag()` can now return a tag that already carries its own `"v"` prefix ("v3.0.1", "v3.0.0l"), where the OLD `SERVICE_VERSION` literal never did (always a bare "3.0.0"-style string).
`shell.js`'s `renderService()` unconditionally prepended `"v "`, producing `"v v3.0.0l"` in the sidebar footer — caught by `evaluate_script`-ing the live DOM against the real running console (`className`/`color` inspection to verify the amber-marking feature; text field happened to reveal this alongside it).
Fixed: only prepend `"v "` when the value does not already start with `v<digit>`.
Verified live afterward: `{"text":"v3.0.0l","className":"ver is-local-build"}`, `color: rgb(240, 195, 122)` matching `--warn-fg` (`#f0c37a`) exactly — the local-build amber marking itself confirmed working correctly via computed style, not just visual inspection of a screenshot.

## User feedback and a branch switch outside this session's own actions

User's own correction, fair: testing self-update repeatedly should have started with `auto_update` disabled proactively, not reactively after two accidental round-trips back to the old real `v3.0.1`.
Noted for next time this feature needs live poking.

Also mid-incident: the user manually confirmed a real update to `v3.0.1` themselves via the console UI (`auto.enabled: false` on that apply record proves it was a manual click, not the countdown re-arming), and separately switched this checkout's branch outside anything this session did — `git reflog` showed `master -> feat/installer-release-and-progress`, HEAD landing on `f591d9c` (PR #3's merge commit, tagged `v3.0.1`).
Verified **before** trusting anything further: `git status --porcelain` still showed every file this session touched as modified, plus the new day-log as untracked — nothing lost across the branch switch (uncommitted changes survive a `checkout` when there's no conflict with the target branch, which held here).
Redeployed once more from this new HEAD: correctly produced `versions/v3.0.1l` this time (not `v3.0.0l`) — genuinely accurate, since HEAD is now the real `v3.0.1` commit with only today's uncommitted diffs on top, exactly the scenario the trailing-`l` scheme exists for.
Final live state: `current: v3.0.1l`, `auto_update: false`, footer reads `v3.0.1l` in the amber local-build color.

## Root cause 6, found live right after the branch-switch redeploy: `v3.0.1l` nagged "update available" against its own base tag forever

User caught it immediately from the footer/banner: `current` was `v3.0.1l`, yet the sidebar still said "Доступна нова версія v3.0.1" — a local build sitting ON TOP of the latest release can never string-match that release's tag, so `record_check()`'s `update_available` formula (and the frontend's compensating `shouldShowUpdateBanner()` guard, same mismatch) nagged forever — offering to "update" to the exact release the local build already contains fixes on top of.

**Fix:** `engine_update.base_version_tag(tag)` — strips a trailing lowercase `l` right after a digit (`re.compile(r"(\d)l$")`), giving `"v3.0.1l"` -> `"v3.0.1"`.
Used in `record_check()`'s comparison (`latest_tag != base_version_tag(effective_current_tag(state))`) — the one call site that matters most, since it also feeds `auto_eligible_tag()` and therefore auto-update's own decision, not just display.
Mirrored in `update.js`'s `shouldShowUpdateBanner()` via a matching `baseVersionTag()` helper, for the same "not recomputed after switch" staleness reason the existing frontend guard was already there for.
`record_installed()`'s own recompute needed no change — it always receives a real release tag as its own parameter, never a local-build one.

Verified: `tests/test_engine_update.py` — new `test_base_version_tag_strips_local_build_marker` (6 checks: plain tag identity, marker stripped, `None` passthrough, bare `"local"` untouched, `record_check` against the exact base tag reads `update_available: False`, against a genuinely newer tag still reads `True`) — **92/92** total.
Redeployed live: `doctor` now reports plain `self-update current v3.0.1l` (no more `, v3.0.1 available`), footer banner gone in the browser, confirmed via screenshot after a full reload.

**Unrelated flake hit during this same redeploy, not a new bug:** the already-documented transient service-start timing flake (`topics/cabinet-text-polish.md`'s prior note) recurred — `install.ps1` reported the backend up, but a moment later it was refusing connections while its process was still alive.
Same fix as before: `mnemo service stop` then `mnemo service start` cleanly, which came up healthy immediately.
Not touched code-wise; just re-confirming the known flake and its known fix still apply.

## Not fixed / not confirmed

- "Green dot doesn't light up" on one machine (WS never visibly connects) — no confirmed root cause; needs that machine's actual browser console/Network tab.
  Flagged to revisit if it recurs.
- `get.ps1`/`get.sh` needed NO changes for root cause 1 — the self-detection approach made a more invasive design (threading an explicit tag through an env var from `get.ps1` into `install.ps1`) unnecessary.
  Considered and dropped.

## Verified live

- `python -m py_compile` clean on `engine_update.py`/`api.py`/`diagnostics.py`.
- `tests/test_engine_update.py`: **85/85** (80 pre-existing + 5 new checks in `test_effective_current_tag_self_detects_fresh_install`), including the real `stage_release()` pipeline running pip install through the rewritten heartbeat function for real.
- `tests/test_install_windows.py`: **29/29** unchanged, including a real isolated install's real `pip install`/venv build through the same rewritten function.
- PowerShell AST parser (`[System.Management.Automation.Language.Parser]:: ParseFile`) clean on `install.ps1` both before and after the `ArgumentList`/`Synchronized` fix-of-the-fix.
- Not done: an actual live redeploy of the real running `~/.mnemo` service on this machine to see the fix end-to-end (that service is still reporting `"version":"3.0.0"`, PID unchanged since before this session's edits — editing this repo's `src/` never touches the installed mirror, per `CLAUDE.md`'s own "Updating the engine" section) — deliberately left for the user to trigger (a normal `install.ps1` re-mirror, or a real self-update once a new tag exists), not run unprompted given it stops and restarts their real production service.

## Root cause 6 (found after PR #4 merged, live) — the STAGING call itself still popped a console

Root cause 3's fix only covered the console spawned *inside* `install.ps1` (`Invoke-CheckedWithHeartbeat`'s own `pip install`/warmup step).
It missed the OUTER spawn: `engine_update._build_engine_version()` (`engine_update.py:828`) runs `subprocess.run(["powershell", ...])` to dot-source the release's `install.ps1` and call `Build-EngineVersion` — this call happens from inside the windowless backend's apply thread, and it had no `creationflags=CREATE_NO_WINDOW` at all.
Confirmed by `grep` across `src/`: it was the only `subprocess.run(["powershell", ...])` in the whole codebase missing the flag (`service_ctl.py`, `scaffold.py`, `autostart.py` all already carry it, with matching comments explaining why).
This is exactly the blank blue window the user saw during a real v3.0.1l -> v3.0.2 self-update apply.

**Fix:** added the same `creationflags=0x08000000` (CREATE_NO_WINDOW) guard, same pattern as everywhere else.

**Verification, and why the quick isolated repro was inconclusive:** a direct `pythonw.exe`-spawned `subprocess.run(["powershell", "Start-Sleep 5"])`, with and without the flag, run via Claude Code's own Bash tool, showed no window either way on this machine — Git Bash's terminal layer apparently doesn't reproduce real desktop console-allocation behaviour for a child's child.
The only test that meant anything was reproducing the REAL spawn chain: a throwaway local-tarball HTTP server (same technique as `tests/test_engine_update.py::_make_local_release_tarball`) plus `service_ctl.spawn_detached()` (the exact function and flags that spawn the real backend) launching a worker that calls `engine_update.stage_release()` directly — a genuine `pip install`-driven venv build running inside a truly console-less, CREATE_NO_WINDOW-spawned process, same as production.
First attempt used the system `pythonw.exe` (no `httpx` there) and failed before reaching the build step; retried with the venv interpreter that has `httpx` and it staged successfully end to end (`STAGE_OK ...\fake-home\versions\test-console-fix`).
The user stepped away during the run and could not confirm visually either way — the fix is verified by code inspection (matches the established, already-proven pattern everywhere else) and by the successful real staging run, not by an eyewitness "no window" observation this time.

**Confirmed live afterwards, eyewitness, real self-update apply:** committed straight to `master` (`ca508e3`) per explicit user request, then published as a real GitHub release.
First attempt retargeted the existing `v3.0.2` draft/release via `PATCH .../releases/{id}` — this silently failed to move the actual git tag: GitHub only honours `target_commitish` when a tag is first CREATED, and the user had already published that draft (creating the `v3.0.2` tag at the OLD pre-fix commit) minutes earlier.
`git ls-remote --tags` confirmed `v3.0.2 -> 1819cfe6` (no fix) after the "successful" PATCH response claimed otherwise — the JSON echoes back whatever `target_commitish` you send even when the underlying ref never moved, so the API response alone is not proof of anything for an already-existing tag.
**Lesson: never trust a release PATCH's `target_commitish` field once the tag might already exist — verify the actual ref with `git ls-remote --tags` before treating a retarget as done.** Fixed by cutting a new `v3.0.3` release instead (immutable tags are never fought once real, same spirit as not force-pushing over a public branch) — verified `v3.0.3 -> ca508e3` for real via `git ls-remote` before telling the user it was safe to test.

User rebuilt locally first (`install.ps1` re-mirror -> `v3.0.2l`, already carrying this fix since it's a from-source local build), then self-updated for real from `v3.0.2l` to the real `v3.0.3` release through the console UI -- eyewitness-confirmed: **no console window** during the `pip install` staging step.
Closes root cause 6 for real, not just by code inspection.

Noted for next time this needs explaining: the staging subprocess call that needs `CREATE_NO_WINDOW` is always the CURRENTLY RUNNING engine's own copy of `_build_engine_version` (it stages the new version, then something else switches to it afterwards) -- so the fix protects an update only once it is present in whatever version is running *before* that update, not merely in the target release.
A machine still on the real, unfixed `v3.0.1` will see the console once more on its first hop to a fixed release; every update after that is clean.

## Two new decisions, same day, on branch `fix/self-update-followups`

Not bugs — new policy/UX the user asked for after the console-window fix shipped and was verified live.

**1. MAJOR-version bumps are manual-only.** `engine_update.auto_eligible_tag()` now compares the parsed `MAJOR` of `base_version_tag(effective_current_tag())` against the candidate's — if the candidate's major is strictly greater, the tag is never offered to the unattended checker (`None`), regardless of blacklist/retry state.
Manual apply via the console button is completely unaffected; only the *unattended* countdown path is gated.
Parsing is `_major_version()` against a strict `^v(\d+)\.(\d+)\.(\d+)$` — deliberately fails **open** (falls back to the old "allow" behaviour) when either side doesn't match that shape, e.g. a bare `"local"` dev tag — an unrecognised format was judged not worth blocking on, since every tag this project mints today matches.
Verified against a local-build tag specifically (`"v3.0.1l"` vs a `v4.0.0` candidate) to make sure the trailing `l` marker doesn't accidentally defeat the gate via a parse failure — `base_version_tag()` strips it before parsing, so it compares correctly.
`tests/test_engine_update.py:: test_auto_eligible_tag_never_offers_a_major_bump` — 100/100 total.

**2. The terminal result modal self-closes 10s after a successful AUTO-triggered apply.** A manual trigger is unchanged (waits for a click on "Закрити"), and a failed/rolled-back outcome is never auto-closed regardless of trigger — it needs a human's attention.
Needed threading `trigger` ("auto"/"manual") all the way through, since it did not exist on `/api/update/status` before this: `engine_update.start_apply()`/`finish_apply()` now take and persist a `trigger` kwarg (default `"manual"`) onto `last_apply`, `cli.py`'s `update-apply` (which already computed `trigger` via `read_pending_trigger()` for blacklist bookkeeping) now passes it to all 5 call sites, and `api.py`'s `_apply_view()`/`_apply_progress` surface it in both the disk-derived and in-memory branches.
Frontend: `update.js`'s `renderUpdateTerminal()` starts a plain client-side `setInterval` (no server-side "seconds_left" needed — this modal is never resumed across a reload, `refreshUpdateStatus()` doesn't reopen it for an already-`done` apply) showing a live countdown (`.upd-countdown`, same class the auto-pending countdown already uses) and calls `closeUpdateModal()` at zero; `closeUpdateModal()`/a re-render both clear any existing timer first to avoid stacking.
New tests: `test_record_installed_and_apply_helpers` extended with explicit-`trigger` cases for both functions.

## Third round, same day: force-reload after apply + stall-based pip timeout

Two more user-driven asks, on top of everything above (not yet on a branch at the time of writing — plan file `majestic-stirring-pnueli.md`, reused after the earlier staleness plan it originally held was long since done).

**1. Force-reload after a successful apply.** `renderUpdateTerminal()`'s dismiss paths (✕, backdrop, Escape, the "Закрити" button, the 10s auto-close countdown) all funnelled into `closeUpdateModal()`, which only re-syncs *data* over WS (`resyncAll()`) — never the page's own JS/CSS, which the just- applied version may have changed.
New `dismissUpdateModal()` is now the one place every dismiss path calls: `window.location.reload()` when `phase === 'terminal' && apply.state === 'done'`, plain `closeUpdateModal()` otherwise (nothing changed on a failed/rolled-back outcome, no reason to reload).

**2. Stall-based pip-install timeout, scoped down deliberately.** Real report: a worse WiFi connection died on the flat 30-minute `_build_engine_version(timeout=1800.0)` ceiling even while `pip install` was still making genuine progress — one number punishes slow-but-alive exactly like dead.
Asked via `AskUserQuestion` whether to also rebuild `_build_engine_version` into a streaming `Popen` with live progress threaded through `api.py`/`update.js` (bigger, riskier — concurrent stdout reads across a process boundary, the same deadlock class already fought once in this file's own `Invoke-CheckedWithHeartbeat`) — user chose internal-only, but also asked for an approximate visible status if it could be had cheaply without that rewrite.

**Design landed:** `--quiet` dropped from both pip-install call sites (`install.ps1`'s `Build-EngineVersion` and the `-DepsOnly` refresh path) so pip prints its normal `Collecting X` / `Installing collected packages` / `Successfully installed` lines, which `Invoke-CheckedWithHeartbeat` already captures into `$captured` for the failure dump.
New `-StallTimeoutSec` param (only the two pip call sites opt in, 120s; the `warmup` call site is untouched — a single silent big-file download is a different failure shape): each heartbeat tick, growth in `$captured` resets an activity clock; real silence past the threshold kills the child and throws `"... (no output for Ns -- looks stalled, not just slow)"` instead of waiting for any outer ceiling.
`_build_engine_version`'s own `timeout` (still a plain blocking `subprocess.run`, never rewritten to streaming) bumped 1800s -> 3600s, now purely a last-resort backstop for a wedged PowerShell process itself.

**The approximate live status, without a streaming rewrite:** a side-channel file.
New `-ProgressFile` param on `Invoke-CheckedWithHeartbeat`/ `Build-EngineVersion`: written (best-effort, `{"phase","count","at"}`) every time `$captured` grows, by regexing the newest lines for `^Collecting ` (count++, phase=collecting) / `^Installing collected packages` (phase=installing) / `^Successfully installed` (phase=done).
`engine_update.stage_release()` computes `progress_file = staging_root / "pip-progress.json"` and starts a small daemon `threading.Thread` (`_watch_pip_progress`) that polls it once a second *while the still- blocking* `_build_engine_version()` call runs, calling `_emit_progress(tag, "venv", detail=...)` on each change — genuinely live-ish progress into the browser without touching `_build_engine_version`'s core blocking-call structure at all.
Worded as approximate on purpose (`"встановлення пакетів… (побачено ~{count})"` — no "N of M": there is no reliable denominator, and pip's text output is not a stable contract across versions/resolver backtracking).

`detail` threaded the rest of the way: `_apply_progress` dict gained a `"detail"` key, `_run_staged_apply`'s `_on_progress` callback now copies it, `_apply_view()`'s two disk-derived branches carry `"detail": None` for shape consistency.
`update.js`: `onUpdateProgress()`/`pollUpdateStatusOnce()` both copy it into `state.update.apply.detail`; `renderUpdateProgress()` shows it (reusing the already-styled `.upd-note` class, no new CSS) under the steps box while `apply.step === 'venv'`.

**Frontend give-up clock made stall-based too**, same principle as the backend: `checkUpdatePollTimeout()` used to measure from `pollStartedAt` (fixed at modal-open) — now from a new `lastProgressAt`, bumped by a shared `noteUpdateProgress(step, detail)` helper called from both the REST poll and the WS preview whenever either actually changes.
Same `UPDATE_POLL_TIMEOUT_MS` (5 min), now meaning "5 minutes of the backend saying nothing new" instead of "5 minutes total no matter what".

**Verified live:** `tests/test_engine_update.py`'s real end-to-end `test_stage_release_real_pipeline` (genuinely builds a venv via a real pip install, no mocking below `stage_release`) needed its `steps == ["download", "venv", "done"]` assertion loosened to `steps[0] == "download" and steps[-1] == "done" and all(s == "venv" for s in steps[1:-1])` — dropping `--quiet` means this real install now legitimately emits extra `"venv"` progress events.
**100/100** after the change.
New `tests/test_install_windows.py::test_heartbeat_stall_detection` — isolated `.ps1` harness (dot-source + direct call, same pattern `check_script_encoding` already uses), two real subprocess cases: a script printing every 0.5s with `-StallTimeoutSec 2` runs to completion; a script that prints once then sleeps 30s with the same threshold throws the "looks stalled" message in well under 15s, proving the child was actually killed rather than waited out.
**32/32** total.
`test_mcp.py` independently confirmed broken on a clean `master` too (`git stash` + rerun) — a local `mcp` client package version mismatch in this machine's venv, unrelated to any of this session's edits.
