"use client";

import { Select } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { useBanks } from "@/hooks/useMemoryQueries";

export type JournalPeriod = "1h" | "24h" | "7d" | "30d";

interface JournalFiltersProps {
  bank: string;
  onBankChange: (bank: string) => void;
  period: JournalPeriod;
  onPeriodChange: (period: JournalPeriod) => void;
}

/** Bank filter matches by NAME, not id — `GET /api/logs`'s `bank` param
 *  resolves a name/id/path ref the same way every other bank-ref endpoint
 *  does (contract 6.4), and the vanilla console filtered by name for the
 *  same reason: `bank_id` on a log row is a stable hash, not something a
 *  person picks out of a dropdown. */
export function JournalFilters({ bank, onBankChange, period, onPeriodChange }: JournalFiltersProps) {
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
          ...banks.map((b) => ({ value: b.name, label: b.name })),
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
        ]}
        aria-label={t("journal.filter.periodLabel")}
      />
    </div>
  );
}
