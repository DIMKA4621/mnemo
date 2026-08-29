"use client";

import { Dropdown, type MenuProps } from "antd";
import { useT } from "@/lib/i18n/hooks";
import type { BankInfo } from "@/lib/api/memory";
import { bankState } from "@/lib/memory/format";

interface BankMenuProps {
  bank: BankInfo;
  onSync: (bank: BankInfo) => void;
  onRebuild: (bank: BankInfo) => void;
  onOpenToken: (bank: BankInfo) => void;
  onSetState: (bank: BankInfo, state: BankInfo["state"]) => void;
  onRemove: (bank: BankInfo) => void;
}

/**
 * The one per-bank action menu — every card action lives here, none on the
 * card itself (`CLAUDE.md`'s recorded decision, ported from the vanilla
 * console's single `···` button).
 */
export function BankMenu({ bank, onSync, onRebuild, onOpenToken, onSetState, onRemove }: BankMenuProps) {
  const t = useT();
  const current = bankState(bank);

  const stateItems: MenuProps["items"] = (["enabled", "frozen", "disabled"] as const).map((value) => ({
    key: `state-${value}`,
    label: t(`memory.bankState.${value}.label`) + (value === current ? " ✓" : ""),
    title: t(`memory.bankState.${value}.note`),
    onClick: () => onSetState(bank, value),
  }));

  const items: MenuProps["items"] = [
    {
      key: "sync",
      label: t("common.bankMenu.sync"),
      title: t("common.bankMenu.syncTitle"),
      onClick: () => onSync(bank),
    },
    {
      key: "rebuild",
      label: t("common.bankMenu.rebuild"),
      title: t("common.bankMenu.rebuildTitle"),
      onClick: () => onRebuild(bank),
    },
    { type: "divider" },
    {
      key: "token",
      label: t("common.token.title"),
      title: t("common.bankMenu.mcpTitle"),
      onClick: () => onOpenToken(bank),
    },
    { type: "divider" },
    {
      key: "state",
      label: t("common.bankMenu.stateLabel"),
      children: stateItems,
    },
    { type: "divider" },
    {
      key: "remove",
      label: t("common.bankMenu.remove"),
      title: t("common.bankMenu.removeTitle"),
      danger: true,
      onClick: () => onRemove(bank),
    },
  ];

  return (
    <Dropdown menu={{ items }} trigger={["click"]}>
      <button
        className="btn-menu"
        title={t("memory.bank.menuBtnTitle")}
        aria-label={t("memory.bank.menuBtnTitle")}
        onClick={(ev) => ev.stopPropagation()}
      >
        <span aria-hidden="true">···</span>
      </button>
    </Dropdown>
  );
}
