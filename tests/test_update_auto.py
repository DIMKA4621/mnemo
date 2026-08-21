"""Unattended auto-apply — live round trip against a REAL, throwaway backend.

Extends the self-update feature (engine-self-update-design topic, design
decision #33) with unattended auto-apply + a per-tag blacklist. The pure
state-machine logic (engine_update.auto_eligible_tag / record_auto_outcome /
pending-trigger handoff) has its own unit coverage in test_engine_update.py;
this file proves the HTTP surface end to end against a live service process
-- never the real installed engine, always a throwaway ``MNEMO_STATE_DIR`` +
``MNEMO_EMBED_PORT`` + a free port, same isolation ``test_service_recovery.py``
already uses.

**Why ``cli/cli`` as the GitHub repo for this run.** This repo
(``DIMKA4621/mnemo``) has no tagged releases yet (confirmed repeatedly
elsewhere in this suite), so a real check against it can never produce an
eligible tag to arm a countdown against. ``cli/cli`` is a real, actively
released public repo already used the same way in
``test_engine_update.py::test_check_latest_release_real_success`` -- pointing
``MNEMO_GITHUB_REPO`` at it for the DURATION OF THIS THROWAWAY PROCESS ONLY
lets the checker discover a genuine "newer tag" without needing a real mnemo
release to exist. Confirming the countdown (step below) then genuinely tries
to stage that tag -- download a real tarball, extract it, and attempt to
build an engine version from it. It is expected to FAIL at the build step
(a ``cli/cli`` checkout has no ``install.ps1``), and that failure is exactly
what proves ``POST /api/update/auto/confirm`` really invokes the same
``_begin_apply`` -> ``_run_staged_apply`` -> ``engine_update.stage_release``
pipeline the manual path uses -- not a stub.

    .venv/bin/python tests/test_update_auto.py

Skips (does not fail) if GitHub is unreachable, since large parts of this
file are a real network round trip.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

_STATE = Path(tempfile.mkdtemp(prefix="mnemo update-auto "))
os.environ["MNEMO_STATE_DIR"] = str(_STATE)

from _hygiene import ResidentGuard, claim_embed_port  # noqa: E402

_EMBED_PORT = claim_embed_port()

from src import service_ctl  # noqa: E402

_passed = _failed = 0
_RESIDENTS = ResidentGuard(_EMBED_PORT)


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _failed += 1
        print(f"FAIL  {name}  {detail}")


def _network_up() -> bool:
    import socket

    try:
        socket.create_connection(("api.github.com", 443), timeout=5).close()
        return True
    except OSError:
        return False


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def token() -> str:
    """The token minted by ``lifespan()``'s ``api_token()`` call.

    That call is the very first line of startup, before ``/health`` can
    answer at all (ASGI lifespan blocks connection-accepting until startup
    completes) -- but poll with a short deadline anyway rather than a bare
    read, the same "never trust immediate readiness" discipline every other
    wait helper in this file already follows.
    """
    path = _STATE / "api.token"
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            time.sleep(0.25)
    return path.read_text(encoding="utf-8").strip()


def wait_healthy(port: int, timeout: float = 60.0) -> bool:
    import httpx

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    return False


def _get(port: int, path: str) -> "httpx.Response":  # noqa: F821
    import httpx

    return httpx.get(
        f"http://127.0.0.1:{port}{path}",
        headers={"Authorization": f"Bearer {token()}"}, timeout=30.0,
    )


def _post(port: int, path: str, json_body: dict | None = None) -> "httpx.Response":  # noqa: F821
    import httpx

    return httpx.post(
        f"http://127.0.0.1:{port}{path}", json=json_body,
        headers={"Authorization": f"Bearer {token()}"}, timeout=30.0,
    )


def _put(port: int, path: str, json_body: dict) -> "httpx.Response":  # noqa: F821
    import httpx

    return httpx.put(
        f"http://127.0.0.1:{port}{path}", json=json_body,
        headers={"Authorization": f"Bearer {token()}"}, timeout=30.0,
    )


def wait_for(port: int, predicate, timeout: float, label: str) -> dict | None:
    """Poll GET /api/update/status until ``predicate(status)`` is true."""
    deadline = time.monotonic() + timeout
    last: dict | None = None
    while time.monotonic() < deadline:
        resp = _get(port, "/api/update/status")
        if resp.status_code == 200:
            last = resp.json()
            if predicate(last):
                return last
        time.sleep(0.5)
    print(f"      (timed out waiting for: {label}; last status={json.dumps(last)})")
    return None


def start_service(port: int) -> int:
    target = [service_ctl.windowless_python(), "-m", "src.cli", "serve", "--port", str(port)]
    return service_ctl.start(target=target)


def main() -> int:
    if not _network_up():
        print("SKIP  test_update_auto.py entirely (no network)")
        return 0

    # This machine may have a REAL mnemo install whose MNEMO_API_TOKEN is
    # exported machine-wide (the installer sets it once and keeps no
    # record). Left alone, a throwaway backend spawned from this shell would
    # inherit that env var and use the real production token instead of
    # minting its own file-based one under our throwaway state dir --
    # `token()` below would then never find `api.token` there (it was never
    # written), and worse, this test would be handling a real secret it has
    # no business touching. Pop it so the throwaway service is genuinely
    # isolated, exactly like a machine with no real install at all.
    os.environ.pop("MNEMO_API_TOKEN", None)

    port = free_port()
    os.environ["MNEMO_API_PORT"] = str(port)
    # Fast tick + fast countdown so this test finishes in well under a
    # minute, and a repo with REAL releases so the checker finds a genuine
    # eligible tag without needing a mnemo release to exist -- see module
    # docstring.
    os.environ["MNEMO_GITHUB_REPO"] = "cli/cli"
    os.environ["MNEMO_UPDATE_CHECK_INTERVAL_S"] = "3"
    os.environ["MNEMO_UPDATE_AUTO_COUNTDOWN_S"] = "15"

    # Seed auto_update=false into the throwaway settings FILE, not an env
    # var: `_resolve()`'s env>file precedence (settings.py, pre-existing,
    # not something this feature changed) means an env var set before
    # spawn would win over every later PUT for the rest of this process's
    # life -- an earlier version of this test set MNEMO_AUTO_UPDATE_ENABLED
    # here and it silently made the PUT-round-trip check at the bottom
    # unobservable (status.auto.enabled stayed the env value forever). The
    # file has no such problem: `settings.load()` re-reads it live, so a
    # later PUT genuinely changes what GET/status report. Seeding it False
    # also removes the race the unseeded default (True) would otherwise
    # create against the checker's first tick -- see the "starts null"
    # checks right below, which need auto_update to be deterministically
    # off until this test turns it on itself, further down.
    _STATE.mkdir(parents=True, exist_ok=True)
    (_STATE / "settings.json").write_text(
        json.dumps({"auto_update": False, "version": 1}), encoding="utf-8"
    )

    check("service starts", start_service(port) == service_ctl.EXIT_OK)
    check("backend is healthy", wait_healthy(port))

    try:
        # --- extended GET /api/update/status shape, before anything fires ---
        status = _get(port, "/api/update/status").json()
        check("status has a top-level 'auto' key", "auto" in status, detail=str(status))
        check("auto.enabled mirrors the seeded-false setting",
              status["auto"]["enabled"] is False)
        check("auto.pending starts null", status["auto"]["pending"] is None)
        check("auto.blacklist starts empty", status["auto"]["blacklist"] == [])

        # --- confirm/cancel with nothing pending: 404 auto_not_pending ---
        r = _post(port, "/api/update/auto/confirm")
        check("confirm with nothing pending -> 404", r.status_code == 404, detail=r.text)
        check("confirm with nothing pending -> auto_not_pending",
              r.json().get("error", {}).get("code") == "auto_not_pending", detail=r.text)

        r = _post(port, "/api/update/auto/cancel")
        check("cancel with nothing pending -> 404", r.status_code == 404, detail=r.text)
        check("cancel with nothing pending -> auto_not_pending",
              r.json().get("error", {}).get("code") == "auto_not_pending", detail=r.text)

        # --- turn it on, the same way a real user would (PUT, not env) ---
        r = _put(port, "/api/settings", {"auto_update": True})
        check("PUT /api/settings enables auto_update", r.status_code == 200, detail=r.text)

        # --- the checker's own tick discovers cli/cli's real latest tag and
        # arms a countdown on its own, with nobody calling apply directly ---
        status = wait_for(
            port, lambda s: bool(s["latest_known"].get("update_available")),
            timeout=20.0, label="a real update_available from cli/cli",
        )
        check("the background checker found a real newer tag on cli/cli",
              status is not None, detail=str(status))

        status = wait_for(
            port, lambda s: s["auto"]["pending"] is not None,
            timeout=20.0, label="auto.pending armed by the checker",
        )
        check("auto-apply armed a countdown on its own (no client call)",
              status is not None, detail=str(status))
        if status is None:
            return 1
        armed_tag = status["auto"]["pending"]["tag"]
        check("armed pending carries seconds_left, computed server-side",
              isinstance(status["auto"]["pending"].get("seconds_left"), int),
              detail=str(status["auto"]["pending"]))

        # --- cancel it: must clear cleanly, touch no blacklist ---
        r = _post(port, "/api/update/auto/cancel")
        check("cancel while pending -> 200", r.status_code == 200, detail=r.text)
        check("cancel echoes the tag", r.json().get("tag") == armed_tag, detail=r.text)

        status = _get(port, "/api/update/status").json()
        check("auto.pending is null again right after cancel",
              status["auto"]["pending"] is None, detail=str(status))
        check("cancel wrote no blacklist entry",
              status["auto"]["blacklist"] == [], detail=str(status))

        r = _post(port, "/api/update/auto/cancel")
        check("cancelling again with nothing pending -> 404",
              r.status_code == 404, detail=r.text)

        # --- the checker re-arms the SAME tag on its next tick (no cooldown,
        # accepted-as-is per the resolved flag) ---
        status = wait_for(
            port, lambda s: s["auto"]["pending"] is not None,
            timeout=20.0, label="auto.pending re-armed after cancel",
        )
        check("the checker re-arms the same tag on its next tick",
              status is not None and status["auto"]["pending"]["tag"] == armed_tag,
              detail=str(status))

        # --- confirm it: must invoke the REAL apply pipeline (_begin_apply),
        # not a stub -- observed via apply.state actually moving off "idle" ---
        r = _post(port, "/api/update/auto/confirm")
        check("confirm while pending -> 202", r.status_code == 202, detail=r.text)
        check("confirm echoes the tag", r.json().get("tag") == armed_tag, detail=r.text)

        status = _get(port, "/api/update/status").json()
        check("auto.pending clears the instant confirm settles it",
              status["auto"]["pending"] is None, detail=str(status))

        # cli/cli's archive has no install.ps1, so this real staging attempt
        # is expected to fail at the build step -- that failure IS the proof
        # confirm reached the real pipeline (download real bytes, extract,
        # try to build), not a mock.
        status = wait_for(
            port, lambda s: s["apply"]["state"] in ("failed", "done", "rolled_back"),
            timeout=90.0, label="the confirmed apply to reach a terminal state",
        )
        check("confirm actually started and finished a real staging attempt",
              status is not None, detail=str(status))
        if status is not None:
            check("it failed at the build step (no install.ps1 in cli/cli), as expected",
                  status["apply"]["state"] == "failed", detail=str(status["apply"]))

        # --- PUT /api/settings 'auto_update' round-trips into status.auto.enabled ---
        r = _put(port, "/api/settings", {"auto_update": False})
        check("PUT /api/settings accepts auto_update", r.status_code == 200, detail=r.text)
        status = _get(port, "/api/update/status").json()
        check("auto.enabled reflects the stored setting",
              status["auto"]["enabled"] is False, detail=str(status["auto"]))

        settings_view = _get(port, "/api/settings").json()
        check("GET /api/settings reports auto_update too",
              settings_view["settings"]["auto_update"]["value"] is False,
              detail=str(settings_view["settings"].get("auto_update")))

        r = _put(port, "/api/settings", {"auto_update": True})
        check("PUT /api/settings restores auto_update", r.status_code == 200, detail=r.text)
    finally:
        check("service stops", service_ctl.stop() == service_ctl.EXIT_OK)
        _RESIDENTS.reap()

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
