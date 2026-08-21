/* Налаштування (contract 9.5: GET/PUT /api/settings) — horizontal tabs under
 * the header instead of the old fixed `.screen` overlay with a vertical
 * `.set-nav`. The section bodies below are a close-to-mechanical port of the
 * current live cabinet's form-building code; only the frame around them
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
  autoUpdateWant: null,  // pending edit for the "auto_update" setting, same
                         // idiom as autostartWant — null when it matches
                         // what GET /api/settings last reported
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
  { id: 'general', label: 'Загальні', render: renderGeneralSection, submit: submitGeneral },
  { id: 'embed', label: 'Модель ембедингу', render: renderEmbedSection, submit: submitSettings },
  { id: 'maint', label: 'Обслуговування', render: renderMaintSection, submit: null },
];

const SECTION_LEDE = {
  general: 'Те, що стосується самого кабінету й машини в цілому — не окремого ' +
           'банку і не бекенда ембедингу.',
  embed: 'Який бекенд рахує вектори для пошуку в банках і скільки оперативної ' +
         'памʼяті він для цього займає.',
  maint: 'Той самий структурований doctor report, який CLI показує текстом. ' +
         'Перевірки запускаються лише при відкритті цього розділу.',
};

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
  return '<span class="page-title">Налаштування</span>' +
    '<span class="page-sub">стосується цієї машини, не окремого банку</span>';
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
  settings.autoUpdateWant = null;
  settings.updateCheckError = null;
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
      text: section.label,
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
    text: 'перекрито змінною ' + item.env_var + ' — збережене тут не подіє, ' +
          'доки вона виставлена',
  });
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

  if (settings.busy && !settings.data) {
    body.appendChild(el('p', { className: 'empty-hint', text: 'Завантаження…' }));
    return;
  }
  if (!settings.data) {
    body.appendChild(el('p', { className: 'modal-error', text: settings.errorText || '—' }));
    return;
  }

  // No section <h2> here: the active tab button right above already names
  // this section — repeating it as a heading said the same word twice.
  const form = el('div', { className: 'set-form' }, [
    el('p', { className: 'lede', text: SECTION_LEDE[section.id] || '' }),
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
    document.createTextNode('Зміна моделі або ширини — це новий ключ перебудови.'),
    el('br'),
    document.createTextNode('Конфігурація діє після збереження налаштувань.'),
    el('br'),
    document.createTextNode('Старі індекси отримають REBUILD PENDING і пошук по них ' +
      'відмовить, доки їх не перегенерувати.'),
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
      text: item.label,
      on: { click: () => chooseBackend(item.id) },
    }));
  }
  body.appendChild(setField('Бекенд ембедингу', tabs, backend ? backend.note : null));

  // Same idiom as autostart/auto-update on Загальні: browsing a tab that
  // isn't what's actually stored is a draft, not a change — say so before
  // «Зберегти» is pressed, or a machine on OpenAI reads as already switched
  // to Ollama the moment you click its tab to look at it.
  if (settings.backendId !== backendForSettings()) {
    const active = backendPreset(backendForSettings());
    body.appendChild(el('p', {
      className: 'set-override',
      text: 'не збережено — зараз активний «' + (active ? active.label : '—') +
            '»; натисніть «Зберегти», щоб перемкнути на «' +
            (backend ? backend.label : '—') + '»',
    }));
  }

  const providerOverride = overrideNote('provider');
  if (providerOverride) body.appendChild(providerOverride);

  if (!backend) { renderSettingsMessages(); return; }

  // -- local needs nothing ------------------------------------------------
  if (backend.provider === 'local') {
    const model = (backend.models || [])[0];
    body.appendChild(el('p', { className: 'set-lead' }, [
      document.createTextNode('Вектори рахує резидент на цій машині — '),
      el('code', { text: model ? model.label : '—' }),
      document.createTextNode(model ? ' (' + model.dim + ' вимірів). ' : '. '),
      // "Нічого налаштовувати не треба" dropped: `backend.note` right above
      // ("нічого не треба налаштовувати; працює без мережі") already says
      // it — this line only adds what that one doesn't, that memory stays
      // on the machine.
      document.createTextNode('Жоден байт памʼяті не залишає машину.'),
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
    renderSettingsMessages();
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
      text: settings.form.model + ' (не з довідника)',
      attrs: { value: settings.form.model },
    });
    option.selected = true;
    select.appendChild(option);
  }
  select.addEventListener('change', (ev) => chooseModel(ev.target.value));

  const chosen = modelPreset(backend, settings.form.model);
  body.appendChild(setField('Модель', select, chosen ? chosen.note : null));
  if (chosen && chosen.prefixed) {
    // Said out loud because it is the one property of a model that is
    // invisible in every other way: markers change every vector, and getting
    // them wrong shows up only as quietly worse search.
    body.appendChild(el('p', {
      className: 'set-note',
      text: 'ця модель тренована з маркерами — mnemo підставить їх сама',
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
  body.appendChild(setField('Адреса', url, null));
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
    setField('Вимірів', dim, null),
    setField('Таймаут, с', timeout, null),
  ]));
  body.appendChild(el('p', {
    className: 'set-note',
    text: 'Ширина підставлена з довідника, але останнє слово за самим ' +
          'ендпоінтом: mnemo звіряє її з першим отриманим вектором і ' +
          'відмовиться писати індекс, якщо вони розійшлися.',
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
        placeholder: stored ? 'збережений — введіть новий, щоб замінити' : 'sk-…',
      },
    });
    key.value = settings.form.key;
    key.addEventListener('input', (ev) => {
      settings.form.key = ev.target.value;
      settings.keyTouched = true;
    });
    body.appendChild(setField('Ключ API', key,
      'Зберігається у settings.json на цій машині. Назад не показується — ' +
      'сторінка, яка друкує секрет, друкує його і в скриншот.'));
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

  renderSettingsMessages();
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
const HOLD_LABEL = {
  loaded: 'у памʼяті',
  unloaded: 'не в памʼяті',
  'n/a': 'нічого не тримає',
  unknown: 'невідомо',
};

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
const MEM_LABEL = 'Оперативна памʼять';

function renderEmbedMemory(body) {
  const info = settings.embed;

  // Only when there is truly nothing else to show — the initial `GET
  // /api/embed/state` never landed. A failed `load`/`unload` click below
  // leaves `info` as the last known-good state and is shown as a note next
  // to the card instead, not by replacing the whole field with an error.
  if (!info) {
    body.appendChild(el('div', { className: 'set-field' }, [
      el('span', { className: 'set-label', text: MEM_LABEL }),
      el('p', {
        className: settings.embedError ? 'modal-error' : 'empty-hint',
        text: settings.embedError || 'Стан ще не отримано.',
      }),
    ]));
    return;
  }

  const held = info.holding;
  const field = [el('span', { className: 'set-label', text: MEM_LABEL })];

  // Description right under the heading, before the status card — this is
  // the one thing worth reading before touching any control below it, so it
  // does not wait behind the card the way an ordinary field's note would.
  if (held === 'loaded' || held === 'unloaded') {
    field.push(el('p', { className: 'set-note mem-intro' }, [
      el('strong', { text: 'Модель піднімається сама, коли потрібна для пошуку чи індексації' }),
      document.createTextNode(' — «не в памʼяті» це нормальний стан, не помилка. ' +
        '«Вивантажити» звільняє памʼять одразу, замість тримати модель ' +
        'постійно завантаженою про запас.'),
    ]));
  }

  // -- status: caption + badge, caption + model — no framing box any more,
  // just two lines sitting directly in the field, with the actions below --
  field.push(el('div', { className: 'set-mem-line' }, [
    el('span', { className: 'set-mem-caption', text: 'Статус:' }),
    el('span', {
      // `badge-empty`, never the red `badge-off`: an unloaded model is a
      // normal state that costs one wake-up, not a fault — the same reason
      // `frozen` got its own cold colour instead of the error one.
      className: 'badge ' + (held === 'loaded' ? 'badge-ready' : 'badge-empty'),
      text: HOLD_LABEL[held] || String(held),
    }),
  ]));
  field.push(el('div', { className: 'set-mem-line' }, [
    el('span', { className: 'set-mem-caption', text: 'Модель:' }),
    el('span', { className: 'set-mem-what', text: info.model || '—' }),
  ]));
  // Only where the backend actually has an idle-exit concept (`local`'s own
  // `MNEMO_EMBED_IDLE_TIMEOUT` — Ollama's own TTL is a live `expires_at`
  // timestamp, already shown as a footnote below, not a static duration).
  if (info.idle_timeout_s != null) {
    field.push(el('div', { className: 'set-mem-line' }, [
      el('span', { className: 'set-mem-caption', text: 'Автовивантаження:' }),
      el('span', {
        className: 'set-mem-what',
        text: info.idle_timeout_s > 0 ? humanUptime(info.idle_timeout_s) : 'вимкнено',
      }),
    ]));
  }

  const buttons = el('div', { className: 'set-mem-actions' });
  if (held === 'loaded') {
    buttons.appendChild(el('button', {
      className: 'btn',
      text: 'Вивантажити',
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
        ? 'Підняти в памʼять'
        : (held === 'n/a' || held === 'unknown')
          ? 'Перевірити ендпоінт'
          : 'Перевірити',
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
      el('span', { text: 'Завантаження моделі…' }),
    ]));
  } else if (download) {
    field.push(el('div', { className: 'set-mem-actions' }, [
      el('button', {
        className: 'btn',
        text: 'Завантажити модель на диск (2.2 ГБ)',
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
    notes.push('Підніметься назад за кілька секунд.');
  }
  if (held === 'n/a') {
    // The cabinet's own wording, not the backend's `detail`. A steady state
    // that every client renders the same way belongs to the interface — the
    // API stays English by convention, and echoing it here would put one
    // English line in the middle of a Ukrainian screen.
    notes.push('Цей ендпоінт не тримає нічого на цій машині — модель живе ' +
               'на боці постачальника, тож звільняти нічого.');
    notes.push('«Перевірити ендпоінт» зробить один embedding request. Для ' +
               'тарифікованого API це може бути платний виклик.');
  }
  if (info.expires_at) notes.push('Бекенд тримає її до ' + info.expires_at + '.');
  if (info.others_held) {
    // A count, never the names: the other models are somebody else's, and
    // this cabinet unloads ours alone.
    notes.push('Там же ще ' + info.others_held + ' модел(і/ей) — не наші, ' +
               'їх не чіпаємо.');
  }
  if (download && !download.active && download.failed) {
    notes.push('Завантаження не вдалося — спробуйте ще раз.');
  }
  if (info.detail) notes.push(info.detail);
  for (const text of notes) field.push(el('p', { className: 'set-note', text: text }));

  body.appendChild(el('div', { className: 'set-field' }, field));
}

async function embedAction(what) {
  settings.embedBusy = true;
  settings.embedError = null;
  settings.embedNote = null;
  renderSettings();
  try {
    settings.embed = await api('/api/embed/' + what, { method: 'POST' });
    if (what === 'unload') {
      settings.embedNote = 'Памʼять звільнено. Модель повернеться сама при наступному пошуку.';
    } else if (settings.embed.holding === 'n/a') {
      settings.embedNote = 'Ендпоінт відповів' + (settings.embed.probe_dim
        ? ' — пробний вектор має ' + settings.embed.probe_dim + ' вимірів.'
        : '.');
    } else {
      settings.embedNote = 'Бекенд відповів — модель у памʼяті.';
    }
  } catch (err) {
    if (isAuthError(err)) { openGate('rejected'); return; }
    // The cabinet's own wording for the one error this button realistically
    // hits — same reasoning as the `n/a` notes above: the backend's `detail`
    // stays English by convention, and this is the one code worth naming
    // instead of echoing that English sentence onto a Ukrainian screen.
    settings.embedError = err.code === 'embed_busy'
      ? 'Черга ще працює через цей бекенд — почекайте, доки вона звільниться, і спробуйте ще раз.'
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
  if (h) return h + ' год ' + m + ' хв';
  if (m) return m + ' хв ' + String(s).padStart(2, '0') + ' с';
  return s + ' с';
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
 * What applies to this machine's cabinet regardless of which bank is open:
 * the browser's own preference first (theme — applies on click, nothing for
 * Save to do with it), then what changes the machine (autostart — needs
 * Save), then what merely reports (process facts, sitting under both since
 * nobody opens this section to read them first).
 */
function renderGeneralSection(body) {
  renderTheme(body);
  renderAutostart(body);
  renderAutoUpdate(body);

  const svc = state.service;
  if (!svc) {
    body.appendChild(el('p', { className: 'empty-hint', text: 'Стан служби ще не отримано.' }));
    renderSettingsMessages();
    return;
  }

  // A manual divider, not `.set-field + .set-field`: the auto-update field
  // above ends with a check button and its result badge, neither of them a
  // `.set-field`, so the adjacent-sibling rule that separates every other
  // field here never fires before this one.
  body.appendChild(el('div', { className: 'set-divider' }));

  const box = el('div', { className: 'set-stats' }, [
    setStat('Версія', svc.version, true),
    setStat('PID', svc.pid, true),
    setStat('Адреса', (svc.host || '—') + ':' + (svc.port != null ? svc.port : '—'), true),
    setStat('Провайдер', svc.provider, true),
    setStat('Працює', humanUptime(svc.uptime_s)),
    setStat('Черга пріоритетів', svc.priority_enabled ? 'увімкнена' : 'вимкнена'),
  ]);
  body.appendChild(setField('Стан', box, null));

  renderSettingsMessages();
}

/**
 * `.segmented set-toggle`, the same compact left-aligned control autostart
 * uses below it — not the bare `.segmented`, which stretches to the field's
 * full width and reads as a table row rather than a two-state switch.
 */
function renderTheme(body) {
  const seg = el('div', { className: 'segmented set-toggle', attrs: { id: 'theme' } });
  const current = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
  for (const [value, label] of [['dark', 'Темна'], ['light', 'Світла']]) {
    seg.appendChild(el('button', {
      className: 'seg' + (current === value ? ' is-active' : ''),
      text: label,
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
  body.appendChild(setField('Тема кабінету', seg,
    'Вибір цього браузера — тому застосовується одразу й не чекає «Зберегти».'));
}

/**
 * Start at logon — a `.segmented` pair, the control this cabinet already uses
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
    body.appendChild(setField('Автозапуск', el('p', {
      className: 'set-note',
      text: settings.autostartError || 'стан не отримано',
    }), null));
    return;
  }
  if (!auto.supported) {
    body.appendChild(setField('Автозапуск', el('p', {
      className: 'set-note',
      text: 'на цій системі не підтримується',
    }), null));
    return;
  }

  const chosen = autostartWanted();
  const seg = el('div', { className: 'segmented set-toggle' });
  for (const option of [{ on: true, label: 'Увімкнено' }, { on: false, label: 'Вимкнено' }]) {
    seg.appendChild(el('button', {
      className: 'seg' + (chosen === option.on ? ' is-active' : ''),
      text: option.label,
      attrs: settings.busy ? { disabled: '' } : {},
      on: { click: () => chooseAutostart(option.on) },
    }));
  }

  body.appendChild(setField('Запускати службу при вході в систему', seg,
    'Реєструється як ' + (auto.mechanism || '—') +
    (auto.name ? ' — «' + auto.name + '»' : '') +
    '. Діє з наступного входу; те, що працює зараз, не зачіпає.'));

  // Said plainly, because the control now shows an intention rather than the
  // machine: without this line a page left open on «Вимкнено» reads as a
  // machine with autostart off.
  if (chosen !== !!auto.enabled) {
    body.appendChild(el('p', {
      className: 'set-override',
      text: 'не збережено — зараз ' + (auto.enabled ? 'увімкнено' : 'вимкнено') +
            '; натисніть «Зберегти», щоб застосувати',
    }));
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
  const chosen = autoUpdateWanted();
  const seg = el('div', { className: 'segmented set-toggle' });
  for (const option of [{ on: true, label: 'Увімкнено' }, { on: false, label: 'Вимкнено' }]) {
    seg.appendChild(el('button', {
      className: 'seg' + (chosen === option.on ? ' is-active' : ''),
      text: option.label,
      attrs: settings.busy ? { disabled: '' } : {},
      on: { click: () => chooseAutoUpdate(option.on) },
    }));
  }

  body.appendChild(setField('Автоматичне оновлення', seg,
    'Придатний реліз застосовується сам — з коротким відліком і кнопкою ' +
    '«Скасувати» просто в кабінеті. Вимкнено — лишається тільки банер і ' +
    'ручне підтвердження, як і раніше.'));

  const autoUpdateOverride = overrideNote('auto_update');
  if (autoUpdateOverride) body.appendChild(autoUpdateOverride);

  if (chosen !== autoUpdateStoredValue()) {
    body.appendChild(el('p', {
      className: 'set-override',
      text: 'не збережено — зараз ' + (autoUpdateStoredValue() ? 'увімкнено' : 'вимкнено') +
            '; натисніть «Зберегти», щоб застосувати',
    }));
  }

  body.appendChild(el('div', { className: 'maint-head' }, [
    el('button', {
      className: 'btn',
      text: settings.updateCheckBusy ? 'Перевіряємо…' : 'Перевірити оновлення',
      attrs: settings.updateCheckBusy ? { disabled: '' } : {},
      on: { click: () => runUpdateCheck() },
    }),
  ]));

  if (settings.updateCheckError) {
    body.appendChild(el('p', { className: 'modal-error', text: settings.updateCheckError }));
  } else if (settings.updateCheckResult) {
    // A real result, not page-description text -- badge it the same way the
    // rest of the cabinet marks a live outcome: warm for "there's something
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
          ? 'Доступна нова версія ' + result.latest_tag
          : 'Актуальна версія.');
    if (result.error) settings.updateCheckError = result.error;
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
 */
async function submitGeneral() {
  const autostartWant = settings.autostartWant;
  const autoUpdateWant = settings.autoUpdateWant;
  if (autostartWant == null && autoUpdateWant == null) {
    settings.note = 'Нічого не змінено.';
    renderSettings();
    return;
  }

  settings.busy = true;
  settings.save.disabled = true;
  settings.errorText = null;
  settings.note = null;
  renderSettings();

  const notes = [];
  try {
    if (autostartWant != null) {
      try {
        settings.autostart = await api('/api/autostart', {
          method: 'POST', body: { enabled: autostartWant },
        });
        settings.autostartWant = null;
        notes.push(settings.autostart.enabled
          ? 'Автозапуск: служба підніматиметься при вході в систему.'
          : 'Автозапуск: вимкнено, службу доведеться піднімати самому.');
      } catch (err) {
        if (isAuthError(err)) { openGate('rejected'); return; }
        settings.errorText = err.message;
      }
    }

    if (autoUpdateWant != null) {
      try {
        settings.data = await api('/api/settings', {
          method: 'PUT', body: { auto_update: autoUpdateWant },
        });
        settings.autoUpdateWant = null;
        notes.push(autoUpdateWant ? 'Автооновлення: увімкнено.' : 'Автооновлення: вимкнено.');
      } catch (err) {
        if (isAuthError(err)) { openGate('rejected'); return; }
        settings.errorText = settings.errorText ? settings.errorText + ' ' + err.message : err.message;
      }
    }

    if (notes.length) settings.note = notes.join(' ');
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
      text: settings.maintenanceBusy ? 'Оновлюємо…' : 'Оновити діагностику',
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
      text: settings.maintenanceBusy ? 'Збираємо діагностику…' : 'Звіт ще не отримано.',
    }));
    return;
  }

  const engine = report.engine || {};
  body.appendChild(setField('Рушій', el('div', { className: 'set-stats' }, [
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
    setStat('Провайдер', providerValue, true),
    setStat('Локальна модель', model.needed
      ? (model.cached ? 'кеш повний' : 'НЕ ЗАВАНТАЖЕНА')
      : (model.cached ? 'є, але не потрібна' : 'не потрібна')),
    setStat('sqlite-vec', vec.ok ? 'ok' : 'НЕДОСТУПНИЙ'),
    setStat('Резидент', resident.applicable
      ? ((resident.up ? 'працює' : 'не завантажений') +
         ' · ' + resident.host + ':' + resident.port + ' · машинний порт')
      : 'n/a для цього провайдера'),
  ];
  if (endpoint.applicable) {
    providerStats.push(setStat('API endpoint', endpoint.configured
      ? endpoint.url + ' · ' + endpoint.model + ' · ' + endpoint.dim + ' вимірів' +
        (endpoint.key_set ? ' · key set' : ' · no key')
      : 'НЕ НАЛАШТОВАНО — ' + (endpoint.error || 'невідомо'), true));
  }
  body.appendChild(setField('Ембединг', el('div', { className: 'set-stats' }, providerStats), null));
  if (vec.error) body.appendChild(el('p', { className: 'modal-error', text: vec.error }));

  const backend = report.backend || {};
  const tokenFact = report.token || {};
  body.appendChild(setField('Служба', el('div', { className: 'set-stats' }, [
    setStat('Backend', backend.up
      ? 'працює · pid ' + backend.serving_pid + ' · машинний порт'
      : 'НЕ ДОСТУПНИЙ — ' + (backend.error || 'невідомо')),
    setStat('URL', backend.url, true),
    setStat('Черга', backend.queue_depth),
    setStat('API token', (tokenFact.present ? 'present' : 'MISSING') +
      ' · ' + (tokenFact.where || tokenFact.source || 'unknown') +
      ' · ' + (tokenFact.scope || 'unknown scope'), true),
  ]), null));

  const registry = report.registry || {};
  if (!registry.ok) {
    body.appendChild(setField('Реєстр', el('p', {
      className: 'modal-error',
      text: 'НЕЧИТАНИЙ — ' + (registry.error || 'невідомо'),
    }), null));
  } else {
    const registryBox = el('div', { className: 'maint-list' });
    registryBox.appendChild(maintItem(
      registry.count + ' банк(ів)', null, 'Реєстр читається.', 'ok'));
    for (const bank of registry.banks || []) {
      const flags = [];
      if (bank.state !== 'enabled') flags.push(bank.state);
      if (!bank.exists) flags.push('нема кореня');
      registryBox.appendChild(maintItem(
        bank.name,
        flags.length ? flags.join(' · ') : 'ok',
        bank.root,
        flags.length ? 'warn' : null));
    }
    body.appendChild(setField('Реєстр', registryBox, null));
  }

  const wiring = report.wiring || {};
  const wiringBox = el('div', { className: 'maint-list' });
  if (!wiring.ok) {
    wiringBox.appendChild(maintItem('Невідомо', null, wiring.error || 'помилка', 'error'));
  } else if (!(wiring.stale || []).length) {
    wiringBox.appendChild(maintItem(
      (wiring.total || 0) + ' проєкт(ів)', 'усі актуальні', null, 'ok'));
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
      'Список недоступний', null,
      'Видалення заборонене: ' + (orphans.error || 'реєстр не можна перевірити'),
      'error'));
  } else if (!orphans.count) {
    box.appendChild(maintItem('Сиріт немає', '0 B', 'Кожен index належить банку.', 'ok'));
  } else {
    for (const orphan of orphans.items || []) {
      let note = orphan.error
        ? 'не читається — ' + orphan.error
        : orphan.root || (orphan.schema == null
          ? 'pre-v3 index — root не записаний'
          : 'root не записаний');
      if (orphan.root_exists) note += ' · root досі є на диску';
      box.appendChild(maintItem(
        orphan.id,
        fmtBytes(orphan.size),
        (orphan.files == null ? '? файлів' : orphan.files + ' файлів') + ' · ' + note,
        'warn'));
    }
  }
  body.appendChild(setField(
    'Індекси-сироти' + (orphans.ok && orphans.count
      ? ' · ' + orphans.count + ' · ' + fmtBytes(orphans.bytes)
      : ''),
    box,
    'Doctor лише показує. Прибирає тільки окрема підтверджена дія — ніколи ' +
    'автоматично й ніколи разом із діагностикою.'));

  if (settings.cleanupNote) {
    body.appendChild(el('p', { className: 'tok-ok', text: settings.cleanupNote }));
  }
  if (!orphans.ok || !orphans.count) return;

  if (!settings.cleanupConfirming) {
    body.appendChild(el('div', { className: 'maint-actions' }, [
      el('button', {
        className: 'btn',
        text: 'Прибрати сироти',
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
      text: 'Буде видалено тільки ці показані derived index id: ' + ids.join(', ') +
            '. Перед кожним видаленням реєстр перевіряється знову; .md не чіпаються.',
    }),
    el('div', { className: 'tok-confirm-row' }, [
      el('button', {
        className: 'btn', text: 'Скасувати',
        attrs: settings.cleanupBusy ? { disabled: '' } : {},
        on: { click: () => {
          settings.cleanupConfirming = false;
          renderSettings();
        } },
      }),
      el('button', {
        className: 'btn btn-danger',
        text: settings.cleanupBusy ? 'Прибираємо…' : 'Видалити ' + ids.length,
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
      'видалено ' + result.removed.length + ' з ' + result.requested.length,
      'звільнено ' + fmtBytes(result.freed_bytes),
    ];
    if (result.skipped.length) parts.push('пропущено ' + result.skipped.length);
    if (result.locked.length) parts.push('locked ' + result.locked.length);
    settings.cleanupNote = parts.join(' · ');
    settings.cleanupConfirming = false;
    settings.maintenance = await api('/api/doctor');
    if (result.locked.length) {
      settings.maintenanceError = 'Не всі файли видалено: ' + result.locked.map((item) =>
        item.id + ' (' + item.paths.join(', ') + ')').join(' · ');
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
 *  because a backend that renders fewer fields still gets saved. */
function renderSettingsMessages() {
  if (settings.note) {
    settings.body.appendChild(el('p', { className: 'tok-ok', text: settings.note }));
  }
  if (settings.errorText) {
    settings.body.appendChild(
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
      settings.errorText = 'Вкажіть адресу ендпоінта.';
      renderSettings();
      return;
    }
    if (!(dim > 0)) {
      // Refused here rather than sent: `dim` declares the vector column's
      // width, so a zero would not be a slow degradation but an index that
      // cannot hold what the endpoint returns.
      settings.errorText = 'Вимірів має бути додатним числом.';
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
      ? 'Збережено. Набере чинності після перезапуску служби.'
      : 'Збережено й застосовано. Перевірте бекенд кнопкою вище' +
        (pendingCount
          ? ', потім перегенеруйте банки з REBUILD PENDING на головному екрані.'
          : '.');
    if (refreshFailed) {
      settings.errorText = 'Налаштування збережено, але не всі стани вдалося ' +
        'перечитати. Оновіть сторінку — повторно зберігати не потрібно.';
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
