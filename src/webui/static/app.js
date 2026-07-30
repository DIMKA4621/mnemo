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
    list.appendChild(el('p', { className: 'empty-hint', text: 'Жодного банку не зареєстровано.' }));
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
  const head = el('div', { className: 'bank-row' }, [
    el('span', { className: 'bank-name', text: bank.name, title: 'id: ' + bank.id }),
    ...badges,
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

  const stop = (fn) => (ev) => { ev.stopPropagation(); fn(); };
  card.appendChild(el('div', { className: 'bank-actions' }, [
    el('button', {
      className: 'btn',
      text: 'Синхронізація індексу',
      title: 'Переіндексує лише файли, що змінилися, і знімає з індексу видалені',
      on: { click: stop(() => reindex(bank, { full: false })) },
    }),
    el('button', {
      className: 'btn',
      text: 'Повний реіндекс',
      title: 'Стирає індекс і збирає його заново — довго, пропорційно розміру банку',
      on: { click: stop(() => reindex(bank, { full: true })) },
    }),
  ]));

  return card;
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
  $('banks-refresh').addEventListener('click', () => {
    loadBanks().catch(reportError);
    loadStatus().catch(() => {});
  });

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
  renderService();
  if (!token) {
    // First run: nothing has been rejected, so ask before knocking.
    openGate('missing');
    return;
  }
  await start();
}

boot();
