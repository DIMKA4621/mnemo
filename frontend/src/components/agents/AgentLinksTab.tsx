"use client";

import { useState } from "react";
import { Button } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { useAgentLinks } from "@/hooks/useAgentQueries";
import { useCatalog } from "@/hooks/useCatalogQueries";
import { useDetachLink } from "@/hooks/useAgentMutations";
import { catalogEntryMeta } from "@/lib/registry/format";
import { AttachPickerModal } from "./AttachPickerModal";
import { useInlineNote, InlineNote } from "@/components/common/InlineNote";
import { ApiError } from "@/lib/api/fetcher";
import type { AgentInfo, LinkInfo } from "@/lib/api/agents";
import type { CatalogCategory, CatalogEntry } from "@/lib/api/catalog";

interface AgentLinksTabProps {
  agent: AgentInfo;
}

const CATEGORIES: CatalogCategory[] = ["mcp", "skill", "rule"];

/**
 * MCP / Skills / Rules attached to this one agent — three independent
 * subsections, each showing only what is already attached (never the full
 * registry), ported from the mockup's `renderAgsLinks`/`renderAgsLinkSection`
 * (`.claude/scratch/agents-page-mockup/app.js`). Attaching happens through
 * "+ Add" → `AttachPickerModal`, filtered to registry entries this agent
 * doesn't already carry in that category. A per-subsection "Change" toggle
 * reveals the detach (🗑) button on each row plus the "+ Add" row itself —
 * same two-state UI the mockup used, so a section stays uncluttered until
 * the user actually wants to edit it.
 *
 * Every action here (attach, detach) is an immediate mutation — there is no
 * draft to hold — so this tab registers no `AgentSectionController` with
 * `AgentSettings`, and the shared footer Save button stays hidden while it
 * is active (same as Налаштування's Обслуговування tab, which also
 * registers none).
 */
export function AgentLinksTab({ agent }: AgentLinksTabProps) {
  const t = useT();
  const linksQuery = useAgentLinks(agent.slug, true);
  const detachMutation = useDetachLink();

  const [editModes, setEditModes] = useState<Record<CatalogCategory, boolean>>({
    mcp: false,
    skill: false,
    rule: false,
  });
  const [pickCategory, setPickCategory] = useState<CatalogCategory>("mcp");
  const [pickOpen, setPickOpen] = useState(false);
  const [note, setNote] = useInlineNote();

  function toggleEdit(category: CatalogCategory) {
    setEditModes((prev) => ({ ...prev, [category]: !prev[category] }));
  }

  function openPicker(category: CatalogCategory) {
    setPickCategory(category);
    setPickOpen(true);
  }

  async function detach(category: CatalogCategory, link: LinkInfo) {
    try {
      await detachMutation.mutateAsync({ slug: agent.slug, category, entryId: link.entry_id });
      setNote(t("agents.links.detachedNote", { name: link.name }));
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <>
      <p className="wiz-hint" style={{ marginBottom: 10 }}>{t("agents.links.hint")}</p>
      <div className="ags-links-sections">
        {CATEGORIES.map((category) => (
          <LinkSection
            key={category}
            category={category}
            links={linksQuery.data?.[category] ?? []}
            editing={editModes[category]}
            onToggleEdit={() => toggleEdit(category)}
            onAdd={() => openPicker(category)}
            onDetach={(link) => detach(category, link)}
            detachBusy={detachMutation.isPending}
          />
        ))}
      </div>

      <AttachPickerModal
        key={pickOpen ? "pick-open" : "pick-closed"}
        open={pickOpen}
        category={pickCategory}
        agent={agent}
        existingLinks={linksQuery.data?.[pickCategory] ?? []}
        onClose={() => setPickOpen(false)}
        onAttached={(name) => {
          setPickOpen(false);
          setNote(t("agents.links.attachedNote", { name, agent: agent.name }));
        }}
      />
      <InlineNote text={note} tone="success" />
    </>
  );
}

interface LinkSectionProps {
  category: CatalogCategory;
  links: LinkInfo[];
  editing: boolean;
  onToggleEdit: () => void;
  onAdd: () => void;
  onDetach: (link: LinkInfo) => void;
  detachBusy: boolean;
}

function LinkSection({ category, links, editing, onToggleEdit, onAdd, onDetach, detachBusy }: LinkSectionProps) {
  const t = useT();
  const catalogQuery = useCatalog(category);

  return (
    <div className="reg-section">
      <div className="reg-section-head">
        <h2>{t(`registry.tabs.${category}`)}</h2>
        <span className="cnt">{links.length}</span>
        <div style={{ flex: 1 }} />
        <Button size="small" onClick={onToggleEdit}>
          {editing ? t("agents.links.doneBtn") : t("agents.links.editBtn")}
        </Button>
      </div>

      {!links.length ? (
        <p className="empty-hint">{t("agents.links.empty")}</p>
      ) : (
        links.map((link) => (
          <LinkRow
            key={link.entry_id}
            category={category}
            link={link}
            entry={catalogQuery.data?.find((e) => e.id === link.entry_id) ?? null}
            editing={editing}
            onDetach={() => onDetach(link)}
            busy={detachBusy}
          />
        ))
      )}

      {editing && (
        <button type="button" className="ags-link-add" onClick={onAdd}>
          {t("agents.links.addBtn")}
        </button>
      )}
    </div>
  );
}

interface LinkRowProps {
  category: CatalogCategory;
  link: LinkInfo;
  entry: CatalogEntry | null;
  editing: boolean;
  onDetach: () => void;
  busy: boolean;
}

function LinkRow({ category, link, entry, editing, onDetach, busy }: LinkRowProps) {
  const t = useT();
  return (
    <div className="reg-line">
      <div className="ags-link-row">
        <div className="ags-link-text">
          <div className="reg-item-name">{link.name}</div>
          <div className="reg-item-meta">{entry ? catalogEntryMeta(entry) : t("agents.links.missingFromRegistry")}</div>
        </div>
        <div style={{ flex: 1 }} />
        {editing && (
          <button
            type="button"
            className="icon-btn"
            title={t("agents.links.detachTitle")}
            aria-label={t("agents.links.detachTitle")}
            disabled={busy}
            onClick={onDetach}
          >
            🗑
          </button>
        )}
      </div>
      {category === "mcp" && entry && entry.vars.length > 0 && (
        <div className="ags-vars ags-vars-ro">
          {entry.vars.map((v) => (
            <div className="ags-var-row" key={v}>
              <span>{`{{${v}}}`}</span>
              <code>{link.vars[v] || "—"}</code>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
