# 2026-08-23 — `connectSocket()` never attempted the WS on a fresh, token-less install

## Symptom

On a Windows laptop where `mnemo` had just been installed (`install.ps1`) and `mnemo init` run, the web console (`/ui/`) never showed the "наживо" (live) indicator.
REST calls (`/api/*`, banks list, search) all worked fine — only the WebSocket channel never connected, with no error visible anywhere.
Exactly matches the item flagged as "not fixed / not confirmed" in `logs/2026-08-22-self-update-staleness-and-console-bugs.md`: "Green dot doesn't light up on one machine (WS never visibly connects) — no confirmed root cause; needs that machine's actual browser console/Network tab."

## Root cause

`src/webui/static/shell.js`'s `connectSocket()` had a stale guard from before the 2026-08-21 "`/api` open by default" decision:

```js
if (state.gated || !token) return;
```

The comment above it ("Without a token the handshake can only be refused...") was true **before** 2026-08-21, when `/ws` unconditionally required a valid token.
That decision made `/ws`'s server-side check symmetric with `/api` (`api.py`'s `ws_endpoint`: `configured = _configured_token(); if configured is not None and not _token_ok(token): ... close(1008)`) — with no token configured (the default for a fresh install), the server now accepts a connection with none presented.
But the client-side guard in `shell.js` was never updated to match: with no token configured, the browser's `token` variable is an empty string, `!token` is true, and `connectSocket()` returns immediately **without ever attempting a WebSocket** — no network request, no console error, nothing to see except a socket that just never opens.

Confirmed on the actual affected machine's report (four checks: no console errors, no `/ws` entry in Network at all, `token === ""`, `socket === null`), then reproduced live and organically on the dev machine too: a *fresh browser tab* (no `?token=` in the URL, so no `sessionStorage` entry) hit the exact same state — `gated: false` (REST is fine, since `/api` has no configured token here either), `socket: null`, zero WS network requests.
The bug is universal on any machine with `/api` open by default (i.e. `$MNEMO_API_TOKEN` never set) — the working dev-machine tab that "looked fine" only had a lingering `?token=...` from earlier manual testing sitting in `sessionStorage`, which masked it.

## Fix

`connectSocket()`'s guard is now just `if (state.gated) return;` — matching the server: only an active gate (a real 401 happened, meaning a token *is* configured and the client doesn't have the right one) should stop the socket from being attempted.
An empty token with `/api` open is the *normal* case, not a reason to skip connecting.
Comment rewritten to explain the current (2026-08-21-onward) behavior instead of the pre-decision one.

Verified live: re-mirrored the engine (`install.ps1` → `v3.0.7l`, service restarted), reloaded a token-less tab — `socket.readyState === 1` (OPEN), sidebar shows the green "наживо" dot, zero console errors. Screenshot taken during verification.

## Diagnostic technique worth keeping

A brand-new browser tab (`new_page`, not a reload of an existing one) is a cheap, non-destructive way to reproduce a "no token in sessionStorage" state on a machine that otherwise has a stale token lingering from earlier manual testing — `sessionStorage` is per-tab, so opening a fresh tab to the same origin does not inherit it, while a reload of an already-open tab does.
