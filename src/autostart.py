"""Start the service at login, per OS — block L, NFR-6.

One behaviour, three registrations, and the differences are exactly the ones
NFR-11 allows (installer + autostart mechanism only):

* **Windows** — a hidden Task Scheduler task at logon, running the
  GUI-subsystem ``mnemow.exe`` so nothing can flash (NFR-1). The task is
  registered from XML: the GUI checkbox for "hidden" is not enough on its own
  for a console binary, and XML is the only form that pins every field we
  care about (``<Hidden>``, ``LogonType``, no idle/battery stop).
* **Linux** — a ``systemd --user`` unit plus ``loginctl enable-linger`` so the
  service is genuinely always-on rather than dying with the session.
* **macOS** — a launchd LaunchAgent, the local equivalent of the above.

Every operation is idempotent and reversible: enabling twice changes nothing,
and ``disable`` leaves the machine as it was found. Nothing here touches the
system ``PATH``, and nothing runs elevated.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from .config import USER_HOME

# One name everywhere: the scheduled task, the systemd unit and the launchd
# label all use it, so "is autostart on?" has a single answer per OS.
SERVICE_NAME = "mnemo"
TASK_NAME = "mnemo service"
SYSTEMD_UNIT = "mnemo.service"
LAUNCHD_LABEL = "dev.mnemo.service"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ABSENT = 3


# ------------------------------------------------------------- launcher


def launcher_path(*, windowless: bool = True) -> Path:
    """The canonical launcher to register.

    Autostart always points at the installed engine's ``bin/`` — never at a
    repository checkout and never at a bare interpreter, so the registration
    keeps working after the engine is refreshed in place.
    """
    binary = USER_HOME / "bin"
    if os.name == "nt":
        return binary / ("mnemow.exe" if windowless else "mnemo.exe")
    return binary / "mnemo"


def _run(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    """Run a helper command without ever letting a console appear."""
    creation = {}
    if os.name == "nt":
        from .service_ctl import _CREATE_NO_WINDOW

        creation["creationflags"] = _CREATE_NO_WINDOW
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        **creation,
        **kwargs,
    )


# --------------------------------------------------------------- Windows


def task_xml(
    launcher: Path | None = None,
    *,
    arguments: str = "service start",
    task_name: str = TASK_NAME,
) -> str:
    """The Task Scheduler definition, as XML.

    ``<Hidden>true</Hidden>`` plus a GUI-subsystem target is what makes the
    logon start invisible. ``StopIfGoingOnBatteries``/``DisallowStart…`` are
    switched off deliberately: an always-on memory service that quietly stops
    on an unplugged laptop is the kind of thing nobody notices until a search
    returns nothing.

    ``arguments``/``task_name`` are overridable so the registration can be
    exercised end-to-end under a throwaway name instead of the real one.
    """
    target = launcher or launcher_path()
    user = os.environ.get("USERNAME", "")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>mnemo project-memory service (loopback, no window).</Description>
    <URI>\\{task_name}</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <Hidden>true</Hidden>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{target}</Command>
      <Arguments>{arguments}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def _windows_enable() -> int:
    launcher = launcher_path()
    if not launcher.is_file():
        print(f"mnemo autostart: launcher not found: {launcher}")
        print("mnemo autostart: run install.ps1 first")
        return EXIT_FAILED

    # schtasks reads the XML as UTF-16, as declared in the prolog. It is a
    # hand-off file, not state: state/ holds the index, the bank registry and
    # the journal, and nothing else may accumulate there. Written to a temp
    # directory and removed once the scheduler has taken a copy.
    import tempfile

    handle, raw = tempfile.mkstemp(prefix="mnemo-task-", suffix=".xml")
    os.close(handle)
    xml_path = Path(raw)
    try:
        xml_path.write_text(task_xml(launcher), encoding="utf-16")
        result = _run(
            ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"]
        )
    finally:
        try:
            xml_path.unlink()
        except OSError:
            pass

    if result.returncode != 0:
        print(f"mnemo autostart: schtasks failed: {result.stdout.strip()} {result.stderr.strip()}")
        return EXIT_FAILED
    print(f"mnemo autostart: registered hidden logon task '{TASK_NAME}'")
    return EXIT_OK


def _windows_disable() -> int:
    result = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    if result.returncode != 0:
        print(f"mnemo autostart: no task '{TASK_NAME}' to remove")
        return EXIT_ABSENT
    print(f"mnemo autostart: removed task '{TASK_NAME}'")
    return EXIT_OK


def _windows_status() -> int:
    result = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"])
    if result.returncode != 0:
        print("mnemo autostart: disabled (no logon task)")
        return EXIT_ABSENT
    print(f"mnemo autostart: enabled — hidden logon task '{TASK_NAME}'")
    for line in result.stdout.splitlines():
        if line.split(":")[0].strip() in ("Task To Run", "Status", "Scheduled Task State"):
            print(f"  {line.strip()}")
    return EXIT_OK


# ----------------------------------------------------------------- Linux


def systemd_unit(launcher: Path | None = None) -> str:
    """A ``systemd --user`` unit for the service.

    ``Type=simple`` with the launcher in the foreground: systemd owns the
    process, so the windowless spawn machinery is not involved (and must not
    be — a double fork would leave systemd supervising nothing).
    """
    target = launcher or launcher_path()
    return f"""[Unit]
Description=mnemo project-memory service
Documentation=https://github.com/mnemo
After=default.target

[Service]
Type=simple
ExecStart={target} serve
Restart=on-failure
RestartSec=5
# The index rebuild is CPU-hungry; never let it fight the desktop for CPU.
Nice=5

[Install]
WantedBy=default.target
"""


def _systemd_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "systemd" / "user"


def _linux_enable() -> int:
    launcher = launcher_path()
    if not launcher.is_file():
        print(f"mnemo autostart: launcher not found: {launcher}")
        print("mnemo autostart: run install.sh first")
        return EXIT_FAILED

    unit_dir = _systemd_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / SYSTEMD_UNIT).write_text(systemd_unit(launcher), encoding="utf-8")

    _run(["systemctl", "--user", "daemon-reload"])
    # `enable` only, never `enable --now`: `--now` also starts/stops the unit
    # immediately, and this unit is the same one a live backend already runs
    # under. Registering autostart must not touch a process that is already
    # running — `mnemo service start/stop` (service_ctl.py) owns that.
    enabled = _run(["systemctl", "--user", "enable", SYSTEMD_UNIT])
    if enabled.returncode != 0:
        print(f"mnemo autostart: systemctl failed: {enabled.stderr.strip()}")
        return EXIT_FAILED

    # Without lingering the unit dies with the last session — "always-on"
    # would then quietly mean "on while you are logged in" (NFR-3).
    linger = _run(["loginctl", "enable-linger", os.environ.get("USER", "")])
    if linger.returncode != 0:
        print("mnemo autostart: unit enabled, but enable-linger failed —")
        print("mnemo autostart: the service will stop when you log out")
        print(f"mnemo autostart: fix with: loginctl enable-linger $USER  ({linger.stderr.strip()})")
        return EXIT_OK

    print(f"mnemo autostart: enabled {SYSTEMD_UNIT} (systemd --user, lingering)")
    return EXIT_OK


def _linux_disable() -> int:
    unit = _systemd_dir() / SYSTEMD_UNIT
    if not unit.is_file():
        print("mnemo autostart: disabled (no unit)")
        return EXIT_ABSENT
    # Same reasoning as enable(): `disable` only, never `disable --now` — the
    # unit is the same one a live backend may be running under, and removing
    # the logon registration must not kill it.
    _run(["systemctl", "--user", "disable", SYSTEMD_UNIT])
    unit.unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"])
    # Lingering is deliberately left alone: the user may well have enabled it
    # for their own reasons, and turning it off would reach past mnemo.
    print(f"mnemo autostart: removed {SYSTEMD_UNIT} (lingering left as it was)")
    return EXIT_OK


def _linux_status() -> int:
    unit = _systemd_dir() / SYSTEMD_UNIT
    if not unit.is_file():
        print("mnemo autostart: disabled (no unit)")
        return EXIT_ABSENT
    active = _run(["systemctl", "--user", "is-active", SYSTEMD_UNIT]).stdout.strip()
    enabled = _run(["systemctl", "--user", "is-enabled", SYSTEMD_UNIT]).stdout.strip()
    print(f"mnemo autostart: enabled — {SYSTEMD_UNIT} ({enabled}, {active})")
    return EXIT_OK


# ----------------------------------------------------------------- macOS


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def launchd_plist(launcher: Path | None = None) -> bytes:
    target = launcher or launcher_path()
    return plistlib.dumps(
        {
            "Label": LAUNCHD_LABEL,
            "ProgramArguments": [str(target), "serve"],
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ProcessType": "Background",
        }
    )


def _macos_enable() -> int:
    launcher = launcher_path()
    if not launcher.is_file():
        print(f"mnemo autostart: launcher not found: {launcher}")
        return EXIT_FAILED
    path = _plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(launchd_plist(launcher))
    _run(["launchctl", "unload", str(path)])  # idempotent re-load
    loaded = _run(["launchctl", "load", str(path)])
    if loaded.returncode != 0:
        print(f"mnemo autostart: launchctl failed: {loaded.stderr.strip()}")
        return EXIT_FAILED
    print(f"mnemo autostart: enabled LaunchAgent {LAUNCHD_LABEL}")
    return EXIT_OK


def _macos_disable() -> int:
    path = _plist_path()
    if not path.is_file():
        print("mnemo autostart: disabled (no LaunchAgent)")
        return EXIT_ABSENT
    _run(["launchctl", "unload", str(path)])
    path.unlink(missing_ok=True)
    print(f"mnemo autostart: removed LaunchAgent {LAUNCHD_LABEL}")
    return EXIT_OK


def _macos_status() -> int:
    if not _plist_path().is_file():
        print("mnemo autostart: disabled (no LaunchAgent)")
        return EXIT_ABSENT
    print(f"mnemo autostart: enabled — LaunchAgent {LAUNCHD_LABEL}")
    return EXIT_OK


# ------------------------------------------------------------ dispatch


def state() -> dict:
    """Is autostart registered, as data rather than as printed lines.

    The ``*_status`` functions above print and return an exit code, which is
    right for a terminal and useless to the API — a caller that needed the
    answer would have to parse the very text we are free to reword. This is
    the same question asked once, in the form both faces can use.

    Read-only and side-effect free by contract: it is answered inside
    ``/api/status``, which the console polls, so registering or repairing
    anything from here would turn opening a page into changing the machine.

    ``supported`` is what keeps the console honest about an OS we do not
    register on: absent is not the same fact as "not applicable here", and a
    checkbox cannot show the difference on its own.
    """
    mechanism = {
        "nt": "Task Scheduler",
        "darwin": "launchd",
    }.get("nt" if os.name == "nt" else sys.platform, "systemd --user")

    if os.name == "nt":
        result = _run(["schtasks", "/Query", "/TN", TASK_NAME])
        return {
            "supported": True,
            "enabled": result.returncode == 0,
            "mechanism": mechanism,
            "name": TASK_NAME,
        }
    if sys.platform == "darwin":
        return {
            "supported": True,
            "enabled": _plist_path().is_file(),
            "mechanism": mechanism,
            "name": LAUNCHD_LABEL,
        }
    return {
        "supported": True,
        "enabled": (_systemd_dir() / SYSTEMD_UNIT).is_file(),
        "mechanism": mechanism,
        "name": SYSTEMD_UNIT,
    }


def enable() -> int:
    if os.name == "nt":
        return _windows_enable()
    if sys.platform == "darwin":
        return _macos_enable()
    return _linux_enable()


def disable() -> int:
    if os.name == "nt":
        return _windows_disable()
    if sys.platform == "darwin":
        return _macos_disable()
    return _linux_disable()


def status() -> int:
    if os.name == "nt":
        return _windows_status()
    if sys.platform == "darwin":
        return _macos_status()
    return _linux_status()
