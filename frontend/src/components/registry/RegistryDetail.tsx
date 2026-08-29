"use client";

import { useEffect, useState } from "react";
import { Button, Input } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { useCatalog } from "@/hooks/useCatalogQueries";
import { useUpdateCatalogEntry } from "@/hooks/useCatalogMutations";
import { contentLabelKey, findDuplicateMcp, parseVars } from "@/lib/registry/format";
import { ApiError } from "@/lib/api/fetcher";
import type { CatalogCategory, CatalogEntry } from "@/lib/api/catalog";

const { TextArea } = Input;

interface RegistryDetailProps {
  category: CatalogCategory;
  entry: CatalogEntry | null;
  onSaved: (entry: CatalogEntry) => void;
}

/**
 * Selected entry's full record — read view plus inline name+content editing,
 * ported behaviour from the mockup's `renderRegDetail`/`startRegEdit`
 * (`.claude/scratch/agents-page-mockup/app.js`). No delete here: the mockup
 * never exposed removing a registry entry from this page either — only
 * detaching a link on an agent's ⚙ screen (Фаза C, MN-48).
 */
export function RegistryDetail({ category, entry, onSaved }: RegistryDetailProps) {
  const t = useT();
  // Same query key as `RegistryList`'s `useCatalog(category)` — this shares
  // that cache rather than re-fetching, and gives the live dedup check below
  // the rest of the category's entries to compare against.
  const catalogQuery = useCatalog(category);
  const updateMutation = useUpdateCatalogEntry();

  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [liveError, setLiveError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Debounced JSON/dup validation, same 220ms interval as the mockup's
  // `regEditRecomputeDebounced`. Every `setLiveError` call is inside the
  // timeout callback (not synchronous in the effect body) so a re-render
  // never cascades directly out of this effect. Editing state resets when
  // the selected entry changes via `RegistryPage`'s `key={entry?.id ...}`
  // remount, not an effect here.
  useEffect(() => {
    const timer = setTimeout(() => {
      if (!editing || category !== "mcp") {
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
      const dup = findDuplicateMcp(catalogQuery.data ?? [], content, entry?.id ?? null);
      setLiveError(dup ? t("registry.error.duplicate", { name: dup.name }) : null);
    }, 220);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- catalogQuery.data/t change every render; only content/editing/category should re-arm the debounce
  }, [content, editing, category, entry?.id]);

  if (!entry) {
    return (
      <div className="reg-detail-pane">
        <p className="empty-hint">{t("registry.detail.selectHint")}</p>
      </div>
    );
  }

  function startEdit() {
    setName(entry!.name);
    setContent(entry!.content);
    setLiveError(null);
    setSubmitError(null);
    setEditing(true);
  }

  function cancelEdit() {
    setEditing(false);
    setLiveError(null);
    setSubmitError(null);
  }

  const canSave = !!name.trim() && !!content.trim() && !liveError;

  async function save() {
    if (!entry || !canSave) return;
    const patch: { name?: string; content?: string } = {};
    if (name.trim() !== entry.name) patch.name = name.trim();
    if (content !== entry.content) patch.content = content;
    if (Object.keys(patch).length === 0) {
      setEditing(false);
      return;
    }
    setSubmitError(null);
    try {
      const updated = await updateMutation.mutateAsync({ entryId: entry.id, patch });
      setEditing(false);
      onSaved(updated);
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err));
    }
  }

  const parsedVars = category === "mcp" ? parseVars(content) : [];

  return (
    <div className="reg-detail-pane">
      <div className="reg-detail-head">
        {editing ? (
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("registry.detail.nameLabel")}
          />
        ) : (
          <div className="reg-detail-name">{entry.name}</div>
        )}
      </div>
      <div className="reg-detail-body">
        {editing ? (
          <>
            <TextArea
              className="reg-code"
              rows={category === "mcp" ? 12 : 10}
              value={content}
              onChange={(e) => setContent(e.target.value)}
            />
            {liveError && <p className="modal-error">{liveError}</p>}
            {submitError && <p className="modal-error">{submitError}</p>}
            {category === "mcp" && (
              <>
                <h2 style={{ marginTop: 14 }}>{t("registry.detail.varsLabel")}</h2>
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
            <div className="reg-edit-actions">
              <Button onClick={cancelEdit} disabled={updateMutation.isPending}>
                {t("common.btn.cancel")}
              </Button>
              <Button type="primary" onClick={save} disabled={!canSave} loading={updateMutation.isPending}>
                {t("registry.detail.save")}
              </Button>
            </div>
          </>
        ) : (
          <>
            <div className="reg-detail-head-row">
              <h2>{t(contentLabelKey(category))}</h2>
              <button
                type="button"
                className="icon-btn icon-btn-edit"
                title={t("registry.detail.editTitle")}
                aria-label={t("registry.detail.editTitle")}
                onClick={startEdit}
              >
                ✎
              </button>
            </div>
            <pre className={`reg-code${category === "mcp" ? "" : " reg-code-prose"}`}>{entry.content}</pre>
            {category === "mcp" && (
              <>
                <h2 style={{ marginTop: 14 }}>{t("registry.detail.varsLabel")}</h2>
                {entry.vars.length ? (
                  <div className="reg-var-chips">
                    {entry.vars.map((v) => (
                      <span key={v} className="chip">{`{{${v}}}`}</span>
                    ))}
                  </div>
                ) : (
                  <p className="empty-hint" style={{ padding: 0 }}>{t("registry.detail.varsEmpty")}</p>
                )}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
