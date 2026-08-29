import { theme as antdTheme, type ThemeConfig } from "antd";
import { sharedTokens } from "./design-tokens";

const RADIUS_PX = parseInt(sharedTokens.radius, 10);

/**
 * One AntD theme config, not two — every color token below is a literal
 * `var(--token)` reference into `tokens.css`'s custom properties, not a
 * resolved hex value. AntD's CSS-in-JS happily accepts any valid CSS
 * string, so the actual color resolution happens in the browser at
 * `var()`-lookup time, keyed off `<html data-theme>` — the same attribute
 * the inline bootstrap script in `app/layout.tsx`'s `<head>` already sets
 * before first paint (see that file's comment).
 *
 * This is deliberate, not a shortcut: a static export has no server-known
 * theme, so anything resolved through React state (a `mode: 'dark' |
 * 'light'` branch picked *after* mount) would flash the wrong palette for a
 * frame on every load — exactly the FOUC problem the vanilla console's own
 * inline script exists to prevent for native CSS. Routing AntD through the
 * same custom properties extends that same fix to AntD's components,
 * instead of needing a second, JS-driven fix that could only ever run
 * after hydration.
 *
 * `darkAlgorithm` is used as the fixed base for AntD's own internally
 * *derived* shades (states this file does not pin token-by-token) — since
 * nearly every color that actually reaches the screen is pinned explicitly
 * below, the residual light-mode surfaces this could affect are minor; call
 * out anything that reads wrong in the Phase 1 screenshot comparison rather
 * than solving it preemptively.
 */
export function buildAntdTheme(): ThemeConfig {
  const v = (name: string) => `var(--${name})`;

  return {
    algorithm: antdTheme.darkAlgorithm,
    token: {
      colorPrimary: v("accent"),
      colorLink: v("accent"),
      colorSuccess: v("ok"),
      colorWarning: v("busy"),
      colorError: v("err"),
      colorInfo: v("accent"),

      colorBgContainer: v("bg-pane"),
      colorBgLayout: v("bg"),
      colorBgElevated: v("bg-pane"),
      colorBgSpotlight: v("bg-hover"),
      colorBgMask: v("overlay"),

      colorText: v("fg"),
      colorTextSecondary: v("fg-dim"),
      colorTextTertiary: v("fg-mute"),
      colorTextQuaternary: v("fg-mute"),

      colorBorder: v("line"),
      colorBorderSecondary: v("line-sunken"),
      colorSplit: v("line"),

      colorFillSecondary: v("bg-hover"),
      colorFillTertiary: v("bg-sunken"),

      borderRadius: RADIUS_PX,
      borderRadiusLG: RADIUS_PX,
      borderRadiusSM: RADIUS_PX,

      // Literal font stacks, never `next/font/google` — see the reskin
      // checklist. An external font was the single biggest reason the first
      // wireframe read as "not mnemo".
      fontFamily: sharedTokens.sans,
      fontFamilyCode: sharedTokens.mono,
      // Narrowed from AntD's 14px default toward the current console's
      // dense 12-13px scale (base.css: `body { font: 13px/1.5 ... }`).
      fontSize: 13,

      boxShadow: v("shadow-card"),
      boxShadowSecondary: v("shadow-menu"),

      wireframe: false,
    },
    components: {
      Layout: {
        headerBg: v("bg-pane"),
        bodyBg: v("bg"),
        siderBg: v("bg-pane"),
      },
      Menu: {
        itemBg: "transparent",
        itemColor: v("fg-dim"),
        itemHoverBg: v("bg-hover"),
        itemHoverColor: v("fg"),
        itemSelectedBg: v("sel-bg"),
        itemSelectedColor: v("sel-fg"),
        activeBarBorderWidth: 0,
      },
      Button: {
        borderRadius: RADIUS_PX,
        controlHeight: 26,
        paddingInline: 10,
        primaryShadow: "none",
        defaultShadow: "none",
        dangerShadow: "none",
      },
      Input: {
        colorBgContainer: v("bg-sunken"),
        controlHeight: 26,
      },
      Select: {
        colorBgContainer: v("bg-sunken"),
        controlHeight: 26,
      },
      Table: {
        headerBg: v("bg-sunken"),
        headerColor: v("fg-dim"),
        borderColor: v("line-table"),
        rowHoverBg: v("bg-hover"),
      },
      Modal: {
        contentBg: v("bg-pane"),
        headerBg: v("bg-pane"),
        boxShadow: v("shadow-modal"),
      },
      Tag: {
        defaultBg: v("badge-empty-bg"),
        defaultColor: v("badge-empty-fg"),
      },
      Tabs: {
        itemColor: v("fg-dim"),
        itemSelectedColor: v("fg"),
        itemHoverColor: v("fg"),
        inkBarColor: v("accent"),
      },
      Segmented: {
        itemSelectedBg: v("seg-active-bg"),
        itemSelectedColor: v("fg"),
        itemColor: v("fg-dim"),
        trackBg: "transparent",
      },
      Switch: {
        colorPrimary: v("accent"),
      },
      Tooltip: {
        colorBgSpotlight: v("bg-sunken"),
        colorTextLightSolid: v("fg"),
      },
    },
  };
}
