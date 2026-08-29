"use client";

import { useEffect, useRef, useState } from "react";
import { Terminal, type ITheme } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { useT } from "@/lib/i18n/hooks";
import { useUiStore } from "@/lib/store/ui";
import { useTokenStore } from "@/lib/store/token";
import { getSubagentEvents, uploadChatFile, type SubagentEvent } from "@/lib/api/agentChats";
import type { AgentInfo } from "@/lib/api/agents";
import type { ThemeMode } from "@/lib/theme/design-tokens";
import { tokensFor } from "@/lib/theme/design-tokens";
import { SubagentPanel } from "./SubagentPanel";

interface ChatConsoleProps {
  agent: AgentInfo;
  chatId: string;
}

type ConnStatus = "connecting" | "replaying" | "live" | "error" | "exited" | "limit";

/** Same reasoning as `lib/ws/client.ts`'s `wsBaseUrl()`: `next dev` points
 *  straight at the backend/`devserver.py`, the exported build is same-origin. */
function wsBaseUrl(): string {
  const backend = process.env.NEXT_PUBLIC_MNEMO_BACKEND;
  if (backend) return backend.replace(/^http/, "ws");
  if (typeof window === "undefined") return "";
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}`;
}

/** Not a themed UI element — a 16-color ANSI terminal palette, roughly
 *  matched to the app's own tokens (`err`/`ok`/`busy`/`accent`) so a `claude`
 *  session doesn't clash with the surrounding chrome, but with a couple of
 *  colors (magenta) this codebase's token set has no equivalent for. */
function xtermTheme(mode: ThemeMode): ITheme {
  const c = tokensFor(mode);
  if (mode === "light") {
    return {
      background: c.bgPane,
      foreground: c.fg,
      cursor: c.accent,
      cursorAccent: c.bgPane,
      selectionBackground: c.selBg,
      black: c.fg,
      red: c.err,
      green: c.ok,
      yellow: c.busy,
      blue: c.accent,
      magenta: "#8a4fae",
      cyan: c.accentMuted,
      white: c.fgMute,
      brightBlack: c.fgDim,
      brightRed: "#e0554a",
      brightGreen: "#3fae6d",
      brightYellow: "#b8860f",
      brightBlue: "#4a82e0",
      brightMagenta: "#a565cf",
      brightCyan: "#5a80b8",
      brightWhite: c.fg,
    };
  }
  return {
    background: c.bgPane,
    foreground: c.fg,
    cursor: c.accent,
    cursorAccent: c.bgPane,
    selectionBackground: c.selBg,
    black: c.bgHover,
    red: c.err,
    green: c.ok,
    yellow: c.busy,
    blue: c.accent,
    magenta: "#c792ea",
    cyan: c.accentMuted,
    white: c.fg,
    brightBlack: c.fgMute,
    brightRed: "#f08a82",
    brightGreen: "#6fd99a",
    brightYellow: "#f2c169",
    brightBlue: "#8fbdff",
    brightMagenta: "#dcb6f0",
    brightCyan: "#a7c4e5",
    brightWhite: "#ffffff",
  };
}

/**
 * A plain xterm.js terminal wired to `/ws/agents/{slug}/chats/{chat_id}`
 * (MN-43) — the ttyd/gotty/wetty pattern, not a chat UI. There is no custom
 * input box, no slash-command menu, no model picker: `term.onData` forwards
 * every keystroke raw as `{"type":"input",...}` and the real `claude` CLI
 * draws its own TUI (thinking display, tool-call rendering, autocomplete,
 * line editing) through ANSI/cursor control. See the Jira MN-44 plan
 * comment (29.08.2026) for why this replaces the earlier structured-event
 * mockup instead of porting it.
 *
 * Owns `Terminal`+`FitAddon`+`WebSocket` in refs, all created and torn down
 * together inside one effect keyed on `[agent.slug, chatId]`. Live output
 * writes straight to `term.write()` from `ws.onmessage`, bypassing React
 * state for the hot path — state only drives the status badge and the
 * exited/error banner. `AgentsPage.tsx` mounts this with `key={chatId}`,
 * which is load-bearing, not decorative: switching chats must give every
 * piece of local state (status, banners, the attachment chip) a genuinely
 * fresh start, and a full remount is what does that without a second effect
 * whose only job would be resetting state a render already owns.
 */
export function ChatConsole({ agent, chatId }: ChatConsoleProps) {
  const t = useT();
  const theme = useUiStore((s) => s.theme);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [status, setStatus] = useState<ConnStatus>("connecting");
  const [exitInfo, setExitInfo] = useState<{ code: number | null } | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  // MN-46: set only for a `too_many_sessions` rejection — distinct from
  // `errorMessage` above because this status must never trigger the
  // reconnect-backoff loop `onclose` runs for a generic connection failure
  // (see `limitReachedRef` below).
  const [limitInfo, setLimitInfo] = useState<{ count: number; limit: number } | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [attachment, setAttachment] = useState<{ name: string } | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [subagentEvents, setSubagentEvents] = useState<SubagentEvent[]>([]);

  // MN-45b: initial load only — new events after mount arrive live over the
  // same WS connection below (`subagent_event` envelope), never a second
  // poll of this endpoint. Best-effort: a failed fetch just leaves the
  // panel empty rather than surfacing a banner over the terminal.
  useEffect(() => {
    let cancelled = false;
    getSubagentEvents(agent.slug, chatId)
      .then((res) => {
        if (!cancelled) setSubagentEvents(res.events);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [agent.slug, chatId]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const term = new Terminal({
      fontFamily: "var(--mono)",
      fontSize: 13,
      cursorBlink: true,
      theme: xtermTheme(useUiStore.getState().theme),
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(container);
    termRef.current = term;

    let ws: WebSocket | null = null;
    let disposed = false;
    let retryDelay = 500;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let resizeDebounce: ReturnType<typeof setTimeout> | null = null;
    // MN-46: set by the `too_many_sessions` branch below, read by `onclose`.
    // A rejection because the machine-wide live-session ceiling is full is
    // not a transient network failure — retrying against a still-full
    // ceiling is pointless spam, so `onclose` must skip its usual
    // reconnect-backoff scheduling for this one case. Reset at the top of
    // every `connect()` so a later, genuine network error (after the user
    // reopens this chat) still gets the normal retry behavior.
    let limitReached = false;

    function sendResize(socket: WebSocket) {
      if (socket.readyState !== WebSocket.OPEN) return;
      const { rows, cols } = term;
      if (!rows || !cols) return;
      socket.send(JSON.stringify({ type: "resize", rows, cols }));
    }

    function connect() {
      if (disposed) return;
      limitReached = false;
      setStatus("connecting");
      setErrorMessage(null);
      setLimitInfo(null);
      const { token } = useTokenStore.getState();
      const url =
        `${wsBaseUrl()}/ws/agents/${encodeURIComponent(agent.slug)}` +
        `/chats/${encodeURIComponent(chatId)}?token=${encodeURIComponent(token)}`;
      const socket = new WebSocket(url);
      ws = socket;
      wsRef.current = socket;

      socket.onopen = () => {
        if (ws !== socket) return;
        // The backend replays the ENTIRE history on every connect/reconnect
        // — a full reset-and-repaint is what makes that replay correct, not
        // a workaround for a gap the backend could just as easily avoid.
        term.reset();
        setStatus("replaying");
        retryDelay = 500;
        fit.fit();
        sendResize(socket);
      };

      socket.onmessage = (event) => {
        if (ws !== socket) return;
        let envelope: {
          type: string;
          data?: string;
          message?: string;
          code?: string;
          count?: number;
          limit?: number;
          [key: string]: unknown;
        };
        try {
          envelope = JSON.parse(event.data);
        } catch {
          return;
        }
        switch (envelope.type) {
          case "output":
            term.write(envelope.data ?? "");
            break;
          case "replay_done":
            setStatus("live");
            break;
          case "exited":
            setStatus("exited");
            setExitInfo({ code: typeof envelope.code === "number" ? envelope.code : null });
            break;
          case "error":
            if (envelope.code === "too_many_sessions") {
              // MN-46: a distinct, non-retrying state — `onclose` (below)
              // checks `limitReached` and skips its reconnect-backoff loop.
              limitReached = true;
              setStatus("limit");
              setLimitInfo({
                count: typeof envelope.count === "number" ? envelope.count : 0,
                limit: typeof envelope.limit === "number" ? envelope.limit : 0,
              });
            } else {
              setStatus("error");
              setErrorMessage(envelope.message ?? null);
            }
            break;
          case "subagent_event":
            // `SubagentEvent` carries an index signature, so the leftover
            // `type` field here is harmless — never read by `SubagentPanel`.
            setSubagentEvents((prev) => [...prev, envelope as unknown as SubagentEvent]);
            break;
        }
      };

      socket.onclose = () => {
        if (ws !== socket) return;
        ws = null;
        wsRef.current = null;
        if (disposed) return;
        if (limitReached) {
          // The backend already sent the `too_many_sessions` error envelope
          // and `onmessage` above set status "limit" — this close (code
          // 1013) just follows it. Retrying now would only hit the same
          // still-full ceiling; the user reopening this chat (a fresh mount)
          // is what tries again, not an automatic loop.
          return;
        }
        setStatus("error");
        reconnectTimer = setTimeout(connect, retryDelay);
        retryDelay = Math.min(retryDelay * 2, 10000);
      };

      socket.onerror = () => {
        if (ws !== socket) return;
        setStatus("error");
      };
    }

    connect();

    const dataSub = term.onData((data) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "input", data }));
      }
    });

    const resizeObserver = new ResizeObserver(() => {
      fit.fit();
      if (resizeDebounce) clearTimeout(resizeDebounce);
      resizeDebounce = setTimeout(() => {
        if (ws) sendResize(ws);
      }, 150);
    });
    resizeObserver.observe(container);

    return () => {
      disposed = true;
      dataSub.dispose();
      resizeObserver.disconnect();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (resizeDebounce) clearTimeout(resizeDebounce);
      // Only ever closes the socket — switching away from a chat must never
      // stop its background generation. The one thing allowed to do that is
      // an explicit chat delete, which calls `DELETE .../chats/{chat_id}`
      // (`useDeleteChat`), never anything in this component.
      ws?.close();
      ws = null;
      wsRef.current = null;
      term.dispose();
      termRef.current = null;
    };
  }, [agent.slug, chatId]);

  // Theme changes don't need a socket/terminal rebuild — just repaint.
  useEffect(() => {
    if (termRef.current) termRef.current.options.theme = xtermTheme(theme);
  }, [theme]);

  async function handleUpload(file: File) {
    setUploading(true);
    setUploadError(null);
    try {
      const result = await uploadChatFile(agent.slug, chatId, file);
      setAttachment({ name: result.filename });
      // "Displayed" per the ticket's scope means the human's own outgoing
      // attachment — the chip above. The path itself goes straight into the
      // PTY input stream at the terminal's current cursor position, exactly
      // like dragging a file into a real terminal — no chunking, just one
      // `input` message carrying the literal path text.
      const socket = wsRef.current;
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "input", data: result.path }));
      }
      termRef.current?.focus();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  }

  function onDrop(ev: React.DragEvent<HTMLDivElement>) {
    ev.preventDefault();
    setDragOver(false);
    const file = ev.dataTransfer.files?.[0];
    if (file) void handleUpload(file);
  }

  function onFileInputChange(ev: React.ChangeEvent<HTMLInputElement>) {
    const file = ev.target.files?.[0];
    if (file) void handleUpload(file);
    ev.target.value = "";
  }

  const statusLabel: Record<ConnStatus, string> = {
    connecting: t("agents.console.status.connecting"),
    replaying: t("agents.console.status.replaying"),
    live: t("agents.console.status.live"),
    error: t("agents.console.status.error"),
    exited: t("agents.console.status.exited"),
    limit: t("agents.console.status.limit"),
  };
  // Same `.dot`/`.dot.busy`/`.dot.err`/`.dot.idle` classes `shell.css`
  // already defines globally (`WsStatusIndicator.tsx`'s convention) — no
  // ".dot.ok" modifier exists there since plain `.dot` already means ok.
  const statusDotClass: Record<ConnStatus, string> = {
    connecting: "dot busy",
    replaying: "dot busy",
    live: "dot",
    error: "dot err",
    exited: "dot idle",
    // Not "err": nothing is broken, the machine is just full — same "idle,
    // not alarming" treatment as "exited".
    limit: "dot idle",
  };

  return (
    <>
      <div className="ag-ws-head">
        <div>
          <div className="ag-ws-title">{agent.name}</div>
          <div className="ag-ws-sub">{chatId}</div>
        </div>
        <div style={{ flex: 1 }} />
        <span className="cc-status">
          <span className={statusDotClass[status]} />
          {statusLabel[status]}
        </span>
      </div>

      <SubagentPanel events={subagentEvents} />

      {status === "error" && errorMessage && (
        <div className="cc-banner cc-banner-error">{t("agents.console.errorBanner", { message: errorMessage })}</div>
      )}
      {status === "limit" && limitInfo && (
        <div className="cc-banner cc-banner-idle">
          {t("agents.console.limitBanner", { count: limitInfo.count, limit: limitInfo.limit })}
        </div>
      )}
      {status === "exited" && (
        <div className="cc-banner cc-banner-idle">
          {t("agents.console.exitedBanner", { code: exitInfo?.code ?? "?" })}
        </div>
      )}

      <div
        className={`cc-term-wrap${dragOver ? " is-drag-over" : ""}`}
        onDragOver={(ev) => {
          ev.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
      >
        <div ref={containerRef} className="cc-term" />
        {dragOver && <div className="cc-drop-overlay">{t("agents.console.dropHint")}</div>}
      </div>

      <div className="cc-footer">
        {attachment && (
          <span className="ac-attach-chip">
            <span>{attachment.name}</span>
            <button type="button" title={t("agents.console.clearAttachment")} onClick={() => setAttachment(null)}>
              ×
            </button>
          </span>
        )}
        <button
          type="button"
          className="ac-icon-btn"
          title={t("agents.console.attachTitle")}
          disabled={uploading}
          onClick={() => fileInputRef.current?.click()}
        >
          📎
        </button>
        {uploading && <span className="cc-uploading">{t("agents.console.uploading")}</span>}
        {uploadError && <span className="cc-upload-error">{uploadError}</span>}
        <input ref={fileInputRef} type="file" hidden onChange={onFileInputChange} />
      </div>
    </>
  );
}
