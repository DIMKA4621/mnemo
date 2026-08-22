# 2026-08-21 — get.ps1/get.sh: one-liner install/uninstall without a clone

## What shipped

Two new files at the repo root, `get.ps1` and `get.sh`, mirroring how
Docker/Homebrew/rustup let people install with one piped command:

```powershell
irm https://raw.githubusercontent.com/DIMKA4621/mnemo/master/get.ps1 | iex
```
```bash
curl -fsSL https://raw.githubusercontent.com/DIMKA4621/mnemo/master/get.sh | bash
```

They download a source snapshot from GitHub (`codeload.github.com/<repo>/zip
or tar.gz/refs/heads/<ref>`, default ref `master` — no GitHub release exists
yet, same situation `engine_update.py` documents for self-update), extract to
a fresh temp dir, and invoke the extracted copy's **unmodified**
`install.ps1`/`install.sh` — which now sits on real disk, so
`$PSScriptRoot`/`$0` resolve exactly as a manual `git clone` would give them.
Temp copy removed in a `finally`/`trap EXIT`, success or failure.

`uninstall.ps1`/`uninstall.sh` needed **zero code changes** for the same
one-liner treatment — confirmed by reading both: they only ever touch
`~/.mnemo` and machine state (registry, the `mnemo service` scheduled
task/systemd unit, the PowerShell profile block), never a local checkout.
The only `$MyInvocation` use in either is a dot-source-detection guard for
tests. README just gained the equivalent `curl .../uninstall.sh | bash` /
`irm .../uninstall.ps1 | iex` lines directly, no wrapper script needed.

## Design decision: separate bootstrap script, not self-relaunch

Two shapes were considered: teach `install.ps1`/`install.sh` to self-detect
"no local checkout" (empty `$PSScriptRoot`) and re-launch themselves from a
downloaded copy — mirroring what `engine_update.py::stage_release` already
does for self-update — vs. a small, separate fetcher that calls the
existing, completely unmodified `install.ps1`/`install.sh`. **User chose the
separate script** (`get.ps1`/`get.sh`): lower risk to the already-well-tested
local-repo path (`test_install_windows.py`/`test_install_posix.py`, ~500
tests touch these two files), single responsibility, no new edge cases
inside the already-large `Invoke-Install`.

## Gotcha: PowerShell cannot forward arbitrary named flags via
`ValueFromRemainingArguments`

The first design draft gave `get.ps1` its own `param()` block with
`-Ref`/`-ArchiveUrl` plus
`[Parameter(ValueFromRemainingArguments=$true)][string[]]$InstallArgs`
intended to catch and forward anything else (`-Check`, `-InstallHome`, ...)
straight to `install.ps1`. **This does not work.** PowerShell's parameter
binder tries to match every `-Name`-shaped token against the script's own
declared parameters *before* anything reaches
`ValueFromRemainingArguments` — an unrecognized `-Check` on a script that
has no `-Check` parameter of its own raises "A parameter cannot be found
that matches parameter name 'Check'" immediately, it does not fall through
to the remaining-arguments bucket. `ValueFromRemainingArguments` only
catches *positional* leftovers, not unmatched named flags.

**Fix:** `get.ps1` declares **no `param()` block at all**. A script with no
declared parameters gets every argument — named-looking or not — verbatim
in the automatic `$args` array, with no binder validation against them at
all. `get.ps1`'s own two knobs (which branch to fetch, a test-only archive
URL override) became **environment variables**
(`$env:MNEMO_GET_REF`/`$env:MNEMO_GET_ARCHIVE_URL`,
`MNEMO_GET_REF`/`MNEMO_GET_ARCHIVE_URL` on the bash side for symmetry)
instead of CLI flags, specifically so they can never collide with
`install.ps1`'s flag names and so `get.ps1` never needs updating when
`install.ps1`'s own flag list changes. `& $installScript @args` then splats
the raw array positionally, and *that* hop binds cleanly because
`install.ps1` genuinely does declare `-Check`/`-InstallHome`/etc. by name.
`get.sh` never had this problem — POSIX `"$@"` forwarding is unconditional
either way — but got the same env-var treatment for consistency.

Also worth remembering for anyone building a wrapper script that must pass
`-Flag`-style args through `iex`: **`irm URL | iex` alone never passes
arguments at all** — the piped-script-block form needs the
`iex "& { $(irm URL) } -Check -InstallHome D:\x"` wrapper idiom to bind
params. Documented next to both one-liners in the README (install and
uninstall alike, since `-DryRun`/`-KeepModel` need the same wrapper for the
uninstall one-liner).

## Also found and fixed: non-ASCII `§` broke `install.ps1` parsing

While running `test_install_windows.py` to confirm the new files hadn't
regressed anything, found it already failing — unrelated to this session's
work here. `install.ps1:647`, a comment from commit `f9fcb68` ("/api open by
default", earlier the same day) had a literal `§` character
(`Memory-design-v3.md §13`). Windows PowerShell 5.1 decodes a BOM-less
`.ps1` as the system ANSI codepage, so any non-ASCII character breaks
parsing on a non-UTF8 console — the same class of bug `test_install_windows
.py`'s `check_script_encoding()` exists to catch, and did catch, it just
hadn't been re-run since that commit landed. Fixed by replacing `§13` with
`sec. 13` in a one-line commit (`fix(install): replace non-ASCII
section-sign in a comment`), separate from the feature commit — confirmed
all 29 checks in `test_install_windows.py` pass afterward.

## `get.ps1`/`get.sh` default to downloading the model, install.ps1/install.sh don't

User request (2026-08-22): the one-liner should assume "yes, get the model"
by default, since the whole point of a one-liner is one command that
finishes the job — unlike `install.ps1`/`install.sh` run directly, which
still ask (or skip quietly when there's no one to ask, per the existing
`Confirm-ModelDownload`/POSIX equivalent). This is a real, scoped exception
to the CLAUDE.md "Locked decisions" line "the model is never downloaded
implicitly" — updated that line in place to name the carve-out explicitly
rather than leave the doc quietly wrong.

Mechanism: unless `-Model`/`-NoModel` (`--model`/`--no-model`) is already
among the forwarded args, `get.ps1`/`get.sh` add the download flag
themselves before calling `install.ps1`/`install.sh`.

## Gotcha (bigger than the `ValueFromRemainingArguments` one above): splatting
a *rebuilt* array loses PowerShell's named-argument binding entirely

Implementing the default-`-Model` logic above required modifying the
forwarded argument list (adding `"-Model"` when absent), which meant no
longer splatting the pristine automatic `$args` as-is. Every rebuilt-array
approach tried **broke real installs silently** or loudly, in this order:

1. `$forwardArgs = $args + "-Model"` — when `$args` is empty, PowerShell's
   `+` between an *empty array* and a string collapses the result to a
   **plain string** `"-Model"`, not a 1-element array. Splatting a string
   with `@` then explodes it **character by character** as separate
   positional arguments (confirmed live: a stub `install.ps1` received
   `-`, `M`, `o`, `d`, `e`, `l` as six separate args instead of one).
2. Fixed the type with `[System.Collections.Generic.List[string]]` +
   `.AddRange()`/`.Add()`, confirmed `.GetType()` really was
   `System.String[]`/a real list — **still broken**, differently: a real
   `install.ps1` invocation with `-InstallHome`/`-Python` values then failed
   with `"A positional parameter cannot be found that accepts argument
   '-Python'"`. Root cause, confirmed by bisecting element-by-element: **any
   array splatted with `@` binds every element *positionally*, regardless of
   whether an element's string value looks like `"-Name"`.** Only PowerShell's
   own automatic `$args` variable — populated by the engine itself because
   the calling script declared no `param()` block — carries hidden metadata
   marking which elements were originally typed as `-Name` tokens, and *that*
   is the only thing that lets `@args` bind named parameters downstream. Copy
   the same string values into a fresh array or List and that metadata is
   gone; splatting the copy is indistinguishable from typing everything
   positionally, so `"-InstallHome"` the STRING got bound as `$InstallHome`'s
   *value*, `"E:\..."` got bound as `$Python`'s value, and `"-Python"` (the
   literal string) had no positional slot left to land in.
3. **Actual fix:** never rebuild `$args`. Keep splatting the untouched
   automatic `$args`, and add `-Model` only as a **separate literal token in
   the invocation itself** via a static branch:
   ```powershell
   if ($args -match '^-(No)?Model$') { & $installScript @args }
   else                              { & $installScript @args -Model }
   ```
   Confirmed live with a real `[CmdletBinding()]` stub script: `@args -Model`
   in the same call correctly binds both the original named args from `$args`
   *and* the literal `-Model` switch — because the literal token is typed in
   the actual call site, not smuggled through a rebuilt collection.

   `get.sh` never had any version of this problem — bash's `"$@"` forwarding
   and `set -- "$@" --model` are plain positional-token lists with no
   PowerShell-style advanced parameter binder involved, so appending a
   literal string is always safe there.

Two smaller bugs surfaced by writing a real live test for this (a stub
`install.ps1` that just records `$args` to a file, driven through a local
HTTP server exactly like the rest of this feature's tests):

- **`Set-StrictMode -Version Latest` + `$LASTEXITCODE`**: reading
  `$LASTEXITCODE` after invoking a plain PowerShell script (no native `exe`,
  no `exit` call inside it) throws *"cannot be retrieved because it has not
  been set"* under strict mode, because the variable is genuinely never
  initialized this session. Fixed by setting `$global:LASTEXITCODE = 0`
  before the `&` call.
- **`Out-File -Encoding utf8` writes a BOM.** The Python test side read the
  marker file with `encoding="utf-8"`, which keeps a literal `\ufeff` glued
  to the first token (`"\ufeff-Model" != "-Model"`) — switched to
  `"utf-8-sig"`, which strips it.

`tests/test_get_bootstrap.py` gained a new fast, network-free
`test_get_ps1_default_model_flag` covering exactly this: default-adds
`-Model`, respects an already-present `-NoModel`, and never double-adds an
already-present `-Model` — verified against a stub `install.ps1`, not the
real (slow) pipeline, using an `$env:MNEMO_TEST_MARKER` file path rather
than anything under `$PSScriptRoot` (get.ps1 deletes its own temp copy in
`finally` *before* the test process gets a chance to read a marker left
inside it — first version of this test failed for exactly that reason).

## README: both Installing and Uninstall restructured into the same 4-part sequence

User request (2026-08-22): make both sections read in a fixed order —
(1) heading, (2) a one-line warning to read the whole section before running
anything, (3) the commands (one-liner first, git-clone/local-file second,
with real code blocks for both shells), (4) a flat bullet list of the
flags that differ from default behavior, each stating what the **default**
already is, framed as "leave these alone unless you know you want
something else." Installing's flags: `--no-model`/`-NoModel` (default:
downloads; skipped it? `mnemo warmup` later, or the cabinet's Settings
page) and `--home`/`-InstallHome` (default: `~/.mnemo`). Uninstall's:
`--dry-run`/`-DryRun`, `--keep-model`/`-KeepModel`, `--keep-state`/
`-KeepState`. The old scattered mid-paragraph flag mentions and the
separate "### Just reinstalling?" subheading were folded into this one
list per section.

## Testing

`tests/test_get_bootstrap.py` — new, mirrors the manual `main()`/`ok()`
smoke-test convention of `test_install_windows.py`/`test_install_posix.py`
(neither is pytest-based) rather than introducing a different style for one
file. Spins up a local `http.server.ThreadingHTTPServer` serving a `.zip`
built from this repo's own current working tree (same technique
`test_engine_update.py::_make_local_release_tarball` already uses for
`stage_release`, `.zip` instead of `.tar.gz` because that's what `get.ps1`
actually downloads on Windows), points `get.ps1` at it via
`MNEMO_GET_ARCHIVE_URL` instead of the real GitHub URL, and proves: it
downloads/extracts/installs to the same end state a direct `install.ps1
-InstallHome <x>` run reaches (launcher + venv present), and that the temp
source copy is removed after **both** a successful run and a deliberately
failed one (bad archive URL, nonzero exit). All 8 checks passed live on
this machine. `get.sh` only gets a `bash -n` syntax gate here — its real
pipeline is POSIX-only, matching `test_install_posix.py`'s existing
`os.name == "nt"` skip; this Windows dev machine's Git Bash is treated as a
convenience, not the POSIX test target, consistent with the rest of the
project.

## Commits

`fix(install): replace non-ASCII section-sign in a comment`, then
`feat: add get.ps1/get.sh — one-liner install with no git clone required`
(README restructured in the same commit so the one-liner is the primary
documented path in both Installing and Uninstall, git-clone kept as the
documented alternative for anyone developing mnemo itself; later same-day
commit(s) add the default-`-Model` behavior, the splat-binding fix above,
and the 4-part README restructure).
