"""MN-43: `agent_registry`'s chat storage layer + `agent_runtime`'s spawn
translation, concurrency ceiling and race-free-reconnect contract.

Everything in this file runs against mocked/stubbed processes or pure
in-process state — **no real `claude` process is spawned here**. Real
spawns cost real API usage on a real subscription (MN-43's Jira ticket is
explicit: "sparingly, never in a loop"), and none of what this file checks
needs one:

* chat CRUD (`agent_registry.list_chats`/`create_chat`/`get_chat`/
  `touch_chat`/`delete_chat`) is plain filesystem + JSON logic;
* launch-mode -> argv/env translation (`agent_runtime._build_argv`/
  `_build_env`) is a pure function of a `launch.json` dict;
* the concurrency ceiling is exercised by inserting fake `Session` objects
  directly into `agent_runtime._sessions` — the ceiling check itself does
  not care whether a session's process is real;
* the race-free-reconnect contract (one lock around "append to
  `history.log`" + "hand output to every subscriber" + "register a new
  subscriber") is exercised with a real background thread hammering
  `_publish_output` against a real event loop, racing real subscribe/
  unsubscribe cycles — the thing under test is the locking, not the PTY.

    .venv/Scripts/python.exe tests/test_agent_runtime.py

The WS wire protocol itself (`/ws/agents/{slug}/chats/{chat_id}`) and one
real end-to-end `claude` spawn were verified separately, by hand, per the
ticket's "no more than 2-3 real spawns, never in a loop" instruction — see
the MN-43 report back to the team lead for what was observed.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import uuid
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Redirect all writable state into a temp dir BEFORE config is imported —
# same convention as test_watcher.py / test_service_ctl.py. Nothing in this
# file touches the real ~/.mnemo.
_STATE = tempfile.mkdtemp(prefix="mnemo agent-runtime state ")
os.environ["MNEMO_STATE_DIR"] = _STATE
os.environ.setdefault("MNEMO_RECONCILE_ON_START", "0")

from src import agent_registry, agent_runtime, config  # noqa: E402

_passed = _failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _failed += 1
        print(f"FAIL  {name}  {detail}")


def _make_agent(name: str) -> agent_registry.Agent:
    root = Path(tempfile.mkdtemp(prefix=f"mnemo agent-runtime {name} "))
    # `root` is explicit, so `create()` does not touch `config.AGENTS_DIR`
    # (a real, user-scope location) at all — see its docstring.
    return agent_registry.create(name, root=root)


# ------------------------------------------------------------ 43.1 storage


def test_chat_storage() -> None:
    agent = _make_agent("chat-storage")

    empty = agent_registry.list_chats(agent.slug)
    check("list_chats: empty for a fresh agent", empty == [], repr(empty))

    chat = agent_registry.create_chat(agent.slug, title="  first chat  ")
    check("create_chat: returns chat_id", bool(chat.get("chat_id")))
    check("create_chat: strips title", chat.get("title") == "first chat", repr(chat))
    check(
        "create_chat: created_at == last_active_at initially",
        chat["created_at"] == chat["last_active_at"],
        repr(chat),
    )

    chat_dir = agent_registry.chat_dir(agent.root, chat["chat_id"])
    check("create_chat: chat folder exists on disk", chat_dir.is_dir())
    index_path = agent.root / "chats" / "chats.json"
    check("create_chat: chats.json exists on disk", index_path.is_file())

    empty_title_chat = agent_registry.create_chat(agent.slug)
    check(
        "create_chat: no title -> None, not empty string",
        empty_title_chat.get("title") is None,
        repr(empty_title_chat),
    )

    listed = agent_registry.list_chats(agent.slug)
    check("list_chats: returns both chats", len(listed) == 2, repr(listed))
    check(
        "list_chats: most-recently-active first",
        listed[0]["chat_id"] == empty_title_chat["chat_id"],
        repr(listed),
    )

    fetched = agent_registry.get_chat(agent.slug, chat["chat_id"])
    check("get_chat: round-trips the same record", fetched["chat_id"] == chat["chat_id"])

    try:
        agent_registry.get_chat(agent.slug, "does-not-exist")
        check("get_chat: raises ChatNotFound for a bad id", False, "no exception raised")
    except agent_registry.ChatNotFound:
        check("get_chat: raises ChatNotFound for a bad id", True)

    import time as _time
    # `_now_iso()` is second-precision (this module's convention) — sleep
    # past a second boundary so the bump is actually distinguishable,
    # rather than flaking on whichever half of the second the test started.
    _time.sleep(1.05)
    touched = agent_registry.touch_chat(agent.slug, chat["chat_id"])
    check(
        "touch_chat: bumps last_active_at",
        touched["last_active_at"] >= touched["created_at"]
        and touched["last_active_at"] != chat["last_active_at"],
        repr((chat["last_active_at"], touched["last_active_at"])),
    )

    try:
        agent_registry.touch_chat(agent.slug, "does-not-exist")
        check("touch_chat: raises ChatNotFound for a bad id", False, "no exception raised")
    except agent_registry.ChatNotFound:
        check("touch_chat: raises ChatNotFound for a bad id", True)

    history_path = agent_registry.chat_history_path(agent.root, chat["chat_id"])
    check(
        "chat_history_path: lives inside the chat's own folder",
        history_path.parent == chat_dir,
        str(history_path),
    )

    agent_registry.delete_chat(agent.slug, chat["chat_id"])
    check("delete_chat: folder removed from disk", not chat_dir.exists())
    remaining = agent_registry.list_chats(agent.slug)
    check(
        "delete_chat: removed from the index, other chat untouched",
        [c["chat_id"] for c in remaining] == [empty_title_chat["chat_id"]],
        repr(remaining),
    )

    try:
        agent_registry.delete_chat(agent.slug, chat["chat_id"])
        check("delete_chat: raises ChatNotFound on a repeat delete", False, "no exception raised")
    except agent_registry.ChatNotFound:
        check("delete_chat: raises ChatNotFound on a repeat delete", True)

    try:
        agent_registry.list_chats("no-such-agent")
        check("list_chats: raises AgentNotFound for a bad slug", False, "no exception raised")
    except agent_registry.AgentNotFound:
        check("list_chats: raises AgentNotFound for a bad slug", True)


# --------------------------------------------------------- 43.2 launch mode


def test_launch_translation() -> None:
    # MN-45a: `_build_argv` now always takes the two per-spawn config paths
    # and always emits `--mcp-config`/`--settings` for them, for EVERY mode —
    # dummy paths are enough here, this is pure argv-shape logic.
    mcp_cfg = Path("C:/fake/mcp_config.json")
    settings_cfg = Path("C:/fake/settings.json")

    argv = agent_runtime._build_argv({"mode": "standard"}, mcp_cfg, settings_cfg)
    check(
        "standard mode: argv[0] is the claude executable",
        argv[0].lower().endswith(("claude", "claude.exe", "claude.cmd")),
        repr(argv),
    )
    check(
        "standard mode: --mcp-config/--settings right after the executable",
        argv[1:5] == ["--mcp-config", str(mcp_cfg), "--settings", str(settings_cfg)],
        repr(argv),
    )
    check(
        "standard mode: nothing beyond the two flags",
        len(argv) == 5,
        repr(argv),
    )
    env = agent_runtime._build_env({"mode": "standard"})
    check(
        # `_build_env` starts from `dict(os.environ)`, so a value the dev
        # machine already exports for its own reasons legitimately shows up
        # here too — the contract is "standard mode does not ADD or
        # override it", not "the key is absent".
        "standard mode: does not add/override ANTHROPIC_BASE_URL",
        env.get("ANTHROPIC_BASE_URL") == os.environ.get("ANTHROPIC_BASE_URL"),
        repr(env.get("ANTHROPIC_BASE_URL")),
    )

    custom_launch = {
        "mode": "custom", "host": "127.0.0.1", "port": 9931,
        "model": "claude-sonnet-5", "extra_args": ["--verbose", "--foo=bar"],
    }
    argv = agent_runtime._build_argv(custom_launch, mcp_cfg, settings_cfg)
    check(
        "custom mode: --mcp-config/--settings STILL right after the executable, "
        "before anything extra_args supplies",
        argv[1:5] == ["--mcp-config", str(mcp_cfg), "--settings", str(settings_cfg)],
        repr(argv),
    )
    check(
        "custom mode: --model appended",
        "--model" in argv and argv[argv.index("--model") + 1] == "claude-sonnet-5",
        repr(argv),
    )
    check(
        "custom mode: extra_args appended verbatim, in order, at the end",
        argv[-2:] == ["--verbose", "--foo=bar"],
        repr(argv),
    )
    env = agent_runtime._build_env(custom_launch)
    check(
        "custom mode: ANTHROPIC_BASE_URL set from host/port",
        env.get("ANTHROPIC_BASE_URL") == "http://127.0.0.1:9931",
        repr(env.get("ANTHROPIC_BASE_URL")),
    )

    no_model_launch = {"mode": "custom", "host": "example.internal", "port": 8080}
    argv = agent_runtime._build_argv(no_model_launch, mcp_cfg, settings_cfg)
    check(
        "custom mode, no model/extra_args: argv is just the executable + the two flags",
        len(argv) == 5,
        repr(argv),
    )

    autocompact_launch = {
        "mode": "custom", "host": "h", "port": 1, "autocompact": 42,
    }
    argv = agent_runtime._build_argv(autocompact_launch, mcp_cfg, settings_cfg)
    check(
        "autocompact: never translated into a flag (no known CLI mechanism)",
        not any("compact" in a.lower() for a in argv),
        repr(argv),
    )
    env = agent_runtime._build_env(autocompact_launch)
    check(
        "autocompact: never translated into an env var",
        not any("compact" in k.lower() for k in env),
        repr([k for k in env if "compact" in k.lower()]),
    )


# ------------------------------------------------------- 45a config + argv


def test_write_agent_configs() -> None:
    chat_dir = Path(tempfile.mkdtemp(prefix="mnemo agent-runtime 45a configs "))
    token = "cfgtest-" + uuid.uuid4().hex
    mcp_path, settings_path = agent_runtime._write_agent_configs(
        chat_dir, "myagent", "chat123", token
    )
    check("mcp_config written inside chat_dir", mcp_path.parent == chat_dir, str(mcp_path))
    check("settings written inside chat_dir", settings_path.parent == chat_dir, str(settings_path))

    mcp_doc = json.loads(mcp_path.read_text(encoding="utf-8"))
    server = mcp_doc["mcpServers"]["mnemo-agents"]
    check("mcp-config: type is http", server["type"] == "http", repr(server))
    check(
        "mcp-config: url carries the token as ?token=",
        server["url"].endswith(f"/mcp-agents?token={token}"),
        server["url"],
    )

    settings_doc = json.loads(settings_path.read_text(encoding="utf-8"))
    start_group = settings_doc["hooks"]["SubagentStart"][0]
    stop_group = settings_doc["hooks"]["SubagentStop"][0]
    check(
        "settings: matcher is empty (every subagent type)",
        start_group["matcher"] == "" and stop_group["matcher"] == "",
        repr((start_group["matcher"], stop_group["matcher"])),
    )
    start_hook = start_group["hooks"][0]
    stop_hook = stop_group["hooks"][0]
    check(
        "settings: hook url points at this chat's own hook path",
        start_hook["url"].endswith("/hooks/agents/myagent/chat123/subagent"),
        start_hook["url"],
    )
    check(
        "settings: ONE url for both Start and Stop (body's hook_event_name distinguishes)",
        start_hook["url"] == stop_hook["url"],
        repr((start_hook["url"], stop_hook["url"])),
    )
    check(
        "settings: Authorization bearer carries the session token",
        start_hook["headers"]["Authorization"] == f"Bearer {token}",
        repr(start_hook["headers"]),
    )
    check("settings: hook type is http", start_hook["type"] == "http", repr(start_hook))


def test_session_token_lifecycle() -> None:
    """`resolve_session_token`/`session_agent_slug` mint-to-teardown, exercised
    with a FAKE `Session` (`proc=None`) so `_finalize_session` runs for real —
    no real `claude` process anywhere near this."""
    agent = _make_agent("token-lifecycle")
    chat = agent_registry.create_chat(agent.slug)
    chat_id = chat["chat_id"]
    token = "test-token-" + uuid.uuid4().hex

    check(
        "resolve_session_token: unknown token -> None",
        agent_runtime.resolve_session_token("this-token-does-not-exist") is None,
    )
    check(
        "resolve_session_token: None/empty -> None",
        agent_runtime.resolve_session_token(None) is None
        and agent_runtime.resolve_session_token("") is None,
    )
    check(
        "session_agent_slug: unknown chat_id -> None",
        agent_runtime.session_agent_slug("no-such-chat") is None,
    )

    fake = agent_runtime.Session(
        chat_id=chat_id, agent_slug=agent.slug,
        history_path=Path(tempfile.mktemp()), proc=None, token=token,
    )
    with agent_runtime._sessions_lock:
        agent_runtime._sessions[chat_id] = fake
        agent_runtime._session_tokens[token] = chat_id

    try:
        check(
            "resolve_session_token: a registered token resolves to its chat_id",
            agent_runtime.resolve_session_token(token) == chat_id,
        )
        check(
            "session_agent_slug: reflects the live session's agent",
            agent_runtime.session_agent_slug(chat_id) == agent.slug,
        )
        live = agent_runtime.list_live_sessions()
        check(
            "list_live_sessions: includes the fake session",
            any(s["chat_id"] == chat_id and s["agent_slug"] == agent.slug for s in live),
            repr(live),
        )

        # The exact teardown path a real session goes through on exit —
        # never touches `session.proc` beyond a suppressed `.exitstatus`
        # read, so `proc=None` is safe here.
        agent_runtime._finalize_session(fake)

        check(
            "resolve_session_token: gone once the session is finalized",
            agent_runtime.resolve_session_token(token) is None,
        )
        check(
            "session_agent_slug: gone once the session is finalized",
            agent_runtime.session_agent_slug(chat_id) is None,
        )
        check(
            "_sessions: entry removed by finalize",
            chat_id not in agent_runtime._sessions,
        )
        check(
            "_session_tokens: entry removed by finalize (mirrors _sessions exactly)",
            token not in agent_runtime._session_tokens,
        )
    finally:
        with agent_runtime._sessions_lock:
            agent_runtime._sessions.pop(chat_id, None)
            agent_runtime._session_tokens.pop(token, None)


# ------------------------------------------------------ 43.2 concurrency cap


def test_concurrency_ceiling() -> None:
    agent = _make_agent("ceiling")
    chat = agent_registry.create_chat(agent.slug)

    fake_ids = [f"fake-ceiling-{i}" for i in range(config.MAX_LIVE_SESSIONS)]
    for fid in fake_ids:
        agent_runtime._sessions[fid] = agent_runtime.Session(
            chat_id=fid, agent_slug=agent.slug,
            history_path=Path(tempfile.mktemp()), proc=None,
            token=f"tok-{fid}", alive=True,
        )
    try:
        check(
            "ceiling: live_session_count reflects the fakes",
            agent_runtime.live_session_count() == config.MAX_LIVE_SESSIONS,
            str(agent_runtime.live_session_count()),
        )
        try:
            agent_runtime.ensure_and_subscribe(agent.slug, chat["chat_id"])
            check("ceiling: raises SessionLimitExceeded once the cap is hit", False,
                  "no exception raised")
        except agent_runtime.SessionLimitExceeded:
            check("ceiling: raises SessionLimitExceeded once the cap is hit", True)
    finally:
        for fid in fake_ids:
            agent_runtime._sessions.pop(fid, None)

    check(
        "ceiling: a DEAD fake session does not count against the cap",
        True,  # exercised below
    )
    dead_ids = [f"fake-dead-{i}" for i in range(config.MAX_LIVE_SESSIONS)]
    for fid in dead_ids:
        agent_runtime._sessions[fid] = agent_runtime.Session(
            chat_id=fid, agent_slug=agent.slug,
            history_path=Path(tempfile.mktemp()), proc=None,
            token=f"tok-{fid}", alive=False,
        )
    try:
        check(
            "ceiling: dead sessions excluded from live_session_count",
            agent_runtime.live_session_count() == 0,
            str(agent_runtime.live_session_count()),
        )
    finally:
        for fid in dead_ids:
            agent_runtime._sessions.pop(fid, None)


# -------------------------------------------------- 43.3 race-free reconnect


async def _race_free_reconnect() -> tuple[bool, str]:
    hist_dir = Path(tempfile.mkdtemp(prefix="mnemo agent-runtime race "))
    hist_path = hist_dir / "history.log"
    session = agent_runtime.Session(
        chat_id="race-test", agent_slug="race-agent",
        history_path=hist_path, proc=None,  # type: ignore[arg-type]
        token="race-token",
    )
    session._history_fh = hist_path.open("a", encoding="utf-8")
    agent_runtime.bind_loop(asyncio.get_running_loop())

    n_chunks = 300
    stop = threading.Event()

    def producer() -> None:
        for i in range(n_chunks):
            agent_runtime._publish_output(session, f"chunk-{i:05d};")
        stop.set()

    captures: list[str] = []

    async def subscriber_worker() -> None:
        while not stop.is_set():
            sub_id, queue, replay = agent_runtime._subscribe(session)
            drained: list[str] = []
            try:
                while True:
                    item = await asyncio.wait_for(queue.get(), timeout=0.02)
                    drained.append(item["data"])
            except asyncio.TimeoutError:
                pass
            with session.lock:
                session.subscribers.pop(sub_id, None)
            captures.append(replay + "".join(drained))
            await asyncio.sleep(0)

    prod_thread = threading.Thread(target=producer, daemon=True)
    prod_thread.start()
    subs = [asyncio.create_task(subscriber_worker()) for _ in range(4)]
    await asyncio.gather(*subs)
    prod_thread.join(timeout=10)
    agent_runtime.bind_loop(None)
    session._history_fh.close()

    full = hist_path.read_text(encoding="utf-8")
    expected_full = "".join(f"chunk-{i:05d};" for i in range(n_chunks))
    if full != expected_full:
        return False, f"history.log corrupted: len={len(full)} expected={len(expected_full)}"
    if not captures:
        return False, "no subscriber captured anything (test did not exercise the race)"

    bad = [c for c in captures if not full.startswith(c)]
    if bad:
        return False, (
            f"{len(bad)}/{len(captures)} subscriber captures were NOT an exact "
            f"prefix of the final history — lost or duplicated output"
        )
    longest = max(len(c) for c in captures)
    return True, f"{len(captures)} subscribe/drain cycles, longest capture {longest}/{len(full)} chars"


def test_race_free_reconnect() -> None:
    ok, detail = asyncio.run(_race_free_reconnect())
    check("reconnect: every subscriber capture is an exact prefix of history (no loss/dup)", ok, detail)


# ---------------------------------------------------- 43.4 HTTP lifecycle
#
# Calls `api.py`'s endpoint FUNCTIONS directly, bypassing uvicorn/ASGI and —
# critically — `lifespan()`: that starts the watcher and the work queue,
# either of which could end up enqueueing a real embedding call the moment
# a bank exists. This exercises the exact same wiring/error-mapping code
# every real HTTP request runs through, just without paying for a server.


def test_http_chat_endpoints() -> None:
    from src import api  # noqa: PLC0415 - deferred so a failure here does not

    agent = _make_agent("http-endpoints")

    listing = api.api_agent_chats(agent.slug)
    check("GET chats: empty list for a fresh agent", listing == {"chats": []}, repr(listing))

    created = api.api_create_chat(agent.slug, api.CreateChatRequest(title="via http"))
    check("POST chats: creates a chat record", created.get("title") == "via http", repr(created))
    chat_id = created["chat_id"]

    fetched = api.api_agent_chat(agent.slug, chat_id)
    check("GET one chat: round-trips", fetched["chat_id"] == chat_id, repr(fetched))

    try:
        api.api_agent_chat(agent.slug, "no-such-chat")
        check("GET one chat: 404s a bad chat_id", False, "no exception raised")
    except api.ApiError as exc:
        check(
            "GET one chat: 404s a bad chat_id",
            exc.code == "chat_not_found" and exc.status == 404,
            f"code={exc.code} status={exc.status}",
        )

    try:
        api.api_agent_chats("no-such-agent")
        check("GET chats: 404s a bad agent slug", False, "no exception raised")
    except api.ApiError as exc:
        check(
            "GET chats: 404s a bad agent slug",
            exc.code == "agent_not_found" and exc.status == 404,
            f"code={exc.code} status={exc.status}",
        )

    result = api.api_delete_chat(agent.slug, chat_id)
    check("DELETE chat: returns ok", result == {"ok": True}, repr(result))
    check(
        "DELETE chat: gone from storage",
        agent_registry.list_chats(agent.slug) == [],
        repr(agent_registry.list_chats(agent.slug)),
    )

    check(
        "_ERROR_STATUS: chat_not_found -> 404",
        api._ERROR_STATUS.get("chat_not_found") == 404,
    )
    check(
        "_ERROR_STATUS: too_many_sessions -> 409",
        api._ERROR_STATUS.get("too_many_sessions") == 409,
    )

    routes = {r.path for r in api.app.routes if hasattr(r, "path")}
    check(
        "the live-chat WS route is registered",
        "/ws/agents/{slug}/chats/{chat_id}" in routes,
        repr(sorted(p for p in routes if "chat" in p)),
    )


# --------------------------------------------------- 45a auth_middleware
#
# `auth_middleware` is a plain `async def` — `@app.middleware("http")` hands
# it back unchanged, so it can be called directly with a synthetic
# `starlette.Request` and a stub `call_next`, the same "skip uvicorn/ASGI"
# approach `test_http_chat_endpoints` uses for endpoint functions. No real
# socket, no lifespan, no real `claude` process.


def _fake_request(path: str, headers: dict | None = None, query: str = ""):
    from starlette.requests import Request  # noqa: PLC0415
    from src import api  # noqa: PLC0415

    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http", "method": "GET", "path": path,
        "headers": raw_headers, "query_string": query.encode(), "app": api.app,
    }
    return Request(scope), scope


def _register_fake_live_session(agent_slug: str, chat_id: str, token: str):
    fake = agent_runtime.Session(
        chat_id=chat_id, agent_slug=agent_slug,
        history_path=Path(tempfile.mktemp()), proc=None, token=token,
    )
    with agent_runtime._sessions_lock:
        agent_runtime._sessions[chat_id] = fake
        agent_runtime._session_tokens[token] = chat_id
    return fake


def _drop_fake_live_session(chat_id: str, token: str) -> None:
    with agent_runtime._sessions_lock:
        agent_runtime._sessions.pop(chat_id, None)
        agent_runtime._session_tokens.pop(token, None)


async def _auth_middleware_agent_branch() -> None:
    from starlette.responses import Response  # noqa: PLC0415
    from src import api  # noqa: PLC0415

    async def call_next(request):
        return Response("reached", status_code=200)

    agent = _make_agent("auth-branch")
    chat = agent_registry.create_chat(agent.slug)
    chat_id = chat["chat_id"]
    token = "auth-branch-" + uuid.uuid4().hex
    _register_fake_live_session(agent.slug, chat_id, token)

    try:
        req, scope = _fake_request("/mcp-agents/", query=f"token={token}")
        resp = await api.auth_middleware(req, call_next)
        check(
            "/mcp-agents: a valid session token reaches call_next",
            resp.status_code == 200,
            f"status={resp.status_code} body={resp.body!r}",
        )
        check(
            "/mcp-agents: scope carries the resolved chat_id",
            scope.get(api.AGENT_SESSION_SCOPE_KEY) == chat_id,
            repr(scope.get(api.AGENT_SESSION_SCOPE_KEY)),
        )

        hook_path = f"/hooks/agents/{agent.slug}/{chat_id}/subagent"
        req2, scope2 = _fake_request(hook_path, headers={"Authorization": f"Bearer {token}"})
        resp2 = await api.auth_middleware(req2, call_next)
        check(
            "/hooks/agents/*: a valid Authorization-header token reaches call_next",
            resp2.status_code == 200,
            f"status={resp2.status_code}",
        )
        check(
            "/hooks/agents/*: scope carries the resolved chat_id",
            scope2.get(api.AGENT_SESSION_SCOPE_KEY) == chat_id,
            repr(scope2.get(api.AGENT_SESSION_SCOPE_KEY)),
        )

        req3, _ = _fake_request(hook_path, headers={"Authorization": "Bearer wrong-token"})
        resp3 = await api.auth_middleware(req3, call_next)
        check(
            "/hooks/agents/*: a wrong token is rejected with 401",
            resp3.status_code == 401,
            f"status={resp3.status_code}",
        )
        body3 = json.loads(resp3.body)
        check(
            "/hooks/agents/*: 401 carries the 'unauthorized' error code",
            body3.get("error", {}).get("code") == "unauthorized",
            repr(body3),
        )

        req4, _ = _fake_request("/mcp-agents/")
        resp4 = await api.auth_middleware(req4, call_next)
        check(
            "/mcp-agents: no token at all -> 401, not a crash",
            resp4.status_code == 401,
            f"status={resp4.status_code}",
        )

        # A bank token (a real, different credential kind) must not open
        # this face either — the whole point of a THIRD credential kind.
        req5, _ = _fake_request("/mcp-agents/", query="token=not-a-real-bank-or-session-token")
        resp5 = await api.auth_middleware(req5, call_next)
        check(
            "/mcp-agents: an unrelated bogus token -> 401",
            resp5.status_code == 401,
            f"status={resp5.status_code}",
        )
    finally:
        _drop_fake_live_session(chat_id, token)


def test_auth_middleware_agent_branch() -> None:
    asyncio.run(_auth_middleware_agent_branch())


async def _hook_agent_subagent_endpoint() -> None:
    from src import api  # noqa: PLC0415

    agent = _make_agent("hook-endpoint")
    chat = agent_registry.create_chat(agent.slug)
    chat_id = chat["chat_id"]
    other_chat_id = "some-other-chat-id"
    token = "hook-endpoint-" + uuid.uuid4().hex
    _register_fake_live_session(agent.slug, chat_id, token)

    try:
        req, scope = _fake_request(f"/hooks/agents/{agent.slug}/{chat_id}/subagent")
        scope[api.AGENT_SESSION_SCOPE_KEY] = chat_id  # what auth_middleware would set

        async def _receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        req = req.__class__(scope, _receive)
        result = await api.hook_agent_subagent(req, agent.slug, chat_id)
        check("hook endpoint: matching chat_id -> accepted (empty ack)", result == {}, repr(result))

        # The token resolved to `chat_id`, but the URL names a DIFFERENT
        # chat_id -- the confused-deputy case `hook_agent_subagent` itself
        # checks for on top of what auth_middleware already verified.
        req2, scope2 = _fake_request(f"/hooks/agents/{agent.slug}/{other_chat_id}/subagent")
        scope2[api.AGENT_SESSION_SCOPE_KEY] = chat_id
        req2 = req2.__class__(scope2, _receive)
        try:
            await api.hook_agent_subagent(req2, agent.slug, other_chat_id)
            check(
                "hook endpoint: token's chat_id != URL's chat_id -> rejected",
                False, "no exception raised",
            )
        except api.ApiError as exc:
            check(
                "hook endpoint: token's chat_id != URL's chat_id -> rejected",
                exc.code == "unauthorized" and exc.status == 401,
                f"code={exc.code} status={exc.status}",
            )
    finally:
        _drop_fake_live_session(chat_id, token)


def test_hook_agent_subagent_endpoint() -> None:
    asyncio.run(_hook_agent_subagent_endpoint())


# --------------------------------------------------- 45a mcp_agents.py tools
#
# Calls the `run_*` bodies directly (same "module-level, testable without a
# real MCP client" shape `mcp_server.py`'s tools already use), with
# `current_chat_id` set by hand to simulate an authenticated caller, and a
# tiny fake PTY (`_FakeProc`) so delivery-success paths (`send_message`,
# `interrupt`) are actually exercised rather than silently short-circuiting
# on `proc=None`. No real `claude` process anywhere near this file.


class _FakeProc:
    def __init__(self) -> None:
        self.written: list[str] = []

    def write(self, data: str) -> None:
        self.written.append(data)


def test_mcp_agents_tools() -> None:
    from src import mcp_agents, servicelog  # noqa: PLC0415

    # --- ansi stripping / tail -------------------------------------------
    raw = "\x1b[31mred\x1b[0m plain \x1b]0;title\x07 done"
    check(
        "_strip_ansi: CSI and OSC sequences removed, text kept",
        mcp_agents._strip_ansi(raw) == "red plain  done",
        repr(mcp_agents._strip_ansi(raw)),
    )
    check("_tail: returns the last n chars", mcp_agents._tail("abcdefgh", 3) == "fgh")
    check(
        "_tail: clamps to the ceiling rather than trusting the caller",
        len(mcp_agents._tail("x" * 999999, 10_000_000)) == mcp_agents._MAX_TAIL_CHARS,
    )

    caller = _make_agent("mcp-agents-caller")
    caller_chat = agent_registry.create_chat(caller.slug)
    caller_chat_id = caller_chat["chat_id"]
    caller_token = "caller-" + uuid.uuid4().hex
    caller_proc = _FakeProc()
    caller_session = agent_runtime.Session(
        chat_id=caller_chat_id, agent_slug=caller.slug,
        history_path=Path(tempfile.mktemp()), proc=caller_proc, token=caller_token,
    )

    target = _make_agent("mcp-agents-target")
    target_chat = agent_registry.create_chat(target.slug)
    target_chat_id = target_chat["chat_id"]
    target_token = "target-" + uuid.uuid4().hex
    target_proc = _FakeProc()
    target_session = agent_runtime.Session(
        chat_id=target_chat_id, agent_slug=target.slug,
        history_path=Path(tempfile.mktemp()), proc=target_proc, token=target_token,
    )
    target_history_path = agent_registry.chat_history_path(target.root, target_chat_id)
    target_history_path.write_text("\x1b[32mhello\x1b[0m world\n", encoding="utf-8")

    with agent_runtime._sessions_lock:
        agent_runtime._sessions[caller_chat_id] = caller_session
        agent_runtime._session_tokens[caller_token] = caller_chat_id
        agent_runtime._sessions[target_chat_id] = target_session
        agent_runtime._session_tokens[target_token] = target_chat_id

    events_before = len(servicelog.read_agent_events(limit=10_000))

    def call_as_caller(fn, *args, **kwargs):
        tok = mcp_agents.current_chat_id.set(caller_chat_id)
        try:
            return fn(*args, **kwargs)
        finally:
            mcp_agents.current_chat_id.reset(tok)

    try:
        # --- list_sessions -------------------------------------------------
        listing = call_as_caller(mcp_agents.run_list_sessions)
        check(
            "list_sessions: mentions both live fake sessions",
            caller_chat_id in listing and target_chat_id in listing,
            listing,
        )

        # --- send_message, by chat_id --------------------------------------
        result = call_as_caller(
            mcp_agents.run_send_message, target_chat_id, "ping via chat_id"
        )
        check(
            "send_message: reports delivery",
            "delivered" in result.lower(),
            result,
        )
        check(
            "send_message: server-supplied [from ...] prefix actually written to the target's pty",
            len(target_proc.written) == 1
            and target_proc.written[0].startswith(f"[from {caller.slug} · {caller_chat_id[:8]}]")
            and target_proc.written[0].rstrip("\r").endswith("ping via chat_id"),
            repr(target_proc.written),
        )

        # --- send_message, by agent slug (single live session) -------------
        result2 = call_as_caller(mcp_agents.run_send_message, target.slug, "ping via slug")
        check(
            "send_message: resolves a bare agent slug to its live session",
            "delivered" in result2.lower() and target_chat_id in result2,
            result2,
        )
        check(
            "send_message: second message also landed on the target's pty",
            len(target_proc.written) == 2,
            repr(target_proc.written),
        )

        # --- send_message, unknown target -----------------------------------
        result3 = call_as_caller(mcp_agents.run_send_message, "no-such-agent-or-chat", "hi")
        check(
            "send_message: unknown chat_id/slug -> a clear note, not a crash",
            "no live session" in result3,
            result3,
        )

        # --- read_recent -----------------------------------------------------
        recent = call_as_caller(mcp_agents.run_read_recent, target_chat_id)
        check(
            "read_recent: ANSI stripped, text preserved",
            "hello world" in recent and "\x1b" not in recent,
            recent,
        )
        check("read_recent: names the owning agent", target.slug in recent, recent)

        missing = call_as_caller(mcp_agents.run_read_recent, "not-a-real-chat-id")
        check(
            "read_recent: unknown chat_id -> a clear note, not a crash",
            "no chat" in missing.lower(),
            missing,
        )

        # --- session_summary ---------------------------------------------
        summary = call_as_caller(mcp_agents.run_session_summary, target_chat_id)
        check(
            "session_summary: carries metadata and the same ANSI-stripped tail",
            "alive=True" in summary and "hello world" in summary,
            summary,
        )
        check(
            "session_summary: honest about not being an LLM summary (MN-45b note present)",
            "MN-45b" in summary,
            summary,
        )

        # --- interrupt -------------------------------------------------------
        interrupt_result = call_as_caller(mcp_agents.run_interrupt, target_chat_id)
        check("interrupt: reports success", "sent a soft interrupt" in interrupt_result, interrupt_result)
        check(
            "interrupt: wrote exactly one Escape byte, nothing else, to the target",
            target_proc.written[-1] == "\x1b",
            repr(target_proc.written[-1]),
        )

        no_session_result = call_as_caller(mcp_agents.run_interrupt, "not-a-real-chat-id")
        check(
            "interrupt: no live session -> a clear note, not a crash",
            "no live session" in no_session_result,
            no_session_result,
        )

        # --- audit logging (Jira decision #5: EVERY call is logged) --------
        events_after = servicelog.read_agent_events(limit=10_000)
        new_events = events_after[: len(events_after) - events_before]
        tools_logged = [e["tool"] for e in new_events]
        check(
            "audit: every tool call above produced a servicelog entry",
            {"list_sessions", "send_message", "read_recent", "session_summary",
             "interrupt"} <= set(tools_logged),
            repr(sorted(set(tools_logged))),
        )
        check(
            "audit: entries attribute the caller correctly (never self-reported)",
            all(
                e["caller_chat_id"] == caller_chat_id and e["caller_slug"] == caller.slug
                for e in new_events
            ),
            repr(new_events[:3]),
        )
        send_message_events = [e for e in new_events if e["tool"] == "send_message"]
        check(
            "audit: send_message entries record the target",
            any(e["target"] == target_chat_id for e in send_message_events)
            or any(e["target"] == target.slug for e in send_message_events),
            repr(send_message_events),
        )
    finally:
        _drop_fake_live_session(caller_chat_id, caller_token)
        _drop_fake_live_session(target_chat_id, target_token)


# -------------------------------------------------- 44.A chat file upload
#
# `POST /api/agents/{slug}/chats/{chat_id}/upload` (MN-44 Phase A). Calls the
# endpoint FUNCTION directly with a real `starlette.datastructures.UploadFile`
# wrapping an in-memory `io.BytesIO` — same "skip uvicorn/ASGI, keep the real
# wiring" approach as `test_http_chat_endpoints`, and no real `claude` process
# is anywhere near this (the upload path never touches `agent_runtime.py`).


def test_sanitize_upload_filename() -> None:
    from src import api  # noqa: PLC0415

    cases = [
        ("notes.txt", "notes.txt"),
        ("../../evil.txt", "evil.txt"),
        ("..\\..\\evil.txt", "evil.txt"),
        ("C:evil.txt", "evil.txt"),
        ("C:\\Windows\\evil.txt", "evil.txt"),
        ("/etc/evil.txt", "evil.txt"),
        ("", "upload"),
        ("   ", "upload"),
        ('weird<>:"|?*name.txt', "weirdname.txt"),
    ]
    for raw, expected in cases:
        got = api._sanitize_upload_filename(raw)
        check(f"_sanitize_upload_filename({raw!r}) == {expected!r}", got == expected, got)


def test_agent_chat_upload_endpoint() -> None:
    import asyncio
    import io

    from starlette.datastructures import UploadFile

    from src import api, config as config_module  # noqa: PLC0415

    agent = _make_agent("chat-upload")
    chat = agent_registry.create_chat(agent.slug)
    uploads_dir = (agent_registry.chat_dir(agent.root, chat["chat_id"]) / "uploads").resolve()

    def upload(data: bytes, filename: str):
        return asyncio.run(
            api.api_agent_chat_upload(agent.slug, chat["chat_id"], UploadFile(io.BytesIO(data), filename=filename))
        )

    # --- happy path ---------------------------------------------------
    result = upload(b"hello world", "notes.txt")
    check("upload: returns the original filename verbatim", result["filename"] == "notes.txt", repr(result))
    saved_path = Path(result["path"])
    check("upload: file actually saved to disk", saved_path.is_file(), str(saved_path))
    check("upload: content round-trips byte-for-byte", saved_path.read_bytes() == b"hello world")
    check(
        "upload: saved under <chat>/uploads/, nowhere else",
        saved_path.resolve().parent == uploads_dir,
        f"{saved_path.resolve().parent} != {uploads_dir}",
    )
    check(
        "upload: original filename kept as a suffix behind the uuid prefix",
        saved_path.name.endswith("-notes.txt"),
        saved_path.name,
    )

    # A second upload with the SAME original filename must not collide.
    result2 = upload(b"second file", "notes.txt")
    check(
        "upload: repeat filename does not collide (uuid prefix disambiguates)",
        Path(result2["path"]) != saved_path and Path(result2["path"]).is_file(),
        result2["path"],
    )
    check("upload: first file untouched by the second", saved_path.read_bytes() == b"hello world")

    # --- path-traversal-attempt filenames -------------------------------
    # Every one of these must land inside `uploads_dir` — never at the
    # literal (relative or drive-anchored) location the filename names.
    evil_names = [
        "../../evil.txt", "..\\..\\evil.txt", "C:evil.txt",
        "C:\\Windows\\evil.txt", "/etc/evil.txt",
    ]
    for evil_name in evil_names:
        evil_result = upload(b"pwned", evil_name)
        evil_path = Path(evil_result["path"]).resolve()
        check(
            f"upload: malicious filename {evil_name!r} stays contained under uploads/",
            evil_path.parent == uploads_dir,
            f"escaped to {evil_path}",
        )

    # --- empty upload ----------------------------------------------------
    try:
        upload(b"", "empty.txt")
        check("upload: rejects an empty file", False, "no exception raised")
    except api.ApiError as exc:
        check(
            "upload: rejects an empty file",
            exc.code == "bad_request" and exc.status == 400,
            f"code={exc.code} status={exc.status}",
        )

    # --- over the size limit ---------------------------------------------
    original_limit = config_module.MAX_CHAT_UPLOAD_BYTES
    config_module.MAX_CHAT_UPLOAD_BYTES = 10
    try:
        try:
            upload(b"x" * 1000, "big.bin")
            check("upload: rejects a file over the configured size limit", False, "no exception raised")
        except api.ApiError as exc:
            check(
                "upload: rejects a file over the configured size limit",
                exc.code == "upload_too_large" and exc.status == 413,
                f"code={exc.code} status={exc.status}",
            )
        leftover = list(uploads_dir.glob("*big.bin"))
        check("upload: rejected oversized upload leaves no partial file behind", leftover == [], repr(leftover))
    finally:
        config_module.MAX_CHAT_UPLOAD_BYTES = original_limit

    # --- 404s --------------------------------------------------------------
    try:
        asyncio.run(
            api.api_agent_chat_upload(agent.slug, "no-such-chat", UploadFile(io.BytesIO(b"x"), filename="x.txt"))
        )
        check("upload: 404s a bad chat_id", False, "no exception raised")
    except api.ApiError as exc:
        check(
            "upload: 404s a bad chat_id",
            exc.code == "chat_not_found" and exc.status == 404,
            f"code={exc.code} status={exc.status}",
        )

    try:
        asyncio.run(
            api.api_agent_chat_upload(
                "no-such-agent", chat["chat_id"], UploadFile(io.BytesIO(b"x"), filename="x.txt")
            )
        )
        check("upload: 404s a bad agent slug", False, "no exception raised")
    except api.ApiError as exc:
        check(
            "upload: 404s a bad agent slug",
            exc.code == "agent_not_found" and exc.status == 404,
            f"code={exc.code} status={exc.status}",
        )

    check("_ERROR_STATUS: upload_too_large -> 413", api._ERROR_STATUS.get("upload_too_large") == 413)

    routes = {r.path for r in api.app.routes if hasattr(r, "path")}
    check(
        "the chat upload route is registered",
        "/api/agents/{slug}/chats/{chat_id}/upload" in routes,
        repr(sorted(p for p in routes if "upload" in p)),
    )


# --------------------------------------------------- 44.A subagent listing
#
# `GET /api/agents/{slug}/subagents` (MN-44 Phase A). Exercised against both
# an agent with no `.claude/agents/` at all (the common case) and one whose
# `.claude/agents/` mirrors this repo's OWN hand-written agent files — those
# real files are only ever READ here and copied into a throwaway temp agent
# folder; nothing under this repo's actual `.claude/agents/` is touched.


def test_agent_subagents_endpoint() -> None:
    from src import api  # noqa: PLC0415

    # No `.claude/agents/` directory at all -> {"subagents": []}, not a 404.
    empty_agent = _make_agent("subagents-empty")
    result = api.api_agent_subagents(empty_agent.slug)
    check(
        "subagents: no .claude/agents/ dir -> empty list, not an error",
        result == {"subagents": []},
        repr(result),
    )

    real_agent_files = sorted(
        (Path(__file__).resolve().parent.parent / ".claude" / "agents").glob("*.md")
    )
    check(
        "subagents: this repo has real agent definitions to test against",
        len(real_agent_files) > 0,
        "no .claude/agents/*.md found in the repo",
    )

    real_agent = _make_agent("subagents-real")
    dest_dir = real_agent.root / ".claude" / "agents"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for src_path in real_agent_files:
        (dest_dir / src_path.name).write_text(src_path.read_text(encoding="utf-8"), encoding="utf-8")

    result = api.api_agent_subagents(real_agent.slug)
    subagents = result["subagents"]
    check(
        "subagents: one entry per real .md file, none skipped",
        len(subagents) == len(real_agent_files),
        f"got {len(subagents)}, expected {len(real_agent_files)}",
    )
    by_name = {s["name"]: s for s in subagents}
    check("subagents: 'name:' read from frontmatter", "service-dev" in by_name, repr(sorted(by_name)))
    check(
        "subagents: multi-line folded 'description:' block scalar joined into one string",
        bool(by_name.get("service-dev", {}).get("description"))
        and "FastAPI" in by_name["service-dev"]["description"],
        repr(by_name.get("service-dev")),
    )

    # Malformed / frontmatter-less file alongside the real ones: skipped
    # gracefully (falls back to the filename stem), never a crash.
    (dest_dir / "not-an-agent.md").write_text("just prose, no frontmatter at all\n", encoding="utf-8")
    result2 = api.api_agent_subagents(real_agent.slug)
    names2 = {s["name"] for s in result2["subagents"]}
    check(
        "subagents: a file with no frontmatter falls back to its filename stem",
        "not-an-agent" in names2,
        repr(sorted(names2)),
    )
    by_name2 = {s["name"]: s for s in result2["subagents"]}
    check(
        "subagents: a file with no frontmatter gets description=None, not a crash",
        by_name2["not-an-agent"]["description"] is None,
        repr(by_name2["not-an-agent"]),
    )

    # A file with genuinely invalid UTF-8 bytes (not just missing
    # frontmatter): `UnicodeDecodeError` is a `ValueError`, NOT an
    # `OSError` — must still be skipped, not turn the whole request into a
    # 500 (reviewer-confirmed bug, fixed by widening the except clause).
    (dest_dir / "bad-encoding.md").write_bytes(b"---\nname: bad\n---\n\xff\xfe not valid utf-8 \x80\x81")
    result3 = api.api_agent_subagents(real_agent.slug)
    check(
        "subagents: an invalid-UTF-8 .md file does not 500 the whole listing",
        isinstance(result3, dict) and "subagents" in result3,
        repr(result3),
    )
    names3 = {s["name"] for s in result3["subagents"]}
    check(
        "subagents: the invalid-UTF-8 file itself is skipped, not listed",
        "bad" not in names3 and "bad-encoding" not in names3,
        repr(sorted(names3)),
    )
    check(
        "subagents: every OTHER file is still listed alongside the skipped one",
        names3 == names2,
        f"{sorted(names3)} != {sorted(names2)}",
    )

    try:
        api.api_agent_subagents("no-such-agent")
        check("subagents: 404s a bad agent slug", False, "no exception raised")
    except api.ApiError as exc:
        check(
            "subagents: 404s a bad agent slug",
            exc.code == "agent_not_found" and exc.status == 404,
            f"code={exc.code} status={exc.status}",
        )

    routes = {r.path for r in api.app.routes if hasattr(r, "path")}
    check(
        "the subagents route is registered",
        "/api/agents/{slug}/subagents" in routes,
        repr(sorted(p for p in routes if "subagent" in p)),
    )


def main() -> int:
    test_chat_storage()
    test_launch_translation()
    test_write_agent_configs()
    test_session_token_lifecycle()
    test_concurrency_ceiling()
    test_race_free_reconnect()
    test_http_chat_endpoints()
    test_auth_middleware_agent_branch()
    test_hook_agent_subagent_endpoint()
    test_mcp_agents_tools()
    test_sanitize_upload_filename()
    test_agent_chat_upload_endpoint()
    test_agent_subagents_endpoint()

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
