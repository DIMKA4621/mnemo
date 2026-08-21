# 2026-08-21 (четверте) — кабінет: чистка тексту й перевантаження на Памʼяті/Налаштуваннях

`feat(ui): tidy memory/settings page text and layout`

Serie of small, user-driven UI fixes across `src/webui/static/`, done directly
(no subagent — small enough to hold in one pass), each verified live on the
real installed engine (`~/.claude/mnemo`, redeploy via `install.ps1` after
every edit) with chrome-devtools MCP, both themes.

## Sidebar

`Журнал`'s count badge removed (`shell.js`'s `PAGES.journal.count: null`,
`index.html`'s `#sb-count-journal` span dropped) — unlike Памʼять's bank
count (small, stable), the log total grows without bound and read as noise.
Памʼять's bank count kept.

## Памʼять page

- **Pluralization.** New shared `pluralizeUk(n, [one, few, many])` in
  `app.js` — the standard Slavic count-noun triad (`n%10==1 && n%100!=11` →
  one, `n%10 in 2..4 && n%100 not in 12..14` → few, else → many). Replaces
  the hardcoded `' банк(и) · ' + files + ' файлів · ' + chunks + ' чанків'`
  in `memoryHeaderHtml()` — now agrees for any count (1 банк, 2 банки, 5
  банків), not just the two forms the old string covered.
- **Bank card decluttered.** Three independent fixes in `bankCard()`
  (`page-memory.js`):
  1. `у черзі 0` — an empty queue is the normal state, not worth a stat of
     its own; the span is only appended when `bank.queued` is truthy.
  2. The muted `statusNote(bank)` line under the stats row is now skipped
     entirely when `bank.status === 'ready'` — its text in that case is
     literally "індекс готовий", the same fact the status badge above
     already carries. Still shown for `indexing`/`empty`, where it adds real
     information the badge doesn't.
  3. `востаннє: ...` → `остання індексація: ...` — the old label didn't say
     *which* "last" (last query? last index?); it is `bank.last_indexed`.
- **Third column header stopped being a different visual language.**
  `#file-title` used to have its static "Вміст" text overwritten with the
  open file's path on every `openFile()`/`renderFile()` call, and carried
  its own CSS override (`.pane-head h2.file-title` — lowercase mono, not the
  caps/bold the other two headers use) to make that fit. Fixed at the root:
  the header stays the static "Вміст" caption (id dropped from the `<h2>`
  entirely, JS never touches it), and the filename moved into `.file-meta`
  as its own bold line (`.file-meta-path`) above the existing size/indexed/
  chunks/sha256 line (now `.file-meta-info`) — two lines instead of one
  overloaded one. The now-empty `.pane-head h2.file-title` CSS block deleted
  outright rather than left dead.

## Налаштування page

- **Duplicate section heading removed.** `renderSettings()` built
  `el('h2', {text: section.label})` as the form's first child on every
  section — but the active tab button directly above already shows that
  same label highlighted. Removed; the lede paragraph is now the form's
  first child (`.lede`'s own `margin-top` dropped from `8px` to `0` since it
  no longer follows the h2 it was spaced from). `.set-form h2` CSS rule
  deleted (dead once nothing renders an h2 there).
- **`.set-label` bolder/darker.** Was `color: fg-dim; font-size: 12px;` with
  no explicit weight — read at almost the same visual weight as `.set-note`
  right below it, so a setting's own name didn't stand out from its
  description. Now `color: fg` (full strength) `font-weight: 650`
  `font-size: 12.5px`.
- **Subtle divider between adjacent fields.** `.set-field + .set-field {
  border-top: 1px solid var(--line); padding-top: 12px; }` — the previous
  14px flex `gap` alone read as one unbroken form (theme → autostart →
  auto-update → status all ran together); this doesn't touch spacing
  between non-field siblings (e.g. the "Перевірити оновлення" button block),
  only field-to-field.
- **"Стан" block kept as-is** (short summary; user confirmed the existing
  short version in Загальні is fine even though Обслуговування repeats
  more of it in the doctor report).

## Verification

`node --check` on all four touched JS files (`app.js`, `shell.js`,
`page-memory.js`, `page-settings.js`), then `install.ps1` redeploy to the
real engine, then a live chrome-devtools pass: Памʼять header pluralizes
correctly at 4/223/3910 ("4 банки · 223 файли · 3910 чанків"), bank cards
show no `у черзі 0` and no redundant ready-note, file view shows the
two-line `.file-meta` with a static caps "ВМІСТ" header, Налаштування →
Загальні shows no duplicate heading with bold labels and visible dividers
between fields — all confirmed in **both** dark and light theme (toggled
live, verified via `getComputedStyle` where the screenshot tool showed a
stale-paint artifact — see gotcha below). Zero console errors.

## Follow-up round (same day): ledes, an unreachable divider, a stale warning

A second pass of user feedback on the same two tabs, same method (direct
edit, `install.ps1` redeploy, chrome-devtools verification each time):

- **`.set-note` under "Тема кабінету" simplified.** Was "Вибір цього
  браузера, **не машинне значення** — тому застосовується одразу й не
  чекає «Зберегти»." — the aside was unclear jargon to the user. Now just
  "Вибір цього браузера — тому застосовується одразу й не чекає
  «Зберегти»."
- **Both `SECTION_LEDE.general` and `SECTION_LEDE.embed` rewritten** from
  enumerating-the-controls prose ("Тема кабінету, автозапуск при вході в
  систему й стан процесу цієї машини…" / "Бекенд обирається пресетом: URL,
  модель, ширина вектора й префікси…") to one short sentence naming *what
  the tab is for*, matching Обслуговування's existing lede style (that one
  was never enumerating — it just says "the same structured doctor report
  CLI shows as text").
- **`.set-field + .set-field`'s adjacent-sibling divider has a real gap**:
  it only fires between two directly-adjacent `.set-field` elements, and
  several spots in this page have a non-field element (a check button, a
  runtime note, a `set-lead` paragraph) sitting between two fields that
  visually need separating anyway. Two such gaps existed: before "Стан" in
  Загальні (preceded by the auto-update check button + its result badge)
  and before "Оперативна памʼять" in Модель ембедингу (preceded by a
  `set-lead` paragraph under the local backend, or by the model/url/dim
  fields under an API backend). Fixed with an explicit `.set-divider`
  (`height: 0; border-top: 1px solid var(--line);`) hand-inserted right
  before each of those three render call sites, rather than trying to make
  the CSS rule itself smarter — the three spots are known and few, and a
  general fix would have had to reason about every non-field element type
  that can sit between two fields.
- **Removed the "Зупинку й перезапуск робить лише команда…" `.set-warn`
  paragraph** that used to sit under "Стан" in Загальні — user's call, no
  replacement text.

Verified live both changes render correctly (screenshot + `list_console_
messages` clean) on Загальні and Модель ембедингу.

## Third round (same day): tree pane pluralization, and telling temporary from durable

- **`renderTree()`'s `#tree-sub` line** ("60 файлів · 2 тек") was still the
  one spot on Памʼять never migrated to `pluralizeUk()` — hardcoded singular
  ` файлів`/` тек` produced "61 файлів" (wrong: should agree as "файл" for
  61). Fixed with the same helper as the header, and — per the user's own
  suggestion — reworded "тека" → "директорія" here specifically (genitive
  plural "тек" read ambiguous/informal next to a count; "директорій" reads
  clearer). Scoped to this one line only: other "тека" wording in the
  cabinet (the "Додати банк"/folder-picker copy) describes picking a folder
  in casual terms and was left alone — changing it wasn't asked and would
  have been a bigger, unrelated vocabulary sweep.
- **Bank card: transient vs. durable text was one flat grey.** `statusNote`
  (e.g. "база є, свіжі зміни доїжджають") and "остання індексація: …" both
  rendered `.muted` (`var(--fg-mute)`), identical color — the one thing that
  changes minute to minute and the one thing that's a fixed historical fact
  read as the same kind of information. Fixed two ways in `bankCard()`
  (`page-memory.js`): (1) new `.note-live` class (`color: var(--busy)`,
  same tint as the "індексується" badge and the live `.progress-text` line)
  applied to the status-note span only when `bank.status === 'indexing'` —
  the `empty` case stays plain `.muted`, since an idle empty bank isn't a
  live process, just a fact; (2) reordered the card so the status-note row
  moves from *between* the two permanent facts (stats, then note, then
  "остання індексація") to *after* both of them, immediately before where
  the live progress block/one-off note would append — everything temporary
  now clusters at the card's bottom instead of interrupting the durable
  facts above it.
- Verified the color/reorder by simulating `bank.status = 'indexing'`
  client-side via `evaluate_script` + `renderBanks()` (no real reindex
  needed to see it — restored immediately after) — the note turned the same
  orange as the badge and sat directly under "остання індексація". Tree
  pane pluralization confirmed on a real bank: "61 файл · 2 директорії".

## Gotcha: chrome-devtools MCP screenshot staleness after an SPA route change

After toggling the theme on one page (Налаштування) and then clicking a
sidebar route to a different page (Памʼять) that was already rendered
*before* the toggle, `take_screenshot` returned a **stale dark-theme image**
even though `document.documentElement.dataset.theme` and every computed CSS
custom property (`getComputedStyle(...).getPropertyValue('--bg-pane')`, etc.)
correctly reported the light theme's values. A `navigate_page {type:
"reload"}` (full page reload, not just an SPA route swap) resolved it and
the screenshot then matched the DOM state. Treat a screenshot that looks
"impossibly wrong" after an SPA-only navigation as a tool-side paint/cache
timing artifact worth cross-checking with `evaluate_script` +
`getComputedStyle` before concluding the underlying page is actually broken.
