"use client";

import { Button, Input, Select } from "antd";
import { ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { useT } from "@/lib/i18n/hooks";
import { useBanks } from "@/hooks/useMemoryQueries";

export type JournalPeriod = "1h" | "24h" | "7d" | "30d" | "all";

interface JournalFiltersProps {
  kind: "query" | "index";
  bank: string;
  onBankChange: (bank: string) => void;
  period: JournalPeriod;
  onPeriodChange: (period: JournalPeriod) => void;
  search: string;
  onSearchChange: (search: string) => void;
  onRefresh: () => void;
  refreshing: boolean;
}

/** The bank filter's value is the bank id (labelled by name), so a rename
 *  keeps the selection; `GET /api/logs` resolves an id ref first (contract 6.4). */
export function JournalFilters({
  kind,
  bank,
  onBankChange,
  period,
  onPeriodChange,
  search,
  onSearchChange,
  onRefresh,
  refreshing,
}: JournalFiltersProps) {
  const t = useT();
  const banksQuery = useBanks();
  const banks = banksQuery.data ?? [];

  return (
    <div className="filters">
      <Select
        size="small"
        style={{ minWidth: 160 }}
        value={bank}
        onChange={onBankChange}
        options={[
          { value: "", label: t("journal.filter.allBanks") },
          ...banks.map((b) => ({ value: b.id, label: b.name })),
        ]}
        aria-label={t("journal.filter.bankLabel")}
      />
      <Select
        size="small"
        style={{ minWidth: 130 }}
        value={period}
        onChange={onPeriodChange}
        options={[
          { value: "1h", label: t("journal.filter.period1h") },
          { value: "24h", label: t("journal.filter.period24h") },
          { value: "7d", label: t("journal.filter.period7d") },
          { value: "30d", label: t("journal.filter.period30d") },
          { value: "all", label: t("journal.filter.periodAll") },
        ]}
        aria-label={t("journal.filter.periodLabel")}
      />
      <Input
        size="small"
        style={{ width: 220 }}
        allowClear
        prefix={<SearchOutlined />}
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
        placeholder={t(kind === "query" ? "journal.filter.searchQuery" : "journal.filter.searchPath")}
        aria-label={t(kind === "query" ? "journal.filter.searchQuery" : "journal.filter.searchPath")}
        spellCheck={false}
        autoComplete="off"
      />
      <Button
        size="small"
        icon={<ReloadOutlined spin={refreshing} />}
        title={t("journal.header.refreshTitle")}
        aria-label={t("journal.header.refreshTitle")}
        onClick={onRefresh}
      />
    </div>
  );
}
