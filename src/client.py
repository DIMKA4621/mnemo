"""The one HTTP client every face shares.

From v3 on, the CLI, the auto-inject hook and anything else that wants
memory are **thin clients of the backend** — not second copies of the
engine. This module is the whole of that seam (Memory-contracts-v3 §11.1),
so there is exactly one place that knows the base URL, the token and what a
failure looks like.

**Degradation is the contract, not an afterthought.** A backend that is down
must never turn into a traceback in someone's editor: every call raises
``ServiceDown``, the CLI turns that into one line and exit code **3**, and
the hook swallows it and exits 0. Nothing in a Claude Code session ever waits
on us.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from . import config


class ServiceDown(RuntimeError):
    """The backend is not reachable (not running, wrong port, refused)."""


class ApiFailure(RuntimeError):
    """The backend answered with an error envelope (§9.2).

    Carries the machine-readable ``code`` so a caller can react to
    ``bank_not_found`` differently from ``embed_unavailable`` without
    parsing prose.
    """

    def __init__(self, code: str, message: str, status: int,
                 detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail


def default_base_url() -> str:
    configured = getattr(config, "API_URL", None)
    if configured:
        return str(configured).rstrip("/")
    env = os.environ.get("MNEMO_API_URL")
    if env:
        return env.rstrip("/")
    host = getattr(config, "API_HOST", None) or os.environ.get(
        "MNEMO_API_HOST", "127.0.0.1"
    )
    port = getattr(config, "API_PORT", None) or os.environ.get(
        "MNEMO_API_PORT", "4646"
    )
    return f"http://{host}:{port}"


def default_token() -> str:
    """The API token: env first, then the file the backend wrote.

    Read fresh rather than cached at import — a CLI process is short-lived,
    and a service restart that rotates the token must not need one too.
    """
    env = os.environ.get("MNEMO_API_TOKEN")
    if env and env.strip():
        return env.strip()
    path = Path(config.STATE_DIR) / "api.token"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# Autostart is attempted at most once per process. A face that cannot reach
# the backend and cannot start one must fail fast on every subsequent call,
# not re-spawn on each retry.
_autostart_tried = False


def autostart_enabled() -> bool:
    configured = getattr(config, "API_AUTOSTART", None)
    if configured is not None:
        return bool(configured)
    return os.environ.get("MNEMO_API_AUTOSTART", "1") != "0"


def autostart_wait_s() -> float:
    configured = getattr(config, "API_AUTOSTART_WAIT_S", None)
    if configured is not None:
        return float(configured)
    return float(os.environ.get("MNEMO_API_AUTOSTART_WAIT_S", "20"))


def ensure_backend(*, wait: bool = True) -> bool:
    """Start the backend if it is not running (design §6, implementation §6).

    The user should not have to know a service exists: the first face that
    needs one brings it up, windowless, and carries on. This also means a
    freshly installed machine works before the reboot-triggered autostart has
    ever fired.

    Uses ``service_ctl.start()`` — platform-dev's tested primitive — rather
    than a second spawn path, because "no console window on Windows" is a
    property of *that* function and duplicating it would duplicate the bug
    surface too.

    ``wait=False`` starts it and returns immediately: for the auto-inject
    hook, which must not make a human wait for a cold start. That prompt gets
    no memory; the next one does.
    """
    global _autostart_tried
    if _autostart_tried or not autostart_enabled():
        return False
    _autostart_tried = True
    try:
        from . import service_ctl
    except ImportError:
        return False
    # `service_ctl.start()` narrates to stdout, which is correct for
    # `mnemo service start` and wrong here: the user ran `mnemo banks list`,
    # and a line of service chatter on stdout corrupts anything piping that
    # command. Capture it and re-emit on stderr, where progress belongs.
    import contextlib
    import io

    noise = io.StringIO()
    try:
        with contextlib.redirect_stdout(noise):
            started = service_ctl.start()
    except Exception:  # noqa: BLE001 - a failed autostart is never fatal
        return False
    finally:
        text = noise.getvalue().strip()
        if text:
            print(text, file=sys.stderr)
    if started != 0:
        return False
    if not wait:
        return True

    deadline = time.monotonic() + autostart_wait_s()
    probe = Client(autostart=False, timeout=2.0)
    while time.monotonic() < deadline:
        try:
            probe.health()
            return True
        except ServiceDown:
            time.sleep(0.3)
    return False


class Client:
    """Thin, synchronous wrapper over the loopback API."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 10.0,
        autostart: bool = True,
    ) -> None:
        self.base_url = (base_url or default_base_url()).rstrip("/")
        self.token = token if token is not None else default_token()
        self.timeout = timeout
        self.autostart = autostart

    # ------------------------------------------------------------ plumbing

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _send(self, method: str, path: str, **kw: Any):
        # Popped rather than passed through: `timeout` is already given
        # below, so letting a caller's copy ride in **kw would be a duplicate
        # keyword and a TypeError on the one call that needed a longer wait.
        timeout = kw.pop("timeout", None)
        return httpx.request(
            method,
            f"{self.base_url}{path}",
            headers=self._headers(),
            timeout=self.timeout if timeout is None else timeout,
            **kw,
        )

    def _request(self, method: str, path: str, **kw: Any) -> Any:
        try:
            resp = self._send(method, path, **kw)
        except httpx.RequestError as exc:
            # Nothing listening. Start one and retry once — the user should
            # not have to know a service exists (design §6).
            if self.autostart and ensure_backend():
                self.token = self.token or default_token()
                try:
                    resp = self._send(method, path, **kw)
                except httpx.RequestError as exc2:
                    raise ServiceDown(
                        f"mnemo backend not reachable at {self.base_url} "
                        f"({exc2})"
                    ) from exc2
            else:
                # Connection refused, DNS, timeout — all the same to a
                # caller: there is no service to talk to right now.
                raise ServiceDown(
                    f"mnemo backend not reachable at {self.base_url} ({exc})"
                ) from exc
        if resp.status_code >= 400:
            code, message, detail = "internal", resp.text.strip(), None
            try:
                err = resp.json().get("error") or {}
                code = err.get("code", code)
                message = err.get("message", message)
                detail = err.get("detail")
            except (json.JSONDecodeError, ValueError, AttributeError):
                pass
            raise ApiFailure(code, message, resp.status_code, detail)
        if not resp.content:
            return None
        return resp.json()

    # ------------------------------------------------------------- surface

    def health(self) -> dict:
        """Liveness. The one call that needs no token."""
        try:
            resp = httpx.get(f"{self.base_url}/health", timeout=self.timeout)
        except httpx.RequestError as exc:
            raise ServiceDown(
                f"mnemo backend not reachable at {self.base_url} ({exc})"
            ) from exc
        return resp.json()

    def search(self, bank: str, query: str, **kw: Any) -> dict:
        body = {"bank": bank, "query": query}
        body.update({k: v for k, v in kw.items() if v is not None})
        return self._request("POST", "/api/search", json=body)

    def reindex(self, bank: str, *, path: str | None = None,
                full: bool = False) -> dict:
        return self._request(
            "POST", "/api/reindex",
            json={"bank": bank, "path": path, "full": full},
        )

    def tree(self, bank: str, *, depth: int = 0, links: bool = False) -> dict:
        return self._request(
            "GET", "/api/tree",
            params={"bank": bank, "depth": depth, "links": str(links).lower()},
        )

    def file(self, bank: str, path: str) -> dict:
        return self._request("GET", "/api/file",
                             params={"bank": bank, "path": path})

    def banks(self) -> list[dict]:
        return (self._request("GET", "/api/banks") or {}).get("banks", [])

    def add_bank(self, root: str, *, name: str | None = None,
                 provider: str | None = None) -> dict:
        return self._request(
            "POST", "/api/banks",
            json={"root": str(root), "name": name, "provider": provider},
        )

    def set_bank_state(self, bank_id: str, state: str) -> dict:
        """Switch a bank between ``enabled`` / ``frozen`` / ``disabled``."""
        return self._request(
            "PATCH", f"/api/banks/{bank_id}", json={"state": state}
        )

    def bank_token(self, bank_id: str) -> dict:
        """This bank's own MCP credential (§9.5). Never part of a bank list."""
        return self._request("GET", f"/api/banks/{bank_id}/token")

    def regenerate_bank_token(self, bank_id: str) -> dict:
        return self._request("POST", f"/api/banks/{bank_id}/token")

    def remove_bank(self, bank_id: str, *, drop_index: bool = True) -> None:
        self._request("DELETE", f"/api/banks/{bank_id}",
                      params={"drop_index": str(drop_index).lower()})

    def status(self) -> dict:
        return self._request("GET", "/api/status")

    def embed_state(self) -> dict:
        """What the embedding backend is holding in memory right now."""
        return self._request("GET", "/api/embed/state")

    def embed_unload(self) -> dict:
        # Longer than the default: `local` waits for the resident to exit,
        # and Ollama's native call goes through its own request queue.
        return self._request("POST", "/api/embed/unload", timeout=30.0)

    def embed_load(self) -> dict:
        # A cold load is ~7-8 s measured, and the probe embedding follows it;
        # the default 10 s would time out on exactly the successful path.
        return self._request("POST", "/api/embed/load", timeout=60.0)

    def logs(self, kind: str, **kw: Any) -> dict:
        params = {"kind": kind}
        params.update({k: v for k, v in kw.items() if v is not None})
        return self._request("GET", "/api/logs", params=params)
