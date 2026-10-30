import { theme as antdTheme, type MappingAlgorithm, type ThemeConfig } from "antd";
import { sharedTokens, tokensFor, type ThemeMode } from "./design-tokens";

const RADIUS_PX = parseInt(sharedTokens.radius, 10);

// `darkAlgorithm` remaps the brand seeds onto its dark palette (#6aa8ff comes
// out as #5d92dc), but our dark palette is already the dark-mode colour. Keep
// the derived hover/active/bg shades and put the seeds themselves back.
const keepSeedColors: MappingAlgorithm = (seed, map) => ({
  ...map!,
  colorPrimary: seed.colorPrimary,
  colorSuccess: seed.colorSuccess,
  colorWarning: seed.colorWarning,
  colorError: seed.colorError,
  colorInfo: seed.colorInfo,
  colorLink: seed.colorLink,
});

/**
 * One AntD theme per mode, with literal colours. AntD derives hover/active/
 * bg/border shades from the seeds with colour math, which cannot parse a
 * `var(--token)` — a `var()` seed silently collapses every derived shade to
 * near-black. The static export prerenders AntD in the dark palette; how the
 * light theme avoids showing that is in `app/layout.tsx`'s bootstrap comment.
 */
function buildAntdTheme(mode: ThemeMode): ThemeConfig {
  const c = tokensFor(mode);

  return {
    algorithm: mode === "light" ? antdTheme.defaultAlgorithm : [antdTheme.darkAlgorithm, keepSeedColors],
    token: {
      colorPrimary: c.accent,
      colorLink: c.accent,
      colorSuccess: c.ok,
      colorWarning: c.busy,
      colorError: c.err,
      colorInfo: c.accent,

      colorBgContainer: c.bgPane,
      colorBgLayout: c.bg,
      colorBgElevated: c.bgPane,
      colorBgSpotlight: c.bgHover,
      colorBgMask: c.overlay,

      colorText: c.fg,
      colorTextSecondary: c.fgDim,
      colorTextTertiary: c.fgMute,
      colorTextQuaternary: c.fgMute,

      colorBorder: c.line,
      colorBorderSecondary: c.lineSunken,
      colorBorderDisabled: c.line,
      colorSplit: c.line,
      // AntD draws every focus ring in this colour; its derived tint is too
      // faint on either background to be seen (base.css: 2px solid accent).
      colorPrimaryBorder: c.accent,
      lineWidthFocus: 2,

      colorFillSecondary: c.bgHover,
      colorFillTertiary: c.bgSunken,

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

      boxShadow: c.shadowCard,
      boxShadowSecondary: c.shadowMenu,

      wireframe: false,
    },
    components: {
      Layout: {
        headerBg: c.bgPane,
        bodyBg: c.bg,
        siderBg: c.bgPane,
      },
      Menu: {
        itemBg: "transparent",
        itemColor: c.fgDim,
        itemHoverBg: c.bgHover,
        itemHoverColor: c.fg,
        itemSelectedBg: c.selBg,
        itemSelectedColor: c.selFg,
        activeBarBorderWidth: 0,
      },
      // The console's own button palette: a neutral default whose hover stays
      // neutral, and solid primary/danger fills distinct from the accent and
      // error text colours. Destructive actions are `type="primary" danger`.
      Button: {
        borderRadius: RADIUS_PX,
        controlHeight: 26,
        paddingInline: 10,
        primaryShadow: "none",
        defaultShadow: "none",
        dangerShadow: "none",
        defaultBg: c.btnBg,
        defaultColor: c.fg,
        defaultBorderColor: c.line,
        defaultHoverBg: c.btnBgHover,
        defaultHoverColor: c.fg,
        defaultHoverBorderColor: c.btnBorderHover,
        defaultActiveBg: c.btnBgHover,
        defaultActiveColor: c.fg,
        defaultActiveBorderColor: c.btnBorderHover,
        colorPrimary: c.btnPrimaryBg,
        colorPrimaryHover: c.btnPrimaryBgHover,
        colorPrimaryActive: c.btnPrimaryBorderHover,
        primaryColor: c.btnPrimaryFg,
        colorError: c.btnDangerBg,
        colorErrorHover: c.btnDangerBgHover,
        colorErrorActive: c.btnDangerBorderHover,
        dangerColor: c.btnDangerFg,
      },
      // AntD's danger item uses one token for its text and its hover fill, so
      // the hover text is set to keep contrast on that fill in both themes.
      Dropdown: {
        controlItemBgHover: c.bgHover,
        colorError: c.menuDangerFg,
        colorTextLightSolid: mode === "light" ? c.bgPane : c.bg,
      },
      Input: {
        colorBgContainer: c.bgSunken,
        controlHeight: 26,
      },
      Select: {
        colorBgContainer: c.bgSunken,
        controlHeight: 26,
        optionActiveBg: c.bgHover,
        optionSelectedBg: c.selBg,
        optionSelectedColor: c.selFg,
        optionSelectedFontWeight: 500,
        // The selected option under the pointer/keyboard uses this alias token,
        // not `optionSelectedBg`; left alone it is a saturated accent fill.
        controlItemBgActiveHover: c.selBg,
      },
      Table: {
        headerBg: c.bgSunken,
        headerColor: c.fgDim,
        borderColor: c.lineTable,
        rowHoverBg: c.bgHover,
      },
      Modal: {
        contentBg: c.bgPane,
        headerBg: c.bgPane,
        boxShadow: c.shadowModal,
      },
      Tag: {
        defaultBg: c.badgeEmptyBg,
        defaultColor: c.badgeEmptyFg,
      },
      Tabs: {
        itemColor: c.fgDim,
        itemSelectedColor: c.fg,
        itemHoverColor: c.fg,
        inkBarColor: c.accent,
      },
      Segmented: {
        itemSelectedBg: c.segActiveBg,
        itemSelectedColor: c.fg,
        itemColor: c.fgDim,
        trackBg: "transparent",
        // One bar, not a row of pills: no inset, square inner corners, so a
        // hovered or selected option meets its neighbour on a straight edge.
        // The outer frame and the corner clip are in shell.css.
        trackPadding: 0,
        borderRadiusSM: 0,
        borderRadiusXS: 0,
      },
      Switch: {
        colorPrimary: c.accent,
      },
      Tooltip: {
        colorBgSpotlight: c.bgSunken,
        colorTextLightSolid: c.fg,
      },
    },
  };
}

export const antdThemes: Record<ThemeMode, ThemeConfig> = {
  dark: buildAntdTheme("dark"),
  light: buildAntdTheme("light"),
};
