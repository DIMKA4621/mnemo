"use client";

import { useEffect, useState } from "react";
import { Button, Input } from "antd";
import { ModalShell } from "@/components/common/ModalShell";
import { useT } from "@/lib/i18n/hooks";
import { useCatalog } from "@/hooks/useCatalogQueries";
import { useCreateCatalogEntry } from "@/hooks/useCatalogMutations";
import { findDuplicateMcp, parseVars } from "@/lib/registry/format";
import { ApiError } from "@/lib/api/fetcher";
import type { CatalogCategory, CatalogEntry } from "@/lib/api/catalog";

const { TextArea } = Input;

interface AddEntryModalProps {
  open: boolean;
  category: CatalogCategory;
  onClose: () => void;
  onAdded: (entry: CatalogEntry) => void;
}

/**
 * Manual "＋ Додати" entry — category fixed to whichever Реєстр tab is
 * active when it opens, same as the mockup's `openAddEntryModal`
 * (`.claude/scratch/agents-page-mockup/app.js`). No scanning of any kind.
 */
export function AddEntryModal({ open, category, onClose, onAdded }: AddEntryModalProps) {
  const t = useT();
  // Shares `RegistryList`'s cache — feeds the live dedup check below without
  // an extra request.
  const catalogQuery = useCatalog(category);
  const createMutation = useCreateCatalogEntry();

  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [liveError, setLiveError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Debounced JSON/dup validation, same 220ms interval as the mockup's
  // `addEntryRecomputeDebounced`. Every `setLiveError` call is inside the
  // timeout callback (not synchronous in the effect body) so a re-render
  // never cascades directly out of this effect — reset-on-open state lives
  // on `RegistryPage`'s `key={addOpen ...}` remount instead, not here.
  useEffect(() => {
    const timer = setTimeout(() => {
      if (!open || category !== "mcp") {
        setLiveError(null);
        return;
      }
      if (!content.trim()) {
        setLiveError(t("registry.error.emptyConfig"));
        return;
      }
      try {
        JSON.parse(content);
      } catch (e) {
        setLiveError(t("registry.error.invalidJson", { message: e instanceof Error ? e.message : String(e) }));
        return;
      }
      const dup = findDuplicateMcp(catalogQuery.data ?? [], content, null);
      setLiveError(dup ? t("registry.error.duplicate", { name: dup.name }) : null);
    }, 220);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- catalogQuery.data/t change every render; only content/open/category should re-arm the debounce
  }, [content, open, category]);

  const parsedVars = category === "mcp" ? parseVars(content) : [];
  const canSubmit = !!name.trim() && !!content.trim() && !liveError;

  async function submit() {
    if (!canSubmit) return;
    setSubmitError(null);
    try {
      const entry = await createMutation.mutateAsync({ category, name: name.trim(), content });
      onAdded(entry);
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <ModalShell
      open={open}
      title={t("registry.addModal.title", { category: t(`registry.category.${category}`) })}
      ariaLabel={t("registry.addModal.ariaLabel")}
      onClose={onClose}
      busy={createMutation.isPending}
      footer={
        <>
          <Button onClick={onClose} disabled={createMutation.isPending}>
            {t("common.btn.cancel")}
          </Button>
          <Button type="primary" onClick={submit} disabled={!canSubmit} loading={createMutation.isPending}>
            {t("registry.addModal.submit")}
          </Button>
        </>
      }
    >
      <label className="fs-label" htmlFor="reg-add-name">
        {t("registry.addModal.nameLabel")}
      </label>
      <Input id="reg-add-name" value={name} onChange={(e) => setName(e.target.value)} />

      <label className="fs-label" htmlFor="reg-add-content">
        {t(
          category === "mcp"
            ? "registry.addModal.configLabel"
            : category === "skill"
              ? "registry.addModal.contentLabelSkill"
              : "registry.addModal.contentLabelRule",
        )}
      </label>
      <TextArea
        id="reg-add-content"
        className="reg-code"
        rows={category === "mcp" ? 9 : 8}
        value={content}
        onChange={(e) => setContent(e.target.value)}
        placeholder={category === "mcp" ? t("registry.addModal.configPlaceholder") : undefined}
      />
      {liveError && <p className="modal-error">{liveError}</p>}
      {submitError && <p className="modal-error">{submitError}</p>}

      {category === "mcp" && (
        <>
          <label className="fs-label">{t("registry.detail.varsLabel")}</label>
          {parsedVars.length ? (
            <div className="reg-var-chips">
              {parsedVars.map((v) => (
                <span key={v} className="chip">{`{{${v}}}`}</span>
              ))}
            </div>
          ) : (
            <p className="empty-hint" style={{ padding: 0 }}>{t("registry.detail.varsEmpty")}</p>
          )}
        </>
      )}
    </ModalShell>
  );
}
