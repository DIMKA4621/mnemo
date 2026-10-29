# 2026-10-29 — console redesign: Hallmark tried and dropped, antd tooling installed

## Decision

The console redesign continues on `feature/webui-nextjs-rewrite` (Next.js 16.3 + antd 6.6 + React 19), not on the vanilla-JS console on `master`.
User's reason: maintainability — a look built from antd tokens and components stays changeable, a look built from hand-written CSS does not.
Tracked as MN-51 (relates to MN-31).
After looking at the branch's existing Памʼять page live, the user found its composition fine and narrowed the work to a pass over every hover, button, menu and modal whose colours are wrong (e.g. the remove-bank button and the "Reindex file" hover rendering black).

## Hallmark (nutlope/hallmark) — what was learned

Tried first, on a throwaway branch off `master` (`feature/webui-hallmark-redesign`, never committed; the work sits in `git stash` as "hallmark restyle of vanilla console").

- It is a rule set + linter (`sloplint`) + 50 themes that emits **plain HTML/CSS**; it is not a component library and fights antd's own design system.
- Its useful part for us: the audit.
  On the vanilla console it found a real horizontal-scroll bug at 1024/1280px (scrollWidth 1327), tracked-caps mono badges, opacity-only disabled states, `width`/`margin-left` animations, sub-12px text.
- Its **Console** theme (graphite in three steps, amber = current/primary, cool blue = what the system measured) was the user's pick; the restyle came out clean (0 Floor fails on the Memory files) but the user found it **too flat** — the theme forbids shadows by design, and a restyle keeps the old three-equal-panes layout.
- Theme faces Host Grotesk / Atkinson Hyperlegible Mono have **no Cyrillic**; Onest + JetBrains Mono (both OFL, Cyrillic) were the substitutes, self-hosted, never Google Fonts (loopback invariant).
- On Windows `npx skills add` copies the skill with CRLF, which breaks its scripts' front-matter parsing (`theme.mjs scaffold` → "no header block"); fix was converting the skill's text files to LF.

## Installed for the antd stack

- `.claude/skills/antd` + global `@ant-design/cli` (v6.6.8): offline props/tokens/demos/semantic slots/lint for antd v4–v6.
  `antd setup` insists on appending a block to `CLAUDE.md`; that write was reverted — the instruction lives in `ui-dev`'s charter instead, because subagents do not read `CLAUDE.md`.
  Its MCP mode was not wired: the skill + CLI cover the same lookups.
- `vercel-react-best-practices`, `vercel-composition-patterns`, `web-design-guidelines` from `vercel-labs/agent-skills`.
- Looked at and **not** installed: `AntGroupDesign/Ant-Design-Skill` (official Ant Group design team) — enterprise admin templates that require `@ant-design/pro-components` plus its own `global-style.css`; covers tables/forms/lists, little of what the Памʼять page is (tree + document viewer).

`.claude/agents/ui-dev.md` gained a Tooling section and a "antd first, CSS last" order: global token → component token / semantic slots → antd layout primitives → scoped CSS module as the last resort.

## MN-51 — black buttons and hovers: antd cannot derive colours from `var()`

Symptom: on the Next.js console the remove-bank button, "Reindex file" hover, `···` menu hovers and modal buttons rendered near-black.
Root cause, measured in the browser: `antd-theme.ts` passed seed tokens as `var(--accent)` strings; antd does colour math on seeds and cannot parse `var()`, so `--ant-color-primary` became `#030303` and every derived shade (hover/active/bg/border, the whole `error` family) followed.
`darkAlgorithm` was also fixed for both themes.

Fix (`ui-dev`, reviewed): one antd theme per mode with literal hex from `design-tokens.ts`, `defaultAlgorithm` for light, `[darkAlgorithm, keepSeedColors]` for dark — `darkAlgorithm` remaps the seed itself (#6aa8ff → #5d92dc), `keepSeedColors` puts the exact seeds back while keeping the derived shades.
Danger buttons are `type="primary" danger`; the dropdown menu border is one CSS rule (antd has no token for it); the submenu arrow is a `menu.expandIcon` glyph because the default outline chevron stays a sub-pixel hairline at any `fontSizeIcon`.

Trade-off the user chose: the static export prerenders antd components (Journal, Settings) in the dark palette, so in the **light** theme the inline bootstrap script sets `data-hydrating` and `.mnemo-main` stays hidden until `providers.tsx`'s layout effect clears it, with a 3 s fallback if the bundle never hydrates.
Rejected alternatives: accept a 0.1–0.4 s dark flash in light mode; generate antd CSS for both themes at build time (new module, depends on `@ant-design/cssinjs` output internals).
Verified frame by frame on a production export: zero dark-palette antd frames in light on `/ui/settings/`, `/ui/journal/`, `/ui/memory/`; dark unchanged.

Two consecutive local `npm run release` runs produce identical `src/webui/static/` — the build is deterministic on one machine; whether CI's Linux build matches a Windows build is what `frontend-static-guard` decides.
