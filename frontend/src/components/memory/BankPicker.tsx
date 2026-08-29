"use client";

import { useState } from "react";
import { Button, Checkbox, Input } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { useFsDirs } from "@/hooks/useMemoryQueries";
import { useAddBank } from "@/hooks/useMemoryMutations";
import { ModalShell } from "@/components/common/ModalShell";
import type { BankInfo } from "@/lib/api/memory";
import { effectiveBankRoot, projectRootForBankPath } from "@/lib/memory/bankRoot";

const LAST_DIR_KEY = "mnemo_fs_last";

interface BankPickerProps {
  open: boolean;
  onClose: () => void;
  onAdded: (bank: BankInfo, note: string) => void;
}

/** Resumes where the last look around ended — session-only, so a "look
 *  around, then add two banks from the same folder" flow doesn't walk down
 *  from home twice. Read once, as the initial state, not via an effect: the
 *  parent remounts this component fresh on every open
 *  (`key={pickerOpen ? … }` in `app/memory/page.tsx`). */
function initialBrowsePath(): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(LAST_DIR_KEY) || null;
}

export function BankPicker({ open, onClose, onAdded }: BankPickerProps) {
  const t = useT();
  const [browsePath, setBrowsePath] = useState<string | null>(initialBrowsePath);
  const [pathInput, setPathInput] = useState("");
  const [name, setName] = useState("");
  const [createChecked, setCreateChecked] = useState(false);
  const [mcpChecked, setMcpChecked] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const fsQuery = useFsDirs(browsePath, open);
  const addBankMutation = useAddBank();
  const data = fsQuery.data ?? null;
  const currentPath = data?.path ?? null;

  const projectRoot = projectRootForBankPath(currentPath);
  const alreadyBank = projectRoot !== null;
  const hasNestedMemory = !!data?.has_claude_memory;
  const canCreate = currentPath !== null && !alreadyBank && !hasNestedMemory;
  const mcpEligible = alreadyBank || createChecked;

  // "Adjust state when a value changes" (React's own documented pattern for
  // this — https://react.dev/learn/you-might-not-need-an-effect), run
  // during render rather than in a `useEffect`: each block below compares
  // against the previous render's tracked key and, on a real change,
  // updates both the tracker and the dependent state in the same pass.
  const [trackedPath, setTrackedPath] = useState<string | null>(null);
  if (currentPath !== trackedPath) {
    setTrackedPath(currentPath);
    setCreateChecked(false);
    if (data) {
      setPathInput(data.display || data.path);
      if (typeof window !== "undefined") window.sessionStorage.setItem(LAST_DIR_KEY, data.path);
    }
  }

  const [trackedMcpKey, setTrackedMcpKey] = useState<string | null>(null);
  const mcpKey = `${currentPath}::${mcpEligible}`;
  if (mcpKey !== trackedMcpKey) {
    setTrackedMcpKey(mcpKey);
    setMcpChecked(mcpEligible);
  }

  const createEffective = canCreate && createChecked;

  let createHint = "";
  let createWarn = false;
  if (currentPath) {
    if (alreadyBank) {
      createHint = t("common.picker.hint.alreadyBank");
      createWarn = true;
    } else if (hasNestedMemory) {
      createHint = t("common.picker.hint.hasNestedMemory");
      createWarn = true;
    } else if (createEffective) {
      createHint = t("common.picker.hint.willBecome", { root: effectiveBankRoot(currentPath, true, data) });
      createWarn = true;
    }
  }

  let initHint = "";
  if (mcpEligible) {
    initHint = alreadyBank
      ? t("common.picker.hint.project", { root: projectRoot ?? "" })
      : t("common.picker.hint.willConnect");
  } else {
    initHint = t("common.picker.hint.projectOnly");
  }

  function go(path: string | null) {
    setBrowsePath(path);
  }

  function handleInputKeyDown(ev: React.KeyboardEvent) {
    if (ev.key !== "Enter") return;
    ev.preventDefault();
    go(pathInput.trim());
  }

  async function submit() {
    if (!currentPath || fsQuery.isFetching || addBankMutation.isPending) return;
    setSubmitError(null);
    const body = {
      root: effectiveBankRoot(currentPath, createEffective, data),
      name: name.trim() || null,
      create_structure: createEffective,
      init: mcpChecked,
    };
    try {
      const info = await addBankMutation.mutateAsync(body);
      const initSuffix = info.init
        ? info.init.skipped
          ? ` · ${t("common.picker.mcpSkipped")}${info.init.reason ? `: ${info.init.reason}` : ""}`
          : ` · ${info.init.ok ? t("common.picker.mcpConnected") : t("common.picker.mcpFailed")}`
        : "";
      onAdded(info, t("common.picker.addedNote") + initSuffix);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    }
  }

  const mdCount = data ? (data.md_capped ? `≥${data.md}` : String(data.md)) : "";
  const nestedSuffix = data && (data.entries ?? []).length ? ` ${t("common.picker.withSubdirs")}` : "";

  return (
    <ModalShell
      open={open}
      title={t("common.picker.title")}
      ariaLabel={t("common.picker.ariaLabel")}
      onClose={onClose}
      busy={addBankMutation.isPending}
      wide
      footer={
        <>
          <Button onClick={onClose}>{t("common.btn.cancel")}</Button>
          <Button
            type="primary"
            loading={addBankMutation.isPending}
            disabled={fsQuery.isFetching || !data || !!data.registered}
            onClick={submit}
          >
            {fsQuery.isFetching ? t("common.picker.reading") : t("common.picker.addDir")}
          </Button>
        </>
      }
    >
      <label className="fs-label">{t("common.picker.pathLabel")}</label>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", margin: "6px 0" }}>
        {data && (
          <>
            <button className="chip" title={data.home} onClick={() => go(data.home)}>
              ⌂ {t("common.picker.home")}
            </button>
            {data.roots.map((root) => (
              <button key={root.path} className="chip" onClick={() => go(root.path)}>
                {root.name}
              </button>
            ))}
          </>
        )}
      </div>
      <Input
        value={pathInput}
        spellCheck={false}
        placeholder={t("common.picker.pathPlaceholder")}
        onChange={(e) => setPathInput(e.target.value)}
        onKeyDown={handleInputKeyDown}
      />
      <div className="fs-list" style={{ maxHeight: 200, overflow: "auto", margin: "8px 0", border: "1px solid var(--line)", borderRadius: "var(--radius)" }}>
        {!data ? (
          <p className="mnemo-muted" style={{ padding: 8, margin: 0 }}>{t("common.picker.reading")}</p>
        ) : (
          <>
            {data.parent && (
              <button className="fs-row is-up" title={data.parent} onClick={() => go(data.parent)}>
                ⬆ ..
              </button>
            )}
            {(data.entries ?? []).map((entry) => (
              <button
                key={entry.path}
                className="fs-row"
                title={entry.registered ? t("common.picker.alreadyBankTitle", { name: entry.registered }) : entry.path}
                onClick={() => go(entry.path)}
              >
                <span className="fs-name">{entry.name}</span>
                {entry.registered && <span className="mnemo-badge mnemo-badge-git">{t("common.picker.bankBadge")}</span>}
              </button>
            ))}
            {(data.entries ?? []).length === 0 && (
              <p className="mnemo-muted" style={{ padding: 8, margin: 0 }}>{t("common.picker.noSubdirs")}</p>
            )}
            {data.truncated && (
              <p className="mnemo-muted" style={{ padding: 8, margin: 0 }}>
                {t("common.picker.truncated", { n: data.entries.length })}
              </p>
            )}
          </>
        )}
      </div>
      {data && (
        <p className="fs-hint" style={{ fontSize: 12 }}>
          <span
            className={data.md ? "" : "fs-warn"}
            title={data.md_capped ? t("common.picker.countTruncatedTitle") : t("common.picker.excludesTitle")}
          >
            {data.md ? t("common.picker.mdCount", { count: mdCount, nested: nestedSuffix }) : t("common.picker.noMd")}
          </span>
          {data.registered && (
            <span className="fs-warn"> · {t("common.picker.alreadyRegistered", { name: data.registered })}</span>
          )}
        </p>
      )}

      <label className="fs-label">{t("common.picker.nameLabel")}</label>
      <Input
        value={name}
        spellCheck={false}
        placeholder={t("common.picker.namePlaceholder")}
        onChange={(e) => setName(e.target.value)}
        onPressEnter={submit}
        style={{ marginBottom: 10 }}
      />

      <div className="fs-init-row">
        <Checkbox checked={createChecked} disabled={!canCreate} onChange={(e) => setCreateChecked(e.target.checked)}>
          {t("common.picker.createStructure")}
        </Checkbox>
      </div>
      {createHint && <p className={createWarn ? "fs-hint fs-warn" : "fs-hint"}>{createHint}</p>}

      <div className="fs-init-row">
        <Checkbox checked={mcpChecked} disabled={!mcpEligible} onChange={(e) => setMcpChecked(e.target.checked)}>
          {t("common.picker.connectMcp")}
        </Checkbox>
      </div>
      <p className={mcpEligible ? "fs-hint" : "fs-hint fs-warn"}>{initHint}</p>

      {(submitError || fsQuery.error) && (
        <p className="modal-error" style={{ color: "var(--err)" }}>
          {submitError ?? (fsQuery.error instanceof Error ? fsQuery.error.message : String(fsQuery.error))}
        </p>
      )}
    </ModalShell>
  );
}
