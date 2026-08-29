/**
 * Same inline-SVG set as the vanilla console (`src/webui/static/index.html`)
 * — literal `<path>` data carried over verbatim so the sidebar nav icons
 * are pixel-identical, not redrawn. `base.css`'s global `svg { ... }` rule
 * (stroke: currentColor, no fill) is ported into `tokens.css`, so these
 * stay unstyled here on purpose.
 */

export function MemoryIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 4.5h11.5A2.5 2.5 0 0 1 19 7v12H7.5A2.5 2.5 0 0 1 5 16.5z" />
      <path d="M5 16.5A2.5 2.5 0 0 1 7.5 14H19" />
    </svg>
  );
}

export function JournalIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 6h16M4 12h16M4 18h10" />
    </svg>
  );
}

export function SettingsIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="3.1" />
      <path d="M19 12c0-.37-.03-.73-.08-1.09l2-1.55-2-3.46-2.44.98a8 8 0 0 0-1.9-1.1L14.2 3H9.8l-.38 2.78a8 8 0 0 0-1.9 1.1l-2.44-.98-2 3.46 2 1.55a7.8 7.8 0 0 0 0 2.18l-2 1.55 2 3.46 2.44-.98a8 8 0 0 0 1.9 1.1L9.8 21h4.4l.38-2.78a8 8 0 0 0 1.9-1.1l2.44.98 2-3.46-2-1.55c.05-.36.08-.72.08-1.09z" />
    </svg>
  );
}

/** No legacy equivalent — the Agents page (MN-32) has no icon in the shipped
 *  console yet. A plain bounded-panel glyph, consistent line weight with
 *  the three above, kept intentionally simple pending the real feature. */
export function AgentsIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="4" y="5" width="16" height="14" rx="2" />
      <path d="M9 10h.01M15 10h.01M8 15h8" />
    </svg>
  );
}

/** Four-square glyph, ported verbatim from the mockup's Реєстр nav item
 *  (`.claude/scratch/agents-page-mockup/index.html`). */
export function RegistryIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="4" y="4" width="7" height="7" rx="1.5" />
      <rect x="13" y="4" width="7" height="7" rx="1.5" />
      <rect x="4" y="13" width="7" height="7" rx="1.5" />
      <rect x="13" y="13" width="7" height="7" rx="1.5" />
    </svg>
  );
}

export function CollapseIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M14 6l-6 6 6 6" />
    </svg>
  );
}
