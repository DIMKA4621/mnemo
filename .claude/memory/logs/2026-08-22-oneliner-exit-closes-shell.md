# 2026-08-22 — `exit` in a piped one-liner closes the user's whole terminal

## Symptom, caught live by the user, not by any test

Testing the just-shipped one-liners for real (a genuine open PowerShell
console, not the test suite): `get.ps1` fetched over the network failed
(unrelated cause — the `master` ref doesn't have `install.ps1` yet, since
all of this work lives on `feat/v3`) and Windows Terminal showed "You can
now close this terminal... or press Enter to restart" — the whole shell
process had exited, not just the piped command. Then `iex "& { $(irm
.../uninstall.ps1) } -Yes -KeepModel -KeepState"` **succeeded** and *still*
closed that same terminal window outright.

## Root cause, confirmed by direct experiment, not by reasoning about it

`exit` in PowerShell always terminates the **current runspace/process**,
full stop — regardless of success or failure, regardless of how deeply
nested in a function or script block. Nothing here is mnemo-specific; this
is well-known, often-complained-about core PowerShell behavior. The only
thing that ever "contains" `exit` to a sub-invocation is when that
sub-invocation is a genuinely separate process (`powershell.exe -File
foo.ps1` spawned by another process) **or** a real script *file* invoked
via the call operator from within an existing session. Tested all four
shapes directly, live, no guessing:

| Invocation | `$PSCommandPath` | Does `exit 0` inside kill the caller? |
|---|---|---|
| `& 'realfile.ps1'` (real file on disk, called from an open session) | non-empty | **No** — caller survives |
| `powershell -File realfile.ps1` (genuine child process) | non-empty | Only that child process ends (expected, desired) |
| `Invoke-Expression "raw text"` (`irm URL \| iex`) | **empty** | **Yes** — kills the whole session |
| `& { scriptblock literal }` (no real file, e.g. `iex "& { ... } args"`) | **empty** | **Yes** — kills the whole session |

`$PSCommandPath` is exactly the right, reliable signal — non-empty whenever
a real `.ps1` file backs the execution (whether that's a genuine child
process via `-File`, or a real file called with `&`/typed as `.\script.ps1`
from an already-open session), empty whenever the code is anonymous text
evaluated in place (`iex` on a string, or `& { ... }` on a literal
scriptblock with no file behind it) — which is exactly what both README
one-liner forms are: `irm URL | iex` and `iex "& { $(irm URL) } args"`.

This matters doubly for **`uninstall.ps1`**, whose driver used to be
`exit (Invoke-Uninstall)` unconditionally — meaning even a fully
**successful** uninstall would slam the user's terminal shut when run via
the one-liner, which is far more surprising/bad UX than a failure doing it.
`install.ps1`'s driver only ever called `exit` in its `catch` (the success
path already just fell off the end), so it only had the milder version of
the bug — but is never actually exposed to the "no real file" case in any
*documented* flow (a clone's `.\install.ps1`, or `get.ps1`'s own extracted
copy invoked via `& $installScript`, are both real-file invocations) — the
guard was still added there for defense-in-depth against someone doing an
undocumented `iex (irm .../install.ps1)` directly.

## Fix — same pattern in all three: `get.ps1`, `install.ps1`, `uninstall.ps1`

Guard every top-level `exit N` with `if ($PSCommandPath) { exit N }`. When
`$PSCommandPath` is empty (iex'd/scriptblock-literal), print the result and
let the script fall off the end normally instead — control returns to the
user's shell, which stays alive, exactly like a normal command finishing.

`uninstall.ps1`'s single-line `exit (Invoke-Uninstall)` had to split into
`$result = Invoke-Uninstall` then the guarded `exit $result`, since the old
form computed and exited in one expression with no room to branch.

**Not a bash/POSIX problem.** `install.sh`/`uninstall.sh`/`get.sh` run as a
genuine **child process** even when piped (`curl | bash` spawns a real,
separate `bash` process reading the script from its stdin) — `exit` inside
never touches the caller's own interactive shell. This asymmetry between
the two platforms is inherent to how each `|`/pipe actually works
(PowerShell's is an in-process object pipeline between cmdlets in the SAME
runspace when `iex` is involved; POSIX's is a real OS-level pipe between
two separate processes) and is not something to "fix" on the bash side —
there is nothing there to fix.

## Verified

Re-ran the existing suites after the fix — both still fully green, proving
zero regression to the real-file path (`-File`, which is 100% of how the
test harness invokes every script): `tests/test_install_windows.py`
**29/29**, `tests/test_get_bootstrap.py` **13/13**. Also re-confirmed live,
directly, with a minimal throwaway driver script mimicking the exact
try/catch shape used in all three real scripts: `& 'file.ps1'` (real file)
still both prints its output AND causes the *caller* to observe `exit`
having fired only for that nested call (parent PID survives, per the table
above); the identical driver logic invoked via `Invoke-Expression ("&amp; {
$content }")` (simulating `iex "& { $(irm ...) } args"` exactly) now prints
"done (no real file, not exiting)" and the parent process survives, where
before the fix it would have printed nothing further and killed the shell.

## Also confirmed separately, while diagnosing this session's `get.ps1`
failure report

`origin/master` does not have `install.ps1` (or `get.ps1`, `get.sh`) at
all — this branch's rewrite of install/uninstall/bootstrap lives entirely
on `feat/v3`, not yet merged. `origin/feat/v3` was confirmed (via `git
fetch`) to already be up to date with every commit from today's session,
including `74fdd53` — so testing the *actual* documented `master`-URL
one-liners from README will not work until this branch merges; testing
before that requires substituting `feat/v3` for `master` in the raw URL by
hand. No code follow-up needed here — this is expected pre-merge state, not
a bug — but worth remembering next time a fetched-from-network one-liner
403/404s during this branch's lifetime.
