# 2026-08-21 (четверте) — кабінет: чистка тексту й перевантаження на Памʼяті/Налаштуваннях

`feat(ui): tidy memory/settings page text and layout`

Serie of small, user-driven UI fixes across `src/webui/static/`, done directly (no subagent — small enough to hold in one pass), each verified live on the real installed engine (`~/.claude/mnemo`, redeploy via `install.ps1` after every edit) with chrome-devtools MCP, both themes.

## Sidebar

`Журнал`'s count badge removed (`shell.js`'s `PAGES.journal.count: null`, `index.html`'s `#sb-count-journal` span dropped) — unlike Памʼять's bank count (small, stable), the log total grows without bound and read as noise.
Памʼять's bank count kept.

## Памʼять page

- **Pluralization.** New shared `pluralizeUk(n, [one, few, many])` in `app.js` — the standard Slavic count-noun triad (`n%10==1 && n%100!=11` → one, `n%10 in 2..4 && n%100 not in 12..14` → few, else → many).
  Replaces the hardcoded `' банк(и) · ' + files + ' файлів · ' + chunks + ' чанків'` in `memoryHeaderHtml()` — now agrees for any count (1 банк, 2 банки, 5 банків), not just the two forms the old string covered.
- **Bank card decluttered.** Three independent fixes in `bankCard()` (`page-memory.js`):
  1. `у черзі 0` — an empty queue is the normal state, not worth a stat of its own; the span is only appended when `bank.queued` is truthy.
  2. The muted `statusNote(bank)` line under the stats row is now skipped entirely when `bank.status === 'ready'` — its text in that case is literally "індекс готовий", the same fact the status badge above already carries.
     Still shown for `indexing`/`empty`, where it adds real information the badge doesn't.
  3. `востаннє: ...` → `остання індексація: ...` — the old label didn't say *which* "last" (last query? last index?); it is `bank.last_indexed`.
- **Third column header stopped being a different visual language.** `#file-title` used to have its static "Вміст" text overwritten with the open file's path on every `openFile()`/`renderFile()` call, and carried its own CSS override (`.pane-head h2.file-title` — lowercase mono, not the caps/bold the other two headers use) to make that fit.
  Fixed at the root: the header stays the static "Вміст" caption (id dropped from the `<h2>` entirely, JS never touches it), and the filename moved into `.file-meta` as its own bold line (`.file-meta-path`) above the existing size/indexed/ chunks/sha256 line (now `.file-meta-info`) — two lines instead of one overloaded one.
  The now-empty `.pane-head h2.file-title` CSS block deleted outright rather than left dead.

## Налаштування page

- **Duplicate section heading removed.** `renderSettings()` built `el('h2', {text: section.label})` as the form's first child on every section — but the active tab button directly above already shows that same label highlighted.
  Removed; the lede paragraph is now the form's first child (`.lede`'s own `margin-top` dropped from `8px` to `0` since it no longer follows the h2 it was spaced from).
  `.set-form h2` CSS rule deleted (dead once nothing renders an h2 there).
- **`.set-label` bolder/darker.** Was `color: fg-dim; font-size: 12px;` with no explicit weight — read at almost the same visual weight as `.set-note` right below it, so a setting's own name didn't stand out from its description.
  Now `color: fg` (full strength) `font-weight: 650` `font-size: 12.5px`.
- **Subtle divider between adjacent fields.** `.set-field + .set-field { border-top: 1px solid var(--line); padding-top: 12px; }` — the previous 14px flex `gap` alone read as one unbroken form (theme → autostart → auto-update → status all ran together); this doesn't touch spacing between non-field siblings (e.g. the "Перевірити оновлення" button block), only field-to-field.
- **"Стан" block kept as-is** (short summary; user confirmed the existing short version in Загальні is fine even though Обслуговування repeats more of it in the doctor report).

## Verification

`node --check` on all four touched JS files (`app.js`, `shell.js`, `page-memory.js`, `page-settings.js`), then `install.ps1` redeploy to the real engine, then a live chrome-devtools pass: Памʼять header pluralizes correctly at 4/223/3910 ("4 банки · 223 файли · 3910 чанків"), bank cards show no `у черзі 0` and no redundant ready-note, file view shows the two-line `.file-meta` with a static caps "ВМІСТ" header, Налаштування → Загальні shows no duplicate heading with bold labels and visible dividers between fields — all confirmed in **both** dark and light theme (toggled live, verified via `getComputedStyle` where the screenshot tool showed a stale-paint artifact — see gotcha below).
Zero console errors.

## Follow-up round (same day): ledes, an unreachable divider, a stale warning

A second pass of user feedback on the same two tabs, same method (direct edit, `install.ps1` redeploy, chrome-devtools verification each time):

- **`.set-note` under "Тема кабінету" simplified.** Was "Вибір цього браузера, **не машинне значення** — тому застосовується одразу й не чекає «Зберегти»." — the aside was unclear jargon to the user.
  Now just "Вибір цього браузера — тому застосовується одразу й не чекає «Зберегти»."
- **Both `SECTION_LEDE.general` and `SECTION_LEDE.embed` rewritten** from enumerating-the-controls prose ("Тема кабінету, автозапуск при вході в систему й стан процесу цієї машини…" / "Бекенд обирається пресетом: URL, модель, ширина вектора й префікси…") to one short sentence naming *what the tab is for*, matching Обслуговування's existing lede style (that one was never enumerating — it just says "the same structured doctor report CLI shows as text").
- **`.set-field + .set-field`'s adjacent-sibling divider has a real gap**: it only fires between two directly-adjacent `.set-field` elements, and several spots in this page have a non-field element (a check button, a runtime note, a `set-lead` paragraph) sitting between two fields that visually need separating anyway.
  Two such gaps existed: before "Стан" in Загальні (preceded by the auto-update check button + its result badge) and before "Оперативна памʼять" in Модель ембедингу (preceded by a `set-lead` paragraph under the local backend, or by the model/url/dim fields under an API backend).
  Fixed with an explicit `.set-divider` (`height: 0; border-top: 1px solid var(--line);`) hand-inserted right before each of those three render call sites, rather than trying to make the CSS rule itself smarter — the three spots are known and few, and a general fix would have had to reason about every non-field element type that can sit between two fields.
- **Removed the "Зупинку й перезапуск робить лише команда…" `.set-warn` paragraph** that used to sit under "Стан" in Загальні — user's call, no replacement text.

Verified live both changes render correctly (screenshot + `list_console_ messages` clean) on Загальні and Модель ембедингу.

## Third round (same day): tree pane pluralization, and telling temporary from durable

- **`renderTree()`'s `#tree-sub` line** ("60 файлів · 2 тек") was still the one spot on Памʼять never migrated to `pluralizeUk()` — hardcoded singular ` файлів`/` тек` produced "61 файлів" (wrong: should agree as "файл" for 61).
  Fixed with the same helper as the header, and — per the user's own suggestion — reworded "тека" → "директорія" here specifically (genitive plural "тек" read ambiguous/informal next to a count; "директорій" reads clearer).
  Scoped to this one line only: other "тека" wording in the cabinet (the "Додати банк"/folder-picker copy) describes picking a folder in casual terms and was left alone — changing it wasn't asked and would have been a bigger, unrelated vocabulary sweep.
- **Bank card: transient vs. durable text was one flat grey.** `statusNote` (e.g. "база є, свіжі зміни доїжджають") and "остання індексація: …" both rendered `.muted` (`var(--fg-mute)`), identical color — the one thing that changes minute to minute and the one thing that's a fixed historical fact read as the same kind of information.
  Fixed two ways in `bankCard()` (`page-memory.js`): (1) new `.note-live` class (`color: var(--busy)`, same tint as the "індексується" badge and the live `.progress-text` line) applied to the status-note span only when `bank.status === 'indexing'` — the `empty` case stays plain `.muted`, since an idle empty bank isn't a live process, just a fact; (2) reordered the card so the status-note row moves from *between* the two permanent facts (stats, then note, then "остання індексація") to *after* both of them, immediately before where the live progress block/one-off note would append — everything temporary now clusters at the card's bottom instead of interrupting the durable facts above it.
- Verified the color/reorder by simulating `bank.status = 'indexing'` client-side via `evaluate_script` + `renderBanks()` (no real reindex needed to see it — restored immediately after) — the note turned the same orange as the badge and sat directly under "остання індексація".
  Tree pane pluralization confirmed on a real bank: "61 файл · 2 директорії".

## Fourth round (same day): Модель ембедингу — dedup, warning placement, drafts

- **Deduped "nothing to configure" between two adjacent lines.** The local backend's `set-lead` paragraph repeated `backend.note` (the caption right above it under "Бекенд ембедингу", "нічого не треба налаштовувати; працює без мережі") almost verbatim.
  Trimmed the `set-lead` sentence down to only what the note doesn't already say — "Жоден байт памʼяті не залишає машину."
- **`.set-warn` (the REBUILD PENDING consequence) restyled and moved.** Was plain grey (`--fg-dim`/`--bg-sunken`/`--line`) sitting at the very bottom of the tab, easy to miss after already having changed something; now uses the same warm `--warn-*` tokens as the sidebar update banner (a real consequence, styled to be noticed) and moved to the very top of `renderEmbedSection`, right after the tab's lede and before the "Бекенд ембедингу" picker — so it's read before any control is touched, not after.
  It now shows for every backend (including `local`), since a provider/model switch triggers REBUILD PENDING regardless of which backend you're switching to or from.
  Wording adjusted for the new position: "кнопкою вище" (used to point at the endpoint-check button that sat above it) → "кнопкою в «Оперативна памʼять» нижче" (that section is now below), and "перевірте ендпоінт" → "перевірте бекенд" since the same sentence now also applies to `local`, which has a resident, not an endpoint.
- **Two missing/broken dividers fixed.** (1) The "Вимірів"/"Таймаут" row: its two `.set-field`s sit side by side in `.set-row` (flex row), but the generic `.set-field + .set-field` sibling rule still fired on the second one, putting a half-height top border only above "Таймаут" — looked skewed rather than like a rule.
  Cancelled with `.set-row .set-field + .set-field { border-top: none; padding-top: 0; }` and replaced with one real `.set-divider` above the whole row.
  (2) "Ключ API" was never preceded by a real divider either (the `set-note` + `set-override` lines above it aren't `.set-field`) — same manual `.set-divider` fix.
- **Backend picker relabeled and given the same draft/unsaved indicator as Загальні.** Field caption "Бекенд" → "Бекенд ембедингу" (specific about what it's a backend *of*, matches the tab's own new lede).
  Clicking a different backend tab only ever previews it (`settings.backendId`); `backendForSettings()` already told you which one is actually stored — the gap was never surfacing that distinction here the way autostart/ auto-update do on Загальні.
  Added the same `.set-override` note ("не збережено — зараз активний «X»; натисніть «Зберегти», щоб перемкнути на «Y»") whenever `settings.backendId !== backendForSettings()`.

Verified live: OpenAI tab previewed without saving shows the busy-colored draft note, both dividers render as clean full-width lines, the warning box is warm-colored and sits above "Бекенд ембедингу", local's description no longer repeats itself.
Zero console errors; nothing was actually saved to `settings.json` during verification (a plain reload discards the client-side preview, confirmed).

## Fifth round (same day): the warning's own text, one sentence per line

Three small text-only fixes to the just-moved `.set-warn` block in `renderEmbedSection` (`page-settings.js`):

- Split into one `<br>`-separated sentence per line — each of the three facts (what triggers a rebuild, when the new config takes effect, what happens to old indexes) is independent, and running them together read as one dense line.
- "Конфігурація діє одразу для нової роботи" → "Конфігурація діє після збереження налаштувань" — the old wording was inaccurate for the block's new position at the top of the tab: nothing takes effect "immediately" from just looking at this tab, only after «Зберегти».
- Dropped the closing "Спершу перевірте бекенд кнопкою в «Оперативна памʼять» нижче." sentence entirely (user's call — the warning states the consequence, doesn't need to also prescribe the next click).

Verified live, zero console errors.

## Sixth round (same day): "Оперативна памʼять" — status card + always-on intro, dedup the per-state notes

`renderEmbedMemory()` restructured (`page-settings.js`):

- **New fixed intro note**, shown whenever `held === 'loaded' || held === 'unloaded'` (i.e. this backend actually holds a resident on this machine — `local` or Ollama's `keep_alive`, not a hosted API where nothing is held here at all): "Модель піднімається сама, коли потрібна для пошуку чи індексації — «не в памʼяті» це нормальний стан, не помилка. «Вивантажити» звільняє памʼять одразу, замість тримати модель постійно завантаженою про запас." Answers the user's worry that "не в памʼяті" reads as broken, and states up front what unloading is *for* — before anyone has a reason to click it, not buried at the bottom of the card.
- **Deduped against this new line** rather than left to say the same thing twice (same call I made earlier this session for `backend.note` vs. `set-lead`): the old `unloaded`-state note ("Файл моделі лишається на диску… Підніметься сам при першому пошуку.") is now fully redundant and dropped; the old `loaded`-state note ("Вивантаження звільняє памʼять зараз; наступний пошук… підніме модель назад за ~Xс.
  Це не вимикач.") is trimmed to just the number that isn't already covered: "Підніметься назад за ~X с."
- **The card itself is now just status + actions.** New `.set-mem-caption` ("СТАТУС", small/muted/uppercase) sits before the badge on its own line; the model name moved to its own line below that; buttons stay at the bottom (`loaded` still puts two side by side — «Вивантажити» + «Перевірити» — `unloaded`/`n/a`/`unknown` still show one).
  All descriptive `.set-note` text (the new intro, wake estimate, `n/a`-specific lines, `expires_at`, `others_held`, `probe_dim`, download-failed, `detail`) moved **out** of `.set-mem` entirely — they're now siblings after the card inside the same `.set-field`, not mixed in among the buttons.
- New helper `memField(control, notes)` replaces the `setField()` call for just this one field: `setField()` only takes a single `note` string, and this field alone can carry several independent notes at once.
  Builds the identical `.set-field` DOM shape (`label`, `control`, then zero or more `.set-note` paragraphs) so it stays visually consistent with every other field on the page, just without the one-note limit.
  Old inline label string `'Оперативна памʼять'` (repeated 3× at the old early-return points) replaced by one `MEM_LABEL` constant.
- CSS: `.set-mem` kept as the bordered/rounded card; new `.set-mem-caption`; `.set-mem-what` no longer shares a row with the badge (moved to its own line, `margin-top: 4px`); `.set-mem .set-note { margin-top: 7px; }` rule deleted — no `.set-note` lives inside `.set-mem` any more, spacing between the card and the notes below it now comes from `.set-field`'s own `gap`.

Verified live for both `unloaded` (real state) and `loaded` (simulated via `evaluate_script` + `renderSettings()`, no real `/api/embed/load` call) — status card reads "СТАТУС [badge]" / model name / button(s), description sits directly below as plain text, two buttons render side by side for `loaded`.
Zero console errors; reload discarded the simulated state cleanly.

## Seventh round (same day): "Оперативна памʼять" — no more box, description moves above the status

Fast follow-up to round six, all in `renderEmbedMemory()`:

- **Wake estimate de-numbered.** "Підніметься назад за ~8 с." → "Підніметься назад за кілька секунд." — the actual figure drifts with hardware/model and was never a number worth trusting at a glance; the fact that matters is "a few seconds", not a specific one.
- **Intro's first clause bolded.** "Модель піднімається сама, коли потрібна для пошуку чи індексації" is now `<strong>`, the rest of the sentence (" — «не в памʼяті» це нормальний стан...") stays regular weight — needed a real DOM node instead of a plain string, so the per-field note list this and the `n/a`/wake/etc. footnotes go through stopped being homogeneous strings; the intro is now built and pushed directly rather than through a shared string-only helper.
- **"МОДЕЛЬ" caption added**, same treatment as "СТАТУС" (`.set-mem-caption` — small, muted, uppercase via `text-transform`) — the model name is now its own `caption + value` line under the status line, not a bare line with no label.
- **The bordered box is gone.** `.set-mem`'s `border`/`border-radius`/ `padding` all dropped; status line, model line and the actions now sit directly in the field with no framing div around them at all (the wrapper div itself was removed too, not just its CSS — nothing left to justify keeping an invisible wrapper).
  New `.set-mem-line + .set-mem-line { margin-top: 6px; }` replaces the spacing that used to come from `.set-mem-what`'s own `margin-top`.
- **Description moved above the status**, not below it — a deliberate one-off deviation from every other field on this page (which all go label → control → note): this field's intro is the one thing worth reading before touching a button below it, so `renderEmbedMemory` now builds its own `.set-field` children array by hand (`[label, intro?, statusLine, modelLine, buttons?, downloadRow?, ...footnoteNotes]`) instead of going through a shared helper that assumed label-first-then-content order for everything.

Verified live for `unloaded` (real) and `loaded` (simulated) — description bold clause renders correctly, "МОДЕЛЬ"/"СТАТУС" captions both show, no visible border anywhere on the block, "кілька секунд" instead of a number.
Zero console errors.

## Eighth round (same day): a colour, two colons, a bit more air

Three small touches to the same field:

- **Bold intro clause coloured `var(--accent)`** (the light-blue token already used for selection/links/the chunk divider elsewhere — reused, not invented) — bold alone wasn't distinguishing it enough from the rest of the sentence.
- **"Статус"/"Модель" captions gained a colon** ("Статус:"/"Модель:") — `text-transform: uppercase` on `.set-mem-caption` renders them "СТАТУС:"/"МОДЕЛЬ:", punctuation untouched by the transform.
- **A bit more space between the description and the status line below it** (`.mem-intro { margin-bottom: 4px; }`, on top of the field's own 5px gap) — they used to sit right on top of each other at the same spacing as every other adjacent pair in the field.

Verified live, zero console errors.

## Ninth round (same day): a real bug — "Підняти в памʼять" could blank the whole field, plus a colour tweak

User hit a genuine bug live: clicking "Підняти в памʼять" while the worker was mid-embed replaced the entire "Оперативна памʼять" field (status, model, buttons — everything) with one raw English sentence: "the queue is still working (0 task(s) pending) — the worker embeds through this backend, so wait for it to drain".
Two separate problems, both fixed:

- **Frontend regression (pre-dated this session's redesign, inherited unchanged through every round):** `renderEmbedMemory()` treated `settings.embedError` as an early-return condition that replaced the whole field.
  But `embedAction()` (the load/unload click handler) never clears `settings.embed` on failure — only `settings.embedError` — so the known state was still sitting right there in `info`, just no longer being shown.
  Fixed: the early-return on `!info` alone now covers "never fetched" *and* "fetch failed" (both leave `info` null); a failed load/unload instead renders the full card as normal and adds the error as a `.modal-error` note right after the buttons, next to the action it describes — same pattern `settings.errorText`/`settings.maintenanceError` already use elsewhere on this page.
- **Raw English backend detail leaking onto a Ukrainian screen** — the same class of issue already fixed once for the `n/a` state's notes (see round six: "the API stays English by convention, and echoing it here would put one English line in the middle of a Ukrainian screen").
  `embedAction()`'s catch block now special-cases `err.code === 'embed_busy'` with a Ukrainian sentence ("Черга ще працює через цей бекенд — почекайте, доки вона звільниться, і спробуйте ще раз."); any other/unexpected code still falls back to the raw `err.message`, same as before.
- **Backend wording fixed too** (`src/api.py`, `_embed_action`), for anyone hitting the raw message directly (Swagger, `/mcp-tools`, a future client): the contradictory "0 task(s) pending" while "still working" came from citing only `depth` (the QUEUED backlog) in the message even when the real reason was `current` (one file actively in flight, not queued at all — `depth == 0` in exactly that case).
  Message now branches: `current` alone → "a file is being embedded through this backend right now — wait for it to finish"; `depth` (with or without `current`) → the original wording, now accurate again since it only fires when there really is a queue.
- **Muted the intro's accent colour.** New `--accent-muted` token added to `base.css` (both themes — `#7fa2d5` dark / `#406199` light, `--accent` blended halfway toward `--fg-dim`) and used instead of bare `--accent` for `.mem-intro strong`: the previous full-saturation blue read as too vivid next to the rest of the muted-grey field.

Verified live: a real click on "Підняти в памʼять" against the idle real queue succeeded normally (status → «У памʼяті», wake/probe notes, green success toast) — confirms the refactor didn't break the working path.
The busy-error path was verified by simulating `settings.embedError` via `evaluate_script` + `renderSettings()` (matching the exact Ukrainian string `embedAction` now produces): card, buttons and other notes all stayed intact, error rendered as a small note, not a takeover.
Model unloaded again afterward to leave the machine at its original idle baseline.
Zero console errors throughout.

## Tenth round (same day): dropped a line, and a real machine-config decision revisited

- **Dropped "Пробний вектор: N вимірів."** from the footnote list per request — `info.probe_dim` is still returned by the backend, just no longer rendered.
- **Moved the embed action's own outcome next to the card**, not through the page-wide `settings.note`/`renderSettingsMessages()` (which renders at the very bottom of the whole tab and read as visually detached from the button that produced it — "кудись улітає рядок").
  New `settings.embedNote` field, parallel to `settings.embedError`, rendered as `.tok-ok` right after the buttons in `renderEmbedMemory()`; both cleared in `chooseSettingsSection` on tab-leave, same rule as every other section's stale-feedback fields.

**Then a real question, not a text tweak.** User asked to show a "time until the local model gets evicted" field.
Local's resident has no such concept by deliberate design (`logs/2026-08-16-embed-memory.md`: `MNEMO_EMBED_IDLE_ TIMEOUT=0` — "why NOT to change it" — paying ~9s repeatedly to reclaim 1.6GB that one command reclaims on purpose is the wrong trade on a dev machine).
Asked via `AskUserQuestion` rather than fabricating data; user recalled setting `OLLAMA_KEEP_ALIVE=3h` for Ollama once and, after I named the distinction (that was Ollama-specific, not `local`), explicitly confirmed: yes, they want the same 3h idle-eviction for `local` too, on this machine.

**The mechanism already exists — confirmed by reading `embed_server.py` directly** (`srv.settimeout(EMBED_IDLE_TIMEOUT or None)` on the resident's own `accept()` loop; a `socket.timeout` with nothing in flight makes the resident return or exit, freeing the ~1.5GB — exactly "last request was N seconds ago -> unload itself").
`EMBED_IDLE_TIMEOUT` reads `MNEMO_EMBED_IDLE_TIMEOUT` once at `config.py` import time, default `"0"` (`settimeout(None)` = block forever = never auto-exits).

**Applied for this machine only** — the shared `config.py` default of `0` and its documented rationale are untouched; this is a personal env-var override, same mechanism as the existing `OLLAMA_KEEP_ALIVE=3h`:
```
[Environment]::SetEnvironmentVariable("MNEMO_EMBED_IDLE_TIMEOUT", "10800", "User")
$env:MNEMO_EMBED_IDLE_TIMEOUT = "10800"   # same session, before the restart
& mnemo.exe service restart
```
**The same gotcha `OLLAMA_KEEP_ALIVE` already hit, hit again while verifying this one.** A registry-persisted `SetEnvironmentVariable(..., "User")` is NOT visible to a process spawned from an already-running ancestor shell — only to a genuinely fresh session/window.
My own verification script (run in a separate Bash-tool `powershell.exe` call, itself a child of a shell that predates the registry write) read back `EMBED_IDLE_TIMEOUT = 0` — proving nothing about the actual restarted backend, only that *that particular verification process* never had the var.
The backend itself was restarted correctly, in the *same* session where `$env:` was set explicitly right before `service restart` — `service_ctl.spawn_detached()`'s `_windowless_kwargs()` sets no `env=` override, so `subprocess.Popen` inherits the caller's full environment by default, confirmed by reading the source.
No code exposes this value for a live process to query externally (no `doctor`/`status` field), so this was reasoned from the source and the known-correct spawn chain rather than observed directly.

**Switched to a more robust mechanism before trusting that reasoning as the final answer.** `config.py`'s `_load_env_file()` merges `<state>/mnemo.env` into `os.environ` on every process's own import — unconditionally, from the state directory on disk, independent of whatever shell spawned it (already the documented fix for the exact same class of gotcha on Linux/macOS autostart — `topics/provider-settings.md`: "справжня змінна оточення б'є файл").
Wrote `MNEMO_EMBED_IDLE_TIMEOUT=10800` into `~/.claude/mnemo/state/mnemo.env` (file did not exist before), restarted the service with **no** manual `$env:` set this time, then re-ran the same `check_timeout.py` probe from a brand-new process spawned off the *same* stale ancestor shell that had earlier shown `0` — it now correctly resolved `10800`, proving the file-based fix works regardless of shell/session staleness, unlike the registry `User` env var alone.
`mnemo doctor` confirms the service healthy after the restart (backend up, resident down/idle — correct baseline, starts fresh on the next search).

## Eleventh round (same day): "no git" recoloured, and the just-set TTL made visible

- **`.badge-nogit` recoloured** to the exact same tokens as `.badge-empty` ("не в памʼяті") — was `color: var(--fg-mute)` with no border/background of its own (falling through to the base `.badge` rule), now `--badge-empty-fg`/`-border`/`-bg`, the same cool blue-grey.
  No git is a neutral fact about a bank, not a fault — `.badge-off`'s red stays reserved for `вимкнено`/`нема кореня`, which are.
- **The idle-eviction TTL configured last round is now a real field**, not just something set blind and trusted.
  `_describe_local()` (`embedctl.py`) now returns `idle_timeout_s: config.EMBED_IDLE_TIMEOUT` (`devserver.py`'s fixture mirrors it at `10800` so the field renders non-trivially in dev too); `renderEmbedMemory()` adds a third `caption:value` line — "Автовивантаження:" — right after "Модель:", using the same `humanUptime()` helper already used for the general-section uptime stat (`0` → "вимкнено", else e.g. "3 год 0 хв").
  Scoped to `info.idle_timeout_s != null` — Ollama's own TTL is a live `expires_at` timestamp (already a footnote below, unrelated mechanism) and gets no line here.

**Gotcha hit again during redeploy — the known transient service-start flake**, not a bug in this round's code: `install.ps1` reported "the service did not start" / doctor showed DOWN/connection-refused right after redeploying `embedctl.py`.
Diagnosed the same way the earlier session documented: ran `python -m src.cli serve` in the foreground — it came up healthy in seconds with zero errors, `curl /health` answered correctly, proving the Python change itself was fine and this was purely a one-off timing/resource flake in the spawn-and-wait window.
Fixed by stopping the stray process and going through `mnemo service start` normally, which picked up cleanly (`doctor` clean afterward).

Verified live: "no git" badge now blue-grey on a real bank card; "Оперативна памʼять" shows "АВТОВИВАНТАЖЕННЯ: 3 год 0 хв" — the actual configured value for this machine, not a simulated one.
Zero console errors.

## Gotcha: chrome-devtools MCP screenshot staleness after an SPA route change

After toggling the theme on one page (Налаштування) and then clicking a sidebar route to a different page (Памʼять) that was already rendered *before* the toggle, `take_screenshot` returned a **stale dark-theme image** even though `document.documentElement.dataset.theme` and every computed CSS custom property (`getComputedStyle(...).getPropertyValue('--bg-pane')`, etc.) correctly reported the light theme's values.
A `navigate_page {type: "reload"}` (full page reload, not just an SPA route swap) resolved it and the screenshot then matched the DOM state.
Treat a screenshot that looks "impossibly wrong" after an SPA-only navigation as a tool-side paint/cache timing artifact worth cross-checking with `evaluate_script` + `getComputedStyle` before concluding the underlying page is actually broken.
