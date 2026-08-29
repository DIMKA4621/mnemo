import type { BankInfo } from "@/lib/api/memory";

export function fmtBytes(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KiB`;
  return `${(n / 1048576).toFixed(1)} MiB`;
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso);
  const p = (v: number) => String(v).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** `bank.state` is always present in this phase's `BankInfo` — kept for
 *  parity with the vanilla console's own fallback-tolerant reader. */
export function bankState(bank: BankInfo): BankInfo["state"] {
  if (bank.state) return bank.state;
  return bank.enabled === false ? "disabled" : "enabled";
}
