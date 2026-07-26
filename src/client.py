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
        "MNEMO_API_PORT", "8918"
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
    path = Path(getattr(config, "API_TOKEN_FILE", None)
                or Path(config.STATE_DIR) / "api.token")
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


class Client:
    """Thin, synchronous wrapper over the loopback API."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = (base_url or default_base_url()).rstrip("/")
        self.token = token if token is not None else default_token()
        self.timeout = timeout

    # ------------------------------------------------------------ plumbing

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kw: Any) -> Any:
        try:
            resp = httpx.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers(),
                timeout=self.timeout,
                **kw,
            )
        except httpx.RequestError as exc:
            # Connection refused, DNS, timeout — all the same to a caller:
            # there is no service to talk to right now.
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

    def remove_bank(self, bank_id: str, *, drop_index: bool = True) -> None:
        self._request("DELETE", f"/api/banks/{bank_id}",
                      params={"drop_index": str(drop_index).lower()})

    def status(self) -> dict:
        return self._request("GET", "/api/status")

    def logs(self, kind: str, **kw: Any) -> dict:
        params = {"kind": kind}
        params.update({k: v for k, v in kw.items() if v is not None})
        return self._request("GET", "/api/logs", params=params)
