/* shell: sidebar routing, the persistent per-page header, and the
 * WebSocket progress channel (contract 9.7) — the three things every page
 * depends on, so they live above page-memory.js / page-journal.js /
 * page-settings.js in the load order but below app.js's shared chrome.
 *
 * Header content is built as an HTML string per page (matching
 * docs/console-design.html) rather than through app.js's `el()`/textContent
 * convention: unlike bank cards, file paths or journal queries, nothing
 * rendered here ever carries backend-sourced text — only static Ukrainian
 * labels and trusted counts — so the stricter convention buys nothing here
 * and the mockup's markup can be reused close to verbatim.
 *
 * Deliberate deviation from the mockup: `docs/console-design.html`'s
 * `renderHead()` rebuilds `#top-left` and re-attaches `addEventListener` on
 * every page switch — a real duplicate-listener bug (documented in
 * `.claude/memory/logs/2026-08-19-cabinet-tabs.md`). Header *content* still
 * swaps per page; the *listener* is bound once, here, via delegation.
 */
'use strict';

// ---------------------------------------------------------------------------
// routing
// ---------------------------------------------------------------------------

// Wrapped in closures rather than referencing the page functions directly:
// this table is built while only app.js has loaded, and the memory/journal/
// settings modules that actually define `memoryHeaderHtml` etc. load after
// it. A closure defers the lookup to call time, when everything exists; a
// bare function reference here would be `undefined` at construction time.
const PAGES = {
  memory: {
    label: 'Памʼять',
    header: () => memoryHeaderHtml(),
    count: () => state.banks.length,
    onEnter: () => {},   // banks/tree are already loaded by `start()`
  },
  journal: {
    label: 'Журнал',
    header: () => journalHeaderHtml(),
    // No sidebar count: unlike Памʼять's bank count (small, stable), the log
    // total grows without bound and read as noise next to the label.
    count: null,
    onEnter: () => { renderJournal(); },
  },
  settings: {
    label: 'Налаштування',
    header: () => settingsHeaderHtml(),
    count: null,
    onEnter: () => { openSettings(); },
    // Replaces the old overlay's "Закрити": there is nothing to close any
    // more, but a secret typed into the API-key field, or an unsaved
    // autostart choice, must not survive the trip to another page.
    onLeave: () => { settingsOnLeave(); },
  },
};

function setPage(name) {
  const page = PAGES[name];
  if (!page) return;
  // The one guard that survives from the old overlay: a save in flight must
  // not be abandoned by navigating away mid-request.
  if (state.page === 'settings' && name !== 'settings' && settings.busy) return;

  const leaving = PAGES[state.page];
  if (leaving && leaving.onLeave && name !== state.page) leaving.onLeave();

  state.page = name;
  for (const panel of document.querySelectorAll('[data-panel]')) {
    panel.hidden = panel.dataset.panel !== name;
  }
  for (const item of document.querySelectorAll('.sb-item[data-page]')) {
    item.classList.toggle('is-active', item.dataset.page === name);
  }
  renderHeader();
  updateSidebarCounts();
  page.onEnter();
}

function renderHeader() {
  $('top-left').innerHTML = PAGES[state.page].header();
}

function updateSidebarCounts() {
  for (const [name, page] of Object.entries(PAGES)) {
    const node = $('sb-count-' + name);
    if (!node || !page.count) continue;
    const n = page.count();
    node.textContent = n ? String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ') : '';
  }
}

/**
 * One delegated listener for every header control, on every page.
 *
 * The header's *content* is rebuilt per page (`renderHeader`), but the
 * listener itself is bound exactly once — this is the fix for the mockup's
 * per-render `addEventListener` (see the file header comment).
 */
function bindHeaderDelegation() {
  $('top-left').addEventListener('click', (ev) => {
    const mob = ev.target.closest('[data-mob]');
    if (mob) { showPane(mob.dataset.mob); return; }

    const kindSeg = ev.target.closest('#jkind .seg');
    if (kindSeg) { setLogKind(kindSeg.dataset.kind); return; }

    if (ev.target.closest('#add-bank')) { openPicker(); return; }
    if (ev.target.closest('#journal-refresh')) { loadLogs().catch(reportError); return; }
  });
}

function initShell() {
  bindHeaderDelegation();

  for (const item of document.querySelectorAll('.sb-nav [data-page]')) {
    item.addEventListener('click', () => setPage(item.dataset.page));
  }

  const app = $('app');
  const toggle = $('sb-toggle');
  const setToggleLabel = (collapsed) => {
    const label = collapsed ? 'Розгорнути навігацію' : 'Згорнути навігацію';
    toggle.title = label;
    toggle.setAttribute('aria-label', label);
  };
  // Width is a stored user choice (mirrors the `mnemo_theme` pattern), so it
  // survives reload — but restored here rather than pre-paint: a 140ms grid
  // transition is a minor resize, not the color flash a late theme switch
  // would be, so it does not need the same head-of-document treatment.
  if (localStorage.getItem('mnemo_sidebar') === 'collapsed') {
    app.classList.add('is-collapsed');
    setToggleLabel(true);
  }
  toggle.addEventListener('click', () => {
    const collapsed = app.classList.toggle('is-collapsed');
    localStorage.setItem('mnemo_sidebar', collapsed ? 'collapsed' : 'expanded');
    setToggleLabel(collapsed);
  });

  setPage('memory');
}

// ---------------------------------------------------------------------------
// machine facts (sidebar footer) — `renderService()` and `setConnState()`
// keep the logic they always had, they just target the new location.
// ---------------------------------------------------------------------------

function renderService() {
  const provider = $('sb-provider-text');
  const version = $('sb-version-text');
  const svc = state.service;
  if (provider) provider.textContent = svc ? (svc.provider || '—') : '—';
  if (version) version.textContent = svc ? ('v ' + (svc.version || '—')) : '—';
  const foot = $('sb-foot');
  if (foot && svc) {
    foot.title = 'провайдер ' + (svc.provider || '—') + ' · версія ' + (svc.version || '—');
  }
}

function setConnState(kind, label) {
  const dot = $('sb-dot');
  const text = $('sb-status-text');
  const cls = kind === 'open' ? 'dot' : kind === 'error' ? 'dot err'
            : kind === 'wait' ? 'dot busy' : 'dot idle';
  if (dot) dot.className = cls;
  if (text) text.textContent = label;
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
      if (data.bank) applyBank(data.bank);
      break;

    // Self-update (design: engine-self-update-design.md, contract:
    // engine_update._emit_progress). bank_id is always null here — a
    // machine-level event, not a per-bank one — and the socket that carries
    // it is the SAME process that stops itself a moment later to switch
    // versions, so update.js's own poll (not this socket's onclose) is what
    // learns the final outcome. See update.js's file header for the full
    // reasoning.
    case 'update_progress':
      onUpdateProgress(data);
      break;

    // Unattended auto-apply's own countdown (backend: commit 4f977b6, api.
    // maybe_begin_auto_apply / _settle_auto_pending). Also bank_id: null —
    // same machine-level event as update_progress above, routed the same
    // way, just to update.js's own auto-pending handler.
    case 'update_auto_pending':
      onUpdateAutoPending(data);
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
