/* mnemo web console (FR-7, v1) — shared chrome.
 *
 * Thin client over the v3 HTTP API (contract 9.5) and the WebSocket progress
 * channel (contract 9.7). It renders what the backend reports and nothing
 * else: no chunking, no indexing, no editing. Every derived number on screen
 * comes from a response field.
 *
 * This file holds what every page depends on: the token, theme, shared
 * state, the tiny DOM/HTTP helpers, the token gate, formatting helpers, bank
 * data fetchers, task-progress bookkeeping, reindex/state actions, and the
 * bank-scoped dialogs (folder picker, token panel, per-bank menu, removal).
 * Routing, the sidebar/header and the WebSocket channel live in shell.js;
 * each page's own rendering lives in page-memory.js / page-journal.js /
 * page-settings.js, loaded after this file and after shell.js.
 */
'use strict';

// ---------------------------------------------------------------------------
// token (contract 9.1)
// ---------------------------------------------------------------------------
//
// `/api` is open by default (no configured token, loopback-only — see
// api.py's auth_middleware), so an empty `token` here is the normal case,
// not an error state: `boot()` proceeds straight to `start()` regardless.
// This machinery — capturing `?token=`, attaching it as `Authorization`,
// the gate on a genuine 401 — stays wired for the day a token IS configured
// ($MNEMO_API_TOKEN, or a future opt-in "generate" step), which is the only
// case it still does anything.

/** Pull `?token=` out of the URL once, keep it in sessionStorage, scrub the bar. */
function resolveToken() {
  const url = new URL(window.location.href);
  const fromUrl = url.searchParams.get('token');
  if (fromUrl) {
    sessionStorage.setItem('mnemo_token', fromUrl);
    url.searchParams.delete('token');
    history.replaceState(null, '', url.pathname + url.search + url.hash);
    return fromUrl;
  }
  return sessionStorage.getItem('mnemo_token') || '';
}

let token = resolveToken();

// ---------------------------------------------------------------------------
// theme
// ---------------------------------------------------------------------------

/** What the inline bootstrap script in <head> already decided, read back for
 *  our own bookkeeping (dark is the default — no stored 'light' means dark). */
function resolveTheme() {
  return localStorage.getItem('mnemo_theme') === 'light' ? 'light' : 'dark';
}

/**
 * Sets the attribute the CSS keys off and persists the choice.
 *
 * There is no permanent theme control in the shell any more — it moved into
 * Settings → General (design decision, `.claude/memory/topics/console-ui.md`)
 * as the one control on that screen that applies on click rather than
 * waiting for a «Save» button. Whatever renders that control is responsible
 * for reflecting the active choice; this function only ever sets it.
 */
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem('mnemo_theme', theme);
}

// ---------------------------------------------------------------------------
// language (i18n, MN-10)
// ---------------------------------------------------------------------------
//
// `i18n/en.js` and `i18n/uk.js` (loaded before this file, index.html) each
// assign a flat `key -> string` dictionary onto `window.MNEMO_I18N.<lang>`;
// a plural entry's value is `{one, other}` (English) or `{one, few, many}`
// (Ukrainian) instead of a string. The active language is read fresh on
// every call — no cached state to go stale — same pattern as `resolveTheme()`.
// Like the theme, the choice lives in `localStorage` and is never sent to the
// backend: it is a browser/person preference, not a machine one (unlike
// autostart/auto_update/require_login in src/settings.py).

const DEFAULT_LANG = 'en';

function resolveLang() {
  return localStorage.getItem('mnemo_lang') === 'uk' ? 'uk' : DEFAULT_LANG;
}

function i18nDict(lang) {
  return (window.MNEMO_I18N && window.MNEMO_I18N[lang]) || {};
}

function interpolate(str, vars) {
  if (!vars) return str;
  return str.replace(/\{(\w+)\}/g, (m, name) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : m);
}

/**
 * Look up `key` in the active language, falling back to English, then to the
 * raw key — so a missing translation degrades to a visible placeholder
 * rather than `undefined` or a thrown error.
 */
function t(key, vars) {
  const lang = resolveLang();
  let value = i18nDict(lang)[key];
  if (value === undefined) value = i18nDict(DEFAULT_LANG)[key];
  if (value === undefined) {
    console.warn('mnemo: missing i18n key', key);
    return key;
  }
  return interpolate(value, vars);
}

// English is a 2-way split (one/other); Ukrainian is the standard Slavic triad.
const PLURAL_RULES = {
  en: (n) => (n === 1 ? 'one' : 'other'),
  uk: (n) => {
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return 'one';
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'few';
    return 'many';
  },
};

/**
 * Like `t()`, but `key`'s dictionary value is a form object rather than a
 * string; `{n}` plus any extra `vars` are interpolated into the form the
 * active language's plural rule selects for `n`.
 */
function plural(key, n, vars) {
  const lang = resolveLang();
  let forms = i18nDict(lang)[key];
  if (forms === undefined) forms = i18nDict(DEFAULT_LANG)[key];
  if (forms === undefined) {
    console.warn('mnemo: missing i18n key', key);
    return key;
  }
  const rule = PLURAL_RULES[lang] || PLURAL_RULES.en;
  const form = forms[rule(n)] || forms.other || forms.many || '';
  return interpolate(form, Object.assign({ n: n }, vars));
}

/**
 * Re-apply every static-markup translation from the active dictionary.
 *
 * Covers `index.html`'s baked English markup (`data-i18n` for textContent,
 * `data-i18n-title` for the `title` attribute, `data-i18n-aria` for
 * `aria-label`, `data-i18n-placeholder` for `placeholder`) and every
 * build-once dialog node in this file that carries the same attributes —
 * `querySelectorAll` runs over the whole document regardless of a node's
 * `hidden` state, so a closed modal's chrome stays correctly translated for
 * the moment it opens next, with no per-dialog refresh code required.
 */
function applyStaticI18n() {
  for (const node of document.querySelectorAll('[data-i18n]')) {
    node.textContent = t(node.dataset.i18n);
  }
  for (const node of document.querySelectorAll('[data-i18n-title]')) {
    node.title = t(node.dataset.i18nTitle);
  }
  for (const node of document.querySelectorAll('[data-i18n-aria]')) {
    node.setAttribute('aria-label', t(node.dataset.i18nAria));
  }
  for (const node of document.querySelectorAll('[data-i18n-placeholder]')) {
    node.placeholder = t(node.dataset.i18nPlaceholder);
  }
}

/**
 * Switch the active language and repaint everything that depends on it.
 *
 * `refreshAllViews()` is the language equivalent of a full re-render: pages
 * not yet migrated to `t()` (MN-10 is landing in five steps) simply repaint
 * their existing hardcoded text, which is harmless now and is what makes
 * them start responding to the toggle the moment each one's own step lands.
 */
function applyLanguage(lang) {
  document.documentElement.lang = lang;
  localStorage.setItem('mnemo_lang', lang);
  refreshAllViews();
}

function refreshAllViews() {
  applyStaticI18n();
  renderHeader();
  updateToggleLabel();
  renderService();
  refreshConnState();
  if (state.gated) {
    regate();
  } else {
    renderBanks();
    renderTree();
    renderFile();
    renderJournal();
  }
  // Transient popovers: closing on a language switch matches the existing
  // "scroll/resize closes the menu" posture rather than repainting one that
  // is about to be dismissed anyway.
  closeBankMenu();
  if (picker.root && !picker.root.hidden) renderPicker();
  if (bankToken.root && !bankToken.root.hidden) renderTokenPanel();
  if (removal.root && !removal.root.hidden) renderRemoval();
  if (rebuildDialog.root && !rebuildDialog.root.hidden) renderRebuildDialog();
  // update.js: static modal chrome first, then either the open modal's
  // current phase or (nothing open) just the sidebar banner text.
  applyUpdateStaticI18n();
  if (updateModal.root && !updateModal.root.hidden) renderUpdateModal();
  else renderSidebarUpdateBanner();
}

// ---------------------------------------------------------------------------
// state — shared by every page
// ---------------------------------------------------------------------------

const state = {
  page: 'memory',
  banks: [],
  selectedBankId: null,
  tree: null,
  expanded: new Set(),          // expanded dir paths of the current tree
  file: null,                   // last /api/file response
  filePath: null,
  chunkViz: true,
  service: null,
  update: null,                 // last GET /api/update/status response (update.js)
  queue: null,
  progress: new Map(),          // bank_id -> live index_progress snapshot
  notes: new Map(),             // bank_id -> transient one-line note
  pendingFiles: new Map(),      // bank_id -> Set<relpath> queued/in-flight (MN-15)
  logKind: 'query',
  logBank: '',                  // '' = every bank
  logPeriod: '24h',             // '1h' | '24h' | '7d' | '30d'
  logRows: [],
  logTotal: 0,
  logSelected: { query: null, index: null },   // selected event id per kind
  gated: false,                 // token gate is up: no requests, no socket
};

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// tiny DOM helper — everything goes through textContent, never innerHTML
// ---------------------------------------------------------------------------

function el(tag, opts, children) {
  const node = document.createElement(tag);
  if (opts) {
    if (opts.className) node.className = opts.className;
    if (opts.text != null) node.textContent = String(opts.text);
    if (opts.title) node.title = opts.title;
    if (opts.attrs) for (const [k, v] of Object.entries(opts.attrs)) node.setAttribute(k, v);
    if (opts.on) for (const [k, v] of Object.entries(opts.on)) node.addEventListener(k, v);
  }
  for (const child of children || []) {
    if (child) node.appendChild(child);
  }
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

// ---------------------------------------------------------------------------
// draggable column dividers — shared by Memory (two handles) and Journal
// (one); only the mouse tracking is common, each page owns its own clamping
// and what the delta actually resizes (an indexed width vs. a single one).
// ---------------------------------------------------------------------------

/**
 * Wires one `.pane-resizer` handle. `onStart()` fires once on mousedown, for
 * capturing whatever the drag is about to move from; `onDrag(deltaX)` fires
 * on every mousemove with the offset from where the drag started, and the
 * caller clamps and applies it to whatever it is resizing; `onCommit()`
 * fires once on mouseup, for persisting the final value.
 *
 * Listens on `document`, not the 6px handle itself, so a fast mouse
 * movement that slips off the narrow track mid-drag does not drop the
 * resize. `body.is-resizing-pane` keeps the handle lit and the cursor a
 * resize arrow for the whole gesture (styles/base.css).
 */
function wireColumnResizer(handleEl, { onStart, onDrag, onCommit }) {
  handleEl.addEventListener('mousedown', (ev) => {
    ev.preventDefault();
    const startX = ev.clientX;
    if (onStart) onStart();
    document.body.classList.add('is-resizing-pane');
    const onMove = (moveEv) => onDrag(moveEv.clientX - startX);
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.classList.remove('is-resizing-pane');
      onCommit();
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });
}

// ---------------------------------------------------------------------------
// HTTP
// ---------------------------------------------------------------------------

class ApiError extends Error {
  constructor(code, message, detail, httpStatus) {
    super(message || code);
    this.code = code;
    this.detail = detail;
    this.httpStatus = httpStatus;
  }
}

async function api(path, options) {
  const opts = options || {};
  const headers = { 'Accept': 'application/json' };
  if (token) headers['Authorization'] = 'Bearer ' + token;
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';

  let response;
  try {
    response = await fetch(path, {
      method: opts.method || 'GET',
      headers: headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    });
  } catch (err) {
    throw new ApiError('unreachable', t('common.error.unreachable', { message: err.message }),
                       null, 0);
  }

  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (err) {
      throw new ApiError('internal', t('common.error.invalidJson'), text.slice(0, 200),
                         response.status);
    }
  }

  if (!response.ok) {
    // Contract 9.2: a single error envelope for every endpoint.
    const box = (payload && payload.error) || {};
    throw new ApiError(box.code || 'internal', box.message || response.statusText,
                       box.detail || null, response.status);
  }
  return payload;
}

function showBanner(message) {
  const box = $('banner');
  box.textContent = message;
  box.hidden = false;
}

function hideBanner() {
  $('banner').hidden = true;
}

function reportError(err) {
  // An auth failure is not a banner-worthy backend error: the service is fine,
  // we simply cannot talk to it. Route it to the gate instead, quietly.
  if (isAuthError(err)) {
    openGate('rejected');
    return;
  }
  const where = err.httpStatus ? ' (HTTP ' + err.httpStatus + ')' : '';
  showBanner('[' + err.code + ']' + where + ' ' + err.message);
  console.error(err);
}

// ---------------------------------------------------------------------------
// token gate
// ---------------------------------------------------------------------------

/**
 * Gate wording, per state.
 *
 * The no-token state says nothing about the service. It cannot: it issues no
 * request at all, so it has observed nothing to report — a page that claimed
 * "service is up" from behind zero requests would say exactly the same thing
 * with the backend down. It asks for a token and shows where to get one.
 *
 * The rejected state may name the service's behaviour, because there it did
 * make a request and did get a 401 back.
 */
function gateCopy(variant) {
  if (variant === 'rejected') {
    return {
      title: t('common.gate.rejected.title'),
      text: t('common.gate.rejected.text'),
      lead: t('common.gate.rejected.lead'),
      note: t('common.gate.rejected.note'),
    };
  }
  return {
    title: t('common.gate.missing.title'),
    text: t('common.gate.missing.text'),
    lead: null,
    note: null,
  };
}

function isAuthError(err) {
  return err instanceof ApiError && (err.httpStatus === 401 || err.code === 'unauthorized');
}

/**
 * The gate is built here rather than in index.html on purpose.
 *
 * `/ui/` is served from disk with an ETag and no `Cache-Control`, so a browser
 * may hand back a cached document while fetching a fresh `app.js`. Markup this
 * script depends on would then be missing and the whole page would die on load.
 * Building it means the script depends on nothing but `<body>`.
 */
const gate = {};

function buildGate() {
  gate.title = el('h1', { className: 'gate-title' });
  gate.text = el('p', { className: 'gate-text' });
  gate.note = el('p', { className: 'gate-note', attrs: { hidden: '' } });
  gate.input = el('input', {
    className: 'gate-input',
    attrs: {
      id: 'gate-token', name: 'gate-token',
      type: 'text', autocomplete: 'off', spellcheck: 'false',
      placeholder: t('common.gate.tokenPlaceholder'),
      'data-i18n-placeholder': 'common.gate.tokenPlaceholder',
    },
  });

  const form = el('form', { className: 'gate-form', on: { submit: submitGate } }, [
    el('label', {
      className: 'gate-label',
      text: t('common.gate.manualLabel'),
      attrs: { for: 'gate-token', 'data-i18n': 'common.gate.manualLabel' },
    }),
    el('div', { className: 'gate-row' }, [
      gate.input,
      el('button', {
        className: 'btn', text: t('common.gate.submit'),
        attrs: { type: 'submit', 'data-i18n': 'common.gate.submit' },
      }),
    ]),
    gate.note,
  ]);

  // Only the rejected state needs a separate line introducing the command;
  // the no-token copy is one sentence that already ends in that colon.
  gate.lead = el('p', { className: 'gate-lead', attrs: { hidden: '' } });

  gate.card = el('div', { className: 'gate-card' }, [
    el('div', { className: 'gate-brand', text: 'mnemo' }),
    gate.title,
    gate.text,
    gate.lead,
    el('code', { className: 'gate-cmd', text: 'mnemo ui' }),
    form,
  ]);

  gate.root = el('div', { className: 'gate', attrs: { hidden: '' } }, [gate.card]);
  document.body.appendChild(gate.root);
}

/**
 * Take over the page until we have a token the service accepts.
 *
 * Raises `state.gated` first: everything else in the page checks it before
 * issuing a request or reopening the socket, so a gated page is silent — no
 * doomed fetches in the console, no 401s in the service log.
 */
function openGate(variant) {
  const copy = gateCopy(variant);
  gate.variant = variant;
  state.gated = true;
  closeSocket();
  syncTicker();          // nothing behind the gate needs a running clock
  hideBanner();
  setConnState('idle', 'common.gate.idle');

  gate.card.classList.toggle('is-error', variant === 'rejected');
  // Without the separate lead line the prose sits directly above the command,
  // so it takes the tighter margin the lead would otherwise have carried.
  gate.card.classList.toggle('is-terse', !copy.lead);
  gate.title.textContent = copy.title;
  gate.text.textContent = copy.text;
  gate.lead.textContent = copy.lead || '';
  gate.lead.hidden = !copy.lead;
  gateNote(copy.note);
  gate.root.hidden = false;
  gate.input.focus();
}

function closeGate() {
  state.gated = false;
  gate.root.hidden = true;
  gateNote(null);
}

/** Re-run `openGate()` for whatever variant is currently up, so a language
 *  switch while gated repaints the gate's copy without touching its state. */
function regate() {
  if (gate.root && !gate.root.hidden) openGate(gate.variant);
}

function gateNote(text) {
  gate.note.textContent = text || '';
  gate.note.hidden = !text;
}

async function submitGate(event) {
  event.preventDefault();
  const value = gate.input.value.trim();
  if (!value) {
    gateNote(t('common.gate.enterToken'));
    return;
  }
  token = value;
  sessionStorage.setItem('mnemo_token', value);
  closeGate();
  await start();
}

// ---------------------------------------------------------------------------
// formatting
// ---------------------------------------------------------------------------

function fmtBytes(n) {
  if (n == null) return '—';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KiB';
  return (n / 1048576).toFixed(1) + ' MiB';
}

function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso);
  const p = (v) => String(v).padStart(2, '0');
  return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
}

function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso);
  const p = (v) => String(v).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) +
         ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}

function fmtMs(v) {
  if (v == null) return '—';
  return v >= 1000 ? (v / 1000).toFixed(2) + ' s' : v.toFixed(1) + ' ms';
}

function statusLabel(status) {
  switch (status) {
    case 'ready': return t('common.status.ready');
    case 'indexing': return t('common.status.indexing');
    case 'empty': return t('common.status.empty');
    default: return status;
  }
}

// ---------------------------------------------------------------------------
// banks — shared data fetchers (page-memory.js owns the rendering)
// ---------------------------------------------------------------------------

async function loadBanks() {
  const data = await api('/api/banks');
  state.banks = data.banks || [];
  if (state.selectedBankId && !state.banks.some((b) => b.id === state.selectedBankId)) {
    selectBank(null);
  }
  renderBanks();
}

async function loadStatus() {
  const data = await api('/api/status');
  state.service = data.service || null;
  state.queue = data.queue || null;
  renderService();
  // A page opened mid-index learns here which task is in flight; `index_start`
  // for it fired before this page existed.
  if (reconcileProgress(state.queue)) renderBanks();
}

function bankById(id) {
  return state.banks.find((b) => b.id === id) || null;
}

// ---------------------------------------------------------------------------
// task progress — fed by REST snapshots and by WebSocket deltas (shell.js),
// rendered into bank cards (page-memory.js)
// ---------------------------------------------------------------------------

// A task is not always a file: `bulk`/`rebuild` work on the whole bank and
// carry no path at all, so they get named rather than left as a blank slot.
// These read the same as the buttons that queue them — a user watching the
// progress bar should recognise the thing they just clicked.
function taskKindLabel(kind) {
  switch (kind) {
    case 'file': return t('common.taskKind.file');
    case 'bulk': return t('common.taskKind.bulk');
    case 'rebuild': return t('common.taskKind.rebuild');
    case 'prune': return t('common.taskKind.prune');
    default: return kind || t('common.taskKind.default');
  }
}

function fmtDuration(seconds) {
  if (seconds < 60) return seconds + ' ' + t('common.unit.sec');
  return Math.floor(seconds / 60) + ' ' + t('common.unit.min') + ' ' +
         String(seconds % 60).padStart(2, '0') + ' ' + t('common.unit.sec');
}

/**
 * How long this task has been running.
 *
 * Exact in two cases: we watched it begin via `index_start`, or the snapshot
 * told us when it began. `queue.current.started_at` is absolute epoch seconds
 * (loopback, so the same clock) — when it is there the age is real even for a
 * task adopted mid-flight.
 *
 * Without it, all we honestly know about an adopted task is how long we have
 * been watching, which is a lower bound: rendered `≥`. Guessing a start would
 * be inventing data the service never sent.
 */
function elapsedLabel(live) {
  if (!live.since) return null;
  const secs = Math.max(0, Math.round((Date.now() - live.since) / 1000));
  return (live.approx ? '≥' : '') + fmtDuration(secs);
}

function progressText(live) {
  const parts = [taskKindLabel(live.kind)];
  if (live.path) parts.push(live.path);
  if (live.batches > 0) parts.push(t('common.progress.batch') + ' ' + live.batch + '/' + live.batches);
  if (live.chunks_total) {
    parts.push(live.chunks_done + '/' + live.chunks_total + ' ' + t('common.progress.chunks'));
  }
  const age = elapsedLabel(live);
  if (age) parts.push(age);
  if (live.yielded) parts.push(t('common.progress.yielded'));
  return parts.join(' · ');
}

function progressBlock(live) {
  const known = live.batches > 0;
  const pct = known ? Math.min(100, Math.round((live.batch / live.batches) * 100)) : 0;

  const bar = el('div', { className: 'bar' + (known ? '' : ' is-indeterminate') }, [
    el('i'),
  ]);
  if (known) bar.firstChild.style.width = pct + '%';

  return el('div', { className: 'bank-progress' }, [
    bar,
    el('div', {
      className: 'progress-text',
      text: progressText(live),
      title: live.approx
        ? t('common.progress.approxTitle')
        : t('common.progress.exactTitle'),
    }),
  ]);
}

/**
 * Re-time the visible bars without rebuilding the bank list.
 *
 * The clock has to move every second, and the cards carry buttons — a full
 * re-render on a timer would rebuild a button out from under a click. Only
 * the one line that changes is touched.
 */
function tickProgress() {
  for (const [bankId, live] of state.progress) {
    const node = document.querySelector('.bank[data-bank="' + bankId + '"] .progress-text');
    if (node) node.textContent = progressText(live);
  }
  syncTicker();
}

let tickTimer = null;

function syncTicker() {
  const wanted = state.progress.size > 0 && !state.gated;
  if (wanted && !tickTimer) {
    tickTimer = setInterval(tickProgress, 1000);
  } else if (!wanted && tickTimer) {
    clearInterval(tickTimer);
    tickTimer = null;
  }
}

/**
 * Retire the live bar only for the task that owns it.
 *
 * Progress is tracked per bank, but several tasks share a bank: a bulk scan
 * finishes and reports `index_done` while the per-file tasks it just spawned
 * are still running. Deleting on any `index_done` therefore blanked the bar
 * of a file that was still indexing, and it only came back when the next
 * file emitted progress — which is exactly "the bar stalls, then jumps".
 */
function clearProgress(bankId, taskId) {
  const live = state.progress.get(bankId);
  if (!live) return;
  if (taskId && live.task_id && live.task_id !== taskId) return;
  state.progress.delete(bankId);
}

/** The live pending-relpaths set for one bank, created empty on first use. */
function pendingSetFor(bankId) {
  let set = state.pendingFiles.get(bankId);
  if (!set) {
    set = new Set();
    state.pendingFiles.set(bankId, set);
  }
  return set;
}

/** Clear-signal for MN-15's tree highlight: either `index_done` or
 *  `index_error` arriving with a truthy `path` means that file is no longer
 *  queued or in flight, regardless of which one fired. */
function clearPendingPath(bankId, path) {
  if (!path) return;
  const set = state.pendingFiles.get(bankId);
  if (!set || !set.delete(path)) return;
  if (bankId === state.selectedBankId) renderTree();
}

/**
 * Make the bars agree with the queue snapshot, in both directions.
 *
 * `index_start` is a delta: it says a task began. A page loaded while a file
 * was already being indexed never saw that event and so showed nothing until
 * the *next* file started. `queue.current` is the service's own statement of
 * what is running right now and rides on `/api/status`, on `hello` and on
 * every queue event — the same "REST is authoritative for initial state, the
 * socket carries deltas" rule as everywhere else on this page.
 *
 * So: adopt a `current` the bars do not know about, and retire a bar the
 * snapshot does not account for. Keyed on `task_id`, so re-stating the same
 * running task never disturbs the counters `index_progress` has been filling
 * in — the snapshot seeds a bar, it does not overwrite a live one.
 */
function reconcileProgress(queue) {
  if (!queue) return false;
  const byBank = queue.by_bank || {};
  const current = queue.current || null;
  let changed = false;

  if (current && current.bank_id) {
    const live = state.progress.get(current.bank_id);
    if (!live || live.task_id !== current.task_id) {
      // Epoch seconds when the service started this task. Absent on older
      // backends, in which case the clock can only run from now and says so.
      const startedAt = Number(current.started_at) || 0;
      state.progress.set(current.bank_id, {
        task_id: current.task_id,
        kind: current.kind || 'file',
        path: current.path || null,
        batch: current.batch || 0,
        // Still "unknown" when 0 — seeding must not imply a percentage the
        // service has not given us.
        batches: current.batches || 0,
        chunks_done: 0,
        chunks_total: 0,
        since: startedAt > 0 ? startedAt * 1000 : Date.now(),
        approx: startedAt <= 0,
      });
      changed = true;
    }
  }

  for (const bankId of [...state.progress.keys()]) {
    if (current && bankId === current.bank_id) continue;
    const entry = byBank[bankId];
    if (entry && (entry.indexing || entry.depth > 0)) continue;
    state.progress.delete(bankId);
    changed = true;
  }
  return changed;
}

/**
 * Replace one bank's row with a fresh BankInfo and repaint.
 *
 * Shared by the `bank_status` event and by any request that answers with a
 * BankInfo, so a change applied through the console lands exactly the way the
 * same change arriving over the socket would.
 */
function applyBank(info) {
  if (!info || !info.id) return;
  const i = state.banks.findIndex((b) => b.id === info.id);
  if (i >= 0) state.banks[i] = info;
  else state.banks.push(info);
  renderBanks();
}

function setNote(bankId, text) {
  state.notes.set(bankId, text);
  renderBanks();
  setTimeout(() => {
    if (state.notes.get(bankId) === text) {
      state.notes.delete(bankId);
      renderBanks();
    }
  }, 6000);
}

// ---------------------------------------------------------------------------
// reindex (contract 9.5 POST /api/reindex) + bank state (PATCH /api/banks/{id})
// ---------------------------------------------------------------------------

function requestReindex(bank, opts) {
  return api('/api/reindex', {
    method: 'POST',
    body: { bank: bank.name, path: opts.path || null, full: !!opts.full },
  });
}

async function reindex(bank, opts) {
  try {
    const res = await requestReindex(bank, opts);
    hideBanner();
    const what = opts.path ? opts.path : taskKindLabel(opts.full ? 'rebuild' : 'bulk');
    setNote(bank.id, t('common.reindex.queuedNote', {
      what: what, n: res.queued, ids: (res.task_ids || []).join(', '),
    }));
  } catch (err) {
    reportError(err);
  }
}

/**
 * Switch a bank between enabled / frozen / disabled (PATCH /api/banks/{id}).
 *
 * The card is refreshed from the response rather than patched in place: going
 * back to `enabled` queues a catch-up on the backend, so `status` and `queued`
 * change along with the state, and guessing them here would leave a stale card
 * until the next poll.
 */
async function setBankState(bank, next) {
  if (bankState(bank) === next) return;
  try {
    const info = await api('/api/banks/' + encodeURIComponent(bank.id), {
      method: 'PATCH',
      body: { state: next },
    });
    hideBanner();
    applyBank(info);
    setNote(bank.id, t('common.bankMenu.stateNote', {
      state: (bankStateLabel(info.state) || info.state).toLowerCase(),
    }));
  } catch (err) {
    reportError(err);
  }
}

// ---------------------------------------------------------------------------
// bank picker (contract 9.5: GET /api/fs/dirs + POST /api/banks)
// ---------------------------------------------------------------------------

/**
 * Why a folder browser instead of the operating system's own dialog.
 *
 * A page cannot learn which folder a person picked: `webkitdirectory` gives
 * relative names only, and `showDirectoryPicker()` returns a handle while
 * withholding the path deliberately. The absolute path can therefore only come
 * from the backend, which is why this walks `/api/fs/dirs` instead of opening a
 * native dialog. The path field stays: pasting from Explorer beats clicking
 * through six levels, and it is the way out when a listing is truncated.
 *
 * Built in JS rather than in index.html for the reason spelled out at the gate:
 * a cached document plus a fresh script must not leave this code addressing
 * markup that is not there.
 */
const picker = { path: null, data: null, busy: false };

const LAST_DIR_KEY = 'mnemo_fs_last';

function buildPicker() {
  picker.roots = el('div', { className: 'fs-roots' });
  picker.input = el('input', {
    className: 'fs-input',
    attrs: {
      type: 'text', spellcheck: 'false', placeholder: t('common.picker.pathPlaceholder'),
      'data-i18n-placeholder': 'common.picker.pathPlaceholder',
      id: 'fs-path',
    },
    on: {
      keydown: (ev) => {
        if (ev.key !== 'Enter') return;
        ev.preventDefault();
        pickerGo(picker.input.value.trim());
      },
    },
  });
  picker.list = el('div', { className: 'fs-list' });
  picker.hint = el('p', { className: 'fs-hint' });
  picker.name = el('input', {
    className: 'fs-input',
    attrs: {
      type: 'text', spellcheck: 'false', placeholder: t('common.picker.namePlaceholder'),
      'data-i18n-placeholder': 'common.picker.namePlaceholder',
      id: 'fs-bank-name',
    },
    on: {
      keydown: (ev) => {
        if (ev.key === 'Enter') { ev.preventDefault(); pickerSubmit(); }
      },
    },
  });
  picker.createCheckbox = el('input', {
    attrs: { type: 'checkbox', id: 'fs-init-create' },
    on: { change: () => pickerUpdateEligibility() },
  });
  picker.createRow = el('div', { className: 'fs-init-row' }, [
    picker.createCheckbox,
    el('label', {
      text: t('common.picker.createStructure'),
      attrs: { for: 'fs-init-create', 'data-i18n': 'common.picker.createStructure' },
    }),
  ]);
  picker.createHint = el('p', { className: 'fs-hint' });
  picker.initCheckbox = el('input', {
    attrs: { type: 'checkbox', id: 'fs-init-mcp' },
  });
  picker.initRow = el('div', { className: 'fs-init-row' }, [
    picker.initCheckbox,
    el('label', {
      text: t('common.picker.connectMcp'),
      attrs: { for: 'fs-init-mcp', 'data-i18n': 'common.picker.connectMcp' },
    }),
  ]);
  picker.initHint = el('p', { className: 'fs-hint' });
  picker.error = el('p', { className: 'modal-error', attrs: { hidden: '' } });
  picker.submit = el('button', {
    className: 'btn btn-primary',
    text: t('common.picker.addDir'),
    on: { click: () => pickerSubmit() },
  });

  const box = el('div', {
    className: 'modal-box',
    attrs: {
      role: 'dialog', 'aria-modal': 'true', 'aria-label': t('common.picker.ariaLabel'),
      'data-i18n-aria': 'common.picker.ariaLabel',
    },
    // The overlay closes on click; inside it, a click is just a click.
    on: { click: (ev) => ev.stopPropagation() },
  }, [
    el('div', { className: 'modal-head' }, [
      el('h2', { text: t('common.picker.title'), attrs: { 'data-i18n': 'common.picker.title' } }),
      el('button', {
        className: 'btn btn-ghost',
        text: '✕',
        title: t('common.btn.closeEsc'),
        attrs: { 'data-i18n-title': 'common.btn.closeEsc' },
        on: { click: () => closePicker() },
      }),
    ]),
    el('div', { className: 'modal-body' }, [
      el('label', {
        className: 'fs-label',
        text: t('common.picker.pathLabel'),
        attrs: { for: 'fs-path', 'data-i18n': 'common.picker.pathLabel' },
      }),
      picker.roots,
      picker.input,
      picker.list,
      picker.hint,
      el('label', {
        className: 'fs-label',
        text: t('common.picker.nameLabel'),
        attrs: { for: 'fs-bank-name', 'data-i18n': 'common.picker.nameLabel' },
      }),
      picker.name,
      picker.createRow,
      picker.createHint,
      picker.initRow,
      picker.initHint,
      picker.error,
    ]),
    el('div', { className: 'modal-foot' }, [
      el('button', {
        className: 'btn', text: t('common.btn.cancel'),
        attrs: { 'data-i18n': 'common.btn.cancel' },
        on: { click: () => closePicker() },
      }),
      picker.submit,
    ]),
  ]);

  picker.root = el('div', {
    className: 'modal',
    attrs: { hidden: '' },
    on: { click: () => closePicker() },
  }, [box]);
  document.body.appendChild(picker.root);

  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && !picker.root.hidden) closePicker();
  });
}

function openPicker() {
  picker.root.hidden = false;
  picker.error.hidden = true;
  picker.name.value = '';
  picker.createCheckbox.checked = false;
  picker.createEvaluatedPath = null;
  picker.initEvaluatedKey = null;
  // Resume where the last look around ended — adding two banks from one folder
  // should not mean walking down from home twice.
  pickerGo(sessionStorage.getItem(LAST_DIR_KEY) || null);
  picker.input.focus();
}

/** `<project>/.claude/memory` → `<project>`, or null when `path` is not
 *  shaped that way. `GET /api/fs/dirs` reports `path` as posix
 *  (`Path.as_posix()`, api.py) even on Windows, but `\` is normalised too
 *  in case a pasted path reaches here before a round trip through the API. */
function projectRootForBankPath(path) {
  if (!path) return null;
  const norm = path.replace(/\\/g, '/').replace(/\/+$/, '');
  const suffix = '.claude/memory';
  if (!norm.endsWith('/' + suffix)) return null;
  const root = norm.slice(0, norm.length - suffix.length - 1);
  return root || null;
}

/** The root a bank gets registered at once checkbox A is checked — the
 *  server's own `memory_dir` for the currently-loaded `picker.data`
 *  (`_memory_dir_for` in api.py), never naive string concatenation here:
 *  a picked path that already ends in `.claude` needs only `memory`
 *  appended, not another `.claude/memory` on top of it. Falls back to plain
 *  concatenation only if `picker.data` hasn't loaded yet, which the caller
 *  never actually hits since checkbox A can't be checked before it has. */
function effectiveBankRoot(path, createStructure) {
  if (!createStructure) return path;
  if (picker.data && picker.data.memory_dir) return picker.data.memory_dir;
  return path.replace(/\/+$/, '') + '/.claude/memory';
}

/** Recomputed on every `renderPicker()` (i.e. whenever `picker.path`
 *  changes) and on every toggle of `picker.createCheckbox`, since checkbox
 *  A's state gates checkbox B's eligibility.
 *
 *  Checkbox A resets to unchecked only when the *path* itself changed — a
 *  `pickerGo()` re-render for the same path (e.g. the busy toggle around its
 *  fetch) must not fight a manual toggle.
 *
 *  Checkbox B's default-checked state is re-decided whenever *either* the
 *  path or its own eligibility changes (tracked as one key) — so checking
 *  checkbox A flips B from disabled+unchecked to enabled+checked, while a
 *  re-render that changes neither leaves a manual uncheck of B alone. */
function pickerUpdateEligibility() {
  const projectRoot = projectRootForBankPath(picker.path);
  const alreadyBank = projectRoot !== null;
  const hasNestedMemory = !!(picker.data && picker.data.has_claude_memory);
  const canCreate = picker.path !== null && !alreadyBank && !hasNestedMemory;

  picker.createCheckbox.disabled = !canCreate;
  if (picker.path !== picker.createEvaluatedPath) {
    picker.createEvaluatedPath = picker.path;
    picker.createCheckbox.checked = false;
  }
  if (!canCreate) picker.createCheckbox.checked = false;

  picker.createHint.textContent = !picker.path
    ? ''
    : alreadyBank
      ? t('common.picker.hint.alreadyBank')
      : hasNestedMemory
        ? t('common.picker.hint.hasNestedMemory')
        : picker.createCheckbox.checked
          ? t('common.picker.hint.willBecome', { root: effectiveBankRoot(picker.path, true) })
          : '';
  // Amber both for "can't create here" and for the checked preview: the
  // latter is not an error, but it does say the bank root differs from the
  // folder that was picked, which deserves the same visual weight.
  picker.createHint.classList.toggle(
    'fs-warn',
    (!canCreate && !!picker.path) || picker.createCheckbox.checked
  );

  const mcpEligible = alreadyBank || picker.createCheckbox.checked;
  const mcpKey = picker.path + '::' + mcpEligible;
  picker.initCheckbox.disabled = !mcpEligible;
  if (mcpKey !== picker.initEvaluatedKey) {
    picker.initEvaluatedKey = mcpKey;
    picker.initCheckbox.checked = mcpEligible;
  }
  if (!mcpEligible) picker.initCheckbox.checked = false;
  picker.initHint.textContent = mcpEligible
    ? (alreadyBank
        ? t('common.picker.hint.project', { root: projectRoot })
        : t('common.picker.hint.willConnect'))
    : t('common.picker.hint.projectOnly');
  picker.initHint.classList.toggle('fs-warn', !mcpEligible);
}

function closePicker() {
  picker.root.hidden = true;
}

async function pickerGo(path) {
  picker.busy = true;
  renderPicker();
  try {
    const q = path ? '?path=' + encodeURIComponent(path) : '';
    const data = await api('/api/fs/dirs' + q);
    picker.data = data;
    picker.path = data.path;
    picker.error.hidden = true;
    sessionStorage.setItem(LAST_DIR_KEY, data.path);
  } catch (err) {
    if (isAuthError(err)) { closePicker(); reportError(err); return; }
    // Keep the previous listing on screen: a mistyped path should leave the
    // browser where it was, not empty it.
    pickerError(err.message);
  } finally {
    picker.busy = false;
    renderPicker();
  }
}

function pickerError(message) {
  picker.error.textContent = message;
  picker.error.hidden = false;
}

function renderPicker() {
  const data = picker.data;

  clear(picker.roots);
  if (data) {
    picker.roots.appendChild(el('button', {
      className: 'chip',
      text: '⌂ ' + t('common.picker.home'),
      title: data.home,
      on: { click: () => pickerGo(data.home) },
    }));
    for (const root of data.roots || []) {
      picker.roots.appendChild(el('button', {
        className: 'chip',
        text: root.name,
        on: { click: () => pickerGo(root.path) },
      }));
    }
  }

  if (data && document.activeElement !== picker.input) {
    picker.input.value = data.display || data.path;
  }

  clear(picker.list);
  if (!data) {
    picker.list.appendChild(el('p', { className: 'muted', text: t('common.picker.reading') }));
  } else {
    if (data.parent) {
      picker.list.appendChild(el('button', {
        className: 'fs-row is-up',
        text: '⬆  ..',
        title: data.parent,
        on: { click: () => pickerGo(data.parent) },
      }));
    }
    for (const entry of data.entries || []) {
      picker.list.appendChild(el('button', {
        className: 'fs-row',
        title: entry.registered
          ? t('common.picker.alreadyBankTitle', { name: entry.registered })
          : entry.path,
        on: { click: () => pickerGo(entry.path) },
      }, [
        el('span', { className: 'fs-name', text: entry.name }),
        entry.registered
          ? el('span', { className: 'badge badge-git', text: t('common.picker.bankBadge') })
          : null,
      ]));
    }
    if (!(data.entries || []).length) {
      picker.list.appendChild(el('p', { className: 'muted', text: t('common.picker.noSubdirs') }));
    }
    if (data.truncated) {
      picker.list.appendChild(el('p', {
        className: 'muted',
        text: t('common.picker.truncated', { n: data.entries.length }),
      }));
    }
  }

  clear(picker.hint);
  if (data) {
    // Nothing here is a veto, only a warning: a folder can be registered while
    // still empty, and the watcher will index the .md that appear later.
    const count = data.md_capped ? '≥' + data.md : String(data.md);
    // A claim about recursion, so it may only appear when there are
    // subfolders to recurse into: on a flat folder it would read as a
    // promise about something that is not there.
    const nested = (data.entries || []).length ? ' ' + t('common.picker.withSubdirs') : '';
    picker.hint.appendChild(el('span', {
      className: data.md ? '' : 'fs-warn',
      text: data.md
        ? t('common.picker.mdCount', { count: count, nested: nested })
        : t('common.picker.noMd'),
      title: data.md_capped
        ? t('common.picker.countTruncatedTitle')
        : t('common.picker.excludesTitle'),
    }));
    if (data.registered) {
      picker.hint.appendChild(el('span', {
        className: 'fs-warn',
        text: ' · ' + t('common.picker.alreadyRegistered', { name: data.registered }),
      }));
    }
  }

  picker.submit.disabled = picker.busy || !data || !!data.registered;
  picker.submit.textContent = picker.busy ? t('common.picker.reading') : t('common.picker.addDir');

  pickerUpdateEligibility();
}

/** One-line summary of `info.init` (contract: `{ok, log}` on an attempt,
 *  `{ok:false, skipped:true, reason}` when the bank root was not
 *  `<project>/.claude/memory`) for the post-add `setNote()` — the full log
 *  stays available via `mnemo doctor` / the service log, not worth a second
 *  dialog for. A failed or skipped init is never presented as the add-bank
 *  action failing, since the bank is already registered by the time this
 *  runs. */
function initNoteSuffix(initInfo) {
  if (!initInfo) return '';
  if (initInfo.skipped) {
    return ' · ' + t('common.picker.mcpSkipped') + (initInfo.reason ? ': ' + initInfo.reason : '');
  }
  return ' · ' + (initInfo.ok ? t('common.picker.mcpConnected') : t('common.picker.mcpFailed'));
}

async function pickerSubmit() {
  if (!picker.path || picker.busy) return;
  const createStructure = !!(picker.createCheckbox && picker.createCheckbox.checked);
  const body = {
    root: effectiveBankRoot(picker.path, createStructure),
    name: picker.name.value.trim() || null,
    create_structure: createStructure,
    init: !!(picker.initCheckbox && picker.initCheckbox.checked),
  };
  picker.busy = true;
  renderPicker();
  try {
    const info = await api('/api/banks', { method: 'POST', body: body });
    hideBanner();
    state.banks = state.banks.concat([info]);
    renderBanks();
    // The bank was registered *and* queued in one call, so say both — and open
    // it, so the first build is visible instead of happening off-screen. The
    // dialog closes right away regardless of `info.init`: leaving it open
    // invited a second click on "Add this directory", which the bank already
    // being registered would only turn into an error.
    setNote(info.id, t('common.picker.addedNote') + initNoteSuffix(info.init));
    selectBank(info.id);
    loadBanks().catch(() => {});
    closePicker();
  } catch (err) {
    if (isAuthError(err)) { closePicker(); reportError(err); return; }
    // `root_not_found` and `bank_exists` are both fixable right here, so the
    // message belongs in the dialog rather than in the page-wide banner.
    pickerError(err.message);
  } finally {
    picker.busy = false;
    renderPicker();
  }
}

// ---------------------------------------------------------------------------
// bank token panel (contract 9.5: GET/POST /api/banks/{id}/token)
// ---------------------------------------------------------------------------

/**
 * The per-bank MCP token, and the two config shapes that carry it.
 *
 * A project's config holds the token of the one bank it may read, never the
 * wide service token — so what a person actually needs here is not the value
 * but a working snippet, which is why the value is one line and the snippets
 * are the rest of the dialog.
 *
 * The token never reaches the DOM until it is asked for: everything on screen
 * renders through `shownToken()`, which is bullets until «show» is pressed,
 * while the copy buttons build their text from `bankToken.value`. So a masked
 * panel still yields a config that works, and the two never diverge in shape.
 *
 * Built in JS rather than in index.html for the reason spelled out at the gate:
 * a cached document plus a fresh script must not leave this code addressing
 * markup that is not there.
 */
const bankToken = {
  bank: null,          // the BankInfo this panel was opened for
  value: null,         // the real token, deliberately kept out of the DOM
  revealed: false,
  scope: 'literal',    // which config shape is on screen
  // The name the config entry will carry. Null rather than a literal: the
  // fallback belongs in `entryName()`, next to `DEFAULT_INSTANCE`, so there is
  // one place that decides it.
  entry: null,
  blocks: [],          // rendered snippets, so typing can repaint them in place
  busy: false,
  confirming: false,   // the regenerate confirmation is up
  errorText: null,
  note: null,
};

/**
 * The port a generated config must point at.
 *
 * `/api/status` is the service's own statement of where it listens; the page's
 * own URL is the fallback, because a console answering at :8919 was plainly
 * served by something on :8919. A hardcoded 8918 would hand out a config that
 * cannot connect the moment the service moves.
 */
function servicePort() {
  const fromStatus = state.service && state.service.port;
  if (fromStatus) return fromStatus;
  const fromUrl = window.location.port;
  if (fromUrl) return fromUrl;
  return window.location.protocol === 'https:' ? 443 : 80;
}

/**
 * The host the service is bound to — from `/api/status`, not from the address
 * bar.
 *
 * `location.hostname` answers a different question: how *this browser* reached
 * the service. On the default loopback bind the two agree, and on any other
 * they need not — `localhost` and a LAN address both work here while only one
 * of them is what the service was told to bind. The snippet is going into
 * someone else's config, so it should carry the binding.
 */
function serviceHost() {
  return (state.service && state.service.host) || window.location.hostname ||
         '127.0.0.1';
}

function maskToken(value) {
  return '•'.repeat(value.length);
}

function shownToken() {
  if (bankToken.value == null) return '…';
  return bankToken.revealed ? bankToken.value : maskToken(bankToken.value);
}

/**
 * Copy without going through the screen.
 *
 * The async clipboard API needs a secure context: 127.0.0.1 is one, but any
 * other host the service may be reached at is not, and there the API is simply
 * absent — hence the textarea path, which is the only moment the real value
 * touches the DOM at all, and only because copying was asked for.
 */
async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (err) {
      // Denied or unavailable — fall through to the legacy path.
    }
  }
  const sink = el('textarea', { className: 'tok-copysink' });
  sink.value = text;
  document.body.appendChild(sink);
  sink.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch (err) {
    ok = false;
  }
  document.body.removeChild(sink);
  return ok;
}

function copyButton(get, title) {
  const button = el('button', {
    className: 'btn',
    text: t('common.btn.copy'),
    title: title,
    on: { click: () => copyInto(button, get()) },
  });
  return button;
}

async function copyInto(button, text) {
  if (text == null) return;
  if (!(await copyText(text))) {
    tokenError(t('common.token.copyFailed'));
    renderTokenPanel();
    return;
  }
  const was = button.textContent;
  button.textContent = t('common.btn.copied');
  button.disabled = true;
  // A re-render in the meantime detaches this node; touching it then is a
  // no-op, which is exactly what should happen.
  setTimeout(() => { button.textContent = was; button.disabled = false; }, 1400);
}

/**
 * Cyrillic to Latin, enough for a config entry name.
 *
 * Not a transliteration standard, and not meant to be: it exists so a bank
 * called «Моя пам'ять» yields a name a person can read and an MCP server name
 * can hold. Ukrainian first, then the four Russian letters that differ.
 */
const TRANSLIT = {
  'а': 'a', 'б': 'b', 'в': 'v', 'г': 'h', 'ґ': 'g', 'д': 'd', 'е': 'e',
  'є': 'ye', 'ж': 'zh', 'з': 'z', 'и': 'y', 'і': 'i', 'ї': 'yi', 'й': 'y',
  'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r',
  'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch',
  'ш': 'sh', 'щ': 'shch', 'ь': '', 'ю': 'yu', 'я': 'ya',
  'ы': 'y', 'э': 'e', 'ъ': '', 'ё': 'e',
};

/**
 * A starting entry name for this bank.
 *
 * The URL no longer names the bank, so with several mnemo servers side by side
 * in `~/.claude.json` the entry name is the only thing telling them apart.
 * Apostrophes drop rather than becoming separators — «пам'ять» should read
 * `pamyat`, not `pam-yat`.
 */
function defaultEntryName(name) {
  const slug = [...String(name || '').toLowerCase().replace(/['’ʼ`]/g, '')]
    .map((ch) => (Object.prototype.hasOwnProperty.call(TRANSLIT, ch) ? TRANSLIT[ch] : ch))
    .join('')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  if (!slug || slug === 'mnemo') return DEFAULT_INSTANCE;
  // A bank already called "mnemo…" must not come out as "mnemo-mnemo…".
  return slug.startsWith('mnemo') ? slug : 'mnemo-' + slug;
}

// What `mnemo init` names the project's own memory entry, and the one name
// whose variables stay on the bare `MNEMO_` prefix. A bank called plainly
// `mnemo` lands here rather than on the bare legacy key: suggesting `mnemo`
// would hand back the exact name the rename moved away from, and the next
// `init` would rename it underneath whoever pasted it.
const DEFAULT_INSTANCE = 'mnemo-memory';

/**
 * The token variable's name — the only one that varies per entry.
 *
 * `MNEMO_HOST` and `MNEMO_PORT` stay shared, and that is the point: they
 * describe the **service**, not the bank. One backend, one address, so giving
 * each bank its own copy would mean editing every one of them the day the
 * port changes — a set of values free to drift out of agreement about a fact
 * that is single.
 *
 * The token is the opposite: it belongs to exactly one bank and to nothing
 * else. Handing a second bank `MNEMO_TOKEN` again would overwrite the first
 * one's — two banks, one variable, and whichever `.mcp.env` line comes last
 * silently wins for both.
 *
 * The default entry keeps the bare `MNEMO_TOKEN` it already has in every
 * adopted project; renaming it would buy nothing and risk the silent failure.
 */
function tokenVar() {
  const name = entryName();
  if (name === DEFAULT_INSTANCE || name === 'mnemo') return 'MNEMO_TOKEN';
  return name.replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
             .toUpperCase() + '_TOKEN' || 'MNEMO_TOKEN';
}

/**
 * What an MCP server name may hold; anything else becomes a separator.
 *
 * Runs collapse, and they have to be collapsed on the *result* rather than on
 * the input: this runs once per keystroke, so a rejected character lands next
 * to the dash the previous one already became — the run is never contiguous in
 * the string being cleaned, and typing a Cyrillic word would otherwise leave a
 * dash per letter.
 */
function sanitizeEntryName(value) {
  return String(value).replace(/[^A-Za-z0-9_-]+/g, '-').replace(/-{2,}/g, '-');
}

function entryName() {
  return bankToken.entry || DEFAULT_INSTANCE;
}

/**
 * The config shapes the projects on this machine actually use.
 *
 * `build` takes the token to print, so one definition serves both the masked
 * line on screen and the full text a copy button produces. Nothing here needs
 * escaping any more: the token identifies the bank on its own, so the URL is
 * the bare `/mcp` endpoint and the bank name never enters it — and the JSON
 * shapes go through `JSON.stringify`, which owns the quoting outright.
 *
 * Both `entryName()` and `servicePort()` are read inside `build` rather than
 * captured around it, so a spec rendered once still follows the entry field as
 * it is typed into.
 */
function mcpDocument(url) {
  // Built as an object and stringified rather than concatenated, so it cannot
  // come out as invalid JSON and the quoting is never this code's problem.
  // The whole `mcpServers` wrapper is shown, not just the one entry: a bare
  // `"name": {…}` line is only meaningful to someone who already knows where
  // it goes, and this dialog is read exactly by the people who do not.
  const servers = {};
  servers[entryName()] = { type: 'http', url: url };
  return JSON.stringify({ mcpServers: servers }, null, 2);
}

function tokenSnippets() {
  if (bankToken.scope === 'literal') {
    return [{
      caption: t('common.token.caption.literal'),
      secret: true,
      build: (tok) => mcpDocument('http://' + serviceHost() + ':' + servicePort() +
                                  '/mcp?token=' + tok),
    }];
  }
  return [
    {
      caption: t('common.token.caption.template'),
      secret: false,
      build: () => mcpDocument('http://{{MNEMO_HOST}}:{{MNEMO_PORT}}' +
                               '/mcp?token={{' + tokenVar() + '}}'),
    },
    {
      caption: t('common.token.caption.env'),
      secret: true,
      build: (tok) => 'MNEMO_HOST=' + serviceHost() + '\n' +
                      'MNEMO_PORT=' + servicePort() + '\n' +
                      tokenVar() + '=' + tok,
    },
  ];
}

/**
 * The template form leads with the command, not with the paste.
 *
 * `mnemo init` writes all three pieces — the fragment into the template, the
 * variables into `.mcp.env`, and the substitution lines into `mcp-setup.sh` —
 * so presenting the manual procedure as the main path would be teaching
 * something strictly worse than a command that already exists. The snippets
 * stay below it because seeing what will land is worth having, and because
 * they are the way out when `init` cannot be run in that project.
 */
function templateLeadNote() {
  return el('p', { className: 'tok-lead' }, [
    document.createTextNode(t('common.token.templateLead.part1')),
    el('code', { text: 'mnemo init' }),
    document.createTextNode(t('common.token.templateLead.part2')),
    el('code', { text: 'cp .mcp.env.example .mcp.env' }),
    document.createTextNode(t('common.token.templateLead.part3')),
    el('code', { text: 'bash mcp-setup.sh' }),
    document.createTextNode(t('common.token.templateLead.part4')),
  ]);
}

/**
 * The failure that makes the manual path worth a warning.
 *
 * A placeholder with no matching `sed` line is copied through into `.mcp.json`
 * verbatim, and `mcp-setup.sh` still prints its success line and exits 0 — so
 * nothing marks the moment it went wrong. Measured, not hypothetical: this is
 * what following the tab's earlier text to the letter actually produced.
 */
function manualPasteNote() {
  // The `sed` line names the token variable, so it has to follow the entry
  // field like the snippets do — a note showing the previous name would be
  // worse than a generic one, because it looks specific enough to trust.
  bankToken.sedLine = el('code', { text: sedLineText() });
  return el('p', { className: 'tok-note' }, [
    document.createTextNode(t('common.token.manualPaste.part1')),
    el('code', { text: 'sed' }),
    document.createTextNode(t('common.token.manualPaste.part2')),
    bankToken.sedLine,
    document.createTextNode(t('common.token.manualPaste.part3')),
  ]);
}

function sedLineText() {
  return '-e "s|{{' + tokenVar() + '}}|${' + tokenVar() + '}|g"';
}

/**
 * Repaint the snippet bodies in place.
 *
 * Typing in the entry field must not re-render the panel: that would rebuild
 * the field out from under the caret. The copy buttons need nothing — they
 * already call `build` at click time.
 */
function refreshSnippets() {
  for (const block of bankToken.blocks) {
    block.pre.textContent = block.spec.build(shownToken());
  }
  if (bankToken.entryHint) {
    bankToken.entryHint.textContent = entryHintText();
  }
  if (bankToken.sedLine) {
    bankToken.sedLine.textContent = sedLineText();
  }
}

function entryHintText() {
  const own = tokenVar() !== 'MNEMO_TOKEN'
    ? t('common.token.entryHint.own', { var: tokenVar() })
    : '';
  return t('common.token.entryHint.base', { entry: entryName() }) + own;
}

/**
 * The two tabs split by FORM, not by scope, and the labels used to say scope.
 *
 * That was wrong in a way that reliably misled: a project keeping a plain
 * `.mcp.json` needs the literal form, and it was sitting behind a tab labelled
 * "user scope · ~/.claude.json" — which reads as "not about projects". So that
 * person opened "project scope", met `{{MNEMO_HOST}}`, and had no way to know
 * where the braces were supposed to come from.
 *
 * There is really one question here: does this project substitute values from
 * `.mcp.env`, or hold them directly? `SCOPE_HINT` below is what answers it,
 * because the console cannot look at the project and see for itself.
 */
function scopeTabs() {
  return [
    ['literal', t('common.token.scope.literal')],
    ['template', t('common.token.scope.template')],
  ];
}

function scopeHint() {
  return t('common.token.scopeHint');
}

function buildTokenPanel() {
  bankToken.title = el('h2', { text: t('common.token.title') });
  bankToken.body = el('div', { className: 'modal-body' });
  bankToken.regen = el('button', {
    className: 'btn tok-regen',
    text: t('common.token.regen'),
    title: t('common.token.regenTitle'),
    attrs: { 'data-i18n': 'common.token.regen', 'data-i18n-title': 'common.token.regenTitle' },
    on: { click: () => { bankToken.confirming = true; renderTokenPanel(); } },
  });

  const box = el('div', {
    className: 'modal-box is-wide',
    attrs: {
      role: 'dialog', 'aria-modal': 'true', 'aria-label': t('common.token.ariaLabel'),
      'data-i18n-aria': 'common.token.ariaLabel',
    },
    // The overlay closes on click; inside it, a click is just a click.
    on: { click: (ev) => ev.stopPropagation() },
  }, [
    el('div', { className: 'modal-head' }, [
      bankToken.title,
      el('button', {
        className: 'btn btn-ghost',
        text: '✕',
        title: t('common.btn.closeEsc'),
        attrs: { 'data-i18n-title': 'common.btn.closeEsc' },
        on: { click: () => closeTokenPanel() },
      }),
    ]),
    bankToken.body,
    el('div', { className: 'modal-foot' }, [
      bankToken.regen,
      el('button', {
        className: 'btn', text: t('common.btn.close'),
        attrs: { 'data-i18n': 'common.btn.close' },
        on: { click: () => closeTokenPanel() },
      }),
    ]),
  ]);

  bankToken.root = el('div', {
    className: 'modal',
    attrs: { hidden: '' },
    on: { click: () => closeTokenPanel() },
  }, [box]);
  document.body.appendChild(bankToken.root);

  document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape' || bankToken.root.hidden) return;
    // Esc backs out of the confirmation first — it must not be a second way
    // to dismiss a question the user has not answered yet.
    if (bankToken.confirming) {
      bankToken.confirming = false;
      renderTokenPanel();
      return;
    }
    closeTokenPanel();
  });
}

function openTokenPanel(bank) {
  bankToken.bank = bank;
  bankToken.value = null;
  bankToken.revealed = false;
  bankToken.confirming = false;
  bankToken.scope = 'literal';
  bankToken.entry = defaultEntryName(bank.name);
  bankToken.errorText = null;
  bankToken.note = null;
  bankToken.root.hidden = false;
  renderTokenPanel();
  loadBankToken();
}

function closeTokenPanel() {
  bankToken.root.hidden = true;
  // Do not leave the secret sitting in memory behind a closed dialog, nor the
  // rendered panel in the DOM — with no bank, `renderTokenPanel` empties it.
  bankToken.bank = null;
  bankToken.value = null;
  bankToken.revealed = false;
  bankToken.confirming = false;
  renderTokenPanel();
}

function tokenError(message) {
  bankToken.errorText = message || null;
}

async function loadBankToken() {
  const bank = bankToken.bank;
  if (!bank) return;
  bankToken.busy = true;
  renderTokenPanel();
  try {
    const data = await api('/api/banks/' + encodeURIComponent(bank.id) + '/token');
    // The panel can be closed or reopened for another bank while this is in
    // flight; a late answer must not paint someone else's token.
    if (bankToken.bank !== bank) return;
    bankToken.value = data.token || '';
    tokenError(null);
  } catch (err) {
    if (isAuthError(err)) { closeTokenPanel(); reportError(err); return; }
    // Fixable in place — it belongs in the dialog, not in the page-wide banner.
    tokenError(err.message);
  } finally {
    bankToken.busy = false;
    renderTokenPanel();
  }
}

async function regenerateBankToken() {
  const bank = bankToken.bank;
  if (!bank || bankToken.busy) return;
  bankToken.busy = true;
  renderTokenPanel();
  try {
    const data = await api('/api/banks/' + encodeURIComponent(bank.id) + '/token',
                           { method: 'POST' });
    if (bankToken.bank !== bank) return;
    bankToken.value = data.token || '';
    // A fresh secret is masked like any other: the copy buttons already carry
    // the new value, so putting it on screen stays a deliberate act.
    bankToken.revealed = false;
    bankToken.confirming = false;
    bankToken.note = t('common.token.regeneratedNote');
    tokenError(null);
  } catch (err) {
    if (isAuthError(err)) { closeTokenPanel(); reportError(err); return; }
    tokenError(err.message);
  } finally {
    bankToken.busy = false;
    renderTokenPanel();
  }
}

function renderTokenPanel() {
  const bank = bankToken.bank;
  const body = bankToken.body;
  clear(body);
  // The nodes `refreshSnippets` writes into are about to be replaced.
  bankToken.blocks = [];
  bankToken.entryHint = null;
  bankToken.sedLine = null;
  if (!bank) return;

  bankToken.title.textContent = t('common.token.titleFor', { name: bank.name });
  bankToken.regen.disabled = bankToken.busy || bankToken.confirming ||
                             bankToken.value == null;

  const ready = bankToken.value != null;

  const field = el('input', {
    className: 'fs-input tok-value',
    attrs: {
      id: 'tok-value', name: 'tok-value', type: 'text',
      readonly: '', spellcheck: 'false', autocomplete: 'off',
    },
  });
  field.value = shownToken();

  body.appendChild(el('label', {
    className: 'fs-label',
    text: t('common.token.bankTokenLabel'),
    attrs: { for: 'tok-value' },
  }));
  body.appendChild(el('div', { className: 'tok-row' }, [
    field,
    el('button', {
      className: 'btn',
      text: bankToken.revealed ? t('common.token.hide') : t('common.token.show'),
      title: bankToken.revealed ? t('common.token.hideTitle') : t('common.token.showTitle'),
      attrs: { 'aria-pressed': bankToken.revealed ? 'true' : 'false' },
      on: { click: () => { bankToken.revealed = !bankToken.revealed; renderTokenPanel(); } },
    }),
    copyButton(() => bankToken.value, t('common.token.copyTokenTitle')),
  ]));
  for (const button of body.lastChild.querySelectorAll('button')) button.disabled = !ready;

  body.appendChild(el('p', {
    className: 'tok-note',
    text: t('common.token.scopeNote', { name: bank.name }),
  }));

  // The URL no longer carries the bank, so two mnemo entries side by side
  // differ only by an opaque token — the name is what a person reads.
  const entry = el('input', {
    className: 'fs-input',
    attrs: {
      id: 'tok-entry', name: 'tok-entry', type: 'text',
      spellcheck: 'false', autocomplete: 'off', placeholder: 'mnemo',
    },
    on: {
      input: () => {
        const clean = sanitizeEntryName(entry.value);
        if (clean !== entry.value) {
          // Keep the caret where the typing left it, minus whatever the clean
          // removed ahead of it — otherwise a collapse throws it to the end.
          const at = Math.max(0, entry.selectionStart - (entry.value.length - clean.length));
          entry.value = clean;
          entry.setSelectionRange(at, at);
        }
        bankToken.entry = clean;
        refreshSnippets();
      },
    },
  });
  entry.value = bankToken.entry;

  body.appendChild(el('label', {
    className: 'fs-label',
    text: t('common.token.entryLabel'),
    attrs: { for: 'tok-entry' },
  }));
  body.appendChild(entry);
  bankToken.entryHint = el('p', { className: 'tok-note', text: entryHintText() });
  body.appendChild(bankToken.entryHint);

  const tabs = el('div', { className: 'segmented tok-tabs' });
  for (const [scope, label] of scopeTabs()) {
    tabs.appendChild(el('button', {
      className: 'seg' + (bankToken.scope === scope ? ' is-active' : ''),
      text: label,
      on: { click: () => { bankToken.scope = scope; renderTokenPanel(); } },
    }));
  }
  body.appendChild(tabs);
  body.appendChild(el('p', { className: 'tok-note', text: scopeHint() }));

  if (bankToken.scope === 'template') body.appendChild(templateLeadNote());

  for (const spec of tokenSnippets()) {
    const copy = copyButton(() => spec.build(bankToken.value), t('common.token.copyToClipboard'));
    copy.disabled = spec.secret && !ready;
    const pre = el('pre', { className: 'tok-code', text: spec.build(shownToken()) });
    body.appendChild(el('div', { className: 'tok-caption' }, [
      el('span', { text: spec.caption }),
      copy,
    ]));
    body.appendChild(pre);
    bankToken.blocks.push({ spec: spec, pre: pre });
  }

  if (bankToken.scope === 'template') {
    body.appendChild(el('p', {
      className: 'tok-note',
      text: t('common.token.generatedFileNote'),
    }));
    body.appendChild(manualPasteNote());
  }

  if (bankToken.confirming) {
    // The question lands at the bottom of a body that already scrolls, so on a
    // short window it would open below the fold — the button would look dead.
    const confirm = el('div', { className: 'tok-confirm' }, [
      el('p', {
        className: 'tok-confirm-text',
        text: t('common.token.regenConfirm', { name: bank.name }),
      }),
      el('div', { className: 'tok-confirm-row' }, [
        el('button', {
          className: 'btn',
          text: t('common.btn.cancel'),
          on: { click: () => { bankToken.confirming = false; renderTokenPanel(); } },
        }),
        el('button', {
          className: 'btn btn-danger',
          text: t('common.token.regenYes'),
          on: { click: () => regenerateBankToken() },
        }),
      ]),
    ]);
    body.appendChild(confirm);
    confirm.scrollIntoView({ block: 'nearest' });
  }

  if (bankToken.note) {
    body.appendChild(el('p', { className: 'tok-ok', text: bankToken.note }));
  }
  if (bankToken.errorText) {
    body.appendChild(el('p', { className: 'modal-error', text: bankToken.errorText }));
  }
}

// ---------------------------------------------------------------------------
// per-bank menu
//
// Built once and moved, rather than rebuilt per card: the bank list re-renders
// on a timer, and a menu owned by a card would vanish mid-click.
// ---------------------------------------------------------------------------

const bankMenu = { root: null, bank: null };

function buildBankMenu() {
  // `bankMenu.bank` is read at click time, not captured when the menu is
  // built: one menu serves every card, and which bank it belongs to is
  // whatever `openBankMenu` last pointed it at.
  const item = (opts) => el('button', {
    className: 'menu-item' + (opts.danger ? ' is-danger' : ''),
    text: opts.text,
    title: opts.title,
    // The state entries are a choice among three, not three commands, so they
    // announce as radios and carry `aria-checked` (set in `openBankMenu`).
    // `key`/`titleKey` are added as `data-i18n`/`data-i18n-title` so
    // `applyStaticI18n()` keeps this build-once menu correct after a language
    // switch, same as every other build-once dialog node in this file —
    // including the three state items below, whose text/title come from
    // page-memory.js's `bankStateLabel()`/`bankStateNote()`.
    attrs: Object.assign(
      { role: opts.role || 'menuitem' },
      opts.key ? { 'data-i18n': opts.key } : null,
      opts.titleKey ? { 'data-i18n-title': opts.titleKey } : null,
    ),
    on: {
      click: () => {
        const bank = bankMenu.bank;
        closeBankMenu();
        if (bank) opts.run(bank);
      },
    },
  });

  bankMenu.root = el('div', {
    className: 'menu',
    attrs: { hidden: '', role: 'menu' },
    on: { click: (ev) => ev.stopPropagation() },
  }, [
    item({
      text: t('common.bankMenu.sync'), key: 'common.bankMenu.sync',
      title: t('common.bankMenu.syncTitle'), titleKey: 'common.bankMenu.syncTitle',
      run: (bank) => reindex(bank, { full: false }),
    }),
    item({
      text: t('common.bankMenu.rebuild'), key: 'common.bankMenu.rebuild',
      title: t('common.bankMenu.rebuildTitle'), titleKey: 'common.bankMenu.rebuildTitle',
      run: (bank) => reindex(bank, { full: true }),
    }),
    el('div', { className: 'menu-sep' }),
    item({
      text: t('common.token.title'), key: 'common.token.title',
      title: t('common.bankMenu.mcpTitle'), titleKey: 'common.bankMenu.mcpTitle',
      run: (bank) => openTokenPanel(bank),
    }),
    el('div', { className: 'menu-sep' }),
    el('div', {
      className: 'menu-label', text: t('common.bankMenu.stateLabel'),
      attrs: { 'data-i18n': 'common.bankMenu.stateLabel' },
    }),
    // Not a submenu and not a dialog: three states are few enough to show, and
    // the current one has to be visible at the moment of choosing — otherwise
    // "freeze" on an already-frozen bank looks like it did nothing. The marks
    // are refreshed in `openBankMenu`, because one menu serves every card.
    ...['enabled', 'frozen', 'disabled'].map((value) => {
      const button = item({
        text: bankStateLabel(value), key: 'memory.bankState.' + value + '.label',
        title: bankStateNote(value), titleKey: 'memory.bankState.' + value + '.note',
        role: 'menuitemradio',
        run: (bank) => setBankState(bank, value),
      });
      button.dataset.state = value;
      return button;
    }),
    el('div', { className: 'menu-sep' }),
    item({
      text: t('common.bankMenu.remove'), key: 'common.bankMenu.remove',
      title: t('common.bankMenu.removeTitle'), titleKey: 'common.bankMenu.removeTitle',
      danger: true,
      run: (bank) => openRemoval(bank),
    }),
  ]);
  document.body.appendChild(bankMenu.root);

  document.addEventListener('click', () => closeBankMenu());
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') closeBankMenu();
  });
  // A menu pinned to page coordinates does not follow its button. Rather
  // than track the anchor, close: a menu that has drifted off its trigger is
  // a menu whose next click lands on whatever moved underneath it.
  window.addEventListener('scroll', () => closeBankMenu(), true);
  window.addEventListener('resize', () => closeBankMenu());
}

function openBankMenu(anchor, bank) {
  bankMenu.bank = bank;
  // The state marks belong to the bank being opened, not to the one the menu
  // last served — one menu, many cards.
  const current = bankState(bank);
  for (const button of bankMenu.root.querySelectorAll('[data-state]')) {
    const active = button.dataset.state === current;
    button.classList.toggle('is-current', active);
    button.setAttribute('aria-checked', active ? 'true' : 'false');
  }
  // Laid out before it is shown: a `position: fixed` box with no coordinates
  // paints wherever it happens to flow, so measuring it visible costs one
  // frame of the menu sitting in the corner of the page.
  bankMenu.root.style.visibility = 'hidden';
  bankMenu.root.hidden = false;
  const box = anchor.getBoundingClientRect();
  const menu = bankMenu.root.getBoundingClientRect();
  // Flip up, and pull left, when the default placement would leave the
  // viewport. The bank column is at the left edge and the cards run to the
  // bottom of a long list, so both edges are reachable in normal use.
  const top = (box.bottom + menu.height > window.innerHeight)
    ? box.top - menu.height - 4
    : box.bottom + 4;
  // Right edges aligned, because the button sits at the right end of the
  // title row: hanging the menu off its left edge would throw it across the
  // pane boundary into the file tree for no reason.
  const left = Math.max(4, Math.min(box.right - menu.width,
                                    window.innerWidth - menu.width - 4));
  bankMenu.root.style.top = Math.max(4, top) + 'px';
  bankMenu.root.style.left = left + 'px';
  bankMenu.root.style.visibility = 'visible';
}

function closeBankMenu() {
  if (!bankMenu.root || bankMenu.root.hidden) return;
  bankMenu.root.hidden = true;
  bankMenu.bank = null;
}

// ---------------------------------------------------------------------------
// removing a bank (contract 9.5: DELETE /api/banks/{id})
//
// The only irreversible action in the console, and what makes it irreversible
// is not the index — that rebuilds — but the token. Bank ids are derived from
// the root and come back identical on re-registration; tokens are minted, so a
// removed bank cannot be restored to the projects that address it. That is why
// this asks for the name to be typed, and why the dialog leads with the token
// rather than with megabytes.
// ---------------------------------------------------------------------------

const removal = {
  root: null, box: null, body: null, submit: null,
  bank: null, dropIndex: true, typed: '', busy: false, errorText: null,
  // MCP-wiring checkbox (MN-13, contract: GET /api/banks/{id}/mcp-wiring).
  // `projectRoot` is the cheap local check (mirrors `_project_root_from_bank`
  // in api.py — bank.root must end in .claude/memory); `wiring` holds the
  // backend's `{has_wiring, uses_template, project_root}` once resolved, or
  // stays null for a bank that isn't project-shaped at all (no round trip
  // needed, no checkbox to show).
  projectRoot: null, wiring: null, stripMcp: false,
};

function buildRemoval() {
  removal.body = el('div', { className: 'modal-body' });
  removal.submit = el('button', {
    className: 'btn btn-danger',
    text: t('common.removal.submit'),
    on: { click: () => submitRemoval() },
  });

  removal.box = el('div', {
    className: 'modal-box',
    attrs: {
      role: 'dialog', 'aria-modal': 'true', 'aria-label': t('common.removal.ariaLabel'),
      'data-i18n-aria': 'common.removal.ariaLabel',
    },
    on: { click: (ev) => ev.stopPropagation() },
  }, [
    el('div', { className: 'modal-head' }, [
      el('h2', {
        text: t('common.removal.title'), attrs: { 'data-i18n': 'common.removal.title' },
      }),
      el('button', {
        className: 'btn btn-ghost',
        text: '✕',
        title: t('common.btn.closeEsc'),
        attrs: { 'data-i18n-title': 'common.btn.closeEsc' },
        on: { click: () => closeRemoval() },
      }),
    ]),
    removal.body,
    el('div', { className: 'modal-foot' }, [
      el('button', {
        className: 'btn', text: t('common.btn.cancel'),
        attrs: { 'data-i18n': 'common.btn.cancel' },
        on: { click: () => closeRemoval() },
      }),
      removal.submit,
    ]),
  ]);

  removal.root = el('div', {
    className: 'modal',
    attrs: { hidden: '' },
    on: { click: () => closeRemoval() },
  }, [removal.box]);
  document.body.appendChild(removal.root);

  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && !removal.root.hidden) closeRemoval();
  });
}

async function openRemoval(bank) {
  removal.bank = bank;
  removal.dropIndex = true;
  removal.typed = '';
  removal.busy = false;
  removal.errorText = null;
  removal.stripMcp = false;
  removal.wiring = null;
  removal.projectRoot = projectRootForBankPath(bank.root);
  if (removal.projectRoot) {
    // Resolved before the dialog is shown, not after: the checkbox's
    // enabled/disabled state has to be correct the moment it becomes
    // visible, never flash from one to the other once the user can see it.
    try {
      removal.wiring = await api('/api/banks/' + encodeURIComponent(bank.id) + '/mcp-wiring');
    } catch (err) {
      // A lookup that failed is treated the same as "no wiring found" —
      // the checkbox stays disabled rather than offer to strip something
      // that was never confirmed to exist.
      removal.wiring = { has_wiring: false, uses_template: false,
                          project_root: removal.projectRoot };
    }
    // The menu may have opened a different bank (or the dialog may have
    // been closed) while this was in flight; a stale response must not
    // repaint over whatever is current now.
    if (removal.bank !== bank) return;
    // Same default posture as "also delete the index": when wiring is
    // actually found, offer to take it out too rather than leave a dead
    // .mcp.json pointed at a bank that no longer exists.
    removal.stripMcp = removal.wiring.has_wiring;
  }
  removal.root.hidden = false;
  renderRemoval();
}

function closeRemoval() {
  if (removal.busy) return;      // a request is in flight; let it land
  removal.root.hidden = true;
  removal.bank = null;
  removal.typed = '';
}

function renderRemoval() {
  const bank = removal.bank;
  if (!bank) return;
  clear(removal.body);

  removal.body.appendChild(el('p', { className: 'rm-lead' }, [
    document.createTextNode(t('common.removal.leadPrefix')),
    el('strong', { text: bank.name }),
    document.createTextNode(t('common.removal.leadSuffix')),
  ]));

  removal.body.appendChild(el('dl', { className: 'rm-effects' }, [
    el('dt', { className: 'is-loss', text: t('common.removal.goneForever') }),
    el('dd', { text: t('common.removal.goneForeverText') }),
    el('dt', { className: 'is-safe', text: t('common.removal.untouched') }),
    el('dd', null, [
      document.createTextNode(t('common.removal.untouchedPrefix')),
      el('code', { text: bank.root }),
      document.createTextNode(t('common.removal.untouchedSuffix')),
    ]),
  ]));

  const box = el('input', {
    attrs: { type: 'checkbox', id: 'rm-drop-index' },
    on: { change: (ev) => { removal.dropIndex = ev.target.checked; } },
  });
  box.checked = removal.dropIndex;
  box.disabled = removal.busy;
  removal.body.appendChild(el('label', { className: 'rm-check', attrs: { for: 'rm-drop-index' } }, [
    box,
    el('span', {
      text: t('common.removal.dropIndex', { bytes: fmtBytes(bank.db_bytes) }),
    }),
  ]));

  if (removal.projectRoot) {
    const hasWiring = !!(removal.wiring && removal.wiring.has_wiring);
    const mcpBox = el('input', {
      attrs: { type: 'checkbox', id: 'rm-strip-mcp' },
      on: { change: (ev) => { removal.stripMcp = ev.target.checked; } },
    });
    mcpBox.checked = removal.stripMcp;
    mcpBox.disabled = removal.busy || !hasWiring;
    removal.body.appendChild(el('label', { className: 'rm-check', attrs: { for: 'rm-strip-mcp' } }, [
      mcpBox,
      el('span', null, [
        document.createTextNode(t('common.removal.stripMcpPrefix')),
        el('code', { text: removal.projectRoot }),
        document.createTextNode(')'),
      ]),
    ]));
    if (!hasWiring) {
      removal.body.appendChild(el('p', {
        className: 'fs-hint fs-warn',
        text: t('common.removal.noMcpJson'),
      }));
    }
  }

  const confirm = el('input', {
    className: 'fs-input',
    attrs: {
      type: 'text', spellcheck: 'false', autocomplete: 'off',
      id: 'rm-confirm', placeholder: bank.name,
    },
    on: {
      input: (ev) => {
        removal.typed = ev.target.value;
        // Only the button's own state changes, so it is updated in place: a
        // re-render would rebuild the field and drop the caret.
        removal.submit.disabled = !removalReady();
      },
      keydown: (ev) => {
        if (ev.key === 'Enter' && removalReady()) { ev.preventDefault(); submitRemoval(); }
      },
    },
  });
  confirm.value = removal.typed;
  confirm.disabled = removal.busy;
  removal.body.appendChild(el('label', {
    className: 'fs-label', attrs: { for: 'rm-confirm' },
    text: t('common.removal.confirmLabel'),
  }));
  removal.body.appendChild(confirm);

  if (removal.errorText) {
    removal.body.appendChild(el('p', { className: 'modal-error', text: removal.errorText }));
  }

  removal.submit.disabled = !removalReady();
  removal.submit.textContent = removal.busy ? t('common.removal.busy') : t('common.removal.submit');
  if (!removal.busy) confirm.focus();
}

function removalReady() {
  return !removal.busy && !!removal.bank && removal.typed.trim() === removal.bank.name;
}

async function submitRemoval() {
  if (!removalReady()) return;
  const bank = removal.bank;
  const stripMcp = removal.stripMcp;
  removal.busy = true;
  removal.errorText = null;
  renderRemoval();
  let result;
  try {
    result = await api('/api/banks/' + encodeURIComponent(bank.id) +
              '?drop_index=' + (removal.dropIndex ? 'true' : 'false') +
              (stripMcp ? '&strip_mcp=true' : ''),
              { method: 'DELETE' });
  } catch (err) {
    removal.busy = false;
    if (isAuthError(err)) { closeRemoval(); reportError(err); return; }
    // `index_locked` is fixable where it is raised — the bank is still
    // registered and nothing was lost — so it belongs inside the dialog
    // rather than on a page-wide banner the user has to leave to read.
    removal.errorText = err.message;
    renderRemoval();
    return;
  }
  removal.busy = false;
  removal.root.hidden = true;
  removal.bank = null;
  removal.typed = '';
  hideBanner();
  // No toast mechanism exists for a removal that just closed its own dialog
  // and dropped the card — same quiet-success weight as the rest of this
  // flow, just recorded for anyone checking what actually got touched.
  if (stripMcp && result && result.mcp_stripped) {
    console.info('mnemo: MCP wiring stripped from', result.mcp_stripped.join(', ') || '(nothing to strip)');
  }
  // Drop it from the local model immediately rather than waiting for the
  // `bank_removed` event to come back: the socket may be down, and a card
  // that outlives the bank it describes is the one thing this dialog must
  // not leave behind. The reload that follows is the correction, not the
  // mechanism.
  state.banks = state.banks.filter((b) => b.id !== bank.id);
  state.progress.delete(bank.id);
  state.notes.delete(bank.id);
  // Through selectBank(), not a direct field clear: it is also what repaints
  // the tree/file panel, which must not keep showing a bank that no longer
  // exists.
  if (state.selectedBankId === bank.id) selectBank(null);
  renderBanks();
  loadBanks().catch(() => {});
}

// ---------------------------------------------------------------------------
// wiring
// ---------------------------------------------------------------------------

/** First load of everything, then the live channel. Re-runnable after the gate. */
async function start() {
  // This IS a full REST load, so the `hello` that follows has nothing to heal.
  helloSeen = false;
  try {
    await loadBanks();
    await loadStatus();
    await loadLogs();
    hideBanner();
  } catch (err) {
    if (isAuthError(err)) {
      // The one request it takes to learn the token is stale; nothing follows.
      openGate('rejected');
      return;
    }
    reportError(err);
    // Paint the empty states anyway — a blank pane next to an error banner
    // reads as a broken page rather than an unreachable backend.
    renderBanks();
    renderTree();
    renderFile();
    renderJournal();
  }
  connectSocket();
  // Not part of the try/catch above: a self-update check failing must not
  // block the rest of the console, and it has its own error handling (it
  // simply stays silent — see update.js).
  refreshUpdateStatus();
}

async function boot() {
  applyTheme(resolveTheme());
  // Static markup is baked English; a stored Ukrainian preference is
  // substituted in as early as possible — before any dialog is built, so
  // build-once dialog chrome (which reads `t()` at construction time) picks
  // up the right language from its very first paint.
  document.documentElement.lang = resolveLang();
  applyStaticI18n();
  buildGate();
  buildPicker();
  buildTokenPanel();
  buildBankMenu();
  buildRemoval();
  buildUpdateModal();
  initShell();
  renderService();
  // `/api` is open by default (no login token, loopback-only — 2026-08-21):
  // no gate to raise just because `token` is empty. `start()`'s own
  // catch-block still routes a genuine 401 to `openGate('rejected')`, which
  // only fires if a token has been deliberately configured server-side.
  await start();
}

// Not called here: `boot()` reaches into every page module (`initShell`,
// `renderBanks`, `loadLogs`, …), so it can only run once all five scripts
// have been parsed. The last one loaded (page-settings.js) calls it.
