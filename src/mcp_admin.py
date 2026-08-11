"""The admin MCP face — one connection that manages the whole service.

Mounted at ``/mcp-admin`` alongside the per-bank face, and the two differ in
every way that matters:

===============  ==========================  ===========================
                 ``/mcp/<bank>``             ``/mcp-admin``
===============  ==========================  ===========================
addressed to     one bank, by URL segment    the service; no bank segment
opens with       that bank's token, or the   the **service token only**
                 service token
tools            ``search``, ``tree``        the six below
===============  ==========================  ===========================

**A bank token never opens this face.** A project's git-ignored wiring holds
one, and a credential handed to a project must not be able to register a bank
somewhere else on the machine or drop one. That check lives in
``api.auth_middleware``, which reads ``/mcp-admin`` before ``/mcp`` for
exactly this reason.

The two tool sets are kept in **separate MCPServer instances** rather than one
instance filtered per request: a tool list is what a client caches at
handshake time, so "declared but refused later" would show an agent six tools
it can never call. Nothing here is registered on the plain face and nothing
there is registered here.

Like the plain face, these tools call the backend's internal functions
directly (never self-HTTP), and they return **agent-readable text** for domain
problems rather than raising — an exception ends a turn, a sentence tells the
agent what to do next.
"""
from __future__ import annotations

import logging
from typing import Any

from mcp.server.mcpserver import MCPServer

log = logging.getLogger("mnemo.mcp.admin")

_mcp: MCPServer | None = None


# ------------------------------------------------------------ tool bodies


def _problem(exc: Exception) -> str:
    """One sentence an agent can act on, never a traceback."""
    from .api import ApiError

    if isinstance(exc, ApiError):
        return f"[mnemo] {exc.code}: {exc.message}"
    return f"[mnemo] {type(exc).__name__}: {exc}"


def _resolve(ref: str):
    """``(bank, None)`` or ``(None, text)`` — the admin tools' bank lookup.

    Unlike the plain face there is no URL segment to fall back on, so the
    reference is always explicit and always required. A miss lists what IS
    registered, because "no bank matches 'nots'" plus the real names is a
    complete answer and a bare error is not.
    """
    from . import registry
    from .registry import AmbiguousBankRef, BankNotFound

    try:
        return registry.resolve(str(ref).strip()), None
    except (BankNotFound, AmbiguousBankRef) as exc:
        names = ", ".join(b.name for b in registry.load()) or "(none registered)"
        return None, f"[mnemo] {exc}\nRegistered banks: {names}"


def _bank_line(info: dict) -> str:
    flags = []
    if not info.get("enabled"):
        flags.append("disabled")
    if not info.get("exists"):
        flags.append("ROOT MISSING")
    if info.get("rebuild_pending"):
        flags.append("rebuild pending")
    if info.get("last_error"):
        flags.append("last index FAILED")
    suffix = f"  [{', '.join(flags)}]" if flags else ""
    return (
        f"{info['name']}  · {info['status']} · {info['files']} files · "
        f"{info['chunks']} chunks · queued={info['queued']}\n"
        f"    {info['root']}{suffix}"
    )


def run_banks() -> str:
    """Every registered bank with its state and counts."""
    from .api import api_banks

    try:
        banks = api_banks()["banks"]
    except Exception as exc:  # noqa: BLE001 - a tool answers, it does not raise
        return _problem(exc)
    if not banks:
        return ("[mnemo] no banks registered. Register a memory root with "
                "`bank_add`.")
    lines = [f"[mnemo · {len(banks)} bank(s)]"]
    lines.extend(_bank_line(b) for b in banks)
    return "\n".join(lines)


def run_bank_add(path: str, name: str | None = None) -> str:
    """Register a folder of `.md` as a bank and queue its first index."""
    from .api import AddBankRequest, api_add_bank

    try:
        info = api_add_bank(AddBankRequest(root=path, name=name, provider=None))
    except Exception as exc:  # noqa: BLE001
        return _problem(exc)
    # The stored name may carry a uniqueness suffix (`notes` -> `notes-2`), and
    # it is the name every face addresses the bank by afterwards — so it is
    # reported back rather than assumed to be what was asked for.
    return (f"[mnemo] registered {info['name']} ({info['id']})\n"
            f"    {info['root']}\n"
            f"    first index queued; status={info['status']}")


def run_bank_remove(ref: str, drop_index: bool = True) -> str:
    """Unregister a bank. Its `.md` files are never touched — only the index."""
    from .api import api_remove_bank

    bank, problem = _resolve(ref)
    if problem:
        return problem
    try:
        api_remove_bank(bank.id, drop_index=drop_index)
    except Exception as exc:  # noqa: BLE001
        return _problem(exc)
    kept = "" if drop_index else " (its index file was kept on disk)"
    return (f"[mnemo] removed {bank.name} from the registry{kept}. "
            f"The .md under {bank.root.as_posix()} were not touched.")


def run_reindex(bank: str, path: str | None = None, full: bool = False) -> str:
    """Queue a reindex of one file, or of a whole bank."""
    from .mcp_server import run_reindex as _run

    target, problem = _resolve(bank)
    if problem:
        return problem
    try:
        return _run(path, target.id, full)
    except Exception as exc:  # noqa: BLE001
        return _problem(exc)


def run_status() -> str:
    """Service, queue and per-bank state in one answer."""
    from .api import api_status

    try:
        body = api_status()
    except Exception as exc:  # noqa: BLE001
        return _problem(exc)
    svc, queue = body["service"], body["queue"]
    lines = [
        f"[mnemo {svc['version']} · pid={svc['pid']} · port={svc['port']} · "
        f"up {svc['uptime_s']:.0f}s]",
        f"provider {svc.get('provider') or '—'} "
        f"({svc.get('provider_model') or '—'}, dim {svc.get('provider_dim')}) "
        f"· embed "
        + ("reachable" if svc["embed"].get("reachable") else "DOWN"),
        f"queue depth={queue['depth']} high={queue['high']} "
        f"normal={queue['normal']} low={queue['low']}",
    ]
    if svc.get("provider_error"):
        lines.append(f"provider NOT CONFIGURED: {svc['provider_error']}")
    current = queue.get("current")
    if current:
        lines.append(
            f"  current: {current['kind']} {current['path'] or ''} "
            f"batch {current['batch']}/{current['batches']}"
        )
    lines.extend(_bank_line(b) for b in body["banks"])
    return "\n".join(lines)


def run_logs(kind: str = "index", bank: str | None = None, n: int = 20) -> str:
    """The service journal: what was searched, and what was indexed."""
    from .api import api_logs

    if kind not in ("query", "index"):
        return "[mnemo] kind must be 'query' or 'index'."
    if bank:
        target, problem = _resolve(bank)
        if problem:
            return problem
        bank = target.id
    try:
        # `offset` is passed explicitly even though 0 is its documented
        # default: `api_logs` is a FastAPI endpoint, so its default is a
        # `Query(...)` *descriptor*, which FastAPI replaces per request and a
        # direct Python call does not. Leaving it out sends a `Query` object
        # into `servicelog` and fails with a TypeError about `int()`.
        body = api_logs(kind=kind, bank=bank,
                        limit=max(1, min(int(n), 200)), offset=0)
    except Exception as exc:  # noqa: BLE001
        return _problem(exc)
    events = body["events"]
    lines = [f"[mnemo · {kind} · {body['total']} event(s), showing "
             f"{len(events)}]"]
    if not events:
        lines.append("Nothing recorded in this window.")
        return "\n".join(lines)
    for ev in events:
        if kind == "query":
            lines.append(
                f"{ev['ts']}  {ev['face']:<10} {ev['status']:<9} "
                f"n={ev['n_hits']:<3} {ev['took_ms']:.0f}ms  "
                f"{ev['query'][:80]}"
            )
        else:
            error = f"  {ev['error']}" if ev.get("error") else ""
            lines.append(
                f"{ev['ts']}  {ev['kind']:<7} {ev['trigger']:<8} "
                f"{ev['result']:<8} {ev['path'] or ''}{error}"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------- register


def _register(mcp: MCPServer) -> None:
    """The six admin tools. Declarations only — the bodies are above.

    Every one of them takes the bank as an **argument**, which is the opposite
    of the plain face and correct here: this connection is addressed to the
    service, not to a bank, so there is no URL segment to take it from.
    """

    @mcp.tool()
    def banks() -> str:
        """List every registered memory bank with its status and counts."""
        return run_banks()

    @mcp.tool()
    def bank_add(path: str, name: str | None = None) -> str:
        """Register a folder of .md files as a bank and index it."""
        return run_bank_add(path, name)

    @mcp.tool()
    def bank_remove(ref: str, drop_index: bool = True) -> str:
        """Unregister a bank by name or id. Never deletes its .md files."""
        return run_bank_remove(ref, drop_index)

    @mcp.tool()
    def reindex(bank: str, path: str | None = None, full: bool = False) -> str:
        """Queue a reindex of one file, or of a whole bank."""
        return run_reindex(bank, path, full)

    @mcp.tool()
    def status() -> str:
        """Service, queue and per-bank state in one answer."""
        return run_status()

    @mcp.tool()
    def logs(kind: str = "index", bank: str | None = None, n: int = 20) -> str:
        """Recent journal events: kind is 'index' or 'query'."""
        return run_logs(kind, bank, n)


def server() -> MCPServer:
    """The single admin MCPServer instance, built once.

    A different server name from the plain face (`mnemo-admin`, not `mnemo`):
    a client namespaces tools by server name, so this is what keeps
    `mcp__mnemo-admin__reindex` distinct from anything on a project face.
    """
    global _mcp
    if _mcp is None:
        _mcp = MCPServer("mnemo-admin")
        _register(_mcp)
    return _mcp


# ------------------------------------------------------------------- ASGI


def build_app() -> Any:
    """The ASGI app to mount at ``/mcp-admin``.

    Mounted bare, with no shim. A `Mount("/mcp-admin")` compiles to
    ``^/mcp-admin/(?P<path>.*)$``, so the slashless path an MCP client is
    configured with would fall through to Starlette's `redirect_slashes` and
    cost a 307 on every request — but that is normalised once, for both MCP
    faces together, in `api.auth_middleware`, before routing. A shim here that
    forced the inner path to ``/`` would additionally swallow
    ``/mcp-admin/anything`` and answer it as if it were the root, which is the
    same "a path component that does not mean what it says" problem the plain
    face's segment removal exists to end.

    ``stateless_http`` / ``streamable_http_path`` live on this call rather than
    on the constructor: the 2.0 SDK moved them here (see `mcp_server.build_app`
    for the ordering consequence).
    """
    return server().streamable_http_app(
        streamable_http_path="/", stateless_http=True
    )
