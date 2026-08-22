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
 * right after this console's own successful apply.
 *
 * Unattended auto-apply (backend: commit 4f977b6) extends this with a
 * second, independent entry point: the checker's own background tick can
 * arm a short countdown (`GET /api/update/status`'s `"auto"` block, WS
 * `update_auto_pending`) that applies itself unless cancelled. It is a
 * genuinely separate flow, not a variant of the confirm dialog above — the
 * user never asked for it — so it gets its own modal phase (`'auto-pending'`)
 * and settles into the SAME progress phase/render path once it fires,
 * through `enterUpdateProgress()`. The countdown has no "confirmed" event of
 * its own: receiving `update_progress` at all, while a countdown was being
 * shown, IS the signal that it settled (see `onUpdateProgress`). Same
 * poll-is-truth discipline as above — `seconds_left` is always taken from
 * the server's response, a local per-second tick only re-derives it from the
 * polled `deadline` between polls, never counts down blindly on its own.
 */
'use strict';

const UPDATE_POLL_MS = 2000;
const UPDATE_POLL_TIMEOUT_MS = 5 * 60 * 1000; // 5 minutes of silence before giving up

// Ordered for display only — the real state machine is `apply.state` +
// `apply.step` from the backend (see updateStepIndex()).
const UPDATE_STEPS = [
  { key: 'download', label: 'Завантаження з GitHub' },
  { key: 'venv', label: 'Встановлення пакетів' },
  { key: 'switching', label: 'Перемикання версії та перезапуск' },
];

const updateModal = {
  root: null, box: null, title: null, closeBtn: null, body: null, foot: null,
  // 'idle' (not shown) | 'confirm' | 'auto-pending' | 'progress' | 'timeout' | 'terminal'
  phase: 'idle',
  // Observed, THIS session, that staging finished and handoff to the
  // detached `update-apply` began — i.e. the point past which a "failed"
  // outcome would mean the service itself was touched, not just staging.
  // Used only to word the terminal message; the state machine itself does
  // not depend on it.
  everSwitching: false,
  pollTimer: null,
  pollStartedAt: 0,
  // 'auto-pending' phase's own watch: a slower re-sync against the backend
  // (settlement/cancellation can happen without this tab doing anything —
  // another tab, or the timer firing server-side) plus a faster local tick
  // that only re-derives the display from the last polled `deadline`.
  autoPendingPollTimer: null,
  autoPendingTickTimer: null,
  autoPendingError: null,
  // 'terminal' phase, auto-triggered success only (see renderUpdateTerminal):
  // a purely client-side, non-resumable dismiss countdown — nothing to
  // persist across a reload, since this modal itself is never reopened by
  // refreshUpdateStatus() for an already-'done' apply.
  terminalAutoCloseTimer: null,
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
  stopAutoPendingWatch();
  stopTerminalAutoClose();
  updateModal.phase = 'idle';
  updateModal.root.hidden = true;
  renderSidebarUpdateBanner();
}

function stopTerminalAutoClose() {
  if (updateModal.terminalAutoCloseTimer) {
    clearInterval(updateModal.terminalAutoCloseTimer);
    updateModal.terminalAutoCloseTimer = null;
  }
}

function renderUpdateModal() {
  if (updateModal.phase === 'confirm') renderUpdateConfirm();
  else if (updateModal.phase === 'auto-pending') renderUpdateAutoPending();
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
// phase: auto-pending — unattended auto-apply's own countdown, independent
// of the confirm dialog above (design: backend commit 4f977b6, WS
// `update_auto_pending`, `GET /api/update/status`'s `"auto"` block)
// ---------------------------------------------------------------------------

/** Open the countdown modal and start watching it — the one entry point for
 *  all three ways of getting here: the WS event arriving with nothing else
 *  on screen, resuming one still pending after a reload, and reopening one
 *  from the sidebar banner after it was dismissed. */
function openAutoPendingModal() {
  updateModal.phase = 'auto-pending';
  updateModal.everSwitching = false;
  updateModal.autoPendingError = null;
  updateModal.root.hidden = false;
  renderUpdateModal();
  startAutoPendingWatch();
}

function renderUpdateAutoPending() {
  clear(updateModal.body);
  clear(updateModal.foot);
  updateModal.closeBtn.hidden = false;

  const u = state.update || {};
  const pending = (u.auto && u.auto.pending) || {};
  const tag = pending.tag || (u.latest_known && u.latest_known.tag) || '—';
  const secondsLeft = pending.seconds_left != null ? pending.seconds_left : 0;

  updateModal.body.appendChild(el('p', { className: 'upd-row' }, [
    document.createTextNode('Автоматичне оновлення до '),
    el('strong', { text: tag }),
    document.createTextNode(' почнеться через '),
    el('strong', { className: 'upd-countdown', text: String(secondsLeft) }),
    document.createTextNode(' с.'),
  ]));

  updateModal.body.appendChild(el('div', { className: 'tok-confirm' }, [
    el('p', {
      className: 'tok-confirm-text',
      text: 'Якщо нічого не натиснути, оновлення застосується автоматично. ' +
            '«Скасувати» лише відкладає його — ту саму версію може бути ' +
            'запропоновано знову під час наступної перевірки.',
    }),
  ]));

  if (updateModal.autoPendingError) {
    updateModal.body.appendChild(el('p', { className: 'upd-row modal-error', text: updateModal.autoPendingError }));
  }

  updateModal.foot.appendChild(el('button', {
    className: 'btn', text: 'Скасувати', on: { click: () => cancelAutoPending() },
  }));
  updateModal.foot.appendChild(el('button', {
    className: 'btn btn-primary', text: 'OK', on: { click: () => confirmAutoPending() },
  }));
}

/** Confirm now, skipping the rest of the countdown — still an "auto" trigger
 *  for blacklist purposes on the backend (its own docstring, verbatim). */
async function confirmAutoPending() {
  try {
    await api('/api/update/auto/confirm', { method: 'POST' });
  } catch (err) {
    if (isAuthError(err)) { openGate('rejected'); return; }
    if (err.code === 'auto_not_pending') {
      // Race: it already fired, or was cancelled from elsewhere — resync
      // from the truth rather than guess which.
      await pollAutoPendingOnce();
      return;
    }
    updateModal.autoPendingError = err.message;
    renderUpdateModal();
    return;
  }
  stopAutoPendingWatch();
  enterUpdateProgress(false);
}

async function cancelAutoPending() {
  try {
    await api('/api/update/auto/cancel', { method: 'POST' });
  } catch (err) {
    if (isAuthError(err)) { openGate('rejected'); return; }
    if (err.code === 'auto_not_pending') {
      await pollAutoPendingOnce();
      return;
    }
    updateModal.autoPendingError = err.message;
    renderUpdateModal();
    return;
  }
  stopAutoPendingWatch();
  // Touches nothing durable server-side either (backend docstring) — closing
  // here is enough, there is no state left to reflect.
  closeUpdateModal();
}

const AUTO_PENDING_POLL_MS = UPDATE_POLL_MS;
const AUTO_PENDING_TICK_MS = 1000;

function startAutoPendingWatch() {
  stopAutoPendingWatch();
  pollAutoPendingOnce();
  updateModal.autoPendingPollTimer = setInterval(pollAutoPendingOnce, AUTO_PENDING_POLL_MS);
  updateModal.autoPendingTickTimer = setInterval(tickAutoPendingDisplay, AUTO_PENDING_TICK_MS);
}

function stopAutoPendingWatch() {
  if (updateModal.autoPendingPollTimer) {
    clearInterval(updateModal.autoPendingPollTimer);
    updateModal.autoPendingPollTimer = null;
  }
  if (updateModal.autoPendingTickTimer) {
    clearInterval(updateModal.autoPendingTickTimer);
    updateModal.autoPendingTickTimer = null;
  }
}

/** The per-second display update between polls — re-derived from the last
 *  polled `deadline` (a fixed point in time), never a bare decrementing
 *  counter, so it cannot drift from what the server actually meant. */
function tickAutoPendingDisplay() {
  const pending = state.update && state.update.auto && state.update.auto.pending;
  if (!pending) return;
  const deadline = Date.parse(pending.deadline);
  if (!Number.isNaN(deadline)) {
    pending.seconds_left = Math.max(0, Math.round((deadline - Date.now()) / 1000));
  }
  if (updateModal.phase === 'auto-pending') renderUpdateModal();
  else renderSidebarUpdateBanner();
}

/** The re-sync half: catches settlement or cancellation that happened
 *  without this tab doing anything (another tab, the CLI, or the timer
 *  firing server-side) — same "poll is the truth" discipline as
 *  `pollUpdateStatusOnce` for the progress phase. */
async function pollAutoPendingOnce() {
  if (state.gated) return;
  let data;
  try {
    data = await api('/api/update/status');
  } catch (err) {
    return; // transient — same silent treatment as the progress poll
  }
  state.update = data;

  const apply = data.apply || {};
  if (apply.state === 'staging' || apply.state === 'switching') {
    stopAutoPendingWatch();
    if (updateModal.phase === 'auto-pending') enterUpdateProgress(apply.state === 'switching');
    else renderSidebarUpdateBanner();
    return;
  }

  if (!(data.auto && data.auto.pending)) {
    // Cancelled elsewhere, with nothing having started — nothing left to show.
    stopAutoPendingWatch();
    if (updateModal.phase === 'auto-pending') closeUpdateModal();
    else renderSidebarUpdateBanner();
    return;
  }

  if (updateModal.phase === 'auto-pending') renderUpdateModal();
  else renderSidebarUpdateBanner();
}

/** WS `update_auto_pending` — routed here from shell.js's `handleEvent`. */
function onUpdateAutoPending(data) {
  const u = state.update || (state.update = {});
  u.auto = u.auto || {};

  if (data.phase === 'started') {
    u.auto.pending = { tag: data.tag, deadline: data.deadline, seconds_left: data.seconds };
    // Surface it so there is time left to cancel — but never steal the
    // screen from something already open (an apply already running outranks
    // a countdown by construction: the backend never arms one while
    // `_apply_progress` is not idle/terminal).
    if (updateModal.phase === 'idle') {
      openAutoPendingModal();
      return;
    }
  } else if (data.phase === 'cancelled') {
    u.auto.pending = null;
    if (updateModal.phase === 'auto-pending') {
      stopAutoPendingWatch();
      closeUpdateModal();
      return;
    }
  }
  renderSidebarUpdateBanner();
}

// ---------------------------------------------------------------------------
// phase: progress — download -> install packages -> switch & restart
// ---------------------------------------------------------------------------

/**
 * Enter the progress phase and start polling.
 *
 * The shared tail of three separate entry points: resuming an in-flight
 * manual apply (reload, or a banner click while one is running — both
 * pre-existing), and now also the auto-apply countdown settling, whichever
 * way it settles (this tab's own confirm click, the timer firing, or
 * `update_progress` arriving because it settled somewhere else entirely).
 * `confirmUpdateApply` below does NOT use this — it renders the progress
 * phase optimistically, before the `POST /api/update/apply` call it is
 * waiting on even resolves, and that ordering is deliberate and unrelated to
 * resuming/settling into an apply already known to be running.
 */
function enterUpdateProgress(everSwitching) {
  updateModal.phase = 'progress';
  updateModal.everSwitching = !!everSwitching;
  updateModal.root.hidden = false;
  renderUpdateModal();
  startUpdatePolling();
}

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
          'могла ще перезапускатися, або консоль тимчасово не звʼязується з ' +
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
  stopTerminalAutoClose(); // re-render must not stack a second interval

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

  // Auto-triggered AND successful only — a failed/rolled-back outcome needs
  // a human's attention regardless of trigger, and a manual click already
  // means someone is watching this screen right now. Decided live with the
  // user (2026-08-22): unattended updates should not leave a stale "click
  // to dismiss" dialog sitting on screen indefinitely.
  const autoCloseRow = (apply.state === 'done' && apply.trigger === 'auto')
    ? el('p', { className: 'upd-row upd-terminal-autoclose' }, [
        document.createTextNode('Закриється автоматично через '),
        el('strong', { className: 'upd-countdown', text: '10' }),
        document.createTextNode(' с.'),
      ])
    : null;
  if (autoCloseRow) updateModal.body.appendChild(autoCloseRow);

  updateModal.foot.appendChild(el('button', {
    className: 'btn btn-primary', text: 'Закрити', on: { click: () => closeUpdateModal() },
  }));

  if (autoCloseRow) {
    const countdownEl = autoCloseRow.querySelector('.upd-countdown');
    let secondsLeft = 10;
    updateModal.terminalAutoCloseTimer = setInterval(() => {
      secondsLeft -= 1;
      if (secondsLeft <= 0) { closeUpdateModal(); return; }
      if (countdownEl) countdownEl.textContent = String(secondsLeft);
    }, 1000);
  }
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
  const u = state.update || (state.update = {});

  // There is no "settled" phase on `update_auto_pending` (see file header):
  // receiving progress at all, while a countdown was on screen, IS that
  // signal — auto or not, an apply has now begun. Clear it and, if this tab
  // was showing the countdown, follow it straight into the progress phase.
  if (u.auto && u.auto.pending) u.auto.pending = null;
  if (updateModal.phase === 'auto-pending') {
    stopAutoPendingWatch();
    enterUpdateProgress(false);
  }

  u.apply = u.apply || {};
  u.apply.tag = data.tag;
  u.apply.step = data.step;

  if (data.step === 'failed') {
    // Staging itself failed — always pre-switch (see stage_release's own
    // contract: it never touches `current`), so this is authoritative and
    // does not need to wait for a poll to confirm it.
    u.apply.state = 'failed';
    u.apply.error = data.error || null;
    if (updateModal.phase === 'progress') {
      stopUpdatePolling();
      updateModal.phase = 'terminal';
      renderUpdateModal();
    } else {
      renderSidebarUpdateBanner();
    }
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
  // Unlike the manual flow (always watched — the user themselves opened the
  // progress phase to get here), an auto-triggered apply can run with the
  // modal closed the whole time. The sidebar's own busy banner must still
  // track it either way; only the modal render is conditional on watching.
  if (updateModal.phase === 'progress') renderUpdateModal();
  else renderSidebarUpdateBanner();
}

// ---------------------------------------------------------------------------
// sidebar banner
// ---------------------------------------------------------------------------

// A local build's tag carries its base release plus a lowercase "l" marker
// ("v3.0.1l", install.ps1/install.sh's Get-LocalCheckoutVersionTag /
// get_local_checkout_version_tag scheme, 2026-08-22) -- strip it before
// comparing to a release tag, mirroring engine_update.py's own
// base_version_tag(). Without this, a local build sitting on top of the
// latest release can never string-match it and nags "update available"
// forever, offering to overwrite its own fixes with the vanilla release.
function baseVersionTag(tag) {
  return tag ? tag.replace(/(\d)l$/, '$1') : tag;
}

function shouldShowUpdateBanner(u) {
  if (!u || !u.latest_known || !u.latest_known.update_available) return false;
  // See file header: `update_available` is not recomputed after a switch,
  // so a tag comparison against `current` is the narrower, correct guard.
  if (u.current && u.current.tag && baseVersionTag(u.current.tag) === u.latest_known.tag) return false;
  return true;
}

/**
 * Three live states now, in precedence order: an apply actually running
 * outranks everything (shown regardless of what modal is or isn't open —
 * pre-existing behaviour, unchanged); a live countdown outranks the plain
 * "available" banner (the two never coexist — the backend only ever arms a
 * countdown while `_apply_progress` is idle/terminal); the plain banner is
 * the fallback, and only while nothing else is open.
 */
function renderSidebarUpdateBanner() {
  const box = $('sb-update-banner');
  if (!box) return;
  const u = state.update;
  const apply = (u && u.apply) || {};
  const auto = (u && u.auto) || {};
  const busy = apply.state === 'staging' || apply.state === 'switching';

  if (busy) {
    box.hidden = false;
    box.classList.add('is-busy');
    box.textContent = 'Оновлення mnemo триває…';
    return;
  }
  box.classList.remove('is-busy');

  if (auto.pending) {
    box.hidden = false;
    box.textContent = 'Автооновлення до ' + auto.pending.tag + ' очікує підтвердження';
    return;
  }

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
  const auto = (u && u.auto) || {};
  if (apply.state === 'staging' || apply.state === 'switching') {
    // Reopen the progress view onto an update already in flight (e.g. the
    // page was reloaded mid-update) rather than offering a second confirm.
    enterUpdateProgress(apply.state === 'switching');
    return;
  }
  if (auto.pending) {
    // Reopen the countdown rather than the plain confirm dialog — it was
    // dismissed, not cancelled, and is still counting down on the backend.
    openAutoPendingModal();
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
    enterUpdateProgress(apply.state === 'switching');
    return;
  }
  if (data.auto && data.auto.pending) {
    // Same resume, for a countdown already ticking when this tab (re)loaded
    // — server-authoritative `seconds_left`, so the remaining time is
    // correct from the first render, not guessed from `deadline` alone.
    openAutoPendingModal();
    return;
  }
  renderSidebarUpdateBanner();
}
