/* Self-update: sidebar banner + centred modal + irreversible progress.
 *
 * Design source of truth: `.claude/memory/topics/engine-self-update-design.md`
 * (UX flow dictated by the user). Contract: `src/api.py`'s `GET/POST
 * /api/update/*` (§ "self-update (M)") and the `update_progress` WS event
 * emitted by `engine_update._emit_progress` — routed here from shell.js's
 * `handleEvent` (case 'update_progress' -> onUpdateProgress).
 *
 * The one hard trap this file is built around (documented in the design
 * topic, confirmed by reading `_run_staged_apply`/`_apply_view` in
 * `src/api.py`): once staging succeeds, the API process hands off to a
 * detached `update-apply` CLI whose very next action is to STOP that same
 * API process to repoint `current` and restart. The WebSocket carrying
 * `update_progress` dies with it — by design, not as a bug — and nothing
 * can arrive over it for the "switching"/"health" phase. So this modal
 * never treats a WS disconnect as an update failure: `update_progress` is
 * only ever used as a faster, nicer preview of the `download`/`venv` steps
 * while the OLD process is still alive to send them. The single source of
 * truth for the outcome is `GET /api/update/status`, polled on its own
 * timer from the moment `apply` is accepted until a terminal state — which
 * works whether the poll gets a connection-refused (mid-restart, silently
 * ignored) or reaches a brand new process serving the switched-to tag.
 *
 * Known contract gap, found reading `engine_update.py`, not fixed here
 * (out of ui-dev's scope — service-dev/api.py owns it): `last_check.
 * update_available` is computed once, at check time, against whatever
 * `current` was AT THAT MOMENT (`record_check`), and nothing after a
 * successful switch (`record_installed`, `finish_apply`, `cmd_update_apply`
 * in cli.py) re-runs a check or otherwise clears it. So immediately after a
 * successful update, `GET /api/update/status` can still report
 * `update_available: true` until the next background check (hours later)
 * or a manual one. `shouldShowUpdateBanner()` below guards against exactly
 * this by also requiring `current.tag !== latest_known.tag` — strictly
 * narrower than the literal "banner on update_available" instruction, never
 * hiding a real update, only suppressing the false-positive re-appearance
 * right after this cabinet's own successful apply.
 */
'use strict';

const UPDATE_POLL_MS = 2000;
const UPDATE_POLL_TIMEOUT_MS = 3 * 60 * 1000; // 3 minutes of silence before giving up

// Ordered for display only — the real state machine is `apply.state` +
// `apply.step` from the backend (see updateStepIndex()).
const UPDATE_STEPS = [
  { key: 'download', label: 'Завантаження з GitHub' },
  { key: 'venv', label: 'Встановлення пакетів' },
  { key: 'switching', label: 'Перемикання версії та перезапуск' },
];

const updateModal = {
  root: null, box: null, title: null, closeBtn: null, body: null, foot: null,
  // 'idle' (not shown) | 'confirm' | 'progress' | 'timeout' | 'terminal'
  phase: 'idle',
  // Observed, THIS session, that staging finished and handoff to the
  // detached `update-apply` began — i.e. the point past which a "failed"
  // outcome would mean the service itself was touched, not just staging.
  // Used only to word the terminal message; the state machine itself does
  // not depend on it.
  everSwitching: false,
  pollTimer: null,
  pollStartedAt: 0,
};

// ---------------------------------------------------------------------------
// modal chrome — same building blocks as picker/removal/token panel in app.js
// ---------------------------------------------------------------------------

function buildUpdateModal() {
  updateModal.title = el('h2', { text: 'Оновлення mnemo' });
  updateModal.closeBtn = el('button', {
    className: 'btn btn-ghost', text: '✕', title: 'Закрити (Esc)',
    on: { click: () => closeUpdateModal() },
  });
  updateModal.body = el('div', { className: 'modal-body' });
  updateModal.foot = el('div', { className: 'modal-foot' });

  updateModal.box = el('div', {
    className: 'modal-box',
    attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Оновлення mnemo' },
    on: { click: (ev) => ev.stopPropagation() },
  }, [
    el('div', { className: 'modal-head' }, [updateModal.title, updateModal.closeBtn]),
    updateModal.body,
    updateModal.foot,
  ]);

  updateModal.root = el('div', {
    className: 'modal',
    attrs: { hidden: '' },
    // Design point 3: once applied, "без можливості щось натиснути" — the
    // backdrop click that closes every other modal here is a no-op mid-progress.
    on: { click: () => { if (updateModal.phase !== 'progress') closeUpdateModal(); } },
  }, [updateModal.box]);
  document.body.appendChild(updateModal.root);

  document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape' || updateModal.root.hidden) return;
    if (updateModal.phase === 'progress') return; // same guard as the backdrop
    closeUpdateModal();
  });

  const banner = $('sb-update-banner');
  if (banner) banner.addEventListener('click', () => onUpdateBannerClick());
}

function openUpdateModal() {
  updateModal.phase = 'confirm';
  updateModal.everSwitching = false;
  updateModal.root.hidden = false;
  renderUpdateModal();
}

function closeUpdateModal() {
  if (updateModal.phase === 'progress') return; // guarded — see buildUpdateModal
  stopUpdatePolling();
  updateModal.phase = 'idle';
  updateModal.root.hidden = true;
  renderSidebarUpdateBanner();
}

function renderUpdateModal() {
  if (updateModal.phase === 'confirm') renderUpdateConfirm();
  else if (updateModal.phase === 'progress') renderUpdateProgress();
  else if (updateModal.phase === 'timeout') renderUpdateTimeout();
  else if (updateModal.phase === 'terminal') renderUpdateTerminal();
  // Every modal render is a point where `state.update` or `updateModal.phase`
  // just changed — the sidebar banner (busy wording, or hidden while any
  // dialog is open) must never lag one step behind what the modal shows.
  renderSidebarUpdateBanner();
}

// ---------------------------------------------------------------------------
// phase: confirm — "яка версія, і попередження, що сервер буде зупинено"
// ---------------------------------------------------------------------------

function renderUpdateConfirm() {
  clear(updateModal.body);
  clear(updateModal.foot);
  updateModal.closeBtn.hidden = false;

  const u = state.update || {};
  const currentTag = (u.current && u.current.tag) || null;
  const latestTag = (u.latest_known && u.latest_known.tag) || null;

  updateModal.body.appendChild(el('p', { className: 'upd-row' }, [
    document.createTextNode('Поточна версія: '),
    el('code', { text: currentTag || '—' }),
  ]));
  updateModal.body.appendChild(el('p', { className: 'upd-row' }, [
    document.createTextNode('Нова версія: '),
    el('strong', { text: latestTag || '—' }),
  ]));

  // Same visual pattern as the token panel's "перегенерувати" confirmation —
  // an irreversible action, warned about right above the button that starts it.
  updateModal.body.appendChild(el('div', { className: 'tok-confirm' }, [
    el('p', {
      className: 'tok-confirm-text',
      text: 'Службу mnemo буде зупинено й перезапущено на новій версії. На цей ' +
            'час пошук та індексація недоступні. Після «OK» дію не можна ' +
            'скасувати — прогрес показуватиметься до завершення.',
    }),
  ]));

  updateModal.foot.appendChild(el('button', {
    className: 'btn', text: 'Скасувати', on: { click: () => closeUpdateModal() },
  }));
  updateModal.foot.appendChild(el('button', {
    className: 'btn btn-primary', text: 'OK',
    attrs: latestTag ? {} : { disabled: '' },
    on: { click: () => confirmUpdateApply() },
  }));
}

async function confirmUpdateApply() {
  const u = state.update || {};
  const tag = u.latest_known && u.latest_known.tag;
  if (!tag) return;

  updateModal.phase = 'progress';
  updateModal.everSwitching = false;
  renderUpdateModal();

  try {
    await api('/api/update/apply', { method: 'POST', body: { tag: tag } });
  } catch (err) {
    // Rejected before anything started (stale target, one already running) —
    // nothing was touched. Report it the same way a later failure is
    // reported, just without ever having left "confirm".
    let message = err.message;
    if (err.code === 'stale_target' && err.detail && err.detail.latest_tag) {
      message = 'Показана версія застаріла (актуальна: ' + err.detail.latest_tag +
                '). Закрийте вікно й спробуйте ще раз.';
    }
    state.update = state.update || {};
    state.update.apply = {
      state: 'failed', tag: tag, step: null, error: message,
      started_at: null, finished_at: null,
    };
    updateModal.phase = 'terminal';
    renderUpdateModal();
    return;
  }

  startUpdatePolling();
}

// ---------------------------------------------------------------------------
// phase: progress — download -> install packages -> switch & restart
// ---------------------------------------------------------------------------

function updateStepIndex(apply) {
  if (apply.state === 'switching') return 2;
  if (apply.step === 'venv') return 1;
  return 0; // 'download', or staging with no step reported yet
}

function renderUpdateProgress() {
  clear(updateModal.body);
  clear(updateModal.foot); // design point 3: no buttons while this is running
  updateModal.closeBtn.hidden = true;

  const u = state.update || {};
  const apply = u.apply || {};
  const tag = apply.tag || (u.latest_known && u.latest_known.tag) || '—';

  updateModal.body.appendChild(el('p', { className: 'upd-row', text: 'Оновлення до ' + tag + '…' }));

  const activeIndex = updateStepIndex(apply);
  const stepsBox = el('div', { className: 'upd-steps' });
  UPDATE_STEPS.forEach((step, i) => {
    const status = i < activeIndex ? 'done' : i === activeIndex ? 'active' : 'pending';
    stepsBox.appendChild(el('div', { className: 'upd-step is-' + status }, [
      el('span', { className: 'upd-step-mark', text: status === 'done' ? '✓' : String(i + 1) }),
      el('span', { className: 'upd-step-label', text: step.label }),
    ]));
    if (status === 'active') {
      stepsBox.appendChild(el('div', { className: 'bar is-indeterminate upd-step-bar' }, [el('i')]));
    }
  });
  updateModal.body.appendChild(stepsBox);

  if (updateModal.everSwitching) {
    updateModal.body.appendChild(el('p', {
      className: 'upd-note',
      text: 'Служба перезапускається — сторінка на кілька секунд втратить ' +
            'з’єднання. Це очікувано: результат стане відомий одразу після ' +
            'відновлення зв’язку.',
    }));
  }
}

// ---------------------------------------------------------------------------
// phase: timeout — the poll budget ran out with no terminal state
// ---------------------------------------------------------------------------

function renderUpdateTimeout() {
  clear(updateModal.body);
  clear(updateModal.foot);
  updateModal.closeBtn.hidden = false;

  updateModal.body.appendChild(el('p', {
    className: 'upd-row modal-error',
    text: 'Не вдалося дізнатися результат оновлення за відведений час. Служба ' +
          'могла ще перезапускатися, або кабінет тимчасово не звʼязується з ' +
          'нею. Перевірте вручну (mnemo doctor) або спробуйте ще раз.',
  }));

  updateModal.foot.appendChild(el('button', {
    className: 'btn', text: 'Спробувати ще', on: { click: () => retryUpdatePolling() },
  }));
  updateModal.foot.appendChild(el('button', {
    className: 'btn btn-primary', text: 'Закрити', on: { click: () => closeUpdateModal() },
  }));
}

function retryUpdatePolling() {
  updateModal.phase = 'progress';
  renderUpdateModal();
  startUpdatePolling();
}

// ---------------------------------------------------------------------------
// phase: terminal — done / rolled_back / failed
// ---------------------------------------------------------------------------

function renderUpdateTerminal() {
  clear(updateModal.body);
  clear(updateModal.foot);
  updateModal.closeBtn.hidden = false;

  const u = state.update || {};
  const apply = u.apply || {};
  const currentTag = u.current && u.current.tag;

  let text;
  let cls;
  if (apply.state === 'done') {
    cls = 'tok-ok';
    text = 'Оновлено до ' + (currentTag || apply.tag || '—') + '.';
  } else if (apply.state === 'rolled_back') {
    // Design point 5, verbatim: "проблема, відкотили назад" instead of a
    // silent failure or a generic error.
    cls = 'modal-error';
    text = 'Проблема під час оновлення до ' + (apply.tag || '—') + ' — відкотили ' +
           'назад на ' + (currentTag || '—') + '.' +
           (apply.error ? ' (' + apply.error + ')' : '');
  } else { // 'failed'
    cls = 'modal-error';
    text = 'Оновлення не вдалося' + (apply.error ? ': ' + apply.error : '.');
    // See this file's header: reaching 'switching' locally is what
    // distinguishes "staging failed, service untouched" from "the switch
    // itself was attempted" — the safer, narrower claim either way.
    text += updateModal.everSwitching
      ? ' Стан служби може бути невизначеним — перевірте mnemo doctor.'
      : ' Поточна версія не змінювалась.';
  }

  updateModal.body.appendChild(el('p', { className: 'upd-row ' + cls, text: text }));
  updateModal.foot.appendChild(el('button', {
    className: 'btn btn-primary', text: 'Закрити', on: { click: () => closeUpdateModal() },
  }));
}

// ---------------------------------------------------------------------------
// polling — the actual source of truth for the outcome (see file header)
// ---------------------------------------------------------------------------

function startUpdatePolling() {
  stopUpdatePolling();
  updateModal.pollStartedAt = Date.now();
  pollUpdateStatusOnce();
  updateModal.pollTimer = setInterval(pollUpdateStatusOnce, UPDATE_POLL_MS);
}

function stopUpdatePolling() {
  if (updateModal.pollTimer) {
    clearInterval(updateModal.pollTimer);
    updateModal.pollTimer = null;
  }
}

async function pollUpdateStatusOnce() {
  if (state.gated) return;
  let data;
  try {
    data = await api('/api/update/status');
  } catch (err) {
    // Expected mid-switch: the process that would answer this is the one
    // stopping itself (file header). Silence is correct — an error surface
    // here would misreport a routine restart as a failure. The timeout
    // budget below is what acts on prolonged silence, not this catch.
    checkUpdatePollTimeout();
    return;
  }

  state.update = data;
  const apply = data.apply || {};
  if (apply.state === 'switching') updateModal.everSwitching = true;

  if (updateModal.phase !== 'progress') {
    // A poll from a previous cycle landing after the modal moved on
    // (closed, or a fresh confirm was opened) — the sidebar still wants it.
    renderSidebarUpdateBanner();
    return;
  }

  if (apply.state === 'done' || apply.state === 'failed' || apply.state === 'rolled_back') {
    stopUpdatePolling();
    updateModal.phase = 'terminal';
    renderUpdateModal();
    return;
  }

  checkUpdatePollTimeout();
  if (updateModal.phase === 'progress') renderUpdateModal();
}

function checkUpdatePollTimeout() {
  if (updateModal.phase !== 'progress') return;
  if (Date.now() - updateModal.pollStartedAt < UPDATE_POLL_TIMEOUT_MS) return;
  stopUpdatePolling();
  updateModal.phase = 'timeout';
  renderUpdateModal();
}

// ---------------------------------------------------------------------------
// WS update_progress — a faster preview of download/venv while the OLD
// process is still alive to send it (shell.js routes the event here)
// ---------------------------------------------------------------------------

function onUpdateProgress(data) {
  if (updateModal.phase !== 'progress') return; // not watching right now
  const u = state.update || (state.update = {});
  u.apply = u.apply || {};
  u.apply.tag = data.tag;
  u.apply.step = data.step;

  if (data.step === 'failed') {
    // Staging itself failed — always pre-switch (see stage_release's own
    // contract: it never touches `current`), so this is authoritative and
    // does not need to wait for a poll to confirm it.
    u.apply.state = 'failed';
    u.apply.error = data.error || null;
    stopUpdatePolling();
    updateModal.phase = 'terminal';
    renderUpdateModal();
    return;
  }

  if (data.step === 'done') {
    // Staging finished; engine_update._run_staged_apply's very next move is
    // to set state="switching" and hand off to update-apply, which stops
    // THIS process. Nothing more will arrive over this socket for this
    // cycle — reflect the transition locally rather than waiting for a
    // poll to catch up on a fact the backend's own code guarantees.
    updateModal.everSwitching = true;
    u.apply.state = 'switching';
  } else {
    u.apply.state = 'staging';
  }
  renderUpdateModal();
}

// ---------------------------------------------------------------------------
// sidebar banner
// ---------------------------------------------------------------------------

function shouldShowUpdateBanner(u) {
  if (!u || !u.latest_known || !u.latest_known.update_available) return false;
  // See file header: `update_available` is not recomputed after a switch,
  // so a tag comparison against `current` is the narrower, correct guard.
  if (u.current && u.current.tag && u.current.tag === u.latest_known.tag) return false;
  return true;
}

function renderSidebarUpdateBanner() {
  const box = $('sb-update-banner');
  if (!box) return;
  const u = state.update;
  const apply = (u && u.apply) || {};
  const busy = apply.state === 'staging' || apply.state === 'switching';

  if (busy) {
    box.hidden = false;
    box.classList.add('is-busy');
    box.textContent = 'Оновлення mnemo триває…';
    return;
  }
  box.classList.remove('is-busy');

  if (updateModal.phase !== 'idle' || !shouldShowUpdateBanner(u)) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.textContent = 'Доступна нова версія ' + u.latest_known.tag;
}

function onUpdateBannerClick() {
  const u = state.update;
  const apply = (u && u.apply) || {};
  if (apply.state === 'staging' || apply.state === 'switching') {
    // Reopen the progress view onto an update already in flight (e.g. the
    // page was reloaded mid-update) rather than offering a second confirm.
    updateModal.phase = 'progress';
    updateModal.everSwitching = apply.state === 'switching';
    updateModal.root.hidden = false;
    renderUpdateModal();
    startUpdatePolling();
    return;
  }
  openUpdateModal();
}

// ---------------------------------------------------------------------------
// initial load
// ---------------------------------------------------------------------------

async function refreshUpdateStatus() {
  let data;
  try {
    data = await api('/api/update/status');
  } catch (err) {
    // Not banner-worthy on its own — the page already surfaces backend
    // reachability elsewhere; a missing self-update status must not become
    // a second error surface for the same underlying fact.
    return;
  }
  state.update = data;

  const apply = data.apply || {};
  if (apply.state === 'staging' || apply.state === 'switching') {
    // An update this session did not start is already in flight (a reload,
    // or another tab/CLI triggered it) — resume watching it instead of
    // showing a stale "available" banner next to a service mid-switch.
    updateModal.everSwitching = apply.state === 'switching';
    updateModal.phase = 'progress';
    updateModal.root.hidden = false;
    renderUpdateModal();
    startUpdatePolling();
    return;
  }
  renderSidebarUpdateBanner();
}
