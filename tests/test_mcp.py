"""Standalone MCP test client — proves the agent-callable layer.

Talks to the **running service** the way Claude Code does: streamable HTTP at
`/mcp?token=<bank-token>`, no process spawned (NFR-2).

**The token is the whole address.** There is no bank in the path and none in
any tool argument, so these checks are what prove a connection cannot reach a
bank its credential does not own — by any route:

* the **plain face** exposes `search` and `tree`, neither taking a `bank`
  argument, and a given bank token reaches exactly its own bank;
* **no path segment** is accepted (a leftover `/mcp/<bank>` is refused with an
  actionable message) or required;
* the **service token does not open `/mcp` at all** — it has no bank to
  resolve to, and guessing one would be the whole bug;
* the **admin face** at `/mcp-admin` exposes the six management tools, opens
  for the service token only, and the two tool sets do not overlap in either
  direction.

Then the property that keeps the `/mcp-tools/*` mirror honest — that it
returns the *same bytes* as the tool it mirrors (§9.8).

The previous version spawned `mnemo mcp` over stdio. That subcommand died with
phase 4, so the test had been failing for reasons that had nothing to do with
the code under test.

    .venv/bin/python tests/test_mcp.py

Requires a live backend and at least one indexed bank; exits **2** (not 1) if
there is none, so "cannot run" never reads as "failed".
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from mcp import ClientSession
# SDK 2.0 renamed this (`streamablehttp_client` -> `streamable_http_client`),
# the client-side half of the same rename that moved `FastMCP` to `MCPServer`.
from mcp.client.streamable_http import streamable_http_client

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

BASE = os.environ.get("MNEMO_TEST_BASE_URL", "http://127.0.0.1:8918")
_token_file = Path.home() / ".claude" / "mnemo" / "state" / "api.token"

_passed = _failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _failed += 1
        print(f"FAIL  {name}  {detail}")


def _text(result) -> str:
    return "\n".join(
        c.text for c in result.content if getattr(c, "type", "") == "text"
    )


def _token() -> str | None:
    token = os.environ.get("MNEMO_API_TOKEN")
    if token:
        return token.strip()
    try:
        return _token_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _get(path: str, token: str) -> str:
    req = urllib.request.Request(
        BASE + path, headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def _banks(token: str) -> list[dict]:
    try:
        return json.loads(_get("/api/banks", token))["banks"]
    except (urllib.error.URLError, OSError, KeyError, ValueError):
        return []


def _pick_bank(banks: list[dict]) -> str | None:
    """A ready bank with chunks in it — the mirror comparison needs hits."""
    ready = [b for b in banks if b.get("status") == "ready" and b.get("chunks")]
    return ready[0]["name"] if ready else None


def _bank_token(bank_id: str, service_token: str) -> str:
    return json.loads(
        _get(f"/api/banks/{bank_id}/token", service_token)
    )["token"]


def _status(path: str, token: str | None, *, method: str = "GET") -> int:
    """The HTTP status of one request, with 401 reported rather than raised.

    An MCP handshake would be a heavier way to ask the same question and
    would blur the answer: a 401 from the auth middleware and a protocol
    error from the layer behind it look nothing alike, and only the first one
    is what this is testing.
    """
    url = BASE + path
    req = urllib.request.Request(url, method=method,
                                 data=b"" if method == "POST" else None)
    if token:
        # Both transports at once, on purpose. `?token=` is accepted only by
        # the MCP surfaces and `Authorization` by all of them, so sending one
        # form would make "/api refuses a bank token" pass because /api
        # ignores query tokens — true, but not the thing being tested. With
        # both present, every check is about the credential.
        req.add_header("Authorization", f"Bearer {token}")
        url += ("&" if "?" in path else "?") + f"token={token}"
        req.full_url = url
    # The MCP transport rejects a request without these long before auth is
    # the reason — and a 406 read as "not 401" would make the whole matrix
    # pass for the wrong reason.
    req.add_header("Accept", "application/json, text/event-stream")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except urllib.error.URLError:
        return 0


def _body(path: str, token: str | None, *, method: str = "GET") -> str:
    """The error body of a refused request.

    Asserted on, not just the status code: a 401 whose message sends the
    reader after the wrong problem is a 401 that costs an hour.
    """
    url = BASE + path
    req = urllib.request.Request(url, method=method,
                                 data=b"" if method == "POST" else None)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
        req.full_url = url + ("&" if "?" in path else "?") + f"token={token}"
    req.add_header("Accept", "application/json, text/event-stream")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        return str(exc)


_ADMIN_TOOLS = {"banks", "bank_add", "bank_remove", "bank_state",
                "reindex", "status", "logs"}


async def main() -> int:
    token = _token()
    if not token:
        print(f"SKIP  no API token ({_token_file}); is the engine installed?")
        return 2
    banks = _banks(token)
    bank = _pick_bank(banks)
    if not bank:
        print("SKIP  backend unreachable, or no ready bank with an index")
        return 2
    print(f"bank under test: {bank}\n")

    ids = {b["name"]: b["id"] for b in banks}
    mine = _bank_token(ids[bank], token)
    # The connection is opened with the BANK's token — the service token no
    # longer opens this face at all, so there is no way to run these checks
    # with the wrong credential and not notice.
    url = f"{BASE}/mcp?token={mine}"
    query = "як деплоїмо на прод і робимо rollback"
    plain_tools: set[str] = set()

    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            listed = (await session.list_tools()).tools
            plain_tools = {t.name for t in listed}
            # EXACTLY these two. A superset check would have let `reindex`
            # sit on a project face forever — the thing this split removed.
            check("the plain face exposes exactly search and tree",
                  plain_tools == {"search", "tree"},
                  detail=str(sorted(plain_tools)))
            check("write is NOT exposed (decision #6)",
                  "write" not in plain_tools, detail=str(sorted(plain_tools)))

            # The bank comes from the token and nowhere else. While `bank` was
            # a tool argument, a connection pinned to one bank could be sent to
            # another by passing bank="odin-crm" — measured, not hypothetical —
            # which defeats per-connection addressing and, with per-bank
            # tokens, would make one bank's token a key to any bank.
            for tool in listed:
                # `input_schema` in SDK 2.0, `inputSchema` in 1.x. Only the
                # Python attribute moved — the field still serialises under
                # its old name through a pydantic alias, so the wire, and
                # therefore every client, is unaffected.
                props = (tool.input_schema or {}).get("properties", {})
                check(f"`{tool.name}` takes no bank argument",
                      "bank" not in props, detail=str(sorted(props)))

            search_text = _text(await session.call_tool(
                "search", {"query": query, "top_k": 2}))
            # Two things at once, and the second is the point of the whole
            # redesign: the status header lets an agent tell "nothing indexed"
            # from "no match" without a second call (§10.2), and the bank it
            # names is the one this TOKEN owns — nothing in the URL said so.
            check("search answers with a status header naming the token's bank",
                  search_text.startswith(f"[mnemo · bank={bank} ·"),
                  detail=search_text[:120])

            tree_text = _text(await session.call_tool(
                "tree", {"depth": 1}))
            check("tree answers with a file count",
                  tree_text.startswith(f"[mnemo · bank={bank} ·")
                  and "files]" in tree_text.splitlines()[0],
                  detail=tree_text[:120])

    # ------------------------------------------------------- the admin face
    async with streamable_http_client(f"{BASE}/mcp-admin?token={token}") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            admin_tools = {t.name for t in (await session.list_tools()).tools}
            check("the admin face exposes exactly the admin tools",
                  admin_tools == _ADMIN_TOOLS, detail=str(sorted(admin_tools)))
            # Disjoint in both directions. One FastMCP instance filtered per
            # request would have shown every client every tool: a tool list is
            # cached at handshake, so "declared but refused later" is worse
            # than not declaring it.
            check("no admin tool leaks onto the plain face",
                  not (admin_tools & plain_tools),
                  detail=str(sorted(admin_tools & plain_tools)))

            listed_text = _text(await session.call_tool("banks", {}))
            check("admin `banks` answers with a bank count",
                  listed_text.startswith("[mnemo · ")
                  and "bank(s)]" in listed_text.splitlines()[0],
                  detail=listed_text[:120])

            # Every read-only admin tool actually runs. This is not padding:
            # these bodies call FastAPI endpoint functions as plain Python,
            # so a parameter left to its `Query(...)` default reaches the
            # journal as a descriptor and raises — which is exactly how
            # `logs` was broken when it was written, and it looked fine until
            # something called it. A tool that returns a formatted error is
            # still a failure here; the text has to start with the tool's own
            # header, not with `[mnemo] TypeError`.
            for name, args, head in (
                ("status", {}, "[mnemo 3."),
                ("logs", {"kind": "index", "n": 3}, "[mnemo · index ·"),
                ("logs", {"kind": "query", "n": 3}, "[mnemo · query ·"),
            ):
                out = _text(await session.call_tool(name, args))
                check(f"admin `{name}` {args} answers cleanly",
                      out.startswith(head), detail=out[:160])

            # `bank_state` round-trips on a real bank: freeze it, read the
            # state back out of `banks`, then put it back. Freezing is the
            # safe half of the pair — a frozen bank stays searchable, so a
            # failure between these two lines cannot blind anything.
            froze = _text(await session.call_tool(
                "bank_state", {"bank": bank, "state": "frozen"}))
            listed_frozen = _text(await session.call_tool("banks", {}))
            restored = _text(await session.call_tool(
                "bank_state", {"bank": bank, "state": "enabled"}))
            check("admin `bank_state` freezes and says what that means",
                  "is now frozen" in froze and "still searchable" in froze,
                  detail=froze[:160])
            check("and the bank listing shows the new state",
                  "[frozen]" in listed_frozen, detail=listed_frozen[:200])
            check("and it goes back to enabled",
                  "is now enabled" in restored, detail=restored[:160])

            refused = _text(await session.call_tool(
                "bank_state", {"bank": bank, "state": "nonsense"}))
            check("an unknown state is refused as readable text",
                  "bad_request" in refused, detail=refused[:160])

            # A domain problem is TEXT the agent can act on, never a raised
            # exception that ends its turn.
            missing = _text(await session.call_tool(
                "bank_remove", {"ref": "no-such-bank-xyz"}))
            check("an unknown bank comes back as readable text",
                  "no bank matches" in missing
                  and "Registered banks:" in missing,
                  detail=missing[:160])

    # -------------------------------------------------------- the auth matrix
    #
    # Each surface takes exactly ONE kind of credential now — there is no face
    # that accepts two. `/mcp` in particular no longer opens for the service
    # token: the token IS the address, so a service token there resolves to no
    # bank, and accepting it could only mean guessing which one.
    check("a bank token opens the plain face",
          _status("/mcp", mine, method="POST") != 401)
    check("the SERVICE token does NOT open the plain face",
          _status("/mcp", token, method="POST") == 401)
    check("no token at all is refused on the plain face",
          _status("/mcp", None, method="POST") == 401)
    check("a bank token is refused on the admin face",
          _status("/mcp-admin", mine, method="POST") == 401)
    check("the service token opens the admin face",
          _status("/mcp-admin", token, method="POST") != 401)
    check("no token at all is refused on the admin face",
          _status("/mcp-admin", None, method="POST") == 401)

    # The 401 has to be actionable. The likeliest holder of a token rejected
    # here is someone presenting the service token, which is a perfectly good
    # credential on three other surfaces — "missing or invalid API token"
    # would send them hunting for a credential that is not the problem.
    body = _body("/mcp", token, method="POST")
    check("the plain-face 401 says a bank token is required",
          "bank token" in body, detail=body[:200])
    check("the plain-face 401 says where the service token belongs",
          "/mcp-admin" in body and "/mcp-tools" in body, detail=body[:200])

    # No path segment is accepted. Swallowing one would leave a path component
    # that does not mean what it says; 404-ing it bare would read as a broken
    # handshake. It is refused with the fix in the message — and refused
    # BEFORE auth, so a stale config carrying a *valid* token is not told it
    # has a credential problem.
    seg = _status("/mcp/anything", mine, method="POST")
    check("a leftover /mcp/<bank> segment is refused", seg == 400,
          detail=str(seg))
    seg_body = _body("/mcp/anything", mine, method="POST")
    check("the segment refusal names the fix",
          "no path segment" in seg_body and "init --migrate" in seg_body,
          detail=seg_body[:200])
    check("the segment is refused even with no credential at all",
          _status("/mcp/anything", None, method="POST") == 400)

    # A route that does not exist answers with an EMPTY body, and the reason
    # is a real bug that cost three sessions. A rejected MCP client starts
    # OAuth discovery against `/.well-known/oauth-*`; RFC 6749 says that error
    # body is `{"error": "<string>"}`, and ours made `error` an object, so the
    # client's schema check failed and it reported "404 Not Found" — hiding
    # the 401 that actually explained the stale token (`search-quality.md` A6).
    for probe in ("/.well-known/oauth-protected-resource",
                  "/.well-known/oauth-authorization-server",
                  "/no-such-route"):
        check(f"{probe} is 404", _status(probe, None) == 404)
        check(f"{probe} carries NO envelope", _body(probe, None) == "",
              detail=_body(probe, None)[:120])

    # ...while everything that names something the caller did keeps it. The
    # split is "route does not exist" vs "your request was wrong", not
    # "404 vs the rest": a domain 404 is still an answer about a bank.
    missing_bank = _body(f"/api/banks/no-such-bank-xyz", token)
    check("a DOMAIN 404 still carries the envelope",
          '"bank_not_found"' in missing_bank, detail=missing_bank[:160])
    check("a 405 still carries the envelope",
          '"error"' in _body("/api/status", token, method="DELETE"),
          detail=_body("/api/status", token, method="DELETE")[:160])

    others = [name for name in ids if name != bank]
    if others:
        other = others[0]
        theirs = _bank_token(ids[other], token)
        # Both tokens are now valid on the same URL — the URL says nothing.
        # So this is the check that the token, and only the token, decides
        # which bank answers.
        async with streamable_http_client(f"{BASE}/mcp?token={theirs}") as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                reached = _text(await session.call_tool("tree", {"depth": 0}))
        check(f"bank {other!r}'s token reaches {other!r}, not {bank!r}",
              reached.startswith(f"[mnemo · bank={other} ·"),
              detail=reached[:120])
    else:
        print("SKIP  cross-bank token check: only one bank is registered")

    # A bank token opens the READ face and nothing wider: the mirror and the
    # private API take the service token, which already reaches every bank.
    check("a bank token is refused on /mcp-tools",
          _status("/mcp-tools/tree", mine) == 401)
    check("a bank token is refused on /api",
          _status("/api/banks", mine) == 401)

    # The mirror is only useful if it cannot disagree. Same query, same
    # parameters, compared as bytes — this is the check that fails if either
    # side ever grows its own formatting.
    q = urllib.parse.quote(query)
    b = urllib.parse.quote(bank, safe="")
    mirror_text = _get(
        f"/mcp-tools/search?bank={b}&query={q}&top_k=2", token)
    check("mirror /mcp-tools/search is byte-identical to the tool",
          mirror_text == search_text,
          detail=f"mcp={len(search_text)}b mirror={len(mirror_text)}b")

    mirror_tree = _get(f"/mcp-tools/tree?bank={b}&depth=1", token)
    check("mirror /mcp-tools/tree is byte-identical to the tool",
          mirror_tree == tree_text,
          detail=f"mcp={len(tree_text)}b mirror={len(mirror_tree)}b")

    envelope = json.loads(_get(
        f"/mcp-tools/search?bank={b}&query={q}&top_k=2&format=json",
        token))
    check("?format=json wraps the same string, does not reshape it",
          envelope.get("text") == mirror_text
          and envelope.get("tool") == "search",
          detail=str(sorted(envelope)))

    # The private surface must stay private: no /api route in the schema.
    schema = json.loads(_get("/openapi.json", token))
    api_paths = [p for p in schema.get("paths", {}) if p.startswith("/api")]
    check("no /api route is published in OpenAPI", not api_paths,
          detail=str(api_paths))
    check("OpenAPI declares a security scheme (Authorize works in /docs)",
          bool(schema.get("components", {}).get("securitySchemes")),
          detail=str(schema.get("components", {}).keys()))

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
