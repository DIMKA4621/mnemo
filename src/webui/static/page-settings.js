/* Налаштування (contract 9.5: GET/PUT /api/settings) — horizontal tabs under
 * the header instead of the old fixed `.screen` overlay with a vertical
 * `.set-nav`. The section bodies below are a close-to-mechanical port of the
 * current live console's form-building code; only the frame around them
 * changed shape.
 *
 * This is also the last script the page loads, so `boot()` (app.js) is
 * called from the bottom of this file — only here can every page's globals
 * be trusted to exist.
 */
'use strict';

/**
 * Machine settings: which backend produces vectors, and what it needs.
 *
 * A full page rather than a modal, because unlike every other dialog here
 * this one is not about a bank — it is about the machine, and it takes the
 * page over while you are in it.
 *
 * The design principle is that **the backend is picked, not typed**. Prefixes
 * are the reason: e5 is trained with mandatory `passage: ` / `query: ` markers
 * and sending it bare text quietly produces worse vectors with nothing in a
 * log to say so. A free-text form would make that a thing you can forget, so
 * the catalogue (`/api/settings` -> `presets`) supplies the URL, the model,
 * its width and its markers together, and choosing a model is enough to get
 * all four right.
 *
 * `dim` is still shown and still editable: the catalogue's value is what the
 * model publishes, but the endpoint is the authority and a wrong width does
 * not degrade an index, it corrupts one.
 */
const settings = {
  tabs: null,          // #set-tabs
  body: null,           // #set-body
  save: null,           // #settings-save
  data: null,          // the last GET /api/settings response
  section: 'general',  // which tab is open
  backendId: null,     // which backend tab is open
  form: null,          // {model, url, dim, timeout, key} — the edited values
  busy: false,
  errorText: null,
  note: null,
  keyTouched: false,   // the key field was typed into; otherwise leave stored
  autostart: null,     // GET /api/autostart — its own request, its own failure
  autostartWant: null, // the selected state, null when it matches the machine
  autostartError: null,
  // POST /api/autostart's own save verdict — shown right under the
  // autostart control (renderAutostart), NOT through settings.note/
  // renderSettingsMessages(): same reasoning as embedNote below, this is
  // one of two independent controls sharing a single "Зберегти" button, and
  // a merged message read as glued to whichever field happened to be last.
  autostartSaveNote: null,
  autostartSaveError: null,
  autoUpdateWant: null,  // pending edit for the "auto_update" setting, same
                         // idiom as autostartWant — null when it matches
                         // what GET /api/settings last reported
  // PUT /api/settings's own save verdict for auto_update specifically —
  // shown right under the auto-update control (renderAutoUpdate), same
  // reasoning as autostartSaveNote above.
  autoUpdateSaveNote: null,
  autoUpdateSaveError: null,
  requireLoginWant: null,  // pending edit for "require_login" (MN-19), same
                           // idiom as autoUpdateWant
  requireLoginSaveNote: null,
  requireLoginSaveError: null,
  // The service token PUT /api/settings hands back the moment this save
  // turns the gate on (MN-19) — a one-time reveal, never echoed by GET
  // /api/settings afterwards. Cleared on leaving this section/page, same
  // hygiene as bankToken.value in app.js.
  requireLoginToken: null,
  requireLoginTokenRevealed: false,
  updateCheckBusy: false,  // POST /api/update/check in flight
  updateCheckError: null,
  updateCheckResult: null,  // last check's outcome, worded for display
  updateCheckAvailable: false,  // same check's update_available, for the note's color
  embed: null,         // GET /api/embed/state — what the backend holds now
  embedError: null,
  // A load/unload/download's own outcome, shown next to the card that owns
  // it — NOT through `settings.note`/`renderSettingsMessages()`, which
  // renders at the very bottom of the whole tab and read as disconnected
  // from the button that produced it.
  embedNote: null,
  embedBusy: false,    // an unload/load is in flight
  maintenance: null,  // GET /api/doctor — loaded only when this section opens
  maintenanceBusy: false,
  maintenanceError: null,
  cleanupBusy: false,
  cleanupConfirming: false,
  cleanupNote: null,
};

/**
 * The tabs, in order.
 *
 * A table rather than a chain of ifs so that adding one is a line here plus a
 * render function — the tab bar, the routing and the footer all read from
 * this, and there is no fourth place to forget.
 *
 * `submit` is both the footer's Save handler and the answer to whether there
 * is a Save button at all. A section that changes machine state gets one and
 * applies nothing until it is pressed — including the autostart control,
 * which could just as easily have acted on click. A section that only
 * reports (`submit: null`) shows no button — hidden, not disabled: a
 * permanently greyed-out control reads as broken, not as irrelevant.
 */
const SETTINGS_SECTIONS = [
  { id: 'general', labelKey: 'settings.tabs.general', render: renderGeneralSection, submit: submitGeneral },
  { id: 'embed', labelKey: 'settings.tabs.embed', render: renderEmbedSection, submit: submitSettings },
  { id: 'maint', labelKey: 'settings.tabs.maint', render: renderMaintSection, submit: null },
];

function sectionLede(id) {
  return t('settings.lede.' + id);
}

function settingsSection(id) {
  return SETTINGS_SECTIONS.find((s) => s.id === id) || SETTINGS_SECTIONS[0];
}

/** The catalogue entry for a backend id, or null. */
function backendPreset(id) {
  const list = (settings.data && settings.data.presets) || [];
  return list.find((b) => b.id === id) || null;
}

/** The catalogue entry for a model name inside a backend, or null. */
function modelPreset(backend, name) {
  if (!backend) return null;
  return (backend.models || []).find((m) => m.name === name) || null;
}

// The catalogue's `label`/`note` text is Ukrainian, sent by the backend
// (`src/presets.py`) — the language toggle can't reach it by translating
// the string in place. These look up a matching i18n key by the preset's
// stable `id`/`name` instead, and fall back to the raw backend text for
// anything not (yet) in the dictionary, so a future/unlisted preset still
// shows something rather than a missing-key warning or blank text.
// Model `label` is left untranslated everywhere — it's the technical model
// name itself, not descriptive text.
function backendLabel(item) {
  if (!item) return '';
  return tMaybe(`settings.embed.backend.${item.id}.label`) || item.label;
}

function backendNoteText(backend) {
  if (!backend) return null;
  return tMaybe(`settings.embed.backend.${backend.id}.note`) || backend.note || null;
}

function modelNoteText(model) {
  if (!model || !model.note) return model ? model.note : null;
  return tMaybe(`settings.embed.model.${model.name}.note`) || model.note;
}

function settingValue(key) {
  const box = settings.data && settings.data.settings;
  return box && box[key] ? box[key] : null;
}

/**
 * Which backend the stored settings correspond to.
 *
 * `provider` alone does not answer it: `ollama` and `openai` are both the
 * `api` provider, and what tells them apart is the URL. Matching on the URL
 * keeps a configured machine opening on the tab it actually uses, and falls
 * back to the first `api` backend when the URL is one we do not know — the
 * fields are all still editable there, so an unlisted endpoint is usable,
 * just not pre-filled.
 */
function backendForSettings() {
  const provider = (settingValue('provider') || {}).value || 'local';
  const list = (settings.data && settings.data.presets) || [];
  if (provider === 'local') return 'local';
  const url = ((settingValue('api.url') || {}).value || '').trim();
  const match = list.find((b) => b.provider === 'api' && b.url && b.url === url);
  if (match) return match.id;
  const anyApi = list.find((b) => b.provider === 'api');
  return anyApi ? anyApi.id : 'local';
}

// ---------------------------------------------------------------------------
// header + frame
// ---------------------------------------------------------------------------

function settingsHeaderHtml() {
  return '<span class="page-title">' + t('settings.header.title') + '</span>' +
    '<span class="page-sub">' + t('settings.header.sub') + '</span>';
}

function buildSettings() {
  settings.tabs = $('set-tabs');
  settings.body = $('set-body');
  settings.save = $('settings-save');
  // The footer's one control routes through the section table: it belongs to
  // the page, but what it saves belongs to whatever tab is open.
  settings.save.addEventListener('click', () => {
    const section = settingsSection(settings.section);
    if (section.submit) section.submit();
  });
}

/**
 * Move to another tab.
 *
 * The save verdict is dropped on the way out: «Збережено» belongs to the form
 * that produced it, and carrying it to a section with no form would make it
 * read as a report about whatever is on screen now.
 */
function chooseSettingsSection(id) {
  if (settings.section === id) return;
  settings.section = id;
  settings.errorText = null;
  settings.note = null;
  // Same rule for the autostart failure: it describes an attempt made in the
  // section being left, and leaving it to reappear on the way back would
  // report a stale problem as a current one.
  settings.autostartError = null;
  // The unsaved selection goes too. Leaving is not saving, and a draft that
  // survived the trip would show the control on «Вимкнено» for a machine
  // whose autostart is on — the same lie as an unsaved edit that looks stored.
  settings.autostartWant = null;
  settings.autostartSaveNote = null;
  settings.autostartSaveError = null;
  settings.autoUpdateWant = null;
  settings.autoUpdateSaveNote = null;
  settings.autoUpdateSaveError = null;
  settings.requireLoginWant = null;
  settings.requireLoginSaveNote = null;
  settings.requireLoginSaveError = null;
  // Same hygiene as bankToken.value in app.js: a secret must not survive in
  // memory past the section it was revealed in.
  settings.requireLoginToken = null;
  settings.requireLoginTokenRevealed = false;
  settings.updateCheckError = null;
  // Same rule as the save verdicts above: an update-check result describes
  // an attempt made in the section being left, not a fact about the tab
  // coming next.
  settings.updateCheckResult = null;
  settings.updateCheckAvailable = false;
  settings.maintenanceError = null;
  settings.cleanupConfirming = false;
  settings.cleanupNote = null;
  // Same rule for an embed action's own outcome: it describes a click made
  // in the section being left, not a fact about whatever tab comes next.
  settings.embedError = null;
  settings.embedNote = null;
  renderSettings();
  if (id === 'maint' && !settings.maintenance && !settings.maintenanceBusy) {
    refreshMaintenance();
  }
}

function renderSettingsTabs() {
  clear(settings.tabs);
  for (const section of SETTINGS_SECTIONS) {
    settings.tabs.appendChild(el('button', {
      className: 'set-tab' + (section.id === settings.section ? ' is-active' : ''),
      text: t(section.labelKey),
      attrs: { 'data-sec': section.id },
      on: { click: () => chooseSettingsSection(section.id) },
    }));
  }
}

async function openSettings() {
  settings.errorText = null;
  settings.note = null;
  settings.busy = true;
  renderSettings();
  try {
    settings.data = await api('/api/settings');
    settings.backendId = backendForSettings();
    seedSettingsForm();
    // The service section reports uptime and pid out of `state.service`, which
    // is only refreshed on events — opening this page during a quiet spell
    // would otherwise show numbers from whenever the last one happened.
    await loadStatus().catch(() => {});
    // Its own request, and its own failure: costing a subprocess it is not
    // part of `/api/status`, and a machine where the query fails must still
    // get a working backend form. Caught rather than awaited into the outer
    // try for exactly that reason.
    settings.autostartError = null;
    try {
      settings.autostart = await api('/api/autostart');
    } catch (err) {
      if (isAuthError(err)) throw err;
      settings.autostart = null;
      settings.autostartError = err.message;
    }
    // Same shape and the same reason as autostart: asking can cost a round
    // trip to Ollama, so it is fetched when this page opens rather than on
    // every indexing event — and its failure must not cost the whole form.
    await refreshEmbedState();
  } catch (err) {
    if (isAuthError(err)) { openGate('rejected'); return; }
    settings.errorText = err.message;
  } finally {
    settings.busy = false;
    renderSettings();
    if (settings.section === 'maint' && !settings.maintenanceBusy) {
      refreshMaintenance();
    }
  }
}

/**
 * What used to run when the overlay's "Закрити" was pressed. There is
 * nothing to close now — navigating to another sidebar item does that job —
 * but the hygiene still matters: a credential must not survive in memory
 * past the page it was typed into, and an unconfirmed choice must not look
 * saved when you come back.
 */
function settingsOnLeave() {
  if (settings.form) settings.form.key = '';
  settings.keyTouched = false;
  settings.autostartWant = null;
  settings.cleanupConfirming = false;
  settings.cleanupNote = null;
  // Same rule as chooseSettingsSection(): a save/action verdict describes
  // an attempt made in the section being left, and leaving it to reappear
  // on the way back would report a stale outcome as a current one.
  settings.embedNote = null;
  settings.embedError = null;
  settings.autostartSaveNote = null;
  settings.autostartSaveError = null;
  settings.autoUpdateSaveNote = null;
  settings.autoUpdateSaveError = null;
  settings.requireLoginSaveNote = null;
  settings.requireLoginSaveError = null;
  settings.requireLoginToken = null;
  settings.requireLoginTokenRevealed = false;
  settings.updateCheckError = null;
  settings.updateCheckResult = null;
  settings.updateCheckAvailable = false;
  stopDownloadPolling();
}

/** Fill the form from what is stored, for the currently selected backend. */
function seedSettingsForm() {
  const backend = backendPreset(settings.backendId);
  const stored = {
    url: ((settingValue('api.url') || {}).value || ''),
    model: ((settingValue('api.model') || {}).value || ''),
    dim: ((settingValue('api.dim') || {}).value || 0),
    timeout: ((settingValue('api.timeout') || {}).value || 60),
  };
  // Only carry the stored values across when this tab IS the stored backend.
  // Switching to OpenAI must not inherit Ollama's URL — that would produce a
  // config that looks deliberate and cannot work.
  const isStored = settings.backendId === backendForSettings();
  const known = backend && (backend.models || [])[0];
  const model = (isStored && stored.model) || (known ? known.name : '');
  const preset = modelPreset(backend, model);
  settings.form = {
    model: model,
    url: (isStored && stored.url) || (backend ? backend.url : ''),
    dim: (isStored && stored.dim) || (preset ? preset.dim : 0),
    timeout: (isStored && stored.timeout) || 60,
    key: '',
  };
  settings.keyTouched = false;
}

/**
 * Drop the last save's verdict as soon as the form is edited.
 *
 * «Вкажіть адресу» describes the state at the moment Save was pressed. Leaving
 * it under a field the user has since fixed makes the page report a problem
 * that is no longer there — and the same goes for «Збережено», which would
 * otherwise sit above unsaved edits and claim they are stored.
 *
 * The nodes are removed rather than re-rendered: this runs on every keystroke,
 * and rebuilding the form would take the focus out of the input mid-word.
 */
function clearSettingsMessages() {
  if (!settings.errorText && !settings.note) return;
  settings.errorText = null;
  settings.note = null;
  for (const node of settings.body.querySelectorAll('.modal-error, .tok-ok')) {
    node.remove();
  }
}

/** A model was chosen: adopt its width, since that is what it publishes. */
function chooseModel(name) {
  const backend = backendPreset(settings.backendId);
  const preset = modelPreset(backend, name);
  settings.form.model = name;
  if (preset) settings.form.dim = preset.dim;
  settings.errorText = null;
  settings.note = null;
  renderSettings();
}

function chooseBackend(id) {
  if (settings.backendId === id) return;
  settings.backendId = id;
  settings.errorText = null;
  settings.note = null;
  seedSettingsForm();
  renderSettings();
}

/**
 * A captioned row: caption, control, and the note under it.
 *
 * The caption is a `<span>`, not a `<label>`: nothing built here ever pairs
 * it with a control that carries a matching `id` (a `<select>` with no id, a
 * `.segmented` picker, a read-only `.set-stats` report) — so a `<label>`
 * here is not a caption with extra semantics, it is a false association a
 * screen reader cannot resolve. Real form fields with a working label live
 * outside this helper (the gate, the folder picker, the token panel), where
 * `for`/`id` genuinely match.
 */
function setField(label, control, note) {
  return el('div', { className: 'set-field' }, [
    el('span', { className: 'set-label', text: label }),
    control,
    note ? el('p', { className: 'set-note', text: note }) : null,
  ]);
}

/**
 * The "this is overridden by an environment variable" line.
 *
 * Not decoration: precedence is env > file, so a value saved here can be
 * completely inert. A form that stayed silent about it would accept a click,
 * report success and change nothing observable.
 */
function overrideNote(key) {
  const item = settingValue(key);
  if (!item || !item.overridden) return null;
  return el('p', {
    className: 'set-override',
    text: t('settings.overrideNote', { var: item.env_var }),
  });
}

/**
 * Загальні only: is there a Save-gated edit actually waiting?
 *
 * Theme and language do not count — they apply on their own click and were
 * never part of what Save sends. Deliberately mirrors submitGeneral()'s own
 * early-return condition, since the button being clickable and the submit
 * having something to do are the same fact told twice.
 */
function generalHasPendingChange() {
  return settings.autostartWant != null ||
    settings.autoUpdateWant != null ||
    settings.requireLoginWant != null;
}

function renderSettings() {
  const body = settings.body;
  clear(body);
  renderSettingsTabs();

  const section = settingsSection(settings.section);
  // Only a section that can change something gets a Save button. Hidden
  // rather than disabled: a permanently greyed-out control reads as something
  // that ought to work and does not.
  settings.save.hidden = !section.submit;
  // Загальні is the one section with an exact, cheap answer to "is there
  // anything Save would do right now" — so, unlike Embedding model below
  // (a multi-field form, including a write-only key input a saved value can
  // never be diffed against), it gets a Save button that stays dark until a
  // real edit is pending, rather than always clickable. Left untouched while
  // busy: a mid-submit disable must not be overwritten by this recompute.
  if (!settings.busy) {
    settings.save.disabled = section.id === 'general' ? !generalHasPendingChange() : false;
  }

  if (settings.busy && !settings.data) {
    body.appendChild(el('p', { className: 'empty-hint', text: t('settings.loading') }));
    return;
  }
  if (!settings.data) {
    body.appendChild(el('p', { className: 'modal-error', text: settings.errorText || '—' }));
    return;
  }

  // No section <h2> here: the active tab button right above already names
  // this section — repeating it as a heading said the same word twice.
  const form = el('div', { className: 'set-form' }, [
    el('p', { className: 'lede', text: sectionLede(section.id) }),
  ]);
  body.appendChild(form);
  section.render(form);
}

/** Backend, model, endpoint — the settings that decide what produces vectors. */
function renderEmbedSection(body) {
  const presetList = settings.data.presets || [];
  const backend = backendPreset(settings.backendId);

  // Consequences up front, not buried at the bottom of the tab — this is
  // true regardless of which backend you're looking at, and it's the reason
  // to read the rest of the tab carefully before touching anything in it.
  // One `<br>` per sentence: each is its own fact (the trigger, when it
  // takes effect, what happens to existing indexes), not one run-on line.
  body.appendChild(el('p', { className: 'set-warn' }, [
    document.createTextNode(t('settings.embed.warn.line1')),
    el('br'),
    document.createTextNode(t('settings.embed.warn.line2')),
    el('br'),
    document.createTextNode(t('settings.embed.warn.line3')),
  ]));

  // -- backend tabs -----------------------------------------------------
  // `.set-backend-tabs`, not `.set-tabs`: the page's own tab bar (above,
  // outside this form) already owns that name for its full-width, bordered,
  // horizontally-scrolling look, and this is a small `.segmented` picker
  // nested in a field — the same component, styled for a different spot.
  const tabs = el('div', { className: 'segmented set-backend-tabs' });
  for (const item of presetList) {
    tabs.appendChild(el('button', {
      className: 'seg' + (item.id === settings.backendId ? ' is-active' : ''),
      text: backendLabel(item),
      on: { click: () => chooseBackend(item.id) },
    }));
  }
  body.appendChild(setField(t('settings.embed.backendLabel'), tabs, backendNoteText(backend)));

  // Same idiom as autostart/auto-update on Загальні: browsing a tab that
  // isn't what's actually stored is a draft, not a change — say so before
  // «Зберегти» is pressed, or a machine on OpenAI reads as already switched
  // to Ollama the moment you click its tab to look at it.
  if (settings.backendId !== backendForSettings()) {
    const active = backendPreset(backendForSettings());
    body.appendChild(el('p', {
      className: 'set-override',
      text: t('settings.embed.notSavedBackend', {
        active: active ? backendLabel(active) : '—',
        target: backend ? backendLabel(backend) : '—',
      }),
    }));
  }

  const providerOverride = overrideNote('provider');
  if (providerOverride) body.appendChild(providerOverride);

  if (!backend) { renderSettingsMessages(body); return; }

  // -- local needs nothing ------------------------------------------------
  if (backend.provider === 'local') {
    const model = (backend.models || [])[0];
    body.appendChild(el('p', { className: 'set-lead' }, [
      document.createTextNode(t('settings.embed.local.lead')),
      el('code', { text: model ? model.label : '—' }),
      document.createTextNode(model
        ? t('settings.embed.local.dimsSuffix', { dim: model.dim })
        : t('settings.embed.local.noDimSuffix')),
      // "Нічого налаштовувати не треба" dropped: `backend.note` right above
      // ("нічого не треба налаштовувати; працює без мережі") already says
      // it — this line only adds what that one doesn't, that memory stays
      // on the machine.
      document.createTextNode(t('settings.embed.local.tail')),
    ]));
    // The resident is what holds the most (~1.5 GB), so the one backend with
    // nothing to configure is the one where this block matters most.
    if (settings.backendId === backendForSettings()) {
      body.appendChild(el('div', { className: 'set-divider' }));
      renderEmbedMemory(body);
    }
    // Switching TO local is a save like any other, and this branch returns
    // early — without this the one backend that needs no configuration was
    // also the one whose «Збережено» never appeared, so the click read as
    // ignored while the file on disk had already changed.
    renderSettingsMessages(body);
    return;
  }

  // -- model --------------------------------------------------------------
  const select = el('select', { className: 'set-select' });
  for (const model of backend.models || []) {
    const option = el('option', { text: model.label, attrs: { value: model.name } });
    if (model.name === settings.form.model) option.selected = true;
    select.appendChild(option);
  }
  // An endpoint may serve a model the catalogue does not list; keep it
  // selectable rather than silently rewriting what the user configured.
  if (settings.form.model && !modelPreset(backend, settings.form.model)) {
    const option = el('option', {
      text: settings.form.model + t('settings.embed.model.notInCatalog'),
      attrs: { value: settings.form.model },
    });
    option.selected = true;
    select.appendChild(option);
  }
  select.addEventListener('change', (ev) => chooseModel(ev.target.value));

  const chosen = modelPreset(backend, settings.form.model);
  body.appendChild(setField(t('settings.embed.modelLabel'), select, modelNoteText(chosen)));
  if (chosen && chosen.prefixed) {
    // Said out loud because it is the one property of a model that is
    // invisible in every other way: markers change every vector, and getting
    // them wrong shows up only as quietly worse search.
    body.appendChild(el('p', {
      className: 'set-note',
      text: t('settings.embed.model.prefixedNote'),
    }));
  }
  const modelOverride = overrideNote('api.model');
  if (modelOverride) body.appendChild(modelOverride);

  // -- endpoint -------------------------------------------------------------
  const url = el('input', {
    className: 'fs-input set-wide',
    attrs: { type: 'text', spellcheck: 'false', placeholder: 'http://…' },
  });
  url.value = settings.form.url;
  url.addEventListener('input', (ev) => {
    settings.form.url = ev.target.value;
    clearSettingsMessages();
  });
  body.appendChild(setField(t('settings.embed.urlLabel'), url, null));
  const urlOverride = overrideNote('api.url');
  if (urlOverride) body.appendChild(urlOverride);

  // -- dim + timeout --------------------------------------------------------
  const dim = el('input', {
    className: 'fs-input set-narrow',
    attrs: { type: 'number', min: '1', step: '1' },
  });
  dim.value = settings.form.dim || '';
  dim.addEventListener('input', (ev) => {
    settings.form.dim = ev.target.value;
    clearSettingsMessages();
  });

  const timeout = el('input', {
    className: 'fs-input set-narrow',
    attrs: { type: 'number', min: '1', step: '1' },
  });
  timeout.value = settings.form.timeout || '';
  timeout.addEventListener('input', (ev) => {
    settings.form.timeout = ev.target.value;
    clearSettingsMessages();
  });

  // Manual divider: the row below holds two `.set-field`s side by side, not
  // stacked, so the usual `.set-field + .set-field` adjacent-sibling rule
  // (a top border) would land on only the second one and read as a skewed
  // half-line rather than a real divider — cancelled for `.set-row` in CSS,
  // replaced with one full-width rule above the row instead.
  body.appendChild(el('div', { className: 'set-divider' }));
  body.appendChild(el('div', { className: 'set-row' }, [
    setField(t('settings.embed.dimLabel'), dim, null),
    setField(t('settings.embed.timeoutLabel'), timeout, null),
  ]));
  body.appendChild(el('p', {
    className: 'set-note',
    text: t('settings.embed.dimNote'),
  }));
  // Every editable field needs its own override line, not just the obvious
  // ones: an environment variable on `dim` or `timeout` makes that input inert
  // exactly as much as one on the URL does.
  const dimOverride = overrideNote('api.dim');
  if (dimOverride) body.appendChild(dimOverride);
  const timeoutOverride = overrideNote('api.timeout');
  if (timeoutOverride) body.appendChild(timeoutOverride);

  // -- key --------------------------------------------------------------
  if (backend.needs_key) {
    // Preceded by a `set-note` and up to two `set-override` lines, none of
    // them `.set-field` — same reason the row above needed a manual divider.
    body.appendChild(el('div', { className: 'set-divider' }));
    const stored = (settingValue('api.key_set') || {}).value;
    const key = el('input', {
      className: 'fs-input set-wide',
      attrs: {
        type: 'password', spellcheck: 'false', autocomplete: 'off',
        placeholder: stored ? t('settings.embed.key.placeholderStored') : 'sk-…',
      },
    });
    key.value = settings.form.key;
    key.addEventListener('input', (ev) => {
      settings.form.key = ev.target.value;
      settings.keyTouched = true;
    });
    body.appendChild(setField(t('settings.embed.keyLabel'), key, t('settings.embed.keyNote')));
    const keyOverride = overrideNote('api.key_set');
    if (keyOverride) body.appendChild(keyOverride);
  }

  // -- memory --------------------------------------------------------------
  // Only for the backend that is actually in use. `settings.embed` describes
  // the machine as configured, so showing it under a tab the user is merely
  // *considering* would report another backend's memory as this one's.
  if (settings.backendId === backendForSettings()) {
    body.appendChild(el('div', { className: 'set-divider' }));
    renderEmbedMemory(body);
  }

  renderSettingsMessages(body);
}

// -------------------------------------------------------- backend memory
//
// «Вивантажити» is NOT an off switch, and the copy has to keep saying so:
// the model comes back on the next search or indexed file, paying ~7-8 s
// once. A backend that is off is not a mode, it is a fault — what this
// offers is the memory back on purpose, which is the trade the engine
// deliberately left to a command instead of an idle timer.

/**
 * `unloaded` reads «не в памʼяті», never «не завантажена» — that word also
 * names the *download* action right below it (2.2 GB, disk), and the two
 * meanings sitting one line apart is exactly what read as "did it get
 * deleted?" A RAM state needs a RAM word; the file on disk is untouched.
 */
function holdLabel(held) {
  return t('settings.embed.mem.hold.' + (held === 'n/a' ? 'na' : held));
}

async function refreshEmbedState() {
  settings.embedError = null;
  try {
    settings.embed = await api('/api/embed/state');
  } catch (err) {
    if (isAuthError(err)) throw err;
    settings.embed = null;
    settings.embedError = err.message;
  }
}

/**
 * The memory block, appended under the backend form.
 *
 * Deliberately NOT gated behind «Зберегти»: this section's rule is that
 * nothing applies on click — but that rule is about *settings*, values that
 * describe the machine and are saved. Unloading is not a setting, it is an
 * action with an immediate effect and no stored form, the same category as
 * a bank's reindex. Putting it behind Save would mean saving to make it
 * happen and then having nothing left to un-save.
 */
function memLabel() {
  return t('settings.embed.mem.label');
}

function renderEmbedMemory(body) {
  const info = settings.embed;

  // Only when there is truly nothing else to show — the initial `GET
  // /api/embed/state` never landed. A failed `load`/`unload` click below
  // leaves `info` as the last known-good state and is shown as a note next
  // to the card instead, not by replacing the whole field with an error.
  if (!info) {
    body.appendChild(el('div', { className: 'set-field' }, [
      el('span', { className: 'set-label', text: memLabel() }),
      el('p', {
        className: settings.embedError ? 'modal-error' : 'empty-hint',
        text: settings.embedError || t('settings.embed.mem.notFetched'),
      }),
    ]));
    return;
  }

  const held = info.holding;
  const field = [el('span', { className: 'set-label', text: memLabel() })];

  // Description right under the heading, before the status card — this is
  // the one thing worth reading before touching any control below it, so it
  // does not wait behind the card the way an ordinary field's note would.
  if (held === 'loaded' || held === 'unloaded') {
    field.push(el('p', { className: 'set-note mem-intro' }, [
      el('strong', { text: t('settings.embed.mem.introStrong') }),
      document.createTextNode(t('settings.embed.mem.introRest')),
    ]));
  }

  // -- status: caption + badge, caption + model — no framing box any more,
  // just two lines sitting directly in the field, with the actions below --
  field.push(el('div', { className: 'set-mem-line' }, [
    el('span', { className: 'set-mem-caption', text: t('settings.embed.mem.statusCaption') }),
    el('span', {
      // `badge-empty`, never the red `badge-off`: an unloaded model is a
      // normal state that costs one wake-up, not a fault — the same reason
      // `frozen` got its own cold colour instead of the error one.
      className: 'badge ' + (held === 'loaded' ? 'badge-ready' : 'badge-empty'),
      text: holdLabel(held) || String(held),
    }),
  ]));
  field.push(el('div', { className: 'set-mem-line' }, [
    el('span', { className: 'set-mem-caption', text: t('settings.embed.mem.modelCaption') }),
    el('span', { className: 'set-mem-what', text: info.model || '—' }),
  ]));
  // Only where the backend actually has an idle-exit concept (`local`'s own
  // `MNEMO_EMBED_IDLE_TIMEOUT` — Ollama's own TTL is a live `expires_at`
  // timestamp, already shown as a footnote below, not a static duration).
  if (info.idle_timeout_s != null) {
    field.push(el('div', { className: 'set-mem-line' }, [
      el('span', { className: 'set-mem-caption', text: t('settings.embed.mem.idleCaption') }),
      el('span', {
        className: 'set-mem-what',
        text: info.idle_timeout_s > 0 ? humanUptime(info.idle_timeout_s) : t('settings.state.off'),
      }),
    ]));
  }

  const buttons = el('div', { className: 'set-mem-actions' });
  if (held === 'loaded') {
    buttons.appendChild(el('button', {
      className: 'btn',
      text: t('settings.embed.mem.unloadBtn'),
      attrs: settings.embedBusy ? { disabled: '' } : {},
      on: { click: () => embedAction('unload') },
    }));
  }
  // `unloaded` is excluded when nothing is cached: the probe would only
  // fail naming `warmup`, and the download block right below is the actual
  // next step in that case — showing both reads as two competing "load"
  // actions for the same empty state.
  const showProbe = held === 'loaded' || held === 'n/a' || held === 'unknown' ||
    (held === 'unloaded' && info.cached !== false);
  if (showProbe) {
    buttons.appendChild(el('button', {
      className: 'btn',
      // The same probe has three useful names. A cold local/Ollama backend
      // gets pulled back into RAM; one already holding the model is
      // checked; a remote endpoint never holds our memory at all, so only
      // its answer can be verified.
      text: held === 'unloaded'
        ? t('settings.embed.mem.wakeBtn')
        : (held === 'n/a' || held === 'unknown')
          ? t('settings.embed.mem.probeEndpointBtn')
          : t('settings.embed.mem.probeBtn'),
      attrs: settings.embedBusy ? { disabled: '' } : {},
      on: { click: () => embedAction('load') },
    }));
  }
  if (buttons.childNodes.length) field.push(buttons);

  // `cached === false` only happens under `local`/Ollama-shaped `api` (§
  // `_describe_local`/`_describe_api`) — a hosted API reports `cached: null`
  // and never reaches this branch. Separate from the `held` buttons above:
  // this is "put the weights on disk", not "wake the resident" — the label
  // says "на диск" so it never reads like the RAM action above it.
  const download = info.cached === false ? (info.download || {}) : null;
  if (download && download.active) {
    field.push(el('div', { className: 'set-mem-line' }, [
      el('i', { className: 'dot busy' }),
      el('span', { text: t('settings.embed.mem.downloading') }),
    ]));
  } else if (download) {
    field.push(el('div', { className: 'set-mem-actions' }, [
      el('button', {
        className: 'btn',
        text: t('settings.embed.mem.downloadBtn'),
        attrs: settings.embedBusy ? { disabled: '' } : {},
        on: { click: () => startEmbedDownload() },
      }),
    ]));
  }

  // A load/unload/download outcome, next to the card it acted on — not the
  // page-level `settings.note`/`errorText`, which render at the very bottom
  // of the whole tab and read as disconnected from the button that produced
  // them ("кудись улітає рядок").
  if (settings.embedError) {
    field.push(el('p', { className: 'modal-error', text: settings.embedError }));
  } else if (settings.embedNote) {
    field.push(el('p', { className: 'tok-ok', text: settings.embedNote }));
  }

  // -- remaining, per-state footnotes -------------------------------------
  const notes = [];
  // No number: the actual figure moves as hardware/model does, and "a few
  // seconds" is the fact worth knowing, not a specific one to trust.
  if (held === 'loaded' && info.wake_s) {
    notes.push(t('settings.embed.mem.note.wakeSoon'));
  }
  if (held === 'n/a') {
    // The console's own wording, not the backend's `detail`. A steady state
    // that every client renders the same way belongs to the interface — the
    // API stays English by convention, and echoing it here would put one
    // English line in the middle of a Ukrainian screen.
    notes.push(t('settings.embed.mem.note.naHosted'));
    notes.push(t('settings.embed.mem.note.naProbeCost'));
  }
  if (info.expires_at) notes.push(t('settings.embed.mem.note.expiresAt', { when: info.expires_at }));
  if (info.others_held) {
    // A count, never the names: the other models are somebody else's, and
    // this console unloads ours alone.
    notes.push(t('settings.embed.mem.note.othersHeld', { n: info.others_held }));
  }
  if (download && !download.active && download.failed) {
    notes.push(t('settings.embed.mem.note.downloadFailed'));
  }
  if (info.detail) notes.push(info.detail);
  for (const text of notes) field.push(el('p', { className: 'set-note', text: text }));

  body.appendChild(el('div', { className: 'set-field' }, field));
}

/**
 * Auto-expire a `settings.<field>` verdict 5s after it was set — same
 * mechanism as `app.js::setNote()` for bank cards. Leaving the section (or
 * the page) already clears these; this is the second line so the note also
 * disappears on its own if the user just stays put and reads it. The
 * staleness check (field still holds the exact value we scheduled for)
 * guards against erasing a newer note a second click produced in the
 * meantime.
 */
function scheduleSettingsNoteClear(field, value) {
  setTimeout(() => {
    if (settings[field] === value) {
      settings[field] = null;
      renderSettings();
    }
  }, 5000);
}

async function embedAction(what) {
  settings.embedBusy = true;
  settings.embedError = null;
  settings.embedNote = null;
  renderSettings();
  try {
    settings.embed = await api('/api/embed/' + what, { method: 'POST' });
    if (what === 'unload') {
      settings.embedNote = t('settings.embed.mem.unloadedNote');
    } else if (settings.embed.holding === 'n/a') {
      settings.embedNote = t('settings.embed.mem.probeOkBase') + (settings.embed.probe_dim
        ? t('settings.embed.mem.probeOkDimSuffix', { dim: settings.embed.probe_dim })
        : '.');
    } else {
      settings.embedNote = t('settings.embed.mem.loadedNote');
    }
    scheduleSettingsNoteClear('embedNote', settings.embedNote);
  } catch (err) {
    if (isAuthError(err)) { openGate('rejected'); return; }
    // The console's own wording for the one error this button realistically
    // hits — same reasoning as the `n/a` notes above: the backend's `detail`
    // stays English by convention, and this is the one code worth naming
    // instead of echoing that English sentence onto a Ukrainian screen.
    settings.embedError = err.code === 'embed_busy'
      ? t('settings.embed.mem.busyError')
      : err.message;
  } finally {
    settings.embedBusy = false;
    renderSettings();
  }
}

/** Poll `/api/embed/state` while a download is running, so the button's
 *  three states (idle / downloading / failed) track the detached `warmup
 *  --force` without any new IPC — same reasoning as the backend's own
 *  lazy `_download_status()` reconciliation. */
let downloadTimer = null;

function stopDownloadPolling() {
  if (downloadTimer) {
    clearInterval(downloadTimer);
    downloadTimer = null;
  }
}

function startDownloadPolling() {
  if (downloadTimer) return;
  downloadTimer = setInterval(async () => {
    await refreshEmbedState();
    if (!settings.embed || !settings.embed.download || !settings.embed.download.active) {
      stopDownloadPolling();
    }
    renderSettings();
  }, 3000);
}

/** The button's own click handler — the label is the explicit consent, no
 *  confirm dialog on top (same decision as the terminal `warmup`). */
async function startEmbedDownload() {
  settings.embedError = null;
  settings.embedNote = null;
  try {
    await api('/api/embed/download', { method: 'POST' });
    await refreshEmbedState();
    startDownloadPolling();
  } catch (err) {
    if (isAuthError(err)) { openGate('rejected'); return; }
    if (err.code === 'already_cached' || err.code === 'download_in_progress') {
      await refreshEmbedState();
      if (settings.embed && settings.embed.download && settings.embed.download.active) {
        startDownloadPolling();
      }
    } else {
      settings.embedError = err.message;
    }
  } finally {
    renderSettings();
  }
}

/** A read-only `caption — value` line, for the sections that report rather
 *  than edit. */
function setStat(label, value, mono) {
  return el('div', { className: 'set-stat' }, [
    el('span', { className: 'set-stat-label', text: label }),
    el('span', {
      className: 'set-stat-value' + (mono ? ' is-mono' : ''),
      text: value == null || value === '' ? '—' : String(value),
    }),
  ]);
}

/** Seconds as `2 год 14 хв` / `3 хв 05 с` / `47 с`. */
function humanUptime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return t('settings.uptime.hoursMinutes', { h: h, m: m });
  if (m) return t('settings.uptime.minutesSeconds', { m: m, s: String(s).padStart(2, '0') });
  return t('settings.uptime.seconds', { s: s });
}

/**
 * What is running, and where.
 *
 * Reports only. Stopping and restarting the backend are deliberately absent:
 * this page is served BY that process, so a stop button would kill the page
 * that offers it and leave no way back except a terminal. So the honest thing
 * is to show the state and name the command.
 */
/**
 * What applies to this machine's console regardless of which bank is open:
 * the browser's own preferences first (language, theme — both apply on
 * click, nothing for Save to do with either), then what changes the machine
 * (autostart, requireLogin, autoUpdate — all need Save), then what merely
 * reports (process facts, sitting under both since nobody opens this section
 * to read them first).
 */
function renderGeneralSection(body) {
  renderLanguage(body);
  renderTheme(body);
  renderAutostart(body);
  renderRequireLogin(body);
  renderAutoUpdate(body);
  // The one message this can still produce that isn't already handled right
  // under its own field: "Немає що зберігати" (Save clicked with no pending
  // edit to any of the three controls above — theme/language don't count,
  // they apply on their own and were never gated on Save to begin with).
  // Placed here, immediately after all three Save-gated fields and before
  // the unrelated Про-проект/Стан blocks below -- never after them, or it
  // would read as a verdict on those instead.
  renderSettingsMessages(body);

  const svc = state.service;
  if (!svc) {
    body.appendChild(el('p', { className: 'empty-hint', text: t('settings.general.serviceNotLoaded') }));
    renderProjectLink(body);
    return;
  }

  // A manual divider, not `.set-field + .set-field`: the auto-update field
  // above ends with a check button and its result badge, neither of them a
  // `.set-field`, so the adjacent-sibling rule that separates every other
  // field here never fires before this one.
  body.appendChild(el('div', { className: 'set-divider' }));

  const box = el('div', { className: 'set-stats' }, [
    setStat(t('settings.general.stat.version'), svc.version, true),
    setStat(t('settings.general.stat.pid'), svc.pid, true),
    setStat(t('settings.general.stat.address'), (svc.host || '—') + ':' + (svc.port != null ? svc.port : '—'), true),
    setStat(t('settings.general.stat.provider'), svc.provider, true),
    setStat(t('settings.general.stat.uptime'), humanUptime(svc.uptime_s)),
    setStat(t('settings.general.stat.priorityQueue'), svc.priority_enabled
      ? t('settings.general.stat.priorityOn') : t('settings.general.stat.priorityOff')),
  ]);
  body.appendChild(setField(t('settings.general.statusLabel'), box, null));

  renderProjectLink(body);
}

/** A link to the project's own repository — not machine state, so it
 *  renders after the service-status block (or its not-yet-loaded
 *  placeholder) regardless of whether that data has loaded. */
function renderProjectLink(body) {
  body.appendChild(el('div', { className: 'set-divider' }));
  body.appendChild(setField(t('settings.general.aboutLabel'), el('a', {
    className: 'set-link',
    text: 'GitHub',
    attrs: {
      href: 'https://github.com/DIMKA4621/mnemo',
      target: '_blank',
      rel: 'noopener noreferrer',
    },
  }), null));
}

/**
 * `.segmented set-toggle`, the same compact left-aligned control autostart
 * uses below it — not the bare `.segmented`, which stretches to the field's
 * full width and reads as a table row rather than a two-state switch.
 */
function renderTheme(body) {
  const seg = el('div', { className: 'segmented set-toggle', attrs: { id: 'theme' } });
  const current = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
  for (const [value, key] of [['dark', 'settings.general.theme.dark'], ['light', 'settings.general.theme.light']]) {
    seg.appendChild(el('button', {
      className: 'seg' + (current === value ? ' is-active' : ''),
      text: t(key),
      attrs: { 'data-theme': value },
      on: {
        click: () => {
          applyTheme(value);
          for (const button of seg.querySelectorAll('.seg')) {
            button.classList.toggle('is-active', button.dataset.theme === value);
          }
        },
      },
    }));
  }
  body.appendChild(setField(t('settings.general.theme.label'), seg, t('settings.general.theme.note')));
}

/**
 * The language toggle — same instant-apply idiom as the theme control right
 * above it: this browser's own preference, not a machine setting, so it
 * applies on click and does not wait for «Зберегти».
 *
 * `.segmented set-toggle`, not the bare `.segmented`: without `set-toggle`
 * it stretches to the field's full width inside `.set-field`'s flex column
 * — the same bug fixed for theme once already.
 *
 * The two option labels ("English" / "Українська") are the language's own
 * name, shown in itself regardless of which language is currently active —
 * the standard convention for a language switcher, and never routed through
 * `t()`.
 */
function renderLanguage(body) {
  const seg = el('div', { className: 'segmented set-toggle', attrs: { id: 'language' } });
  for (const [value, label] of [['en', 'English'], ['uk', 'Українська']]) {
    seg.appendChild(el('button', {
      className: 'seg' + (resolveLang() === value ? ' is-active' : ''),
      text: label,
      attrs: { 'data-lang': value },
      on: {
        click: () => {
          applyLanguage(value);
          for (const button of seg.querySelectorAll('.seg')) {
            button.classList.toggle('is-active', button.dataset.lang === value);
          }
        },
      },
    }));
  }
  body.appendChild(setField(t('settings.general.language.label'), seg, t('settings.general.language.note')));
}

/**
 * Start at logon — a `.segmented` pair, the control this console already uses
 * for a two-state choice.
 *
 * Unlike stopping the service this is safe to offer: registering a scheduled
 * task changes what happens at the NEXT logon and touches nothing running, so
 * the page it is served from survives the change either way.
 *
 * Clicking selects, it does not apply — «Зберегти» does. The installer
 * switches autostart on by default (`-NoAutostart` opts out), so a normally-
 * installed machine opens this already on: the control reports a state that
 * exists rather than proposing one.
 */
function renderAutostart(body) {
  const auto = settings.autostart;
  if (!auto) {
    body.appendChild(setField(t('settings.general.autostart.label'), el('p', {
      className: 'set-note',
      text: settings.autostartError || t('settings.general.autostart.notFetched'),
    }), null));
    return;
  }
  if (!auto.supported) {
    body.appendChild(setField(t('settings.general.autostart.label'), el('p', {
      className: 'set-note',
      text: t('settings.general.autostart.unsupported'),
    }), null));
    return;
  }

  const chosen = autostartWanted();
  const seg = el('div', { className: 'segmented set-toggle' });
  for (const option of [{ on: true, key: 'settings.toggle.on' }, { on: false, key: 'settings.toggle.off' }]) {
    seg.appendChild(el('button', {
      className: 'seg' + (chosen === option.on ? ' is-active' : ''),
      text: t(option.key),
      attrs: settings.busy ? { disabled: '' } : {},
      on: { click: () => chooseAutostart(option.on) },
    }));
  }

  const named = auto.name ? t('settings.general.autostart.namedSuffix', { name: auto.name }) : '';
  body.appendChild(setField(t('settings.general.autostart.label'), seg,
    t('settings.general.autostart.note', { mechanism: auto.mechanism || '—', named: named })));

  // Said plainly, because the control now shows an intention rather than the
  // machine: without this line a page left open on «Вимкнено» reads as a
  // machine with autostart off.
  if (chosen !== !!auto.enabled) {
    body.appendChild(el('p', {
      className: 'set-override',
      text: t('settings.notSavedToggle', { state: t(auto.enabled ? 'settings.state.on' : 'settings.state.off') }),
    }));
  }

  // The save verdict for THIS control, right under it — not the shared
  // settings.note/renderSettingsMessages() at the bottom of the tab, which
  // would land it after «Про проект»/«Стан», nowhere near what changed.
  if (settings.autostartSaveError) {
    body.appendChild(el('p', { className: 'modal-error', text: settings.autostartSaveError }));
  } else if (settings.autostartSaveNote) {
    body.appendChild(el('p', { className: 'tok-ok', text: settings.autostartSaveNote }));
  }
}

/** The autostart state the form is showing: the edit if there is one, else
 *  what the machine reports. */
function autostartWanted() {
  if (settings.autostartWant != null) return settings.autostartWant;
  return !!(settings.autostart && settings.autostart.enabled);
}

/** Select an autostart state. Nothing is registered until Save. */
function chooseAutostart(want) {
  if (settings.busy) return;
  // Back to the stored value -> no pending edit at all, rather than an edit
  // that happens to match. Otherwise Save would fire a request that changes
  // nothing, and the "не збережено" line would need to guess.
  settings.autostartWant =
    (!!want === !!(settings.autostart && settings.autostart.enabled)) ? null : !!want;
  settings.errorText = null;
  settings.note = null;
  renderSettings();
}

/**
 * Unattended auto-apply (contract: `GET/PUT /api/settings`'s `"auto_update"`
 * key, `POST /api/update/check`) — same Save-gated `.segmented set-toggle`
 * idiom as autostart above, not a second visual language, and part of the
 * same `/api/settings` document the embed section already saves through
 * `submitSettings` — so it can carry an env override like any other field
 * there (`overrideNote`), unlike autostart which is its own endpoint.
 */
function autoUpdateStoredValue() {
  const item = settingValue('auto_update');
  return item ? !!item.value : true;
}

/** The auto-update state the form is showing: the edit if there is one,
 *  else what the machine reports — same shape as `autostartWanted()`. */
function autoUpdateWanted() {
  if (settings.autoUpdateWant != null) return settings.autoUpdateWant;
  return autoUpdateStoredValue();
}

/** Select an auto-update state. Nothing is saved until «Зберегти». */
function chooseAutoUpdate(want) {
  if (settings.busy) return;
  settings.autoUpdateWant = (!!want === autoUpdateStoredValue()) ? null : !!want;
  settings.errorText = null;
  settings.note = null;
  renderSettings();
}

function renderAutoUpdate(body) {
  // Manual divider: `renderRequireLogin` above ends with its two-line
  // "Off (default).../On:..." note, not a `.set-field`, so the adjacent-
  // sibling rule that separates every other field here never fires before
  // this one — same reasoning as the divider before requireLogin itself.
  body.appendChild(el('div', { className: 'set-divider' }));
  const chosen = autoUpdateWanted();
  const seg = el('div', { className: 'segmented set-toggle' });
  for (const option of [{ on: true, key: 'settings.toggle.on' }, { on: false, key: 'settings.toggle.off' }]) {
    seg.appendChild(el('button', {
      className: 'seg' + (chosen === option.on ? ' is-active' : ''),
      text: t(option.key),
      attrs: settings.busy ? { disabled: '' } : {},
      on: { click: () => chooseAutoUpdate(option.on) },
    }));
  }

  body.appendChild(setField(t('settings.general.autoUpdate.label'), seg, t('settings.general.autoUpdate.note')));

  const autoUpdateOverride = overrideNote('auto_update');
  if (autoUpdateOverride) body.appendChild(autoUpdateOverride);

  if (chosen !== autoUpdateStoredValue()) {
    body.appendChild(el('p', {
      className: 'set-override',
      text: t('settings.notSavedToggle', {
        state: t(autoUpdateStoredValue() ? 'settings.state.on' : 'settings.state.off'),
      }),
    }));
  }

  // The save verdict for THIS control, right under it -- same reasoning as
  // renderAutostart's own block above. Placed before the "Перевірити
  // оновлення" button below: that button is a separate, unrelated feature
  // (checking for a new release) sharing this field only visually.
  if (settings.autoUpdateSaveError) {
    body.appendChild(el('p', { className: 'modal-error', text: settings.autoUpdateSaveError }));
  } else if (settings.autoUpdateSaveNote) {
    body.appendChild(el('p', { className: 'tok-ok', text: settings.autoUpdateSaveNote }));
  }

  body.appendChild(el('div', { className: 'maint-head' }, [
    el('button', {
      className: 'btn',
      text: settings.updateCheckBusy
        ? t('settings.general.autoUpdate.checking')
        : t('settings.general.autoUpdate.checkBtn'),
      attrs: settings.updateCheckBusy ? { disabled: '' } : {},
      on: { click: () => runUpdateCheck() },
    }),
  ]));

  if (settings.updateCheckError) {
    body.appendChild(el('p', { className: 'modal-error', text: settings.updateCheckError }));
  } else if (settings.updateCheckResult) {
    // A real result, not page-description text -- badge it the same way the
    // rest of the console marks a live outcome: warm for "there's something
    // new" (matches the sidebar banner, same wording too, so the same fact
    // never reads as two different things in two places), a green bordered
    // chip for "you're already current" (matches .tok-ok's success styling
    // elsewhere). When something's available, this is now a second, real
    // button onto the same modal the sidebar banner already opens -- click
    // whichever one is closer, they do the same thing. "Up to date" has
    // nothing to click through to, so it stays plain text.
    if (settings.updateCheckAvailable) {
      body.appendChild(el('button', {
        className: 'upd-check-note is-available', text: settings.updateCheckResult,
        on: { click: () => openUpdateModal() },
      }));
    } else {
      body.appendChild(el('p', { className: 'upd-check-note is-current', text: settings.updateCheckResult }));
    }
  }
}

/**
 * Whether `/api` (console + CLI) requires a token (MN-19: `require_login`,
 * `PUT /api/settings`) — same Save-gated `.segmented set-toggle` idiom as
 * autostart/auto-update above, and part of the same `/api/settings`
 * document, so it carries an env override (`overrideNote`) the same way.
 */
function requireLoginStoredValue() {
  const item = settingValue('require_login');
  return item ? !!item.value : false;
}

/** The require-login state the form is showing: the edit if there is one,
 *  else what the machine reports — same shape as `autoUpdateWanted()`. */
function requireLoginWanted() {
  if (settings.requireLoginWant != null) return settings.requireLoginWant;
  return requireLoginStoredValue();
}

/** Select a require-login state. Nothing is saved until «Зберегти». */
function chooseRequireLogin(want) {
  if (settings.busy) return;
  settings.requireLoginWant = (!!want === requireLoginStoredValue()) ? null : !!want;
  settings.errorText = null;
  settings.note = null;
  renderSettings();
}

function renderRequireLogin(body) {
  const chosen = requireLoginWanted();
  const seg = el('div', { className: 'segmented set-toggle' });
  for (const option of [{ on: false, key: 'settings.toggle.off' }, { on: true, key: 'settings.toggle.on' }]) {
    seg.appendChild(el('button', {
      className: 'seg' + (chosen === option.on ? ' is-active' : ''),
      text: t(option.key),
      attrs: settings.busy ? { disabled: '' } : {},
      on: { click: () => chooseRequireLogin(option.on) },
    }));
  }

  // Manual divider: `renderAutostart` above ends with a check button and
  // its result note, neither of them a `.set-field`, so the adjacent-sibling
  // rule that separates every other field here never fires before this one
  // — same reasoning as the divider before the service-status stats box.
  body.appendChild(el('div', { className: 'set-divider' }));
  body.appendChild(setField(t('settings.general.requireLogin.label'), seg, null));
  // Two facts, two lines: what's off (the current default) and what's on —
  // same `<br>`-per-sentence shape as the embed section's consequences block.
  body.appendChild(el('p', { className: 'set-note' }, [
    document.createTextNode(t('settings.general.requireLogin.noteOff')),
    el('br'),
    document.createTextNode(t('settings.general.requireLogin.noteOn')),
  ]));

  const requireLoginOverride = overrideNote('require_login');
  if (requireLoginOverride) body.appendChild(requireLoginOverride);

  if (chosen !== requireLoginStoredValue()) {
    body.appendChild(el('p', {
      className: 'set-override',
      text: t('settings.notSavedToggle', {
        state: t(requireLoginStoredValue() ? 'settings.state.on' : 'settings.state.off'),
      }),
    }));
  }

  // The save verdict for THIS control, right under it — same reasoning as
  // renderAutostart's/renderAutoUpdate's own blocks above.
  if (settings.requireLoginSaveError) {
    body.appendChild(el('p', { className: 'modal-error', text: settings.requireLoginSaveError }));
  } else if (settings.requireLoginSaveNote) {
    body.appendChild(el('p', { className: 'tok-ok', text: settings.requireLoginSaveNote }));
  }

  // The one-time reveal: only appears the instant a save handed back a
  // fresh `service_token` (see submitGeneral). Not gated behind
  // scheduleSettingsNoteClear like the notes above — a secret must stay on
  // screen until the user leaves, not vanish on its own after 5s.
  if (settings.requireLoginToken) {
    // Manual divider, not `.set-field + .set-field`: the toggle's own field
    // ends several plain `<p>` notes/verdicts before this one, so the
    // adjacent-sibling rule that separates every other field here never
    // fires before it — same reasoning as renderEmbedSection's memory block.
    body.appendChild(el('div', { className: 'set-divider' }));
    body.appendChild(renderRequireLoginToken());
  }
}

/**
 * The freshly-minted service token, shown once — same masked-field +
 * show/hide + copy idiom as the bank-token panel (app.js), reusing its
 * generic pieces (`maskToken`, `copyButton`) directly rather than that
 * panel's bank-specific modal/snippet machinery, which has nothing to do
 * with a machine-wide service token.
 */
function renderRequireLoginToken() {
  const value = settings.requireLoginToken;
  const revealed = settings.requireLoginTokenRevealed;

  const field = el('input', {
    className: 'fs-input tok-value',
    attrs: {
      id: 'require-login-token', name: 'require-login-token', type: 'text',
      readonly: '', spellcheck: 'false', autocomplete: 'off',
    },
  });
  field.value = revealed ? value : maskToken(value);

  return el('div', { className: 'set-field' }, [
    el('label', {
      className: 'set-label', text: t('settings.general.requireLogin.tokenLabel'),
      attrs: { for: 'require-login-token' },
    }),
    el('div', { className: 'tok-row' }, [
      field,
      el('button', {
        className: 'btn',
        text: revealed ? t('common.token.hide') : t('common.token.show'),
        title: revealed ? t('common.token.hideTitle') : t('common.token.showTitle'),
        attrs: { 'aria-pressed': revealed ? 'true' : 'false' },
        on: { click: () => {
          settings.requireLoginTokenRevealed = !settings.requireLoginTokenRevealed;
          renderSettings();
        } },
      }),
      copyButton(() => settings.requireLoginToken, t('common.token.copyTokenTitle')),
    ]),
    el('p', {
      className: 'tok-note',
      text: t('settings.general.requireLogin.tokenNote'),
    }),
  ]);
}

/**
 * `POST /api/update/check` — the same manual trigger the sidebar banner's
 * own periodic background check uses, reachable here too. Never itself
 * shows the auto-pending countdown modal: it only refreshes `latest_tag`/
 * `update_available` (via `refreshUpdateStatus()`, update.js's existing
 * single source of truth for that), and that never opens anything on its
 * own — `update.js`'s own `refreshUpdateStatus` only ever reopens a
 * countdown that is independently real, armed by the checker's background
 * tick, never by this click.
 */
async function runUpdateCheck() {
  if (settings.updateCheckBusy) return;
  settings.updateCheckBusy = true;
  settings.updateCheckError = null;
  renderSettings();
  try {
    const result = await api('/api/update/check', { method: 'POST' });
    await refreshUpdateStatus();
    settings.updateCheckAvailable = !!result.update_available;
    settings.updateCheckResult = result.error
      ? null
      : (result.update_available
          // Same wording the sidebar banner uses (renderSidebarUpdateBanner,
          // update.js) -- one fact, one sentence, wherever it shows up.
          ? t('update.banner.available', { tag: result.latest_tag })
          : t('settings.general.autoUpdate.upToDate'));
    if (result.error) settings.updateCheckError = result.error;
    // `updateCheckAvailable` has no text of its own to compare against on
    // expiry, so its clear rides along with its paired `updateCheckResult`
    // — the field that actually carries the note shown on screen.
    if (settings.updateCheckResult) {
      const resultAtSchedule = settings.updateCheckResult;
      setTimeout(() => {
        if (settings.updateCheckResult === resultAtSchedule) {
          settings.updateCheckResult = null;
          settings.updateCheckAvailable = false;
          renderSettings();
        }
      }, 5000);
    }
  } catch (err) {
    if (isAuthError(err)) { openGate('rejected'); return; }
    settings.updateCheckError = err.message;
  } finally {
    settings.updateCheckBusy = false;
    renderSettings();
  }
}

/**
 * Apply the selected autostart and/or auto-update state, then adopt
 * whatever each endpoint reports back.
 *
 * Two independent drafts, two independent endpoints (`POST /api/autostart`,
 * `PUT /api/settings`) — neither click is ever trusted as the new state on
 * its own: each response is re-read, so a task the scheduler refused, or a
 * setting the server rejected, leaves its control showing the machine
 * instead of showing an intention as a fact. One request failing does not
 * stop the other from being tried.
 *
 * Each field's own verdict is kept separate (`autostartSaveNote`/
 * `autoUpdateSaveNote`, rendered right under that field by
 * `renderAutostart`/`renderAutoUpdate`) rather than joined into one
 * `settings.note` — one save can touch either field alone or both, and a
 * merged sentence at the bottom of the tab read as belonging to whichever
 * field happened to be rendered last.
 */
async function submitGeneral() {
  const autostartWant = settings.autostartWant;
  const autoUpdateWant = settings.autoUpdateWant;
  const requireLoginWant = settings.requireLoginWant;
  if (autostartWant == null && autoUpdateWant == null && requireLoginWant == null) {
    settings.note = t('settings.messages.nothingChanged');
    renderSettings();
    return;
  }

  settings.busy = true;
  settings.save.disabled = true;
  settings.errorText = null;
  settings.note = null;
  settings.autostartSaveNote = null;
  settings.autostartSaveError = null;
  settings.autoUpdateSaveNote = null;
  settings.autoUpdateSaveError = null;
  settings.requireLoginSaveNote = null;
  settings.requireLoginSaveError = null;
  renderSettings();

  try {
    if (autostartWant != null) {
      try {
        settings.autostart = await api('/api/autostart', {
          method: 'POST', body: { enabled: autostartWant },
        });
        settings.autostartWant = null;
        settings.autostartSaveNote = settings.autostart.enabled
          ? t('settings.general.autostart.savedOn')
          : t('settings.general.autostart.savedOff');
        scheduleSettingsNoteClear('autostartSaveNote', settings.autostartSaveNote);
      } catch (err) {
        if (isAuthError(err)) { openGate('rejected'); return; }
        settings.autostartSaveError = err.message;
      }
    }

    if (autoUpdateWant != null) {
      try {
        settings.data = await api('/api/settings', {
          method: 'PUT', body: { auto_update: autoUpdateWant },
        });
        settings.autoUpdateWant = null;
        settings.autoUpdateSaveNote = autoUpdateWant
          ? t('settings.general.autoUpdate.savedOn')
          : t('settings.general.autoUpdate.savedOff');
        scheduleSettingsNoteClear('autoUpdateSaveNote', settings.autoUpdateSaveNote);
      } catch (err) {
        if (isAuthError(err)) { openGate('rejected'); return; }
        settings.autoUpdateSaveError = err.message;
      }
    }

    if (requireLoginWant != null) {
      try {
        const data = await api('/api/settings', {
          method: 'PUT', body: { require_login: requireLoginWant },
        });
        settings.data = data;
        settings.requireLoginWant = null;
        settings.requireLoginSaveNote = requireLoginWant
          ? t('settings.general.requireLogin.savedOn')
          : t('settings.general.requireLogin.savedOff');
        // `data.service_token` rides in this exact response the moment the
        // save turns the gate on (contract: MN-19) — never echoed by a plain
        // GET afterwards, so it has to be picked up right here or it is
        // gone. Adopted into this session's own credential immediately too:
        // otherwise the very next `/api` request (the status/embed refresh
        // below, or a WS reconnect in shell.js) would 401 and slam the gate
        // shut on the same screen that just handed the token out.
        if (data.service_token) {
          settings.requireLoginToken = data.service_token;
          settings.requireLoginTokenRevealed = false;
          token = data.service_token;
          sessionStorage.setItem('mnemo_token', token);
        }
      } catch (err) {
        if (isAuthError(err)) { openGate('rejected'); return; }
        settings.requireLoginSaveError = err.message;
      }
    }
  } finally {
    settings.busy = false;
    settings.save.disabled = false;
    renderSettings();
  }
}

/** Fetch the structured report only when Maintenance is actually opened. */
async function refreshMaintenance(options) {
  if (settings.maintenanceBusy) return;
  const keepNote = options && options.keepNote;
  settings.maintenanceBusy = true;
  settings.maintenanceError = null;
  if (!keepNote) settings.cleanupNote = null;
  if (settings.section === 'maint') renderSettings();
  try {
    settings.maintenance = await api('/api/doctor');
  } catch (err) {
    if (isAuthError(err)) { openGate('rejected'); return; }
    settings.maintenanceError = err.message;
  } finally {
    settings.maintenanceBusy = false;
    if (settings.section === 'maint') renderSettings();
  }
}

function maintItem(title, value, note, tone) {
  return el('div', { className: 'maint-item' + (tone ? ' is-' + tone : '') }, [
    el('div', { className: 'maint-item-line' }, [
      el('strong', { text: title }),
      value ? el('code', { text: value }) : null,
    ]),
    note ? el('p', { className: 'set-note', text: note }) : null,
  ]);
}

/** Diagnostics and cleanup — the rare commands, kept off the main screen. */
function renderMaintSection(body) {
  const report = settings.maintenance;
  body.appendChild(el('div', { className: 'maint-head' }, [
    el('button', {
      className: 'btn',
      text: settings.maintenanceBusy ? t('settings.maint.refreshing') : t('settings.maint.refreshBtn'),
      attrs: settings.maintenanceBusy ? { disabled: '' } : {},
      on: { click: () => refreshMaintenance() },
    }),
  ]));

  if (settings.maintenanceError) {
    body.appendChild(el('p', { className: 'modal-error', text: settings.maintenanceError }));
  }
  if (!report) {
    body.appendChild(el('p', {
      className: 'empty-hint',
      text: settings.maintenanceBusy ? t('settings.maint.collecting') : t('settings.maint.notFetched'),
    }));
    return;
  }

  const engine = report.engine || {};
  body.appendChild(setField(t('settings.maint.engineLabel'), el('div', { className: 'set-stats' }, [
    setStat('Engine home', engine.home, true),
    setStat('State dir', engine.state_dir, true),
    setStat('Python', engine.python, true),
  ]), null));

  const provider = report.provider || {};
  const model = report.model || {};
  const vec = report.sqlite_vec || {};
  const resident = report.resident || {};
  const endpoint = report.endpoint || {};
  const providerValue = provider.machine + ((provider.overrides || []).length
    ? ' · overrides: ' + provider.overrides.join(', ')
    : '');
  const providerStats = [
    setStat(t('settings.maint.providerLabel'), providerValue, true),
    setStat(t('settings.maint.localModelLabel'), model.needed
      ? (model.cached ? t('settings.maint.model.cachedFull') : t('settings.maint.model.notLoaded'))
      : (model.cached ? t('settings.maint.model.cachedNotNeeded') : t('settings.maint.model.notNeeded'))),
    setStat('sqlite-vec', vec.ok ? 'ok' : t('settings.maint.unavailable')),
    setStat(t('settings.maint.residentLabel'), resident.applicable
      ? (t(resident.up ? 'settings.maint.resident.up' : 'settings.maint.resident.down') +
         ' · ' + resident.host + ':' + resident.port + t('settings.maint.resident.portSuffix'))
      : t('settings.maint.resident.na')),
  ];
  if (endpoint.applicable) {
    providerStats.push(setStat('API endpoint', endpoint.configured
      ? endpoint.url + ' · ' + endpoint.model + ' · ' + endpoint.dim + ' ' +
        t('settings.maint.endpoint.dimsUnit') + (endpoint.key_set ? ' · key set' : ' · no key')
      : t('settings.maint.endpoint.notConfigured', { error: endpoint.error || t('settings.maint.unknown') }), true));
  }
  body.appendChild(setField(t('settings.maint.embedLabel'), el('div', { className: 'set-stats' }, providerStats), null));
  if (vec.error) body.appendChild(el('p', { className: 'modal-error', text: vec.error }));

  const backend = report.backend || {};
  const tokenFact = report.token || {};
  body.appendChild(setField(t('settings.maint.serviceLabel'), el('div', { className: 'set-stats' }, [
    setStat('Backend', backend.up
      ? t('settings.maint.backend.upSummary', { pid: backend.serving_pid })
      : t('settings.maint.backend.down', { error: backend.error || t('settings.maint.unknown') })),
    setStat('URL', backend.url, true),
    setStat(t('settings.maint.queueLabel'), backend.queue_depth),
    setStat('API token', tokenFact.present
      ? 'set · ' + (tokenFact.where || tokenFact.source || 'unknown') +
        ' · ' + (tokenFact.scope || 'unknown scope')
      : t('settings.maint.token.notSet'), true),
  ]), null));

  const registry = report.registry || {};
  if (!registry.ok) {
    body.appendChild(setField(t('settings.maint.registryLabel'), el('p', {
      className: 'modal-error',
      text: t('settings.maint.registryUnreadable', { error: registry.error || t('settings.maint.unknown') }),
    }), null));
  } else {
    const registryBox = el('div', { className: 'maint-list' });
    registryBox.appendChild(maintItem(
      plural('memory.count.banks', registry.count), null, t('settings.maint.registryReadable'), 'ok'));
    for (const bank of registry.banks || []) {
      const flags = [];
      if (bank.state !== 'enabled') flags.push(bank.state);
      if (!bank.exists) flags.push(t('settings.maint.registry.noRoot'));
      registryBox.appendChild(maintItem(
        bank.name,
        flags.length ? flags.join(' · ') : 'ok',
        bank.root,
        flags.length ? 'warn' : null));
    }
    body.appendChild(setField(t('settings.maint.registryLabel'), registryBox, null));
  }

  const wiring = report.wiring || {};
  const wiringBox = el('div', { className: 'maint-list' });
  if (!wiring.ok) {
    wiringBox.appendChild(maintItem(t('settings.maint.unknownTitle'), null, wiring.error || t('settings.maint.genericError'), 'error'));
  } else if (!(wiring.stale || []).length) {
    wiringBox.appendChild(maintItem(
      plural('settings.maint.count.projects', wiring.total || 0), t('settings.maint.wiring.allCurrent'), null, 'ok'));
  } else {
    for (const project of wiring.stale) {
      wiringBox.appendChild(maintItem(
        project.root,
        project.command,
        project.reason,
        'warn'));
    }
  }
  body.appendChild(setField('Project wiring', wiringBox, null));

  renderOrphanMaintenance(body, report.orphans || {});
}

function renderOrphanMaintenance(body, orphans) {
  const box = el('div', { className: 'maint-list' });
  if (!orphans.ok) {
    box.appendChild(maintItem(
      t('settings.maint.orphans.unavailableTitle'), null,
      t('settings.maint.orphans.deletionForbidden', {
        reason: orphans.error || t('settings.maint.orphans.registryUncheckable'),
      }),
      'error'));
  } else if (!orphans.count) {
    box.appendChild(maintItem(t('settings.maint.orphans.noneTitle'), '0 B', t('settings.maint.orphans.noneNote'), 'ok'));
  } else {
    for (const orphan of orphans.items || []) {
      let note = orphan.error
        ? t('settings.maint.orphans.unreadable', { error: orphan.error })
        : orphan.root || (orphan.schema == null
          ? t('settings.maint.orphans.preV3NoRoot')
          : t('settings.maint.orphans.noRoot'));
      if (orphan.root_exists) note += t('settings.maint.orphans.rootStillOnDisk');
      box.appendChild(maintItem(
        orphan.id,
        fmtBytes(orphan.size),
        (orphan.files == null ? t('settings.maint.orphans.unknownFiles') : plural('memory.count.files', orphan.files)) +
          ' · ' + note,
        'warn'));
    }
  }
  body.appendChild(setField(
    t('settings.maint.orphans.sectionLabel') + (orphans.ok && orphans.count
      ? ' · ' + orphans.count + ' · ' + fmtBytes(orphans.bytes)
      : ''),
    box,
    t('settings.maint.orphans.sectionNote')));

  if (settings.cleanupNote) {
    body.appendChild(el('p', { className: 'tok-ok', text: settings.cleanupNote }));
  }
  if (!orphans.ok || !orphans.count) return;

  if (!settings.cleanupConfirming) {
    body.appendChild(el('div', { className: 'maint-actions' }, [
      el('button', {
        className: 'btn',
        text: t('settings.maint.orphans.cleanupBtn'),
        attrs: settings.cleanupBusy ? { disabled: '' } : {},
        on: { click: () => {
          settings.cleanupConfirming = true;
          renderSettings();
        } },
      }),
    ]));
    return;
  }

  const ids = (orphans.items || []).map((item) => item.id);
  body.appendChild(el('div', { className: 'tok-confirm' }, [
    el('p', {
      className: 'tok-confirm-text',
      text: t('settings.maint.orphans.confirmText', { ids: ids.join(', ') }),
    }),
    el('div', { className: 'tok-confirm-row' }, [
      el('button', {
        className: 'btn', text: t('common.btn.cancel'),
        attrs: settings.cleanupBusy ? { disabled: '' } : {},
        on: { click: () => {
          settings.cleanupConfirming = false;
          renderSettings();
        } },
      }),
      el('button', {
        className: 'btn btn-danger',
        text: settings.cleanupBusy
          ? t('settings.maint.orphans.cleaning')
          : t('settings.maint.orphans.deleteBtn', { n: ids.length }),
        attrs: settings.cleanupBusy ? { disabled: '' } : {},
        on: { click: () => submitOrphanCleanup(ids) },
      }),
    ]),
  ]));
}

async function submitOrphanCleanup(ids) {
  if (settings.cleanupBusy || !ids.length) return;
  settings.cleanupBusy = true;
  settings.maintenanceError = null;
  settings.cleanupNote = null;
  renderSettings();
  try {
    const result = await api('/api/clean-orphans', {
      method: 'POST', body: { ids: ids },
    });
    const parts = [
      t('settings.maint.orphans.result.removed', { removed: result.removed.length, total: result.requested.length }),
      t('settings.maint.orphans.result.freed', { bytes: fmtBytes(result.freed_bytes) }),
    ];
    if (result.skipped.length) parts.push(t('settings.maint.orphans.result.skipped', { n: result.skipped.length }));
    if (result.locked.length) parts.push(t('settings.maint.orphans.result.locked', { n: result.locked.length }));
    settings.cleanupNote = parts.join(' · ');
    settings.cleanupConfirming = false;
    settings.maintenance = await api('/api/doctor');
    if (result.locked.length) {
      settings.maintenanceError = t('settings.maint.orphans.lockedError', {
        list: result.locked.map((item) => item.id + ' (' + item.paths.join(', ') + ')').join(' · '),
      });
    }
  } catch (err) {
    if (isAuthError(err)) { openGate('rejected'); return; }
    settings.maintenanceError = err.message;
  } finally {
    settings.cleanupBusy = false;
    if (settings.section === 'maint') renderSettings();
  }
}

/** The last save's verdict. Called from every branch of `renderSettings`,
 *  because a backend that renders fewer fields still gets saved.
 *
 *  Appends into `container` — the same `.set-form` div every section
 *  renderer already received as its own `body` parameter — not into
 *  `settings.body`. `settings.body` holds `.set-form` as its only child,
 *  so appending there instead used to land the note as a SIBLING of the
 *  whole form: after every field, at the very bottom of the tab, and
 *  outside the form's 640px-constrained column. */
function renderSettingsMessages(container) {
  if (settings.note) {
    container.appendChild(el('p', { className: 'tok-ok', text: settings.note }));
  }
  if (settings.errorText) {
    container.appendChild(
      el('p', { className: 'modal-error', text: settings.errorText }));
  }
}

async function submitSettings() {
  const backend = backendPreset(settings.backendId);
  if (!backend) return;
  settings.errorText = null;
  settings.note = null;

  const payload = { provider: backend.provider };
  if (backend.provider === 'api') {
    const dim = parseInt(settings.form.dim, 10);
    if (!settings.form.url.trim()) {
      settings.errorText = t('settings.embed.errors.missingUrl');
      renderSettings();
      return;
    }
    if (!(dim > 0)) {
      // Refused here rather than sent: `dim` declares the vector column's
      // width, so a zero would not be a slow degradation but an index that
      // cannot hold what the endpoint returns.
      settings.errorText = t('settings.embed.errors.dimNotPositive');
      renderSettings();
      return;
    }
    payload.api = {
      url: settings.form.url.trim(),
      model: settings.form.model,
      dim: dim,
      timeout: parseFloat(settings.form.timeout) || 60,
    };
    // Sent only when typed. An untouched field means "leave what is stored";
    // sending its empty value would erase a working credential.
    if (settings.keyTouched) payload.api.key = settings.form.key;
  }

  settings.busy = true;
  settings.save.disabled = true;
  try {
    const data = await api('/api/settings', { method: 'PUT', body: payload });
    settings.data = data;
    settings.keyTouched = false;
    settings.form.key = '';
    // The provider cache is already dropped by the PUT. Re-read every view of
    // that fact now: the memory block must point at the saved endpoint, and the
    // main screen must expose the resulting stale indexes immediately.
    await refreshEmbedState();
    const refreshed = await Promise.allSettled([loadBanks(), loadStatus()]);
    const refreshFailed = refreshed.some((item) => item.status === 'rejected');
    const pending = pendingRebuilds();
    const pendingCount = pending.actionable.length + pending.running.length + pending.disabled.length;
    settings.note = data.restart_required
      ? t('settings.embed.saved.restartRequired')
      : t(pendingCount ? 'settings.embed.saved.appliedPending' : 'settings.embed.saved.appliedNoPending');
    if (refreshFailed) {
      settings.errorText = t('settings.embed.errors.refreshFailed');
    }
  } catch (err) {
    if (isAuthError(err)) { openGate('rejected'); return; }
    settings.errorText = err.message;
  } finally {
    settings.busy = false;
    settings.save.disabled = false;
    renderSettings();
  }
}

buildSettings();

// The last of the five scripts: only here can `boot()` trust every page's
// globals (`initShell`, `renderBanks`, `loadLogs`, `openSettings`, …) to
// exist.
boot();
