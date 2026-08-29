"use client";

import { useMemo, useState } from "react";
import { useT } from "@/lib/i18n/hooks";
import { useTree } from "@/hooks/useMemoryQueries";
import { useIndexProgressStore } from "@/lib/store/index-progress";
import type { TreeNode } from "@/lib/api/memory";

function walkDirs(node: TreeNode | undefined, fn: (dir: TreeNode) => void) {
  if (!node || node.type !== "dir") return;
  fn(node);
  for (const child of node.children ?? []) walkDirs(child, fn);
}

interface FileTreeProps {
  bankId: string | null;
  selectedPath: string | null;
  onSelectFile: (path: string) => void;
  /** Below 940px, whether this pane is the one currently shown by the
   *  mobile drill-down (`page.tsx`'s `mobPane`). */
  isMob?: boolean;
  /** Below 940px only: drills back to the Банки pane. */
  onBack?: () => void;
}

export function FileTree({ bankId, selectedPath, onSelectFile, isMob, onBack }: FileTreeProps) {
  const t = useT();
  const treeQuery = useTree(bankId);
  // `null` means "no manual toggle yet — use the default (every directory
  // open)". The parent keys this component by `bankId` (`app/memory/
  // page.tsx`), so switching banks remounts it fresh and this starts at
  // `null` again on its own — no reset effect needed. A later live refresh
  // (index_done invalidating the tree query) must not re-open a directory
  // the user has since collapsed, which is why the default is computed
  // rather than written into state until the first real toggle.
  const [expanded, setExpanded] = useState<Set<string> | null>(null);
  const liveMap = useIndexProgressStore((s) => (bankId ? s.byBank.get(bankId) : undefined));

  const defaultExpanded = useMemo(() => {
    const all = new Set<string>();
    if (treeQuery.data) walkDirs(treeQuery.data.tree, (dir) => all.add(dir.path));
    return all;
  }, [treeQuery.data]);

  const effectiveExpanded = expanded ?? defaultExpanded;

  const pending = useMemo(() => {
    const set = new Set(treeQuery.data?.pending ?? []);
    if (liveMap) for (const path of liveMap.keys()) set.add(path);
    return set;
  }, [treeQuery.data, liveMap]);

  function toggleDir(path: string) {
    const next = new Set(effectiveExpanded);
    if (next.has(path)) next.delete(path);
    else next.add(path);
    setExpanded(next);
  }

  function renderNode(node: TreeNode, depth: number): React.ReactNode {
    const pad = 12 + depth * 14;
    if (node.type === "dir") {
      const open = effectiveExpanded.has(node.path);
      return (
        <div key={node.path}>
          <div
            className="tree-node tree-dir"
            style={{ paddingLeft: pad }}
            onClick={() => toggleDir(node.path)}
          >
            <span className="tree-twisty">{open ? "▾" : "▸"}</span>
            <span className="tree-label">{node.name}/</span>
          </div>
          {open && (node.children ?? []).map((child) => renderNode(child, depth + 1))}
        </div>
      );
    }

    const classes = ["tree-node", "is-file"];
    if (node.path === selectedPath) classes.push("is-selected");
    if (!node.indexed) classes.push("not-indexed");
    if (pending.has(node.path)) classes.push("is-pending");

    return (
      <div
        key={node.path}
        className={classes.join(" ")}
        style={{ paddingLeft: pad }}
        title={(node.headings ?? []).join(" · ") || node.path}
        onClick={() => onSelectFile(node.path)}
      >
        <span className="tree-twisty" />
        <span className="tree-label">{node.name}</span>
        <span className="tree-chunks">
          {node.indexed ? `${node.chunks}×` : t("memory.indexedState.no")}
        </span>
      </div>
    );
  }

  return (
    <div className={`pane${isMob ? " is-mob" : ""}`}>
      <div className="pane-head">
        <button type="button" className="pane-back" onClick={onBack}>
          {t("common.btn.back")}
        </button>
        <h2>{t("memory.pane.files")}</h2>
        {treeQuery.data && (
          <span className="pane-sub">
            {t("memory.count.files", { n: treeQuery.data.files })} ·{" "}
            {t("memory.count.dirs", { n: treeQuery.data.dirs })}
          </span>
        )}
      </div>
      <div className="pane-body">
        {!bankId ? (
          <p className="empty-hint">{t("memory.tree.selectBankHint")}</p>
        ) : !treeQuery.data ? (
          <p className="empty-hint">{t("memory.tree.loading")}</p>
        ) : (treeQuery.data.tree.children ?? []).length === 0 ? (
          <p className="empty-hint">{t("memory.tree.emptyMd")}</p>
        ) : (
          <div className="tree">
            {(treeQuery.data.tree.children ?? []).map((child) => renderNode(child, 0))}
          </div>
        )}
      </div>
    </div>
  );
}
