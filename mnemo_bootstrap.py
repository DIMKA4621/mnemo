"""Self-locating console entry point for the installed mnemo engine."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _configure_utf8() -> None:
    """Use UTF-8 for hook and MCP pipes when the stream supports it."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


# Subcommands that may run under the GUI-subsystem launcher. Everything else
# needs stdio: `hook-inject` writes the injected context to stdout, the MCP
# faces speak over pipes. Under pythonw ``sys.stdout`` is None and ``print``
# returns silently instead of raising, so a stdio face launched that way
# would do all its work, "print" nothing, and exit 0 — a silent no-op with no
# error anywhere. Hence a real gate rather than a comment.
_BACKGROUND_ONLY = frozenset({"serve", "service", "autostart", "embed-server"})


def main() -> int:
    """Locate the engine beside its venv, then delegate to the real CLI."""
    engine_home = Path(sys.prefix).resolve().parent
    os.environ["MNEMO_HOME"] = str(engine_home)
    sys.path.insert(0, str(engine_home))
    _configure_utf8()

    from src.cli import main as cli_main

    return cli_main()


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
    return main()
