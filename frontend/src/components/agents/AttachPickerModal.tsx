"use client";

import { useState } from "react";
import { Button, Input } from "antd";
import { ModalShell } from "@/components/common/ModalShell";
import { useT } from "@/lib/i18n/hooks";
import { useCatalog } from "@/hooks/useCatalogQueries";
import { useAttachLink } from "@/hooks/useAgentMutations";
import { catalogEntryMeta } from "@/lib/registry/format";
import { ApiError } from "@/lib/api/fetcher";
import type { AgentInfo, LinkInfo } from "@/lib/api/agents";
import type { CatalogCategory, CatalogEntry } from "@/lib/api/catalog";

function normName(s: string): string {
  return s.trim().toLowerCase();
}

interface AttachPickerModalProps {
  open: boolean;
  category: CatalogCategory;
  agent: AgentInfo;
  /** This agent's own links in `category` — used both to filter step 1's
   *  list (never re-offer an already-attached entry) and to client-side
   *  dedup step 2's name field against a sibling link's name. */
  existingLinks: LinkInfo[];
  onClose: () => void;
  onAttached: (name: string) => void;
}

/**
 * Two-step "＋ Add" on the ⚙ screen's Links tab: (1) pick an unattached
 * registry entry, (2) a local name (editable, defaults to the entry's own
 * name) plus values for any `{{VAR}}` the entry declares — per-agent only,
 * the registry entry itself is never touched. Ported from the mockup's
 * `openPickModal`/`renderPickModal` (`.claude/scratch/agents-page-mockup/
 * app.js`).
 */
export function AttachPickerModal({ open, category, agent, existingLinks, onClose, onAttached }: AttachPickerModalProps) {
  const t = useT();
  const catalogQuery = useCatalog(category);
  const attachMutation = useAttachLink();

  const [selected, setSelected] = useState<CatalogEntry | null>(null);
  const [name, setName] = useState("");
  const [vars, setVars] = useState<Record<string, string>>({});
  const [submitError, setSubmitError] = useState<string | null>(null);

  const attachedIds = new Set(existingLinks.map((l) => l.entry_id));
  const available = (catalogQuery.data ?? []).filter((e) => !attachedIds.has(e.id));

  function pick(entry: CatalogEntry) {
    setSelected(entry);
    setName(entry.name);
    setVars({});
    setSubmitError(null);
  }

  function back() {
    setSelected(null);
    setSubmitError(null);
  }

  const nameClash = existingLinks.some((l) => normName(l.name) === normName(name));
  const canSubmit = !!name.trim() && !nameClash;

  async function submit() {
    if (!selected || !canSubmit) return;
    setSubmitError(null);
    try {
      const link = await attachMutation.mutateAsync({
        slug: agent.slug,
        category,
        req: { entry_id: selected.id, name: name.trim(), vars: category === "mcp" ? vars : undefined },
      });
      onAttached(link.name);
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <ModalShell
      open={open}
      title={t("agents.links.pick.title", { category: t(`registry.category.${category}`) })}
      ariaLabel={t("agents.links.pick.ariaLabel")}
      onClose={onClose}
      busy={attachMutation.isPending}
      wide
      footer={
        !selected ? (
          <Button onClick={onClose}>{t("common.btn.cancel")}</Button>
        ) : (
          <>
            <Button onClick={back} disabled={attachMutation.isPending}>
              {t("agents.links.pick.back")}
            </Button>
            <div style={{ flex: 1 }} />
            <Button type="primary" disabled={!canSubmit} loading={attachMutation.isPending} onClick={submit}>
              {t("agents.links.pick.submit")}
            </Button>
          </>
        )
      }
    >
      {!selected ? (
        <div className="pick-list">
          {available.length === 0 ? (
            <p className="empty-hint">{t("agents.links.pick.empty")}</p>
          ) : (
            available.map((entry) => (
              <button key={entry.id} type="button" className="pick-row" onClick={() => pick(entry)}>
                <div className="pick-row-text">
                  <div className="pick-row-name">{entry.name}</div>
                  <div className="pick-row-meta">{catalogEntryMeta(entry)}</div>
                </div>
              </button>
            ))
          )}
        </div>
      ) : (
        <>
          <p className="wiz-hint">{catalogEntryMeta(selected)}</p>
          <label className="wiz-field-label" htmlFor="pick-name">
            {t("agents.links.pick.nameLabel")}
          </label>
          <Input id="pick-name" value={name} onChange={(e) => setName(e.target.value)} />
          {nameClash && <p className="modal-error">{t("agents.links.error.nameTaken", { name: name.trim() })}</p>}

          {category === "mcp" && selected.vars.length > 0 && (
            <>
              <label className="wiz-field-label" style={{ marginTop: 8 }}>
                {t("agents.links.pick.varsLabel")}
              </label>
              <div className="ags-vars" style={{ padding: 0 }}>
                {selected.vars.map((v) => (
                  <label className="ags-var-row" key={v}>
                    <span>{`{{${v}}}`}</span>
                    <Input
                      value={vars[v] || ""}
                      placeholder={t("agents.links.pick.varPlaceholder")}
                      onChange={(e) => setVars((prev) => ({ ...prev, [v]: e.target.value }))}
                    />
                    {!vars[v]?.trim() && <span className="ags-var-unset">{t("agents.links.pick.varUnset")}</span>}
                  </label>
                ))}
              </div>
            </>
          )}
          {submitError && <p className="modal-error">{submitError}</p>}
        </>
      )}
    </ModalShell>
  );
}
