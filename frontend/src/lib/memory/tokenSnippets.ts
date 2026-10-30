/**
 * Config-snippet helpers for `BankTokenPanel` — ported from the pre-cutover
 * vanilla console (`git show a5361db~1:src/webui/static/app.js`). Best-effort
 * polish per the MN-34 plan, not gate-blocking; kept faithful to the
 * original since the source was already fully available.
 */

// What `mnemo init` names the project's own memory entry, and the one name
// whose variable stays on the bare `MNEMO_` prefix.
export const DEFAULT_INSTANCE = "mnemo-memory";

/** Cyrillic to Latin, enough for a config entry name — not a transliteration
 *  standard, just readable and MCP-server-name-safe. */
const TRANSLIT: Record<string, string> = {
  а: "a", б: "b", в: "v", г: "h", ґ: "g", д: "d", е: "e",
  є: "ye", ж: "zh", з: "z", и: "y", і: "i", ї: "yi", й: "y",
  к: "k", л: "l", м: "m", н: "n", о: "o", п: "p", р: "r",
  с: "s", т: "t", у: "u", ф: "f", х: "kh", ц: "ts", ч: "ch",
  ш: "sh", щ: "shch", ь: "", ю: "yu", я: "ya",
  ы: "y", э: "e", ъ: "", ё: "e",
};

/** A starting entry name for a bank — used to tell several mnemo servers
 *  apart in `~/.claude.json` (the URL no longer carries the bank name). */
export function defaultEntryName(name: string): string {
  const slug = [...String(name || "").toLowerCase().replace(/['’ʼ`]/g, "")]
    .map((ch) => TRANSLIT[ch] ?? ch)
    .join("")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  if (!slug || slug === "mnemo") return DEFAULT_INSTANCE;
  return slug.startsWith("mnemo") ? slug : `mnemo-${slug}`;
}

/** What an MCP server name may hold; anything else becomes a separator. */
export function sanitizeEntryName(value: string): string {
  return String(value).replace(/[^A-Za-z0-9_-]+/g, "-").replace(/-{2,}/g, "-");
}

/** The token variable's name — the only one that varies per entry.
 *  `MNEMO_HOST`/`MNEMO_PORT` stay shared: they describe the service, not
 *  the bank. */
export function tokenVar(entry: string): string {
  if (entry === DEFAULT_INSTANCE || entry === "mnemo") return "MNEMO_TOKEN";
  const cleaned = entry.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").toUpperCase();
  return cleaned ? `${cleaned}_TOKEN` : "MNEMO_TOKEN";
}

export function mcpDocument(entry: string, url: string): string {
  const servers: Record<string, { type: string; url: string }> = {};
  servers[entry] = { type: "http", url };
  return JSON.stringify({ mcpServers: servers }, null, 2);
}

export function sedLineText(entry: string): string {
  const v = tokenVar(entry);
  return `-e "s|{{${v}}}|\${${v}}|g"`;
}

/** Copy without going through the screen — the async clipboard API needs a
 *  secure context (127.0.0.1 is one, another LAN host may not be), so this
 *  falls back to a legacy `execCommand('copy')` textarea. */
export async function copyText(text: string): Promise<boolean> {
  if (typeof navigator !== "undefined" && navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // fall through to the legacy path
    }
  }
  if (typeof document === "undefined") return false;
  const sink = document.createElement("textarea");
  sink.className = "tok-copysink";
  sink.value = text;
  document.body.appendChild(sink);
  sink.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(sink);
  return ok;
}
