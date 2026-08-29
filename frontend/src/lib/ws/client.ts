import { useTokenStore } from "../store/token";
import { useWsStatusStore } from "../store/ws-status";
import { dispatchEvent, resetHelloSeen, type WsEnvelope } from "./dispatch";

/**
 * `NEXT_PUBLIC_MNEMO_BACKEND` is set only under `next dev` (see
 * `next.config.ts`) and points straight at the real backend or
 * `devserver.py`'s own `/ws` — bypassing the `rewrites()` HTTP proxy
 * entirely. Next's rewrite layer forwards plain HTTP fine but does not
 * reliably forward a WebSocket Upgrade handshake (a long-standing framework
 * limitation, not specific to this backend or to `devserver.py`'s
 * hand-rolled framing); dialing the backend's own `/ws` directly sidesteps
 * the problem rather than working around it with a custom proxy server.
 * The exported production build never sets this env var, so it always
 * falls through to same-origin — correct, since the real `mnemo` service
 * serves both the console and `/ws` from the same process.
 */
function wsBaseUrl(): string {
  const backend = process.env.NEXT_PUBLIC_MNEMO_BACKEND;
  if (backend) return backend.replace(/^http/, "ws");
  if (typeof window === "undefined") return "";
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}`;
}

let socket: WebSocket | null = null;
let retryDelay = 500;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

/** Drop the socket without arming the reconnect timer. */
export function closeSocket(): void {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (!socket) return;
  const dead = socket;
  socket = null;
  dead.onclose = null;
  dead.onerror = null;
  dead.onmessage = null;
  dead.close();
  useWsStatusStore.getState().setStatus("idle");
}

export function connectSocket(): void {
  const { gateOpen, token } = useTokenStore.getState();
  // `/api` (and `/ws` with it) is open by default: with no token configured
  // the server accepts the handshake with none presented. An empty `token`
  // here is the normal fresh-install case, not a reason to skip connecting —
  // only an active gate (a real 401 happened) means the socket would be
  // refused. See `.claude/memory/logs/2026-08-23-ws-connect-guard-fix.md`
  // for why this guard is `gateOpen`, never `!token`.
  if (gateOpen) return;
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return; // already connecting/connected — StrictMode double-invoke guard
  }

  const url = `${wsBaseUrl()}/ws?token=${encodeURIComponent(token)}`;
  useWsStatusStore.getState().setStatus("connecting");

  const ws = new WebSocket(url);
  socket = ws;

  ws.onopen = () => {
    if (socket !== ws) return;
    retryDelay = 500;
    useWsStatusStore.getState().setStatus("open");
  };

  ws.onclose = () => {
    if (socket !== ws) return; // superseded by a newer connect/closeSocket()
    socket = null;
    if (useTokenStore.getState().gateOpen) return;
    useWsStatusStore.getState().setStatus("error");
    reconnectTimer = setTimeout(connectSocket, retryDelay);
    retryDelay = Math.min(retryDelay * 2, 10000);
  };

  ws.onerror = () => {
    if (socket !== ws) return;
    if (!useTokenStore.getState().gateOpen) useWsStatusStore.getState().setStatus("error");
  };

  ws.onmessage = (event) => {
    let envelope: WsEnvelope;
    try {
      envelope = JSON.parse(event.data);
    } catch {
      return;
    }
    // `ping`/`pong` are transport-level, not a query-cache concern, and only
    // this scope has the live socket to reply on — kept out of
    // `dispatchEvent`'s routing table on purpose.
    if (envelope.type === "ping") {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "pong" }));
      return;
    }
    dispatchEvent(envelope);
  };
}

/** A rejected/regenerated token invalidates whatever socket is open — same
 *  as `openGate()` calling `closeSocket()` synchronously in the vanilla
 *  console (`app.js`). Subscribed once, at module load, from
 *  `components/shell/AppShell.tsx`'s mount effect via `initWsClient()`. */
export function initWsClient(): () => void {
  resetHelloSeen();
  const unsubscribe = useTokenStore.subscribe((state, prev) => {
    if (state.gateOpen && !prev.gateOpen) {
      closeSocket();
    } else if (!state.gateOpen && prev.gateOpen) {
      // Gate just closed (a token was accepted) — (re)connect with it.
      connectSocket();
    }
  });
  connectSocket();
  return () => {
    unsubscribe();
    closeSocket();
  };
}
