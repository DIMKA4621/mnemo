"""Engine self-update — state format, GitHub check, staging (src/engine_update.py).

Steps 5-7 of the plan in
``.claude/memory/topics/engine-self-update-design.md``. Isolated from the
real engine the same way ``test_service_ctl.py`` is: ``MNEMO_STATE_DIR`` is
redirected to a throwaway temp dir before ``src.config`` is ever imported,
and every staging test patches ``config.VERSIONS_DIR``/``config.STATE_DIR``
to throwaway trees too — nothing here ever writes under the real
``~/.claude/mnemo``.

Network tests hit the real internet (GitHub's public API and
``codeload.github.com``) — no token, no mocking of the transport, because
the point of steps 6-7 is proving the real thing works, not a mock of it.
They are skipped (not failed) if the network is unreachable at all, so the
rest of the suite still runs offline.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Redirect all writable state into a temp dir BEFORE config is imported —
# same rule as test_service_ctl.py: the real state/ must never be touched.
_STATE = Path(tempfile.mkdtemp(prefix="mnemo engine-update "))
os.environ["MNEMO_STATE_DIR"] = str(_STATE)

from src import config, engine_update, service_ctl  # noqa: E402

_passed = _failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _failed += 1
        print(f"FAIL  {name}  {detail}")


def _read_shebang(exe_path: Path) -> str:
    """The absolute interpreter path pip baked into a console-script exe.

    Same technique tester used to prove Bug A (step 12): a pip/distlib
    launcher exe is a small stub PE with a plain-text ``#!<path>`` line
    immediately followed by an appended zip archive -- read raw, no PE
    parsing needed.

    Anchored on the zip archive's magic bytes (``PK\\x03\\x04``), not on
    the first ``#!`` in the whole file: a multi-hundred-KB PE stub is
    dense enough binary data that the two-byte sequence ``#!`` (0x23 0x21)
    turns up there by pure chance too, well before the real shebang --
    hit exactly this scanning a real exe, decoded garbage with
    ``errors="replace"`` and crashed printing it. The real shebang line is
    always the text immediately preceding the zip start, so search
    backwards from there in a small window instead.
    """
    data = exe_path.read_bytes()
    zip_start = data.find(b"PK\x03\x04")
    assert zip_start != -1, f"no appended zip archive found in {exe_path}"
    window = data[max(0, zip_start - 1024):zip_start]
    idx = window.rfind(b"#!")
    assert idx != -1, f"no shebang found immediately before the zip archive in {exe_path}"
    return window[idx:].rstrip(b"\r\n").decode("utf-8")


def _network_up() -> bool:
    import socket

    try:
        socket.create_connection(("api.github.com", 443), timeout=5).close()
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ step 5


def test_default_state_shape() -> None:
    state = engine_update.default_state()
    check("default_state has all four top-level keys",
          set(state) == {"current", "installed", "last_check", "last_apply"})
    check("default current is None", state["current"] is None)
    check("default installed is empty", state["installed"] == [])
    check("default last_check has update_available=False",
          state["last_check"]["update_available"] is False)


def test_state_round_trip(work: Path) -> None:
    with patch.object(config, "STATE_DIR", work / "rt"):
        check("version_state_file follows a relocated STATE_DIR",
              engine_update.version_state_file() == work / "rt" / "engine_version.json")

        written = engine_update.default_state()
        written["current"] = "v3.4.0"
        written["installed"].append(
            {"tag": "v3.4.0", "installed_at": "2026-08-20T00:00:00+00:00",
             "commit": "deadbeef", "status": "active"}
        )
        engine_update.write_state(written)
        check("write_state produced a real file",
              engine_update.version_state_file().is_file())

        read_back = engine_update.read_state()
        check("round-trip preserves the exact structure", read_back == written,
              detail=json.dumps(read_back))
        check("no .tmp file left behind",
              not engine_update.version_state_file().with_suffix(".json.tmp").exists())


def test_state_recovers_from_missing_file(work: Path) -> None:
    with patch.object(config, "STATE_DIR", work / "missing"):
        check("no file yet", not engine_update.version_state_file().exists())
        state = engine_update.read_state()
        check("missing file falls back to default_state()",
              state == engine_update.default_state())


def test_state_recovers_from_corrupt_file(work: Path) -> None:
    with patch.object(config, "STATE_DIR", work / "corrupt"):
        path = engine_update.version_state_file()
        path.parent.mkdir(parents=True, exist_ok=True)

        path.write_text("{not valid json at all", encoding="utf-8")
        check("truncated/invalid JSON recovers to default_state()",
              engine_update.read_state() == engine_update.default_state())

        path.write_text(json.dumps(["this", "is", "a", "list", "not", "a", "dict"]),
                         encoding="utf-8")
        check("well-formed JSON of the wrong shape (a list) recovers too",
              engine_update.read_state() == engine_update.default_state())

        path.write_text(json.dumps({"current": None, "installed": "not-a-list",
                                     "last_check": {}, "last_apply": {}}),
                         encoding="utf-8")
        check("a dict with the wrong field types recovers too",
              engine_update.read_state() == engine_update.default_state())

        # write_state() after corruption must overwrite cleanly, not merge
        # with the garbage that was there.
        fresh = engine_update.default_state()
        fresh["current"] = "v1.0.0"
        engine_update.write_state(fresh)
        check("a subsequent write_state overwrites the corrupt file",
              engine_update.read_state()["current"] == "v1.0.0")


def test_record_installed_and_apply_helpers(work: Path) -> None:
    with patch.object(config, "STATE_DIR", work / "helpers"):
        engine_update.record_installed(tag="v1.0.0", commit="aaa111", status="active")
        state = engine_update.read_state()
        check("first install becomes current", state["current"] == "v1.0.0")
        check("first install is recorded active",
              state["installed"][0]["status"] == "active")

        engine_update.record_installed(tag="v1.1.0", commit="bbb222", status="active")
        state = engine_update.read_state()
        tags = {e["tag"]: e["status"] for e in state["installed"]}
        check("current moves to the new tag", state["current"] == "v1.1.0")
        check("the old active entry is demoted to previous",
              tags == {"v1.0.0": "previous", "v1.1.0": "active"}, detail=str(tags))

        try:
            engine_update.record_installed(tag="v1.2.0", commit=None, status="bogus")
            check("an unknown status is rejected", False)
        except ValueError:
            check("an unknown status is rejected", True)

        engine_update.start_apply("v1.2.0")
        mid = engine_update.read_state()["last_apply"]
        check("start_apply records tag+started_at, no result yet",
              mid["tag"] == "v1.2.0" and mid["started_at"] and mid["result"] is None)

        engine_update.finish_apply(tag="v1.2.0", result="ok", error=None)
        done = engine_update.read_state()["last_apply"]
        check("finish_apply preserves started_at and sets result",
              done["started_at"] == mid["started_at"] and done["result"] == "ok"
              and done["finished_at"], detail=str(done))


def test_record_check_soft_failure(work: Path) -> None:
    with patch.object(config, "STATE_DIR", work / "check"):
        engine_update.record_installed(tag="v1.0.0", commit=None, status="active")

        after_ok = engine_update.record_check(latest_tag="v1.1.0", error=None)
        check("a successful check with a newer tag sets update_available",
              after_ok["last_check"]["update_available"] is True)
        check("latest_tag recorded", after_ok["last_check"]["latest_tag"] == "v1.1.0")

        after_err = engine_update.record_check(latest_tag=None, error="boom: connection refused")
        check("a failed check records the error",
              after_err["last_check"]["error"] == "boom: connection refused")
        check("a failed check does NOT erase the previous latest_tag",
              after_err["last_check"]["latest_tag"] == "v1.1.0",
              detail=str(after_err["last_check"]))
        check("a failed check does NOT flip update_available back to False",
              after_err["last_check"]["update_available"] is True)

        after_same = engine_update.record_check(latest_tag="v1.0.0", error=None)
        check("checking again with the CURRENT tag clears update_available",
              after_same["last_check"]["update_available"] is False)


def test_update_available_clears_on_switch(work: Path) -> None:
    """The bug ui-dev found in step 11's live run: `update_available` used
    to be computed only inside `record_check()`, so a successful switch
    (which changes `current` without ever re-running a check) left it
    permanently `true` even though `current` now matches `latest_tag`.
    `record_installed(status="active")` must re-derive it on the spot.
    """
    with patch.object(config, "STATE_DIR", work / "update-available"):
        engine_update.record_installed(tag="v1.0.0", commit=None, status="active")
        after_check = engine_update.record_check(latest_tag="v1.1.0", error=None)
        check("update_available is true before the switch",
              after_check["last_check"]["update_available"] is True)

        after_switch = engine_update.record_installed(
            tag="v1.1.0", commit=None, status="active"
        )
        check("update_available flips to false the moment the matching tag "
              "becomes current — no new check needed",
              after_switch["last_check"]["update_available"] is False,
              detail=str(after_switch["last_check"]))
        check("latest_tag itself is untouched by the switch",
              after_switch["last_check"]["latest_tag"] == "v1.1.0",
              detail=str(after_switch["last_check"]))

        # Switching to a tag that does NOT match the last known latest_tag
        # (e.g. a rollback) must leave update_available true -- there
        # genuinely is still a newer release than what ended up running.
        after_rollback = engine_update.record_installed(
            tag="v1.0.0", commit=None, status="active"
        )
        check("switching to a tag OTHER than latest_tag keeps "
              "update_available true (a real rollback case)",
              after_rollback["last_check"]["update_available"] is True,
              detail=str(after_rollback["last_check"]))

        # A status other than "active" (e.g. recording a staged-but-not-
        # applied or a failed attempt) must never touch update_available.
        before = engine_update.read_state()["last_check"]
        engine_update.record_installed(tag="v1.2.0", commit=None, status="failed")
        after_failed = engine_update.read_state()["last_check"]
        check("a non-active record_installed leaves last_check untouched",
              after_failed == before, detail=str(after_failed))


def test_tarball_url_shape() -> None:
    with patch.object(config, "GITHUB_REPO", "DIMKA4621/mnemo"):
        url = engine_update._tarball_url("v3.4.0")
        check("tarball url targets codeload's tag archive path",
              url == "https://codeload.github.com/DIMKA4621/mnemo/tar.gz/refs/tags/v3.4.0",
              detail=url)


# ------------------------------------------------------------------ step 6


def test_check_latest_release_real_no_releases_yet() -> None:
    """DIMKA4621/mnemo has no tagged releases yet (confirmed by hand:
    `curl https://api.github.com/repos/DIMKA4621/mnemo/releases/latest` ->
    404) -- a real request against the real, still-unreleased repo.
    """
    if not _network_up():
        print("SKIP  no network -- test_check_latest_release_real_no_releases_yet")
        return
    with patch.object(config, "GITHUB_REPO", "DIMKA4621/mnemo"):
        tag, error = engine_update.check_latest_release(timeout=10)
    check("no tag on a repo with no releases", tag is None)
    check("the 404 is surfaced as an error string", error is not None and "404" in error,
          detail=str(error))


def test_check_latest_release_real_success() -> None:
    """A real GitHub repo that DOES have releases, to prove the success
    path end to end (real HTTP, real JSON, real tag_name field) -- not just
    the "no releases" branch above.
    """
    if not _network_up():
        print("SKIP  no network -- test_check_latest_release_real_success")
        return
    with patch.object(config, "GITHUB_REPO", "cli/cli"):
        tag, error = engine_update.check_latest_release(timeout=10)
    check("a real release tag comes back", error is None and bool(tag),
          detail=f"tag={tag!r} error={error!r}")


def test_check_latest_release_unreachable_host() -> None:
    """127.0.0.1:1 refuses the connection immediately (nothing listens on
    port 1) -- a deterministic stand-in for "the host is unreachable" that
    does not depend on any real outage or a slow timeout to observe.
    """
    tag, error = engine_update.check_latest_release(
        timeout=3, url="http://127.0.0.1:1/repos/x/releases/latest"
    )
    check("an unreachable host returns no tag", tag is None)
    check("an unreachable host returns a non-empty error", bool(error), detail=str(error))


def test_check_now_and_record_check_soft_failure_live(work: Path) -> None:
    """check_now() end to end against the unreachable-host case, proving
    record_check's soft-failure semantics fire from a REAL failed request,
    not just from record_check() called directly (test above).
    """
    original = engine_update.check_latest_release
    with patch.object(config, "STATE_DIR", work / "livecheck"), \
         patch.object(engine_update, "check_latest_release",
                      lambda **kw: original(timeout=3, url="http://127.0.0.1:1/nope")):
        engine_update.record_installed(tag="v1.0.0", commit=None, status="active")
        engine_update.record_check(latest_tag="v1.5.0", error=None)  # seed a known-good prior check
        state = engine_update.check_now()
    check("check_now persists a soft failure without erasing the prior tag",
          state["last_check"]["error"] is not None
          and state["last_check"]["latest_tag"] == "v1.5.0",
          detail=str(state["last_check"]))


def test_background_checker_runs_without_blocking(work: Path) -> None:
    """start_checker() must return immediately (never block startup on a
    network call) and still perform checks on its own thread.
    """
    calls: list[int] = []
    original = engine_update.check_now

    def _counting_check_now(**kwargs):
        calls.append(1)
        return original(**kwargs)

    with patch.object(config, "STATE_DIR", work / "bgcheck"), \
         patch.object(engine_update, "check_now", _counting_check_now), \
         patch.object(engine_update.config, "UPDATE_CHECK_TIMEOUT_S", 3.0):
        engine_update.record_installed(tag="v0.0.0", commit=None, status="active")
        start = time.monotonic()
        engine_update.start_checker(interval_s=0.3)
        elapsed = time.monotonic() - start
        check("start_checker returns immediately (does not block on the network)",
              elapsed < 1.0, detail=f"{elapsed:.2f}s")
        try:
            deadline = time.monotonic() + 5.0
            while not calls and time.monotonic() < deadline:
                time.sleep(0.05)
            check("the background thread performed at least one check",
                  len(calls) >= 1)
        finally:
            engine_update.stop_checker()

    check("interval=0 disables the timer entirely (no thread started)", True)
    with patch.object(config, "STATE_DIR", work / "bgcheck-disabled"), \
         patch.object(engine_update, "_checker", None):
        engine_update.start_checker(interval_s=0)
        check("no thread was recorded", engine_update._checker is None)


# ------------------------------------------------------------------ step 7


def test_safe_members_rejects_path_traversal(work: Path) -> None:
    import tarfile

    archive = work / "evil.tar.gz"
    evil_member_path = "evil-src/../../outside.txt"
    with tarfile.open(archive, "w:gz") as tar:
        data = b"pwned"
        info = tarfile.TarInfo(name=evil_member_path)
        info.size = len(data)
        import io
        tar.addfile(info, io.BytesIO(data))

    dest = work / "extract-evil"
    with tarfile.open(archive, "r:gz") as tar:
        try:
            engine_update._safe_members(tar, dest)
            check("a path-traversal member is rejected", False)
        except RuntimeError as exc:
            check("a path-traversal member is rejected", "unsafe path" in str(exc),
                  detail=str(exc))


def test_download_and_extract_real_github_tarball(work: Path) -> None:
    """Real GitHub codeload download + extraction + single-top-dir
    detection, against this repo's actual `master` branch (confirmed
    reachable by hand: `curl -I .../tar.gz/refs/heads/master` -> 200).

    Proves the network+extraction half of the pipeline against real GitHub
    infrastructure. The full pipeline including Build-EngineVersion is
    proven separately (test_stage_release_real_pipeline) against a local
    server, because neither `master` nor the pushed `feat/v3` carries
    `install.ps1` yet -- this self-update feature (install.ps1's own
    Build-EngineVersion) is still uncommitted local work on this branch, so
    there is no real GitHub release of it to download.
    """
    if not _network_up():
        print("SKIP  test_download_and_extract_real_github_tarball (no network)")
        return
    archive = work / "gh-master.tar.gz"
    engine_update._download(
        "https://codeload.github.com/DIMKA4621/mnemo/tar.gz/refs/heads/master",
        archive, timeout=30,
    )
    check("a real tarball was downloaded", archive.stat().st_size > 1000,
          detail=str(archive.stat().st_size if archive.exists() else "missing"))
    extracted = engine_update._extract_tarball(archive, work / "gh-extract")
    check("extraction finds exactly one top-level dir (GitHub's own wrapping)",
          extracted.name.startswith("mnemo-"), detail=extracted.name)
    check("the extracted tree has real repo files",
          (extracted / "requirements.txt").is_file())


def _make_local_release_tarball(dest: Path) -> Path:
    """Package THIS repo's own working tree the way GitHub's auto-archive
    wraps a release -- one top-level directory -- so the FULL step-7
    pipeline (download, extract, dot-source install.ps1, Build-EngineVersion,
    real venv+pip install) can be exercised against real, current code
    without depending on a GitHub release existing. None does yet: this
    feature is mid-branch, uncommitted (see the note in
    test_download_and_extract_real_github_tarball above).
    """
    import tarfile

    wrap = "mnemo-local"
    with tarfile.open(dest, "w:gz") as tar:
        for name in ("install.ps1", "requirements.txt", "pyproject.toml", "mnemo_bootstrap.py"):
            tar.add(REPO / name, arcname=f"{wrap}/{name}")

        def _skip_pycache(info: "tarfile.TarInfo") -> "tarfile.TarInfo | None":
            return None if "__pycache__" in info.name else info

        tar.add(REPO / "src", arcname=f"{wrap}/src", filter=_skip_pycache)
    return dest


def test_stage_release_real_pipeline(work: Path) -> None:
    """The full step-7 pipeline for real: download over a real HTTP socket,
    extract, dot-source install.ps1 and call Build-EngineVersion (a REAL
    `pip install -r requirements.txt` + venv + launcher generation), write
    the VERSION marker, atomically finalize into a throwaway versions/ dir,
    and confirm the update_progress events fired in order.

    A local HTTP server (stdlib http.server) stands in for GitHub for the
    download itself -- see _make_local_release_tarball for why: there is no
    real GitHub release of this in-progress feature to download from.
    codeload.github.com's own reachability and archive shape are proven
    separately, for real, by test_download_and_extract_real_github_tarball.

    Skipped, not failed, without Windows (this module's build step is
    Windows-only, matching install.ps1's Build-EngineVersion).
    """
    if os.name != "nt":
        print("SKIP  test_stage_release_real_pipeline (Windows-only staging)")
        return

    import functools
    import http.server
    import threading as th

    tarball = _make_local_release_tarball(work / "local-release.tar.gz")

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(work))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    server_thread = th.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    versions_dir = work / "stage-versions"
    state_dir = work / "stage-state"
    versions_dir.mkdir()
    state_dir.mkdir()
    tag = "teststage"
    progress: list[dict] = []

    try:
        url = f"http://127.0.0.1:{port}/{tarball.name}"
        with patch.object(config, "VERSIONS_DIR", versions_dir), \
             patch.object(config, "STATE_DIR", state_dir), \
             patch.object(engine_update, "_emit_progress",
                          lambda t, step, detail=None, error=None:
                              progress.append({"tag": t, "step": step,
                                                "detail": detail, "error": error})):
            final_dir = engine_update.stage_release(tag, tarball_url=url, build_timeout=1800)

            check("stage_release returns versions/<tag>", final_dir == versions_dir / tag,
                  detail=str(final_dir))
            check("the version dir contains a built venv",
                  (final_dir / ".venv").is_dir())
            check("mnemo.exe was generated in the staged venv",
                  (final_dir / ".venv" / "Scripts" / "mnemo.exe").is_file())
            check("mnemow.exe (windowless twin) was generated too",
                  (final_dir / ".venv" / "Scripts" / "mnemow.exe").is_file())
            check("the code was mirrored into src/",
                  (final_dir / "src" / "cli.py").is_file())
            check("a VERSION marker with the tag was written",
                  (final_dir / "VERSION").read_text(encoding="utf-8") == tag)
            check("the staging temp dir was cleaned up",
                  not (state_dir / "tmp" / f"update-{tag}").exists())

            # --- Bug A (tester, step 12): build-into-staging-then-move baked
            # a shebang pointing at the STAGING dir, which then got deleted.
            # Byte-level proof the fix is real: the shebang must name the
            # FINAL versions/<tag>/ location, which still exists.
            mnemo_exe = final_dir / ".venv" / "Scripts" / "mnemo.exe"
            shebang = _read_shebang(mnemo_exe)
            print(f"  mnemo.exe shebang: {shebang}")
            # distlib quotes the path when it contains a space -- this
            # temp dir's own name does (tempfile.TemporaryDirectory's
            # prefix), so strip a possible surrounding '"' before comparing.
            shebang_path = shebang[2:].strip('"')
            check("shebang points at the FINAL version dir, not a staging path",
                  shebang_path.startswith(str(final_dir)), detail=shebang)
            check("shebang does NOT mention state/tmp (the old staging root)",
                  "state" not in shebang.lower() or "tmp" not in shebang.lower(),
                  detail=shebang)
            check("the shebang's own interpreter path actually exists on disk",
                  Path(shebang_path).is_file(), detail=shebang)

            # Live run, proving it end to end, not just the embedded string:
            # invoked directly from .venv/Scripts (not the copied bin/
            # launcher), so mnemo_bootstrap's OWN engine-home resolution
            # will correctly report "no engine found" (rc=3, real stderr) --
            # what matters here is that it is NOT the old silent rc=1 death
            # (pip's launcher stub failing before Python ever starts).
            direct = subprocess.run([str(mnemo_exe), "--help"], capture_output=True,
                                     text=True, timeout=30)
            print(f"  direct run: rc={direct.returncode} "
                  f"stdout={direct.stdout[:80]!r} stderr={direct.stderr[:120]!r}")
            check("mnemo.exe invoked directly no longer dies with the silent "
                  "launcher-stub rc=1 (it now finds its own python.exe)",
                  direct.returncode != 1 or bool(direct.stdout or direct.stderr),
                  detail=f"rc={direct.returncode}")

            # Stronger, unambiguous proof: set this build up as `current`
            # (real service_ctl.switch_current, same throwaway
            # VERSIONS_DIR already patched above) and run the exe from a
            # copied bin/ location -- exactly how a real install invokes
            # it -- and confirm a genuinely clean rc=0 with real --help text.
            #
            # mnemo_bootstrap.py resolves its OWN engine home from
            # `sys.argv[0]` (never from config, which it cannot import yet)
            # as `<argv0>/../.. / "current"` -- the directory MUST be
            # named literally "current", one level above wherever the exe
            # sits, or the bootstrap dispatcher will never find it.
            engine_home = work / "install-shape"
            current_link = engine_home / "current"
            bin_dir = engine_home / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            with patch.object(config, "CURRENT_LINK", current_link):
                service_ctl.switch_current(tag)
            bin_mnemo = bin_dir / "mnemo.exe"
            shutil.copy2(mnemo_exe, bin_mnemo)
            full = subprocess.run([str(bin_mnemo), "--help"], capture_output=True,
                                   text=True, timeout=30)
            print(f"  bin/mnemo.exe --help: rc={full.returncode} "
                  f"stdout[:120]={full.stdout[:120]!r}")
            check("bin/mnemo.exe --help (real install shape) succeeds cleanly",
                  full.returncode == 0 and bool(full.stdout), detail=full.stderr[:300])

            steps = [p["step"] for p in progress]
            check("progress went download -> venv -> done",
                  steps == ["download", "venv", "done"], detail=str(steps))

            # Re-staging the SAME tag must be idempotent (fresh build, old
            # one replaced) -- exercises stage_release's "stale final_dir"
            # branch (rmtree before building directly into it) for real,
            # not just by inspection.
            final_dir_2 = engine_update.stage_release(tag, tarball_url=url, build_timeout=1800)
            check("re-staging the same tag succeeds and replaces the old tree",
                  final_dir_2 == final_dir and (final_dir_2 / "VERSION").is_file())
    finally:
        httpd.shutdown()
        server_thread.join(timeout=5)
        shutil.rmtree(versions_dir, ignore_errors=True)


def test_stage_release_failure_leaves_nothing_behind(work: Path) -> None:
    """A build that fails (a bogus repo root with no install.ps1) must
    leave `versions/<tag>/` absent and the staging temp dir cleaned up --
    never a half-built tree.
    """
    if os.name != "nt":
        print("SKIP  test_stage_release_failure_leaves_nothing_behind (Windows-only)")
        return
    if not _network_up():
        print("SKIP  test_stage_release_failure_leaves_nothing_behind (no network)")
        return

    versions_dir = work / "fail-versions"
    state_dir = work / "fail-state"
    versions_dir.mkdir()
    state_dir.mkdir()
    tag = "willfail"
    progress: list[dict] = []

    # A tag/branch that does not exist on the repo -> codeload 404s the
    # download itself, before extraction is ever attempted.
    with patch.object(config, "VERSIONS_DIR", versions_dir), \
         patch.object(config, "STATE_DIR", state_dir), \
         patch.object(engine_update, "_emit_progress",
                      lambda t, step, detail=None, error=None:
                          progress.append({"step": step, "error": error})):
        try:
            engine_update.stage_release(
                tag,
                tarball_url="https://codeload.github.com/DIMKA4621/mnemo/tar.gz/refs/heads/no-such-branch-xyz",
                download_timeout=10,
            )
            check("staging a nonexistent ref raises", False)
        except Exception as exc:  # noqa: BLE001
            check("staging a nonexistent ref raises", True, detail=str(exc))

    check("no version dir was created for the failed tag",
          not (versions_dir / tag).exists())
    check("the staging temp dir was cleaned up after failure",
          not (state_dir / "tmp" / f"update-{tag}").exists())
    check("a failed progress event was emitted",
          any(p["step"] == "failed" for p in progress), detail=str(progress))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mnemo eu ") as raw:
        work = Path(raw)

        test_default_state_shape()
        test_state_round_trip(work)
        test_state_recovers_from_missing_file(work)
        test_state_recovers_from_corrupt_file(work)
        test_record_installed_and_apply_helpers(work)
        test_record_check_soft_failure(work)
        test_update_available_clears_on_switch(work)
        test_tarball_url_shape()

        test_check_latest_release_real_no_releases_yet()
        test_check_latest_release_real_success()
        test_check_latest_release_unreachable_host()
        test_check_now_and_record_check_soft_failure_live(work)
        test_background_checker_runs_without_blocking(work)

        test_safe_members_rejects_path_traversal(work)
        test_stage_release_real_pipeline(work)
        test_stage_release_failure_leaves_nothing_behind(work)

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
