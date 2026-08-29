/**
 * Exact port of `src/webui/static/styles/base.css`'s `:root` custom
 * properties. This file is the single source of truth for color/shape
 * values in the React console — `antd-theme.ts` (AntD `ConfigProvider`) and
 * `tokens.css` (native, non-AntD elements) both read from it, so a palette
 * change happens in one place.
 *
 * Keep this in sync with `base.css` by hand until that file is retired at
 * cutover; after cutover this file *is* the source of truth and `base.css`
 * is gone.
 */

export interface ColorTokens {
  bg: string;
  bgPane: string;
  bgSunken: string;
  bgHover: string;
  line: string;
  lineSunken: string;
  lineTable: string;
  fg: string;
  fgDim: string;
  fgMute: string;
  accent: string;
  accentMuted: string;
  ok: string;
  busy: string;
  idle: string;
  err: string;

  btnBg: string;
  btnBgHover: string;
  btnBorderHover: string;

  selBg: string;
  selFg: string;
  segActiveBg: string;

  badgeGitFg: string;
  badgeGitBorder: string;
  badgeGitBg: string;
  badgeOffFg: string;
  badgeOffBorder: string;
  badgeOffBg: string;
  badgeFrozenFg: string;
  badgeFrozenBorder: string;
  badgeFrozenBg: string;
  badgeReadyFg: string;
  badgeReadyBorder: string;
  badgeReadyBg: string;
  badgeIndexingFg: string;
  badgeIndexingBorder: string;
  badgeIndexingBg: string;
  badgeEmptyFg: string;
  badgeEmptyBorder: string;
  badgeEmptyBg: string;

  dangerBg: string;
  dangerBorder: string;
  dangerFg: string;
  warnBg: string;
  warnBorder: string;
  warnFg: string;

  trackBg: string;

  btnPrimaryBg: string;
  btnPrimaryBorder: string;
  btnPrimaryFg: string;
  btnPrimaryBgHover: string;
  btnPrimaryBorderHover: string;

  btnDangerBg: string;
  btnDangerBorder: string;
  btnDangerFg: string;
  btnDangerBgHover: string;
  btnDangerBorderHover: string;

  dangerBgSoft: string;
  dangerFgStrong: string;
  dangerFgSoft: string;
  menuDangerFg: string;

  successBg: string;
  successBorder: string;
  successFg: string;

  shadowCard: string;
  shadowModal: string;
  shadowMenu: string;
  overlay: string;

  chunkLine: string;
  flashBg: string;
}

/** Not theme-dependent — the same in dark and light (base.css's `:root`). */
export interface SharedTokens {
  radius: string;
  mono: string;
  sans: string;
  sidebarW: string;
  sidebarRail: string;
}

// Dark is the default palette (base.css `:root`, no attribute needed).
export const darkTokens: ColorTokens = {
  bg: "#14161a",
  bgPane: "#1b1e24",
  bgSunken: "#101216",
  bgHover: "#23272f",
  line: "#2c313a",
  lineSunken: "#191c22",
  lineTable: "#23262c",
  fg: "#dfe3ea",
  fgDim: "#949cab",
  fgMute: "#8d97a8",
  accent: "#6aa8ff",
  accentMuted: "#7fa2d5",
  ok: "#4bbf7a",
  busy: "#e0a94a",
  idle: "#7b8494",
  err: "#e2685f",

  btnBg: "#2a2f38",
  btnBgHover: "#333a45",
  btnBorderHover: "#3d444f",

  selBg: "#232c3a",
  selFg: "#cfe1ff",
  segActiveBg: "#2f3742",

  badgeGitFg: "#9ecb8a",
  badgeGitBorder: "#35482f",
  badgeGitBg: "#212b1e",
  badgeOffFg: "#d0a0a0",
  badgeOffBorder: "#4a3030",
  badgeOffBg: "#2c2020",
  badgeFrozenFg: "#8fc6dd",
  badgeFrozenBorder: "#2f4756",
  badgeFrozenBg: "#1b2a33",
  badgeReadyFg: "#8ee0ac",
  badgeReadyBorder: "#2e4d3a",
  badgeReadyBg: "#1c2b23",
  badgeIndexingFg: "#f0c37a",
  badgeIndexingBorder: "#574427",
  badgeIndexingBg: "#2e2618",
  badgeEmptyFg: "#9fb2cc",
  badgeEmptyBorder: "#33415a",
  badgeEmptyBg: "#1d2531",

  dangerBg: "#3a2020",
  dangerBorder: "#5a2f2c",
  dangerFg: "#f3c3bf",
  warnBg: "#2e2618",
  warnBorder: "#574427",
  warnFg: "#f0c37a",

  trackBg: "#2a2f38",

  btnPrimaryBg: "#2c4d7d",
  btnPrimaryBorder: "#3a5f95",
  btnPrimaryFg: "#e8f0fb",
  btnPrimaryBgHover: "#35598e",
  btnPrimaryBorderHover: "#47709f",

  btnDangerBg: "#6d2e2a",
  btnDangerBorder: "#8a3d38",
  btnDangerFg: "#f7dedb",
  btnDangerBgHover: "#7d3631",
  btnDangerBorderHover: "#9c4741",

  dangerBgSoft: "#33201f",
  dangerFgStrong: "#f0aaa3",
  dangerFgSoft: "#eda9a3",
  menuDangerFg: "#f0958c",

  successBg: "#1d2b21",
  successBorder: "#2e4d3a",
  successFg: "#a9dcbc",

  shadowCard: "0 18px 40px rgba(0, 0, 0, .38)",
  shadowModal: "0 18px 40px rgba(0, 0, 0, .45)",
  shadowMenu: "0 8px 22px rgba(0, 0, 0, .45)",
  overlay: "rgba(8, 9, 12, .62)",

  chunkLine: "#3c536f",
  flashBg: "#2a3b2c",
};

// `:root[data-theme="light"]` — a complete override of every color token.
// `fgMute` is deliberately darker than the frozen mockup's original
// (`#6a7280` → `#5a6676`): at the shell's smallest text (10–10.5px) the
// mockup's value measured 4.0–4.3:1 against the lightest sunken/selected
// backgrounds, under the 4.5:1 AA floor — see base.css's own comment.
export const lightTokens: ColorTokens = {
  bg: "#f2f3f6",
  bgPane: "#ffffff",
  bgSunken: "#e9ebef",
  bgHover: "#e4e7ec",
  line: "#d7dbe2",
  lineSunken: "#e6e9ee",
  lineTable: "#e8ebef",
  fg: "#1b1f27",
  fgDim: "#545c69",
  fgMute: "#5a6676",
  accent: "#2c66c9",
  accentMuted: "#406199",
  ok: "#2f9257",
  busy: "#9d6c0c",
  idle: "#7b8494",
  err: "#c4423a",

  btnBg: "#f4f5f8",
  btnBgHover: "#e9ecf1",
  btnBorderHover: "#c5cbd5",

  selBg: "#e2ebfb",
  selFg: "#17417e",
  segActiveBg: "#e2e5eb",

  badgeGitFg: "#3d6b2c",
  badgeGitBorder: "#c3dcb6",
  badgeGitBg: "#eef7ea",
  badgeOffFg: "#8f3a35",
  badgeOffBorder: "#e2bcb9",
  badgeOffBg: "#fbeeed",
  badgeFrozenFg: "#1d6b8a",
  badgeFrozenBorder: "#b6d7e4",
  badgeFrozenBg: "#eaf5fa",
  badgeReadyFg: "#22703f",
  badgeReadyBorder: "#b9debe",
  badgeReadyBg: "#ecf8ef",
  badgeIndexingFg: "#8a6209",
  badgeIndexingBorder: "#e5d3a4",
  badgeIndexingBg: "#fdf6e5",
  badgeEmptyFg: "#3d5580",
  badgeEmptyBorder: "#c3cfe4",
  badgeEmptyBg: "#eef2f9",

  dangerBg: "#fbeceb",
  dangerBorder: "#e6bfbc",
  dangerFg: "#97302a",
  warnBg: "#fdf4e3",
  warnBorder: "#e6d3a8",
  warnFg: "#8a6209",

  trackBg: "#e4e7ec",

  btnPrimaryBg: "#2f6fe0",
  btnPrimaryBorder: "#2559bd",
  btnPrimaryFg: "#ffffff",
  btnPrimaryBgHover: "#2559bd",
  btnPrimaryBorderHover: "#1f4a9e",

  btnDangerBg: "#d1453a",
  btnDangerBorder: "#b23a30",
  btnDangerFg: "#ffffff",
  btnDangerBgHover: "#b23a30",
  btnDangerBorderHover: "#9c322a",

  dangerBgSoft: "#f7e3e1",
  dangerFgStrong: "#7a281f",
  dangerFgSoft: "#9c473d",
  menuDangerFg: "#b23a30",

  successBg: "#e4f7ec",
  successBorder: "#a9e0bf",
  successFg: "#1f6b3f",

  shadowCard: "0 12px 28px rgba(30, 41, 59, .12)",
  shadowModal: "0 16px 34px rgba(30, 41, 59, .16)",
  shadowMenu: "0 6px 16px rgba(30, 41, 59, .16)",
  overlay: "rgba(30, 34, 44, .4)",

  chunkLine: "#a9c6e8",
  flashBg: "#dff3e4",
};

export const sharedTokens: SharedTokens = {
  radius: "6px",
  mono: 'ui-monospace, "Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace',
  sans: 'ui-sans-serif, "Segoe UI", system-ui, sans-serif',
  sidebarW: "208px",
  sidebarRail: "52px",
};

export type ThemeMode = "dark" | "light";

export function tokensFor(mode: ThemeMode): ColorTokens {
  return mode === "light" ? lightTokens : darkTokens;
}
