"""Self-locating console entry point for the installed mnemo engine.

This is a thin dispatcher, NOT a CLI implementation: it never imports
``src.cli`` in-process. Instead it resolves ``ENGINE_HOME/current`` -- a
stable alias (a junction on Windows, a plain directory reference on POSIX)
repointed atomically by a self-update -- and spawns a subprocess:
``current/.venv/Scripts/(python|pythonw).exe -m src.cli <argv>``, inheriting
stdio and returning that subprocess's own exit code.

Why not import ``src.cli`` here, as before the engine grew versioned
installs (``versions/<tag>/{src,.venv}`` + ``current``): on Windows,
``bin\\mnemo.exe`` is a launcher stub pip/setuptools generates *inside one
particular venv* -- the interpreter path is embedded as data at build time,
not resolved at run time. Confirmed empirically (spike, 2026-08-20): moving
or copying that exe elsewhere does NOT change which python.exe it launches
(``sys.prefix`` / ``sys.executable`` / ``__file__`` all still point at the
venv that built it), so an update that repoints ``current`` to a new
versioned venv would silently keep running the OLD one forever unless the
exe is rebuilt on every update -- which defeats the "stop -> repoint
junction -> start" atomicity the update mechanic depends on. The one thing
that DOES reflect where the exe actually lives, regardless of which venv
built it, is ``sys.argv[0]`` -- also confirmed empirically. So this module
locates itself from ``sys.argv[0]`` (the stable ``bin/`` the exe is copied
into at install time), not from ``sys.prefix``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _engine_home() -> Path:
    """ENGINE_HOME, derived from where THIS launcher was invoked from.

    ``bin/`` is the one stable, unversioned location (aliases and any future
    PATH entry point here and nowhere else), so ``argv[0]``'s grandparent is
    ENGINE_HOME on every platform: ``<ENGINE_HOME>/bin/mnemo[.exe]``.

    Deliberately NOT ``sys.prefix`` / ``sys.executable`` / ``__file__`` --
    those name the venv that built/is running THIS interpreter, which is
    frozen at pip-install time on Windows and has nothing to do with which
    version is ``current`` right now. See the module docstring.
    """
    return Path(sys.argv[0]).resolve().parent.parent


def _versioned_python(engine_home: Path, *, gui: bool) -> Path:
    """The ``current`` version's interpreter -- resolved fresh every call.

    ``current`` is repointed atomically by an update; nothing here caches
    the resolved path across invocations, so a switch takes effect on the
    very next command with no launcher changes of its own.
    """
    current = engine_home / "current"
    if os.name == "nt":
        name = "pythonw.exe" if gui else "python.exe"
        return current / ".venv" / "Scripts" / name
    return current / ".venv" / "bin" / "python"


def _configure_utf8() -> None:
    """Best-effort UTF-8 on the dispatcher's OWN stderr.

    The dispatched CLI reconfigures its own stdio (``src.cli.main``) -- this
    is only for the "no engine found" message this module can print before
    ever reaching that code.
    """
    reconfigure = getattr(sys.stderr, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass


# Subcommands that may run under the GUI-subsystem launcher. Everything else
# needs stdio: `hook-inject` writes the injected context to stdout, the MCP
# faces speak over pipes. Under pythonw ``sys.stdout`` is None and ``print``
# returns silently instead of raising, so a stdio face launched that way
# would do all its work, "print" nothing, and exit 0 — a silent no-op with no
# error anywhere. Hence a real gate rather than a comment. `update-apply`
# joins this set because it is spawned detached by the future
# `/api/update/apply` handler (step 9) the same way `service start` is —
# no console to write to, no human waiting on stdout.
_BACKGROUND_ONLY = frozenset(
    {"serve", "service", "autostart", "embed-server", "update-apply"}
)


def _dispatch(*, gui: bool) -> int:
    _configure_utf8()
    engine_home = _engine_home()
    python = _versioned_python(engine_home, gui=gui)
    if not python.is_file():
        message = (
            f"mnemo: no engine found at {python}\n"
            f"mnemo: expected {engine_home / 'current'} to be a valid link "
            f"to a versioned install (versions/<tag>/.venv)\n"
        )
        if sys.stderr is not None:
            sys.stderr.write(message)
        return 3

    # version_root/.venv/Scripts/python.exe -> version_root (3 parents up on
    # Windows: Scripts, .venv, version_root; 2 on POSIX: bin, .venv... no --
    # POSIX is .venv/bin/python, so 2 parents lands on .venv, needs a 3rd).
    # Compute it the same way on both: strip ".venv/<bindir>/<exe>".
    version_root = python.parent.parent.parent

    env = dict(os.environ)
    env["MNEMO_HOME"] = str(engine_home)
    # This is how `-m src.cli` finds the `src` package: NOT the child's cwd
    # (see below), just this. A child that changes directory, or an odd
    # invocation of this dispatcher from a different cwd, must not lose
    # sight of `src` either way.
    env["PYTHONPATH"] = str(version_root) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )

    argv = [str(python), "-m", "src.cli", *sys.argv[1:]]
    kwargs: dict = {}
    if gui:
        # The GUI (mnemow) dispatcher has no real stdio of its own to hand
        # down -- it was launched under pythonw.exe, which has no console
        # and no valid stdin/stdout/stderr handles. Leaving subprocess.run's
        # stdio unset here means "inherit whatever the parent has", and
        # inheriting invalid/console-less handles through a THIRD process
        # hop (mnemow.exe -> this dispatcher -> `-m src.cli <cmd>`) is not
        # the same as having none at all: confirmed by a real run (self-
        # update step 12) -- `update-apply` invoked via mnemow.exe silently
        # rolled back a switch that succeeded reliably when invoked via the
        # console `mnemo.exe`, while the exact same logic driven directly
        # under pythonw.exe with EXPLICIT stdio (a real file, or DEVNULL)
        # worked every time. Explicit beats inherited for a background
        # dispatch the same way ``service_ctl._windowless_kwargs()`` never
        # leaves the spawned backend's stdio to inheritance either.
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
    try:
        # No `cwd=` override here: the dispatched `src.cli` process must see
        # the REAL caller's working directory. `config.resolve()` (`init`)
        # and `cli._bank_ref()` (`search`/`tree`/`reindex`) both default to
        # `Path.cwd()` when no explicit `--root`/`--bank` is given — that is
        # the entire documented contract ("Project root (default: cwd)").
        # An earlier version of this dispatcher pinned `cwd=str(version_root)`
        # (belt-and-braces for `-m src.cli`'s own import, redundant with the
        # `PYTHONPATH` set above) and it silently broke that contract for
        # every plain invocation of the installed launcher: `Path.cwd()`
        # inside the child was always the engine's own version directory,
        # never the terminal's actual directory — `mnemo init`/`search`/
        # `tree`/`reindex` run without an explicit root/bank silently
        # resolved against the engine's install tree instead of the caller's
        # project (found live 2026-09-04, `.claude/memory/topics/
        # config-root-resolution.md`). Nothing else in the codebase reads
        # `cwd` (everything else is absolute paths off the registry/
        # `MNEMO_HOME`), so inheriting the real cwd here is safe.
        completed = subprocess.run(argv, env=env, **kwargs)
    except KeyboardInterrupt:
        # Ctrl-C on a foreground command (e.g. `mnemo search` waiting on the
        # service) reaches both us and the child directly: no
        # CREATE_NEW_PROCESS_GROUP / start_new_session is used for this
        # spawn, so the child shares our console / process group and gets
        # the signal itself. subprocess.run() already waits for it to exit
        # before propagating the interrupt here, so there is no orphan to
        # clean up -- but report a conventional 128+SIGINT code rather than
        # let a raw KeyboardInterrupt traceback out of a console entry point.
        return 130
    return completed.returncode


def main() -> int:
    """Locate the engine beside its venv, then delegate to the real CLI."""
    return _dispatch(gui=False)


def main_gui() -> int:
    """Entry point for ``mnemow`` — background subcommands only.

    Refuses anything that would need stdout, with a non-zero exit so the
    caller sees a failure instead of a silent success.
    """
    command = next((arg for arg in sys.argv[1:] if not arg.startswith("-")), None)
    if command not in _BACKGROUND_ONLY:
        message = (
            f"mnemow: '{command or ''}' needs stdio and cannot run under the "
            f"windowless launcher; use mnemo instead "
            f"(allowed here: {', '.join(sorted(_BACKGROUND_ONLY))})\n"
        )
        # stderr is None under pythonw unless the caller redirected it.
        if sys.stderr is not None:
            sys.stderr.write(message)
        return 2
    return _dispatch(gui=True)
