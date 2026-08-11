/* mnemo web cabinet (FR-7, v1).
 *
 * Thin client over the v3 HTTP API (contract 9.5) and the WebSocket progress
 * channel (contract 9.7). It renders what the backend reports and nothing
 * else: no chunking, no indexing, no editing. Every derived number on screen
 * comes from a response field.
 */
'use strict';

// ---------------------------------------------------------------------------
// token (contract 9.1)
// ---------------------------------------------------------------------------

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
// state
// ---------------------------------------------------------------------------

const state = {
  banks: [],
  selectedBankId: null,
  tree: null,
  expanded: new Set(),          // expanded dir paths of the current tree
  file: null,                   // last /api/file response
  filePath: null,
  chunkViz: true,
  service: null,
  queue: null,
  progress: new Map(),          // bank_id -> live index_progress snapshot
  notes: new Map(),             // bank_id -> transient one-line note
  logKind: 'query',
  logScope: 'all',
  logRows: [],
  logTotal: 0,
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
    throw new ApiError('unreachable', 'бекенд недоступний: ' + err.message, null, 0);
  }

  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (err) {
      throw new ApiError('internal', 'невалідний JSON у відповіді', text.slice(0, 200),
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
 * "сервіс працює" from behind zero requests would say exactly the same thing
 * with the backend down. It asks for a token and shows where to get one.
 *
 * The rejected state may name the service's behaviour, because there it did
 * make a request and did get a 401 back.
 */
const GATE_COPY = {
  missing: {
    title: 'Потрібен токен доступу',
    text: 'Щоб відкрити кабінет, потрібен токен. ' +
          'Команда друкує посилання з чинним токеном і відкриває його:',
    lead: null,
    note: null,
  },
  rejected: {
    title: 'Токен не підійшов',
    text: 'Сервіс відхилив наданий токен (HTTP 401). Найімовірніше він застарілий ' +
          'або скопійований не повністю — актуальний токен видає сама команда.',
    lead: 'Команда друкує готове посилання з чинним токеном і відкриває його:',
    note: 'Токен відхилено сервісом.',
  },
};

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
      placeholder: '48 шістнадцяткових символів',
    },
  });

  const form = el('form', { className: 'gate-form', on: { submit: submitGate } }, [
    el('label', {
      className: 'gate-label',
      text: 'Або вставте токен вручну:',
      attrs: { for: 'gate-token' },
    }),
    el('div', { className: 'gate-row' }, [
      gate.input,
      el('button', { className: 'btn', text: 'Увійти', attrs: { type: 'submit' } }),
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
  const copy = GATE_COPY[variant] || GATE_COPY.missing;
  state.gated = true;
  closeSocket();
  syncTicker();          // nothing behind the gate needs a running clock
  hideBanner();
  setConnState('idle', 'не автентифіковано');

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

function gateNote(text) {
  gate.note.textContent = text || '';
  gate.note.hidden = !text;
}

async function submitGate(event) {
  event.preventDefault();
  const value = gate.input.value.trim();
  if (!value) {
    gateNote('Введіть токен.');
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

const STATUS_LABEL = { ready: 'готово', indexing: 'індексується', empty: 'порожньо' };

/**
 * Second line under the status badge.
 *
 * Precedence is indexing > empty > ready (lead amendment), and BankInfo always
 * carries both `queued` and `chunks` — so these four readings stay distinct
 * instead of collapsing into one ambiguous "empty".
 */
function statusNote(bank) {
  if (bank.status === 'indexing') {
    return bank.chunks > 0
      ? 'база є, свіжі зміни доїжджають'
      : 'перший білд у процесі — ще порожньо';
  }
  if (bank.status === 'empty') {
    return bank.queued > 0 ? 'порожньо, задачі в черзі' : 'справді порожньо, нічого не заплановано';
  }
  return 'індекс готовий';
}

// ---------------------------------------------------------------------------
// banks
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

function renderService() {
  const box = $('service-info');
  clear(box);
  const svc = state.service;
  if (!svc) {
    box.appendChild(el('span', { className: 'muted', text: 'сервіс невідомий' }));
    return;
  }
  const q = state.queue || { depth: 0, high: 0, normal: 0, low: 0 };
  const bit = (label, value) => el('span', {}, [
    document.createTextNode(label + ' '),
    el('b', { text: value }),
  ]);
  box.appendChild(bit('v', svc.version || '—'));
  box.appendChild(bit('pid', svc.pid != null ? svc.pid : '—'));
  box.appendChild(bit('порт', svc.port != null ? svc.port : '—'));
  box.appendChild(bit('провайдер', svc.provider || '—'));
  box.appendChild(bit('черга', q.depth + ' (H' + q.high + '/N' + q.normal + '/L' + q.low + ')'));
  if (svc.embed) {
    box.appendChild(bit('embed', svc.embed.reachable ? 'ok' : 'недоступний'));
  }
}

function renderBanks() {
  const list = $('banks-list');
  clear(list);

  if (!state.banks.length) {
    list.appendChild(el('p', {
      className: 'empty-hint',
      text: 'Жодного банку не зареєстровано — «＋ додати» у шапці вибирає теку з .md.',
    }));
    syncTicker();
    return;
  }

  for (const bank of state.banks) {
    list.appendChild(bankCard(bank));
  }
  syncTicker();
}

function bankCard(bank) {
  const selected = bank.id === state.selectedBankId;
  const classes = ['bank'];
  if (selected) classes.push('is-selected');
  if (bank.enabled === false) classes.push('is-disabled');

  const badges = [
    el('span', {
      className: 'badge badge-' + bank.status,
      text: STATUS_LABEL[bank.status] || bank.status,
      title: statusNote(bank),
    }),
    bank.git
      ? el('span', { className: 'badge badge-git', text: 'git' })
      : el('span', { className: 'badge badge-nogit', text: 'no git' }),
  ];
  if (bank.enabled === false) {
    badges.push(el('span', { className: 'badge badge-off', text: 'вимкнено' }));
  }
  if (bank.exists === false) {
    badges.push(el('span', { className: 'badge badge-off', text: 'нема кореня' }));
  }

  // Human-facing address is the name, never the hash (lead amendment); the id
  // stays available as a tooltip for debugging.
  //
  // Every action lives in the menu at the end of this row. Buttons used to sit
  // in a row of their own at the bottom of the card, which cost four lines of
  // height per bank and pinned the column's width from both sides — under
  // 287px the row wrapped, over 311px the document ended up narrower than the
  // file list. With nothing but a glyph to fit, the column is free again.
  const head = el('div', { className: 'bank-row' }, [
    el('span', { className: 'bank-name', text: bank.name, title: 'id: ' + bank.id }),
    ...badges,
    el('button', {
      className: 'btn btn-menu',
      text: '···',
      title: 'Дії над банком',
      attrs: { 'aria-haspopup': 'menu', 'aria-label': 'Дії над банком' },
      // Not `stop(...)`: that wrapper calls the handler with no arguments and
      // no `this`, and this one needs the button it fired on to place the menu.
      on: {
        click: (ev) => {
          ev.stopPropagation();
          openBankMenu(ev.currentTarget, bank);
        },
      },
    }),
  ]);

  const stats = el('div', { className: 'bank-stats' }, [
    el('span', { text: 'файлів ' + bank.files }),
    el('span', { text: 'чанків ' + bank.chunks }),
    el('span', { text: 'у черзі ' + bank.queued }),
    el('span', { text: fmtBytes(bank.db_bytes), title: 'розмір індексу' }),
  ]);

  const card = el('div', {
    className: classes.join(' '),
    attrs: { 'data-bank': bank.id },      // so the ticker can find this card
    on: { click: () => selectBank(bank.id) },
  }, [
    head,
    el('span', { className: 'bank-root', text: bank.root }),
    stats,
    el('div', { className: 'bank-stats' }, [
      el('span', { className: 'muted', text: statusNote(bank) }),
    ]),
    el('div', { className: 'bank-stats' }, [
      el('span', { className: 'muted', text: 'востаннє: ' + fmtDateTime(bank.last_indexed) }),
    ]),
  ]);

  const live = state.progress.get(bank.id);
  if (live) card.appendChild(progressBlock(live));

  const note = state.notes.get(bank.id);
  if (note) {
    card.appendChild(el('div', { className: 'progress-text', text: note }));
  }

  if (bank.last_error) {
    card.appendChild(el('div', { className: 'bank-error', text: bank.last_error }));
  }

  return card;
}

// ---------------------------------------------------------------------------
// per-bank menu
//
// Built once and moved, rather than rebuilt per card: the bank list re-renders
// on a timer, and a menu owned by a card would vanish mid-click.

const bankMenu = { root: null, bank: null };

function buildBankMenu() {
  // `bankMenu.bank` is read at click time, not captured when the menu is
  // built: one menu serves every card, and which bank it belongs to is
  // whatever `openBankMenu` last pointed it at.
  const item = (opts) => el('button', {
    className: 'menu-item' + (opts.danger ? ' is-danger' : ''),
    text: opts.text,
    title: opts.title,
    attrs: { role: 'menuitem' },
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
      text: 'Синхронізація індексу',
      title: 'Переіндексує лише файли, що змінилися, і знімає з індексу видалені',
      run: (bank) => reindex(bank, { full: false }),
    }),
    item({
      text: 'Повний реіндекс',
      title: 'Стирає індекс і збирає його заново — довго, пропорційно розміру банку',
      run: (bank) => reindex(bank, { full: true }),
    }),
    el('div', { className: 'menu-sep' }),
    item({
      text: 'Доступ MCP',
      title: 'Токен цього банку і готовий фрагмент конфігурації для проєкту',
      run: (bank) => openTokenPanel(bank),
    }),
    el('div', { className: 'menu-sep' }),
    item({
      text: 'Прибрати банк',
      title: 'Зняти банк з реєстру; .md не чіпаються',
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
// The only irreversible action in the cabinet, and what makes it irreversible
// is not the index — that rebuilds — but the token. Bank ids are derived from
// the root and come back identical on re-registration; tokens are minted, so a
// removed bank cannot be restored to the projects that address it. That is why
// this asks for the name to be typed, and why the dialog leads with the token
// rather than with megabytes.

const removal = {
  root: null, box: null, body: null, submit: null,
  bank: null, dropIndex: true, typed: '', busy: false, errorText: null,
};

function buildRemoval() {
  removal.body = el('div', { className: 'modal-body' });
  removal.submit = el('button', {
    className: 'btn btn-danger',
    text: 'Прибрати',
    on: { click: () => submitRemoval() },
  });

  removal.box = el('div', {
    className: 'modal-box',
    attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Прибрати банк' },
    on: { click: (ev) => ev.stopPropagation() },
  }, [
    el('div', { className: 'modal-head' }, [
      el('h2', { text: 'Прибрати банк' }),
      el('button', {
        className: 'btn btn-ghost',
        text: '✕',
        title: 'Закрити (Esc)',
        on: { click: () => closeRemoval() },
      }),
    ]),
    removal.body,
    el('div', { className: 'modal-foot' }, [
      el('button', { className: 'btn', text: 'Скасувати', on: { click: () => closeRemoval() } }),
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

function openRemoval(bank) {
  removal.bank = bank;
  removal.dropIndex = true;
  removal.typed = '';
  removal.busy = false;
  removal.errorText = null;
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
    document.createTextNode('Банк '),
    el('strong', { text: bank.name }),
    document.createTextNode(' перестане існувати для цієї машини.'),
  ]));

  removal.body.appendChild(el('dl', { className: 'rm-effects' }, [
    el('dt', { className: 'is-loss', text: 'Зникає назавжди' }),
    el('dd', {
      text: 'Реєстрація банку та його токен. Токен видається випадково і не ' +
            'відтворюється: кожен .mcp.json, який ним підключається, ' +
            'перестане працювати, і повернути той самий токен неможливо.',
    }),
    el('dt', { className: 'is-safe', text: 'Лишається недоторканим' }),
    el('dd', null, [
      document.createTextNode('Усі .md за шляхом '),
      el('code', { text: bank.root }),
      document.createTextNode('. Кабінет не видаляє вміст банку — тільки те, ' +
                              'що з нього виведено.'),
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
      text: 'видалити також індекс (' + fmtBytes(bank.db_bytes) + ') — ' +
            'відновлюваний повним реіндексом',
    }),
  ]));

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
    text: 'Введіть назву банку, щоб підтвердити',
  }));
  removal.body.appendChild(confirm);

  if (removal.errorText) {
    removal.body.appendChild(el('p', { className: 'modal-error', text: removal.errorText }));
  }

  removal.submit.disabled = !removalReady();
  removal.submit.textContent = removal.busy ? 'Прибираю…' : 'Прибрати';
  if (!removal.busy) confirm.focus();
}

function removalReady() {
  return !removal.busy && !!removal.bank && removal.typed.trim() === removal.bank.name;
}

async function submitRemoval() {
  if (!removalReady()) return;
  const bank = removal.bank;
  removal.busy = true;
  removal.errorText = null;
  renderRemoval();
  try {
    await api('/api/banks/' + encodeURIComponent(bank.id) +
              '?drop_index=' + (removal.dropIndex ? 'true' : 'false'),
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
  // Drop it from the local model immediately rather than waiting for the
  // `bank_removed` event to come back: the socket may be down, and a card
  // that outlives the bank it describes is the one thing this dialog must
  // not leave behind. The reload that follows is the correction, not the
  // mechanism.
  state.banks = state.banks.filter((b) => b.id !== bank.id);
  if (state.selectedBankId === bank.id) state.selectedBankId = null;
  state.progress.delete(bank.id);
  state.notes.delete(bank.id);
  renderBanks();
  loadBanks().catch(() => {});
}

// A task is not always a file: `bulk`/`rebuild` work on the whole bank and
// carry no path at all, so they get named rather than left as a blank slot.
// These read the same as the buttons that queue them — a user watching the
// progress bar should recognise the thing they just clicked.
const TASK_KIND_LABEL = {
  file: 'файл',
  bulk: 'синхронізація індексу',
  rebuild: 'повний реіндекс',
  prune: 'зняття з індексу',
};

function fmtDuration(seconds) {
  if (seconds < 60) return seconds + ' с';
  return Math.floor(seconds / 60) + ' хв ' + String(seconds % 60).padStart(2, '0') + ' с';
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
  const parts = [TASK_KIND_LABEL[live.kind] || live.kind || 'задача'];
  if (live.path) parts.push(live.path);
  if (live.batches > 0) parts.push('батч ' + live.batch + '/' + live.batches);
  if (live.chunks_total) parts.push(live.chunks_done + '/' + live.chunks_total + ' чанків');
  const age = elapsedLabel(live);
  if (age) parts.push(age);
  if (live.yielded) parts.push('витіснено');
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
        ? 'час відколи кабінет побачив цю задачу — вона почалася раніше'
        : 'час від початку задачі',
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
// reindex (contract 9.5 POST /api/reindex)
// ---------------------------------------------------------------------------

async function reindex(bank, opts) {
  const body = { bank: bank.name, path: opts.path || null, full: !!opts.full };
  try {
    const res = await api('/api/reindex', { method: 'POST', body: body });
    hideBanner();
    const what = opts.path
      ? opts.path
      : TASK_KIND_LABEL[opts.full ? 'rebuild' : 'bulk'];
    setNote(bank.id, 'поставлено: ' + what + ' · у черзі ' + res.queued +
                     ' · task ' + (res.task_ids || []).join(', '));
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
      type: 'text', spellcheck: 'false', placeholder: 'або вставте шлях',
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
      type: 'text', spellcheck: 'false', placeholder: 'вгадається з назви теки',
      id: 'fs-bank-name',
    },
    on: {
      keydown: (ev) => {
        if (ev.key === 'Enter') { ev.preventDefault(); pickerSubmit(); }
      },
    },
  });
  picker.error = el('p', { className: 'modal-error', attrs: { hidden: '' } });
  picker.submit = el('button', {
    className: 'btn btn-primary',
    text: 'Додати цю теку',
    on: { click: () => pickerSubmit() },
  });

  const box = el('div', {
    className: 'modal-box',
    attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Додати банк' },
    // The overlay closes on click; inside it, a click is just a click.
    on: { click: (ev) => ev.stopPropagation() },
  }, [
    el('div', { className: 'modal-head' }, [
      el('h2', { text: 'Додати банк' }),
      el('button', {
        className: 'btn btn-ghost',
        text: '✕',
        title: 'Закрити (Esc)',
        on: { click: () => closePicker() },
      }),
    ]),
    el('div', { className: 'modal-body' }, [
      el('label', {
        className: 'fs-label',
        text: 'Тека з .md — вона стане коренем банку',
        attrs: { for: 'fs-path' },
      }),
      picker.roots,
      picker.input,
      picker.list,
      picker.hint,
      el('label', {
        className: 'fs-label',
        text: 'Назва банку (необов’язково)',
        attrs: { for: 'fs-bank-name' },
      }),
      picker.name,
      picker.error,
    ]),
    el('div', { className: 'modal-foot' }, [
      el('button', { className: 'btn', text: 'Скасувати', on: { click: () => closePicker() } }),
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
  // Resume where the last look around ended — adding two banks from one folder
  // should not mean walking down from home twice.
  pickerGo(sessionStorage.getItem(LAST_DIR_KEY) || null);
  picker.input.focus();
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
      text: '⌂ дім',
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
    picker.list.appendChild(el('p', { className: 'muted', text: 'читаю…' }));
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
        title: entry.registered ? 'уже банк: ' + entry.registered : entry.path,
        on: { click: () => pickerGo(entry.path) },
      }, [
        el('span', { className: 'fs-name', text: entry.name }),
        entry.registered
          ? el('span', { className: 'badge badge-git', text: 'банк' })
          : null,
      ]));
    }
    if (!(data.entries || []).length) {
      picker.list.appendChild(el('p', { className: 'muted', text: 'жодної підтеки' }));
    }
    if (data.truncated) {
      picker.list.appendChild(el('p', {
        className: 'muted',
        text: 'показано перші ' + data.entries.length + ' тек — решту вставте шляхом',
      }));
    }
  }

  clear(picker.hint);
  if (data) {
    // Nothing here is a veto, only a warning: a folder can be registered while
    // still empty, and the watcher will index the .md that appear later.
    const count = data.md_capped ? '≥' + data.md : String(data.md);
    // "(з підтеками)" is a claim about recursion, so it may only appear when
    // there are subfolders to recurse into: on a flat folder it reads as a
    // promise about something that is not there.
    const nested = (data.entries || []).length ? ' (з підтеками)' : '';
    picker.hint.appendChild(el('span', {
      className: data.md ? '' : 'fs-warn',
      text: data.md
        ? 'у цій теці ' + count + ' .md' + nested
        : 'у цій теці немає .md — індексувати буде нічого',
      title: data.md_capped
        ? 'рахунок обірвано за часом — файлів щонайменше стільки, ' +
          'індексуватися будуть усі'
        : 'без .git, .venv, node_modules — так само, як їх пропускає індексатор',
    }));
    if (data.registered) {
      picker.hint.appendChild(el('span', {
        className: 'fs-warn',
        text: ' · уже зареєстрована як «' + data.registered + '»',
      }));
    }
  }

  picker.submit.disabled = picker.busy || !data || !!data.registered;
  picker.submit.textContent = picker.busy ? 'читаю…' : 'Додати цю теку';
}

async function pickerSubmit() {
  if (!picker.path || picker.busy) return;
  const body = { root: picker.path, name: picker.name.value.trim() || null };
  picker.busy = true;
  renderPicker();
  try {
    const info = await api('/api/banks', { method: 'POST', body: body });
    closePicker();
    hideBanner();
    state.banks = state.banks.concat([info]);
    renderBanks();
    // The bank was registered *and* queued in one call, so say both — and open
    // it, so the first build is visible instead of happening off-screen.
    setNote(info.id, 'банк додано · індексація стала в чергу');
    selectBank(info.id);
    loadBanks().catch(() => {});
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
 * renders through `shownToken()`, which is bullets until «показати» is pressed,
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
  scope: 'user',       // which config shape is on screen
  entry: 'mnemo',      // the name the config entry will carry
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
 * own URL is the fallback, because a cabinet answering at :8919 was plainly
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
    text: 'копіювати',
    title: title,
    on: { click: () => copyInto(button, get()) },
  });
  return button;
}

async function copyInto(button, text) {
  if (text == null) return;
  if (!(await copyText(text))) {
    tokenError('Не вдалося скопіювати — виділіть текст і скопіюйте вручну.');
    renderTokenPanel();
    return;
  }
  const was = button.textContent;
  button.textContent = 'скопійовано';
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
  if (!slug) return 'mnemo';
  // A bank already called "mnemo…" must not come out as "mnemo-mnemo…".
  return slug.startsWith('mnemo') ? slug : 'mnemo-' + slug;
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
  return bankToken.entry || 'mnemo';
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
  if (bankToken.scope === 'user') {
    return [{
      caption: 'Для ~/.claude.json — злити з наявним «mcpServers»',
      secret: true,
      build: (t) => mcpDocument('http://' + serviceHost() + ':' + servicePort() +
                                '/mcp?token=' + t),
    }];
  }
  return [
    {
      caption: 'Для .mcp.json.template — злити з наявним «mcpServers»',
      secret: false,
      build: () => mcpDocument('http://{{MNEMO_HOST}}:{{MNEMO_PORT}}' +
                               '/mcp?token={{MNEMO_TOKEN}}'),
    },
    {
      caption: 'Рядки для .mcp.env',
      secret: true,
      build: (t) => 'MNEMO_HOST=' + serviceHost() + '\n' +
                    'MNEMO_PORT=' + servicePort() + '\n' +
                    'MNEMO_TOKEN=' + t,
    },
  ];
}

/**
 * Project scope leads with the command, not with the paste.
 *
 * `mnemo init` writes all three pieces — the fragment into the template, the
 * variables into `.mcp.env`, and the substitution lines into `mcp-setup.sh` —
 * so presenting the manual procedure as the main path would be teaching
 * something strictly worse than a command that already exists. The snippets
 * stay below it because seeing what will land is worth having, and because
 * they are the way out when `init` cannot be run in that project.
 */
function projectLeadNote() {
  return el('p', { className: 'tok-lead' }, [
    document.createTextNode('У проєкті це робить '),
    el('code', { text: 'mnemo init' }),
    document.createTextNode(' — він сам вписує фрагмент у .mcp.json.template, ' +
      'змінні у .mcp.env і рядки підстановки в mcp-setup.sh. Нижче — те саме, ' +
      'що він запише: щоб побачити наперед або вписати руками, якщо запустити ' +
      'init у цьому проєкті не можна.'),
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
  return el('p', { className: 'tok-note' }, [
    document.createTextNode('Якщо вписуєте руками, додайте до виклику '),
    el('code', { text: 'sed' }),
    document.createTextNode(' у mcp-setup.sh по рядку '),
    el('code', { text: '-e "s|{{VAR}}|${VAR}|g"' }),
    document.createTextNode(' на кожну змінну. Без цього плейсхолдер потрапляє ' +
      'в .mcp.json дослівно, а скрипт усе одно звітує про успіх — і поломка ' +
      'виявиться аж тоді, коли сервер мовчки не підключиться.'),
  ]);
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
}

function entryHintText() {
  return 'За нею запис видно серед інших mcp-серверів; вона ж стає префіксом ' +
         'імен інструментів — mcp__' + entryName() + '__search.';
}

const SCOPE_TABS = [
  ['user', 'user scope · ~/.claude.json'],
  ['project', 'project scope · .mcp.json.template'],
];

function buildTokenPanel() {
  bankToken.title = el('h2', { text: 'Доступ MCP' });
  bankToken.body = el('div', { className: 'modal-body' });
  bankToken.regen = el('button', {
    className: 'btn tok-regen',
    text: 'Перегенерувати',
    title: 'Видати банку новий токен; старий одразу перестане діяти',
    on: { click: () => { bankToken.confirming = true; renderTokenPanel(); } },
  });

  const box = el('div', {
    className: 'modal-box is-wide',
    attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Доступ MCP до банку' },
    // The overlay closes on click; inside it, a click is just a click.
    on: { click: (ev) => ev.stopPropagation() },
  }, [
    el('div', { className: 'modal-head' }, [
      bankToken.title,
      el('button', {
        className: 'btn btn-ghost',
        text: '✕',
        title: 'Закрити (Esc)',
        on: { click: () => closeTokenPanel() },
      }),
    ]),
    bankToken.body,
    el('div', { className: 'modal-foot' }, [
      bankToken.regen,
      el('button', { className: 'btn', text: 'Закрити', on: { click: () => closeTokenPanel() } }),
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
  bankToken.scope = 'user';
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
    bankToken.note = 'Токен перегенеровано. Конфіги зі старим токеном більше не ' +
                     'підключаться — впишіть у них новий.';
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
  if (!bank) return;

  bankToken.title.textContent = 'Доступ MCP — ' + bank.name;
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
    text: 'Токен банку',
    attrs: { for: 'tok-value' },
  }));
  body.appendChild(el('div', { className: 'tok-row' }, [
    field,
    el('button', {
      className: 'btn',
      text: bankToken.revealed ? 'сховати' : 'показати',
      title: bankToken.revealed ? 'Прибрати значення з екрана' : 'Показати значення на екрані',
      attrs: { 'aria-pressed': bankToken.revealed ? 'true' : 'false' },
      on: { click: () => { bankToken.revealed = !bankToken.revealed; renderTokenPanel(); } },
    }),
    copyButton(() => bankToken.value, 'Скопіювати токен, не показуючи його'),
  ]));
  for (const button of body.lastChild.querySelectorAll('button')) button.disabled = !ready;

  body.appendChild(el('p', {
    className: 'tok-note',
    text: 'Відкриває лише банк «' + bank.name + '». Службовий токен, яким ' +
          'відкрито цей кабінет, ширший — у конфіг проєкту він не потрібен.',
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
    text: 'Назва запису в конфігурації',
    attrs: { for: 'tok-entry' },
  }));
  body.appendChild(entry);
  bankToken.entryHint = el('p', { className: 'tok-note', text: entryHintText() });
  body.appendChild(bankToken.entryHint);

  const tabs = el('div', { className: 'segmented tok-tabs' });
  for (const [scope, label] of SCOPE_TABS) {
    tabs.appendChild(el('button', {
      className: 'seg' + (bankToken.scope === scope ? ' is-active' : ''),
      text: label,
      on: { click: () => { bankToken.scope = scope; renderTokenPanel(); } },
    }));
  }
  body.appendChild(tabs);

  if (bankToken.scope === 'project') body.appendChild(projectLeadNote());

  for (const spec of tokenSnippets()) {
    const copy = copyButton(() => spec.build(bankToken.value), 'Скопіювати у буфер');
    copy.disabled = spec.secret && !ready;
    const pre = el('pre', { className: 'tok-code', text: spec.build(shownToken()) });
    body.appendChild(el('div', { className: 'tok-caption' }, [
      el('span', { text: spec.caption }),
      copy,
    ]));
    body.appendChild(pre);
    bankToken.blocks.push({ spec: spec, pre: pre });
  }

  if (bankToken.scope === 'project') {
    body.appendChild(el('p', {
      className: 'tok-note',
      text: '.mcp.json — згенерований файл: він у .gitignore, і mcp-setup.sh ' +
            'переписує його з шаблону. Запис має лежати в .mcp.json.template, ' +
            'інакше наступний запуск скрипта його зітре.',
    }));
    body.appendChild(manualPasteNote());
  }

  if (bankToken.confirming) {
    // The question lands at the bottom of a body that already scrolls, so on a
    // short window it would open below the fold — the button would look dead.
    const confirm = el('div', { className: 'tok-confirm' }, [
      el('p', {
        className: 'tok-confirm-text',
        text: 'Перегенерувати токен банку «' + bank.name + '»? Старий перестане ' +
              'діяти негайно: кожен конфіг, який його вже містить — ~/.claude.json, ' +
              '.mcp.env інших проєктів — більше не підключиться, доки ви не ' +
              'впишете туди новий токен.',
      }),
      el('div', { className: 'tok-confirm-row' }, [
        el('button', {
          className: 'btn',
          text: 'Скасувати',
          on: { click: () => { bankToken.confirming = false; renderTokenPanel(); } },
        }),
        el('button', {
          className: 'btn btn-danger',
          text: 'Так, перегенерувати',
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
// tree
// ---------------------------------------------------------------------------

function selectBank(bankId) {
  if (state.selectedBankId === bankId) return;
  state.selectedBankId = bankId;
  state.tree = null;
  state.file = null;
  state.filePath = null;
  state.expanded = new Set();
  renderBanks();
  renderFile();
  renderTree();
  if (bankId) loadTree({ expandAll: true }).catch(reportError);
  if (state.logScope === 'bank') loadLogs().catch(reportError);
}

/**
 * Refetch the tree.
 *
 * `expandAll` belongs to the first load of a bank only. A live refresh must
 * not re-open directories the user has since collapsed, and must not fight
 * them for the scroll position while a rebuild streams in.
 */
async function loadTree(opts) {
  const bank = bankById(state.selectedBankId);
  if (!bank) return;
  const data = await api('/api/tree?bank=' + encodeURIComponent(bank.name) + '&links=false&depth=0');
  // The selection can move while the request is in flight; a late response
  // for the previous bank must not overwrite the current one's tree.
  if (bank.id !== state.selectedBankId) return;
  state.tree = data;
  if (opts && opts.expandAll) {
    // Open every directory by default — v1 banks are small and hiding files
    // defeats the "видно, багато файлів чи ні" goal of design §7.
    walkDirs(data.tree, (dir) => state.expanded.add(dir.path));
  }
  renderTree();
}

function walkDirs(node, fn) {
  if (!node || node.type !== 'dir') return;
  fn(node);
  for (const child of node.children || []) walkDirs(child, fn);
}

function renderTree() {
  const body = $('tree-body');
  const sub = $('tree-sub');
  // This now re-runs on every live index_done, so the pane must not jump back
  // to the top under someone who is reading halfway down it.
  const keepTop = body.scrollTop;
  const keepLeft = body.scrollLeft;
  clear(body);
  clear(sub);

  const bank = bankById(state.selectedBankId);
  if (!bank) {
    body.appendChild(el('p', { className: 'empty-hint', text: 'Оберіть банк ліворуч.' }));
    return;
  }
  if (!state.tree) {
    body.appendChild(el('p', { className: 'empty-hint', text: 'Завантаження…' }));
    return;
  }

  sub.textContent = state.tree.files + ' файлів · ' + state.tree.dirs + ' тек';

  const root = state.tree.tree;
  const children = (root && root.children) || [];
  if (!children.length) {
    body.appendChild(el('p', { className: 'empty-hint', text: 'У цьому банку немає .md файлів.' }));
    return;
  }

  const box = el('div', { className: 'tree' });
  for (const child of children) renderNode(child, 0, box);
  body.appendChild(box);
  body.scrollTop = keepTop;
  body.scrollLeft = keepLeft;
}

function renderNode(node, depth, out) {
  const pad = 12 + depth * 14;

  if (node.type === 'dir') {
    const open = state.expanded.has(node.path);
    const row = el('div', {
      className: 'tree-node tree-dir',
      on: {
        click: () => {
          if (open) state.expanded.delete(node.path);
          else state.expanded.add(node.path);
          renderTree();
        },
      },
    }, [
      el('span', { className: 'tree-twisty', text: open ? '▾' : '▸' }),
      el('span', { className: 'tree-label', text: node.name + '/' }),
    ]);
    row.style.paddingLeft = pad + 'px';
    out.appendChild(row);
    if (open) {
      for (const child of node.children || []) renderNode(child, depth + 1, out);
    }
    return;
  }

  const classes = ['tree-node', 'is-file'];
  if (node.path === state.filePath) classes.push('is-selected');
  if (!node.indexed) classes.push('not-indexed');

  const row = el('div', {
    className: classes.join(' '),
    title: (node.headings || []).join(' · ') || node.path,
    on: { click: () => openFile(node.path) },
  }, [
    el('span', { className: 'tree-twisty' }),
    el('span', { className: 'tree-label', text: node.name }),
    el('span', {
      className: 'tree-chunks',
      text: node.indexed ? node.chunks + '×' : 'не в індексі',
    }),
  ]);
  row.style.paddingLeft = pad + 'px';
  out.appendChild(row);
}

// ---------------------------------------------------------------------------
// file view + chunk visualisation
// ---------------------------------------------------------------------------

async function openFile(path) {
  const bank = bankById(state.selectedBankId);
  if (!bank) return;
  state.filePath = path;
  renderTree();
  try {
    state.file = await api('/api/file?bank=' + encodeURIComponent(bank.name) +
                           '&path=' + encodeURIComponent(path));
    hideBanner();
  } catch (err) {
    state.file = null;
    reportError(err);
  }
  renderFile();
}

/**
 * Map character (code point) offsets to JS string indices.
 *
 * `start_char`/`end_char` come from Python, which counts code points; a JS
 * string counts UTF-16 units, so anything above the BMP (an emoji in a log
 * file is enough) shifts every later offset. Returns null when the two agree,
 * which is the common case and costs one regex.
 */
function buildCodePointIndex(text) {
  if (!/[\uD800-\uDBFF]/.test(text)) return null;
  const index = [];
  for (let i = 0; i < text.length;) {
    index.push(i);
    i += text.codePointAt(i) > 0xFFFF ? 2 : 1;
  }
  index.push(text.length);
  return index;
}

function makeSlicer(text) {
  const index = buildCodePointIndex(text);
  const total = index ? index.length - 1 : text.length;
  return {
    total: total,
    slice(from, to) {
      const a = Math.max(0, Math.min(total, from));
      const b = Math.max(a, Math.min(total, to));
      return index ? text.slice(index[a], index[b]) : text.slice(a, b);
    },
  };
}

function renderFile() {
  const body = $('file-body');
  const title = $('file-title');
  const button = $('file-reindex');
  clear(body);

  const file = state.file;
  if (!file) {
    title.textContent = 'Вміст';
    button.disabled = true;
    body.appendChild(el('p', { className: 'empty-hint', text: 'Оберіть файл у дереві.' }));
    return;
  }

  title.textContent = file.path;
  button.disabled = false;

  const chunks = (file.chunks || []).slice().sort((a, b) => a.start_char - b.start_char);

  body.appendChild(el('div', { className: 'file-meta' }, [
    el('span', { text: fmtBytes(file.size) }),
    el('span', { text: file.indexed ? 'в індексі' : 'не в індексі' }),
    el('span', { text: chunks.length + ' чанків' }),
    el('span', { text: 'sha256 ' + String(file.sha256 || '').slice(0, 12), title: file.sha256 }),
  ]));

  const doc = el('div', { className: 'doc' });
  const text = file.text || '';

  if (!state.chunkViz || !chunks.length) {
    doc.appendChild(el('pre', { text: text }));
    body.appendChild(doc);
    return;
  }

  const cut = makeSlicer(text);
  let cursor = 0;

  for (const chunk of chunks) {
    appendGap(doc, cut.slice(cursor, chunk.start_char));
    doc.appendChild(chunkDivider(chunk));
    doc.appendChild(el('pre', {
      className: 'chunk-body',
      text: cut.slice(chunk.start_char, chunk.end_char),
    }));
    cursor = Math.max(cursor, chunk.end_char);
  }

  appendGap(doc, cut.slice(cursor, cut.total));

  doc.appendChild(el('div', { className: 'chunk-divider is-end' }, [
    el('span', { className: 'cd-label', text: 'кінець · ' + cut.total + ' символів' }),
  ]));

  body.appendChild(doc);
}

/**
 * Render text that no chunk claims.
 *
 * The splitter leaves the blank line between sections outside both chunks, so
 * a whitespace-only gap is ordinary and gets no marker. A gap with real
 * content in it means the index does not cover part of the file — that is
 * worth seeing, so it keeps the marker.
 */
function appendGap(doc, text) {
  if (!text) return;
  const blank = text.trim() === '';
  if (!blank) {
    doc.appendChild(el('div', { className: 'gap-note', text: '· поза чанками ·' }));
  }
  // A blank gap is still the file's own text and stays in the DOM, selectable
  // and copyable — it is only rendered tighter. See `.gap-body.is-blank`.
  doc.appendChild(el('pre', {
    className: blank ? 'gap-body is-blank' : 'gap-body',
    text: text,
  }));
}

function chunkDivider(chunk) {
  // Displayed 1-based; `chunk_index` is and stays 0-based everywhere else.
  // This is a reading surface, and "#0" is an implementation detail leaking
  // into it. Do NOT shift the stored value to match: `chunk_uid` is
  // sha1(path\0chunk_index), so changing it would rewrite every chunk id and
  // force a full re-embed of every bank. The one other place a human sees the
  // raw number is the hit list in `queryRow`, which stays 0-based on purpose —
  // it is a locator, not prose. So the two differ by one by design.
  const label = '#' + (chunk.chunk_index + 1) + (chunk.heading ? ' · ' + chunk.heading : '');
  return el('div', { className: 'chunk-divider', title: 'chunk_uid ' + chunk.chunk_uid }, [
    el('span', { className: 'cd-label', text: label }),
    el('span', {
      className: 'cd-range',
      text: chunk.start_char + '–' + chunk.end_char,
    }),
  ]);
}

// ---------------------------------------------------------------------------
// log (contract 9.5 GET /api/logs, 7.1 row shapes)
// ---------------------------------------------------------------------------

async function loadLogs() {
  const params = new URLSearchParams({ kind: state.logKind, limit: '200', offset: '0' });
  if (state.logScope === 'bank') {
    const bank = bankById(state.selectedBankId);
    if (!bank) {
      state.logRows = [];
      state.logTotal = 0;
      renderLogs();
      return;
    }
    params.set('bank', bank.name);
  }
  const data = await api('/api/logs?' + params.toString());
  state.logRows = data.events || [];
  state.logTotal = data.total || 0;
  renderLogs();
}

const QUERY_COLUMNS = ['час', 'банк', 'обличчя', 'запит', 'префікс', 'статус', 'хітів', 'час, мс'];
const INDEX_COLUMNS = ['час', 'банк', 'вид', 'тригер', 'шлях', 'результат',
                       'файлів', 'чанків', 'знято', 'час, мс'];

function bankLabel(bankId) {
  const bank = bankById(bankId);
  return bank ? bank.name : (bankId || '—');
}

function renderLogs() {
  const body = $('log-body');
  clear(body);

  if (!state.logRows.length) {
    body.appendChild(el('p', { className: 'empty-hint', text: 'Порожньо.' }));
    return;
  }

  const columns = state.logKind === 'query' ? QUERY_COLUMNS : INDEX_COLUMNS;
  const head = el('tr', {}, columns.map((c) => el('th', { text: c })));
  const tbody = el('tbody');

  for (const row of state.logRows) {
    tbody.appendChild(state.logKind === 'query' ? queryRow(row) : indexRow(row));
  }

  body.appendChild(el('table', { className: 'log-table' }, [
    el('thead', {}, [head]),
    tbody,
  ]));
}

function queryRow(ev) {
  // Stays 0-based, unlike the label in `chunkDivider`. `path#index` is a
  // locator you match against the store or against what a search face
  // reported, not something to read as prose — so it has to agree with the
  // data rather than with the viewer.
  const hits = (ev.hits || []).map((h) => h.path + '#' + h.chunk_index).join(', ');
  const tr = el('tr', { className: ev._live ? 'is-live' : '', title: hits }, [
    el('td', { text: fmtTime(ev.ts) }),
    el('td', { text: bankLabel(ev.bank_id) }),
    el('td', { text: ev.face }),
    el('td', { className: 'col-q', text: ev.query }),
    el('td', { text: ev.path_prefix || '—' }),
    el('td', { text: STATUS_LABEL[ev.status] || ev.status }),
    el('td', { className: 'num', text: ev.n_hits }),
    el('td', { className: 'num', text: fmtMs(ev.took_ms) }),
  ]);
  return tr;
}

function indexRow(ev) {
  return el('tr', { className: ev._live ? 'is-live' : '', title: ev.error || '' }, [
    el('td', { text: fmtTime(ev.ts) }),
    el('td', { text: bankLabel(ev.bank_id) }),
    el('td', { text: ev.kind }),
    el('td', { text: ev.trigger }),
    el('td', { className: 'col-q', text: ev.path || (ev.error ? ev.error : '—') }),
    el('td', { className: 'res-' + ev.result, text: ev.result }),
    el('td', { className: 'num', text: ev.files_indexed }),
    el('td', { className: 'num', text: ev.chunks_indexed }),
    el('td', { className: 'num', text: ev.files_pruned }),
    el('td', { className: 'num', text: fmtMs(ev.took_ms) }),
  ]);
}

/** A live WS event is prepended only when the current filter would include it. */
function pushLiveLog(kind, row) {
  if (state.logKind !== kind) return;
  if (state.logScope === 'bank' && row.bank_id !== state.selectedBankId) return;
  row._live = true;
  state.logRows.unshift(row);
  if (state.logRows.length > 200) state.logRows.pop();
  state.logTotal += 1;
  renderLogs();
}

// ---------------------------------------------------------------------------
// WebSocket (contract 9.7)
// ---------------------------------------------------------------------------

let socket = null;
let retryDelay = 500;
let banksReloadTimer = null;
let treeRefreshTimer = null;
// The first `hello` follows the load boot() just did, so there is nothing to
// resync. Every later one means the socket dropped and deltas were missed.
let helloSeen = false;

function setConnState(kind, label) {
  const box = $('conn-state');
  box.className = 'conn is-' + kind;
  box.lastElementChild.textContent = label;
}

function scheduleBanksReload() {
  if (banksReloadTimer || state.gated) return;
  banksReloadTimer = setTimeout(() => {
    banksReloadTimer = null;
    if (state.gated) return;
    loadBanks().catch(reportError);
    loadStatus().catch(() => {});
  }, 350);
}

/**
 * Pull the tree again after indexing changed something in it.
 *
 * Throttled rather than debounced: a bulk rebuild emits a steady stream of
 * `index_done`, and a debounce that keeps getting reset would never fire
 * until the very end. This fires at most once per window and the last event
 * in a burst still gets its own trailing refresh, so the tree converges.
 */
function scheduleTreeRefresh() {
  if (treeRefreshTimer || state.gated || !state.selectedBankId) return;
  treeRefreshTimer = setTimeout(() => {
    treeRefreshTimer = null;
    if (state.gated || !state.selectedBankId) return;
    loadTree().catch(() => {});
  }, 700);
}

/**
 * Re-read everything over REST.
 *
 * Contract 9.7: the socket carries deltas only and `hello` means "refetch" —
 * REST is authoritative for initial state. Anything missed while the socket
 * was down heals here, which is the only reason a gap cannot persist.
 */
function resyncAll() {
  state.progress.clear();
  loadBanks().catch(reportError);
  loadStatus().catch(() => {});
  loadLogs().catch(() => {});
  if (state.selectedBankId) loadTree().catch(() => {});
}

/** Drop the socket without arming the reconnect timer. */
function closeSocket() {
  if (!socket) return;
  const dead = socket;
  socket = null;
  dead.onclose = null;
  dead.onerror = null;
  dead.close();
}

function connectSocket() {
  // Without a token the handshake can only be refused, and a refused socket
  // would reconnect forever behind the gate.
  if (state.gated || !token) return;

  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = scheme + '//' + window.location.host + '/ws' +
              '?token=' + encodeURIComponent(token);
  setConnState('wait', 'підключення…');

  socket = new WebSocket(url);

  socket.onopen = () => {
    retryDelay = 500;
    setConnState('open', 'наживо');
  };

  socket.onclose = () => {
    socket = null;
    if (state.gated) return;
    setConnState('error', 'розірвано');
    setTimeout(connectSocket, retryDelay);
    retryDelay = Math.min(retryDelay * 2, 10000);
  };

  socket.onerror = () => {
    if (!state.gated) setConnState('error', 'помилка');
  };

  socket.onmessage = (event) => {
    let envelope;
    try {
      envelope = JSON.parse(event.data);
    } catch (err) {
      return;
    }
    handleEvent(envelope);
  };
}

function handleEvent(envelope) {
  const type = envelope.type;
  const data = envelope.data || {};
  const bankId = envelope.bank_id;

  switch (type) {
    case 'hello':
      if (data.queue) {
        state.queue = data.queue;
        renderService();
        if (reconcileProgress(data.queue)) renderBanks();
      }
      if (helloSeen) resyncAll();
      helloSeen = true;
      break;

    case 'ping':
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'pong' }));
      }
      break;

    case 'queue':
      state.queue = data;
      renderService();
      if (reconcileProgress(data)) renderBanks();
      break;

    case 'index_start':
      // We watched this one begin, so its age is exact — no `≥`.
      state.progress.set(bankId, {
        task_id: data.task_id, kind: data.kind, path: data.path,
        batch: 0, batches: data.batches || 0, chunks_done: 0, chunks_total: 0,
        since: Date.now(), approx: false,
      });
      renderBanks();
      break;

    case 'index_progress': {
      const prev = state.progress.get(bankId) || {};
      const same = prev.task_id === data.task_id;
      state.progress.set(bankId, {
        task_id: data.task_id, kind: prev.kind || 'file', path: data.path,
        batch: data.batch, batches: data.batches,
        chunks_done: data.chunks_done, chunks_total: data.chunks_total,
        // Keep the clock running across progress events; if this is the first
        // we have seen of the task, we joined it late and must say so.
        since: same && prev.since ? prev.since : Date.now(),
        approx: same ? !!prev.approx : true,
      });
      renderBanks();
      break;
    }

    case 'index_yield': {
      const prev = state.progress.get(bankId);
      if (prev && prev.task_id === data.task_id) { prev.yielded = true; renderBanks(); }
      break;
    }

    case 'index_done':
      clearProgress(bankId, data.task_id);
      setNote(bankId, 'готово: ' + (data.path || data.kind) + ' · ' +
                      (data.chunks_indexed || 0) + ' чанків · ' + fmtMs(data.took_ms));
      scheduleBanksReload();
      // A file just changed its indexed/chunk state — that is what the tree
      // shows, so it has to be pulled again or it stays stale until a reload.
      if (bankId === state.selectedBankId) scheduleTreeRefresh();
      // The open file may have been re-chunked — pull fresh boundaries.
      if (bankId === state.selectedBankId && state.filePath &&
          (!data.path || data.path === state.filePath)) {
        openFile(state.filePath);
      }
      break;

    case 'index_error':
      clearProgress(bankId, data.task_id);
      setNote(bankId, 'помилка: ' + (data.path || data.kind) + ' — ' + data.error);
      scheduleBanksReload();
      break;

    case 'prune':
      setNote(bankId, 'знято з індексу: ' + (data.count || 0));
      scheduleBanksReload();
      if (bankId === state.selectedBankId) scheduleTreeRefresh();
      break;

    case 'bank_added':
    case 'bank_removed':
      scheduleBanksReload();
      break;

    case 'bank_status':
      if (data.bank) {
        const i = state.banks.findIndex((b) => b.id === data.bank.id);
        if (i >= 0) state.banks[i] = data.bank;
        else state.banks.push(data.bank);
        renderBanks();
      }
      break;

    case 'query':
      pushLiveLog('query', {
        id: null, ts: envelope.ts, bank_id: bankId, face: data.face,
        query: data.query, path_prefix: data.path_prefix || null,
        status: data.status, n_hits: data.n_hits, took_ms: data.took_ms, hits: [],
      });
      break;

    default:
      // Contract 9.7: unknown types are ignored on purpose, so the backend can
      // add events without breaking this page.
      break;
  }
}

// ---------------------------------------------------------------------------
// wiring
// ---------------------------------------------------------------------------

function bindControls() {
  const refresh = $('banks-refresh');
  refresh.addEventListener('click', () => {
    loadBanks().catch(reportError);
    loadStatus().catch(() => {});
  });

  // Inserted rather than written into index.html: the document may come from
  // cache while this script does not, and a button this code addresses by id
  // would then be missing (same reasoning as the gate).
  refresh.parentNode.insertBefore(el('button', {
    className: 'btn btn-ghost',
    text: '＋ додати',
    title: 'Зареєструвати нову теку з .md як банк',
    on: { click: () => openPicker() },
  }), refresh);

  $('log-refresh').addEventListener('click', () => loadLogs().catch(reportError));

  $('chunkviz-toggle').addEventListener('change', (ev) => {
    state.chunkViz = ev.target.checked;
    renderFile();
  });

  $('file-reindex').addEventListener('click', () => {
    const bank = bankById(state.selectedBankId);
    if (bank && state.filePath) reindex(bank, { path: state.filePath });
  });

  for (const button of $('log-kind').querySelectorAll('.seg')) {
    button.addEventListener('click', () => {
      state.logKind = button.dataset.kind;
      for (const b of $('log-kind').querySelectorAll('.seg')) {
        b.classList.toggle('is-active', b === button);
      }
      loadLogs().catch(reportError);
    });
  }

  for (const button of $('log-scope').querySelectorAll('.seg')) {
    button.addEventListener('click', () => {
      state.logScope = button.dataset.scope;
      for (const b of $('log-scope').querySelectorAll('.seg')) {
        b.classList.toggle('is-active', b === button);
      }
      loadLogs().catch(reportError);
    });
  }
}

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
    renderLogs();
  }
  connectSocket();
}

async function boot() {
  bindControls();
  buildGate();
  buildPicker();
  buildTokenPanel();
  buildBankMenu();
  buildRemoval();
  renderService();
  if (!token) {
    // First run: nothing has been rejected, so ask before knocking.
    openGate('missing');
    return;
  }
  await start();
}

boot();
