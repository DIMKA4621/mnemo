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

## «Додати банк» moved from the topbar into the Banks pane header

User's call after looking at the finished Памʼять page: the button sat next to the language/theme toggles and read as part of them.
Now it is the Banks column's own action (right side of its `pane-head`), the same place "Reindex file" lives in the Content column, so each pane carries its own actions.
This revises the 2026-08-19 rule in `topics/console-ui.md` ("the header holds the tab's actions, e.g. `Додати банк`"): on Памʼять the topbar now holds only the title, the counters and the shell's language/theme toggles.

## MN-53 / MN-30 / MN-52 — Памʼять actions, Journal and Settings brought to the Памʼять level

User-driven decisions, all recorded so they are not relitigated:

- Памʼять: «Додати банк» is an icon-only «+» in the Banks pane header; single-file reindex moved from the Content header button to a counter-clockwise ↻ that appears on a file row's hover/focus (left of the name, mirrored `ReloadOutlined` — `UndoOutlined` reads as "undo"), confirmed by a «Переіндексувати файл?» modal whose buttons are just «Так» / «Скасувати» (the title already says the action); the queued note is an antd `message`, so the tree never shifts.
- Journal: ↻ refresh is the third control of the filter row, not in the topbar; a hit's file path is the link into Памʼять (no separate «Відкрити файл» button, also in the Indexing detail); the whole snippet block toggles expand/collapse but a drag-selection does not toggle it; `score` and `sim` are gone from hits — `sim` is computed only by the retired auto-inject gate, so it was always «—», and `score` is a raw RRF value with no meaning to a person, the rank number says the same; the events list header looks like a Памʼять `pane-head` («Події»); cards label their numbers («3 хіти · 41 мс») and show time-only for today; the detail panel keeps only facts the card does not show; the period filter has «Весь час» (no `since`, bounded only by log retention, 30 days by default).
- Settings: «Оновити діагностику» stays in its own row inside Обслуговування — moving it into the tab strip was tried and reverted at the user's request.
- Text search over queries is deferred to MN-54 (likely needs `GET /api/logs` support).

Root cause found on the way: `tokens.css` had a bare `svg { fill: none; stroke: currentColor; … }` for the hand-drawn icons, which blanked every `@ant-design/icons` glyph (Select arrows, the new «+», the Modal.confirm icon).
It is now `svg:where(:not(.anticon *))`.
Also learned: antd's `--ant-*` CSS variables exist only on antd component roots, so hand CSS outside an antd node must use the console's own tokens (`--accent`, …).
Two agents edited the same tree in parallel (Памʼять vs Journal/Settings) and once raced on that `tokens.css` rule; giving each one a disjoint file list in the brief prevented the rest.

## MN-55 — Journal snippet cue and hit separation

The snippet's «показати повністю / згорнути» text is gone: a collapsed snippet ends with «…»; screen readers get «Розгорнути / Згорнути фрагмент» as the label and the text via `aria-describedby`.
Hits merged into one panel because they were ruled in `--line-table`, nearly invisible on both themes; each hit is now its own row ruled in `--line`, like the Памʼять bank list.

## Journal snippet preview: 8 visual lines, not 2 sentences

The preview used to be the chunk's first two sentences, split by a regex ported from the vanilla console.
That regex required whitespace after the stop, so on a dot inside `6.6`, `page-journal.js` or `3.29:1` `String.match` failed and skipped ahead, silently dropping text: previews started mid-sentence («6 + React 19)…», «jsonl\` sidecar…»).
The vanilla console on `master` (`page-journal.js`'s `splitSentences`) has the same bug.
User's call: no sentence logic at all — the collapsed snippet is CSS `line-clamp: 8` on an inner span (on the padded outer box line 9 shows through the padding), the browser draws the «…», and the toggle appears only when a measurement (`scrollHeight > clientHeight`, re-run by a `ResizeObserver`) says the clamp actually cut something.
