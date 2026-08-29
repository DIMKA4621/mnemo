"use client";

import { useState } from "react";
import { Input } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { useFsDirs } from "@/hooks/useMemoryQueries";
import "@/components/common/dialogs.css";

const LAST_DIR_KEY = "mnemo_fs_agent_last";

function initialBrowsePath(): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(LAST_DIR_KEY) || null;
}

interface FolderBrowserProps {
  onPick: (path: string) => void;
  busy?: boolean;
}

/**
 * Directory browser for the create-agent wizard's "Вказати наявну теку"
 * step — the same `GET /api/fs/dirs` (`useFsDirs`) Памʼять's `BankPicker`
 * already browses with, just without that dialog's bank-specific
 * "create structure"/"connect MCP" checkboxes: an agent root is not a bank
 * root, those concerns don't apply here. No standalone reusable browser
 * component existed to import — `BankPicker.tsx` has this logic inlined —
 * so this is the extraction, scoped to what the wizard actually needs:
 * navigate, then hand the chosen path back to the caller, which owns the
 * adoption-preview flow (`CreateAgentWizard.tsx`).
 */
export function FolderBrowser({ onPick, busy }: FolderBrowserProps) {
  const t = useT();
  const [browsePath, setBrowsePath] = useState<string | null>(initialBrowsePath);
  const [pathInput, setPathInput] = useState("");

  const fsQuery = useFsDirs(browsePath, true);
  const data = fsQuery.data ?? null;

  // Same "adjust state when a value changes during render" pattern as
  // `BankPicker.tsx` — no post-mount `setState`-in-effect needed.
  const [trackedPath, setTrackedPath] = useState<string | null>(null);
  if ((data?.path ?? null) !== trackedPath) {
    setTrackedPath(data?.path ?? null);
    if (data) {
      setPathInput(data.display || data.path);
      if (typeof window !== "undefined") window.sessionStorage.setItem(LAST_DIR_KEY, data.path);
    }
  }

  function go(path: string | null) {
    setBrowsePath(path);
  }

  function handleInputKeyDown(ev: React.KeyboardEvent) {
    if (ev.key !== "Enter") return;
    ev.preventDefault();
    go(pathInput.trim());
  }

  return (
    <div>
      <label className="fs-label">{t("agents.folder.pathLabel")}</label>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", margin: "6px 0" }}>
        {data && (
          <>
            <button type="button" className="chip" title={data.home} onClick={() => go(data.home)}>
              ⌂ {t("agents.folder.home")}
            </button>
            {data.roots.map((root) => (
              <button key={root.path} type="button" className="chip" onClick={() => go(root.path)}>
                {root.name}
              </button>
            ))}
          </>
        )}
      </div>
      <Input
        value={pathInput}
        spellCheck={false}
        onChange={(e) => setPathInput(e.target.value)}
        onKeyDown={handleInputKeyDown}
        disabled={busy}
      />
      <div className="fs-list" style={{ maxHeight: 180, overflow: "auto", margin: "8px 0", border: "1px solid var(--line)", borderRadius: "var(--radius)" }}>
        {!data ? (
          <p className="mnemo-muted" style={{ padding: 8, margin: 0 }}>{t("agents.folder.reading")}</p>
        ) : (
          <>
            {data.parent && (
              <button type="button" className="fs-row is-up" title={data.parent} onClick={() => go(data.parent)}>
                ⬆ ..
              </button>
            )}
            {(data.entries ?? []).map((entry) => (
              <button key={entry.path} type="button" className="fs-row" title={entry.path} onClick={() => go(entry.path)}>
                <span className="fs-name">{entry.name}</span>
              </button>
            ))}
            {(data.entries ?? []).length === 0 && (
              <p className="mnemo-muted" style={{ padding: 8, margin: 0 }}>{t("agents.folder.noSubdirs")}</p>
            )}
          </>
        )}
      </div>
      <button
        type="button"
        className="chip"
        disabled={!data || fsQuery.isFetching || busy}
        onClick={() => data && onPick(data.path)}
      >
        {fsQuery.isFetching ? t("agents.folder.reading") : t("agents.folder.chooseBtn")}
      </button>
    </div>
  );
}
