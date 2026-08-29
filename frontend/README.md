mnemo's web console frontend — Next.js (App Router) + TypeScript, static export (`output: 'export'`), Ant Design + Zustand + TanStack Query.

Not a standalone deployable app: `npm run release` builds it and copies the exported static site into `../src/webui/static/`, where the existing FastAPI `StaticFiles` mount (`src/api.py`, `/ui`) serves it. Nothing here ever runs a Node process on an end user's machine — the committed `src/webui/static/` output is what ships.

## Day-to-day dev workflow: use `next dev`, not repeated full builds

**Iterate with `npm run dev` (hot reload) — do not rebuild+copy for every small tweak.** A full `npm run release` cycle (build, static export, copy into `src/webui/static/`, restart a static server to see the result) is slow and is meant for verifying the actual shipped artifact, not for every CSS/markup adjustment while developing. Reserve a full build for:
- right before a commit that lands a real chunk of work (a phase, or a meaningful fix), and
- whenever you need to confirm something that genuinely differs between dev and export mode (routing through the real `StaticFiles` mount, `basePath`, etc.).

```bash
npm run dev
```

By default this proxies `/api/*` to the real running `mnemo` service on `127.0.0.1:4646` (see `next.config.ts`'s `rewrites()` — dev-mode only, disabled under `output: 'export'`). Point it at the stdlib-only Python fixture mock instead when you don't want to touch a real backend:

```bash
MNEMO_BACKEND=http://127.0.0.1:8919 npm run dev
```

(with `python ../src/webui/devserver.py --port 8919` running separately). Note the WS client dials the backend's own `/ws` directly in dev, bypassing the rewrite proxy — Next's rewrite layer does not reliably forward a WebSocket upgrade handshake (see the comment in `next.config.ts` and `src/lib/ws/client.ts`).

## Before a commit

```bash
npx vitest run   # unit tests (includes the uk plural-rule CLDR cross-check)
npx eslint .      # must exit 0
npm run release   # next build (static export) + copy into ../src/webui/static/
```

Then smoke-test the actual exported output — not the dev server — against either `python ../src/webui/devserver.py` or a real `mnemo` instance, cold-loading each route directly (not just navigating client-side), since only that path exercises the real `StaticFiles`/`html=True` directory-index resolution the dev server has to emulate separately.

## Design tokens / theming

`src/lib/theme/design-tokens.ts` and `src/app/tokens.css` are a 1:1 port of the old console's `base.css` custom properties (both themes). Ant Design is reskinned through `ConfigProvider` in `src/app/providers.tsx` against `var(--token)` references into the same tokens — not resolved hex per theme, so both AntD and hand-built components read the exact same source at paint time with no flash on load or toggle. Do not introduce a second source of truth for a color/spacing value; add it to `design-tokens.ts` first.

## i18n

`src/lib/i18n/messages/{en,uk}.json`, wired through `next-intl` (client-only, no locale-prefixed routing). See `src/lib/i18n/hooks.ts` for the `tMaybe`-equivalent wrapper the old console relied on. The Ukrainian plural rule is verified against `Intl.PluralRules` in `src/lib/i18n/plural.test.ts` — re-run that test if you touch anything plural-related.
