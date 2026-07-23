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


def main() -> int:
    """Locate the engine beside its venv, then delegate to the real CLI."""
    engine_home = Path(sys.prefix).resolve().parent
    os.environ["MNEMO_HOME"] = str(engine_home)
    sys.path.insert(0, str(engine_home))
    _configure_utf8()

    from src.cli import main as cli_main

    return cli_main()
