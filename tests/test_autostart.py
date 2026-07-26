"""Autostart registration checks (block L, NFR-6).

The generated artefacts are asserted on all platforms; the Windows path is
additionally registered with Task Scheduler for real — under a throwaway task
name, never the one the installer uses — run, and observed to produce no
console window. The task is always removed again.
"""
from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

_STATE = Path(tempfile.mkdtemp(prefix="mnemo autostart "))
os.environ["MNEMO_STATE_DIR"] = str(_STATE)

# The console-less test runner may hand us a cp1252 stdout; a mangled
# glyph in a failure detail must never mask the failure itself.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from src import autostart, service_ctl  # noqa: E402

_passed = _failed = 0

TEST_TASK = "mnemo test task (delete me)"

PROBE = '''\
import ctypes, json, os, sys

console = 0
if os.name == "nt":
    console = int(ctypes.WinDLL("kernel32").GetConsoleWindow() or 0)
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump({"pid": os.getpid(), "console": console}, fh)
'''


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _failed += 1
        print(f"FAIL  {name}  {detail}")


def test_artifacts() -> None:
    """What gets written must say what we think it says, on every OS."""
    launcher = Path("/opt/engine/bin/mnemo")

    xml = autostart.task_xml(launcher)
    check("task XML hides the window", "<Hidden>true</Hidden>" in xml)
    check("task XML triggers at logon", "<LogonTrigger>" in xml)
    check("task XML runs the launcher", f"<Command>{launcher}</Command>" in xml)
    check("task XML calls `service start`", "<Arguments>service start</Arguments>" in xml)
    check(
        "task XML does not stop on battery",
        "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>" in xml,
    )
    check("task XML sets no execution time limit", "<ExecutionTimeLimit>PT0S" in xml)

    unit = autostart.systemd_unit(launcher)
    check("systemd unit serves in the foreground", f"ExecStart={launcher} serve" in unit)
    check("systemd unit installs into default.target", "WantedBy=default.target" in unit)
    check("systemd unit restarts on failure", "Restart=on-failure" in unit)

    plist = plistlib.loads(autostart.launchd_plist(launcher))
    check("LaunchAgent runs at load", plist.get("RunAtLoad") is True)
    check(
        "LaunchAgent runs the launcher",
        plist.get("ProgramArguments") == [str(launcher), "serve"],
        detail=str(plist.get("ProgramArguments")),
    )
    check("LaunchAgent is a background job", plist.get("ProcessType") == "Background")

    # The registration must point at the installed engine, never at a repo
    # checkout — otherwise autostart breaks the moment the repo moves.
    target = autostart.launcher_path()
    check(
        "autostart targets the installed engine's bin/",
        target.parent.name == "bin" and "mnemo" in str(target.parent.parent),
        detail=str(target),
    )
    if os.name == "nt":
        check(
            "Windows autostart targets the windowless launcher",
            target.name == "mnemow.exe",
            detail=str(target),
        )


def schtasks(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["schtasks", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def test_windows_registration(work: Path) -> None:
    """Register the real XML with the real Task Scheduler, then run it."""
    if os.name != "nt":
        print("SKIP  Task Scheduler registration (not Windows)")
        return

    probe = work / "probe.py"
    probe.write_text(PROBE, encoding="utf-8")
    report = work / "task-report.json"

    xml = autostart.task_xml(
        Path(service_ctl.windowless_python()),
        arguments=f'"{probe}" "{report}"',
        task_name=TEST_TASK,
    )
    xml_path = work / "task.xml"
    xml_path.write_text(xml, encoding="utf-16")

    created = schtasks("/Create", "/TN", TEST_TASK, "/XML", str(xml_path), "/F")
    check(
        "schtasks accepts the generated XML",
        created.returncode == 0,
        detail=f"{created.stdout.strip()} {created.stderr.strip()}",
    )
    if created.returncode != 0:
        return

    try:
        # Read the definition back from the scheduler, not from our own file:
        # this is what Windows actually stored.
        stored = schtasks("/Query", "/TN", TEST_TASK, "/XML")
        check(
            "the registered task is marked hidden",
            "<Hidden>true</Hidden>" in stored.stdout,
            detail=stored.stdout[:200],
        )
        check(
            "the registered task triggers at logon",
            "<LogonTrigger>" in stored.stdout,
        )

        run = schtasks("/Run", "/TN", TEST_TASK)
        check("scheduler runs the task on demand", run.returncode == 0,
              detail=f"{run.stdout.strip()} {run.stderr.strip()}")

        data = None
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if report.is_file() and report.stat().st_size:
                try:
                    data = json.loads(report.read_text(encoding="utf-8"))
                    break
                except ValueError:
                    pass
            time.sleep(0.2)

        check("the scheduled task actually ran", data is not None)
        check(
            "the scheduled task had NO console window",
            data is not None and data["console"] == 0,
            detail=str(data),
        )
    finally:
        schtasks("/Delete", "/TN", TEST_TASK, "/F")

    gone = schtasks("/Query", "/TN", TEST_TASK)
    check("the test task was removed", gone.returncode != 0)


def test_powershell_profile(work: Path) -> None:
    """The installer's profile block: fenced, idempotent, non-destructive."""
    if os.name != "nt":
        print("SKIP  PowerShell profile registration (not Windows)")
        return

    profile = work / "Profile.ps1"
    profile.write_text("# the user's own profile\nSet-Alias ll Get-ChildItem\n", encoding="utf-8")
    installer = REPO / "install.ps1"
    launcher = "C:\\engine\\bin\\mnemo.exe"

    script = f"""
$ErrorActionPreference = 'Stop'
. '{installer}'
$PROFILE = [pscustomobject]@{{ CurrentUserAllHosts = '{profile}' }}
Register-PowerShellProfile '{launcher}'
Register-PowerShellProfile '{launcher}'
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    check(
        "install.ps1 can be dot-sourced without installing",
        result.returncode == 0,
        detail=f"{result.stdout.strip()} {result.stderr.strip()}",
    )

    content = profile.read_text(encoding="utf-8")
    check("the user's own profile content is preserved",
          "Set-Alias ll Get-ChildItem" in content, detail=content)
    check("the profile registers mnemo as a function",
          f"function mnemo {{ & '{launcher}' @args }}" in content, detail=content)
    check("the block is fenced", content.count("# >>> mnemo >>>") == 1
          and content.count("# <<< mnemo <<<") == 1, detail=content)
    check("running twice does not duplicate the block",
          content.count("function mnemo") == 1, detail=content)
    check("no PATH mutation", "$env:PATH" not in content and "setx" not in content.lower())


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mnemo autostart ") as raw:
        work = Path(raw)
        test_artifacts()
        test_windows_registration(work)
        test_powershell_profile(work)

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
