/* Памʼять: Банки → Файли → Вміст. A close-to-mechanical port of the current
 * live console's three-pane workspace — same ids, same behaviour — plus the
 * header summary line and the mobile pane switch the new shell needs now
 * that the three panes are no longer always side by side.
 */
'use strict';

/**
 * A bank's registry state: 'enabled' | 'frozen' | 'disabled'.
 *
 * `status` and `state` are different questions and the card shows both:
 * `status` is what the index is doing right now (ready / indexing / empty),
 * `state` is what the user set it to. A frozen bank reads `ready` — its index
 * is complete, it simply stopped following the files.
 *
 * Falls back to the boolean it replaced, so the page keeps working against a
 * backend older than this field.
 */
function bankState(bank) {
  if (bank.state) return bank.state;
  return bank.enabled === false ? 'disabled' : 'enabled';
}

const BANK_STATE_LABEL = {
  enabled: 'Активний',
  frozen: 'Заморожений',
  disabled: 'Вимкнений',
};

const BANK_STATE_NOTE = {
  enabled: 'Стежимо за файлами, індекс оновлюється сам, пошук працює.',
  frozen: 'За файлами не стежимо — індекс лишається як є, але пошук працює. ' +
          'Це те, що рятує від повної перебудови при зміні моделі.',
  disabled: 'Не стежимо й не шукаємо. Банк лишається в реєстрі.',
};

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
// header
// ---------------------------------------------------------------------------

let mobPane = 'banks';

function memoryHeaderHtml() {
  const banks = state.banks;
  const files = banks.reduce((sum, b) => sum + (b.files || 0), 0);
  const chunks = banks.reduce((sum, b) => sum + (b.chunks || 0), 0);
  const mob = (id, label) =>
    '<button class="seg' + (mobPane === id ? ' is-active' : '') + '" data-mob="' + id + '">' +
    label + '</button>';
  return '<span class="page-title">Памʼять</span>' +
    '<span class="page-sub">' +
    banks.length + ' ' + pluralizeUk(banks.length, ['банк', 'банки', 'банків']) + ' · ' +
    files + ' ' + pluralizeUk(files, ['файл', 'файли', 'файлів']) + ' · ' +
    chunks + ' ' + pluralizeUk(chunks, ['чанк', 'чанки', 'чанків']) +
    '</span>' +
    '<div class="grow"></div>' +
    '<div class="segmented mob-panes" aria-label="Панель">' +
      mob('banks', 'Банки') + mob('tree', 'Файли') + mob('file', 'Вміст') +
    '</div>' +
    '<button class="btn btn-sm" id="add-bank" title="Зареєструвати нову теку з .md як банк">' +
      '＋ Додати банк</button>';
}

/**
 * New: a single-page layout never needed a pane switch — the three columns
 * were always side by side. Under 940px they cannot be, so the header's
 * segmented control (above) picks which of the three fills the page.
 */
function showPane(which) {
  mobPane = which;
  for (const pane of document.querySelectorAll('[data-mob-panel]')) {
    pane.classList.toggle('is-mob', pane.dataset.mobPanel === which);
  }
  if (state.page === 'memory') renderHeader();
}

// ---------------------------------------------------------------------------
// stale-provider rebuild notice
// ---------------------------------------------------------------------------
//
// Separate from `#banner`, which is an error channel. A provider change is not
// a failed request: it is durable machine state with a remedy, and hiding it
// because some later request succeeded would be the same silence that made the
// settings switch look as if it did nothing.

const rebuildNotice = { root: null, text: null, action: null };
const rebuildDialog = {
  root: null, body: null, submit: null,
  banks: [], busy: false, errorText: null,
};

function pendingRebuilds() {
  const pending = state.banks.filter((bank) => !!bank.rebuild_pending);
  return {
    actionable: pending.filter((bank) =>
      bankState(bank) !== 'disabled' && bank.status !== 'indexing' && !bank.indexing),
    running: pending.filter((bank) =>
      bankState(bank) !== 'disabled' && (bank.status === 'indexing' || bank.indexing)),
    disabled: pending.filter((bank) => bankState(bank) === 'disabled'),
  };
}

function buildRebuildNotice() {
  rebuildNotice.text = el('div', { className: 'rebuild-banner-text' });
  rebuildNotice.action = el('button', {
    className: 'btn',
    text: 'Перегенерувати',
    on: { click: () => openRebuildDialog() },
  });
  rebuildNotice.root = el('div', {
    className: 'rebuild-banner',
    attrs: { hidden: '', role: 'status' },
  }, [rebuildNotice.text, rebuildNotice.action]);

  // Lives inside the Памʼять page (it is what pending rebuilds affect), as
  // its first child — not next to the shared `#banner`, which is page-
  // agnostic and must not carry a fact that is only true here.
  const panel = document.querySelector('[data-panel="memory"]');
  panel.insertBefore(rebuildNotice.root, panel.firstChild);
}

function renderRebuildNotice() {
  if (!rebuildNotice.root) return;
  const groups = pendingRebuilds();
  const total = groups.actionable.length + groups.running.length + groups.disabled.length;
  rebuildNotice.root.hidden = total === 0;
  if (!total) return;

  const parts = [];
  if (groups.actionable.length) {
    parts.push(groups.actionable.length + ' банк(и) мають індекс від попередньої моделі');
  }
  if (groups.running.length) {
    parts.push(groups.running.length + ' вже перегенеровуються');
  }
  if (groups.disabled.length) {
    parts.push(groups.disabled.length + ' вимкнено — спершу їх треба увімкнути');
  }
  rebuildNotice.text.textContent = parts.join(' · ') +
    '. Пошук по застарілих векторах відмовляє, а не змішує два простори.';
  rebuildNotice.action.hidden = groups.actionable.length === 0;
  rebuildNotice.action.disabled = rebuildDialog.busy;
}

function buildRebuildDialog() {
  rebuildDialog.body = el('div', { className: 'modal-body' });
  rebuildDialog.submit = el('button', {
    className: 'btn btn-primary',
    text: 'Перегенерувати',
    on: { click: () => submitPendingRebuilds() },
  });
  const box = el('div', {
    className: 'modal-box',
    attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Перегенерувати індекси' },
    on: { click: (ev) => ev.stopPropagation() },
  }, [
    el('div', { className: 'modal-head' }, [
      el('h2', { text: 'Перегенерувати індекси' }),
      el('button', {
        className: 'btn btn-ghost', text: '✕', title: 'Закрити (Esc)',
        on: { click: () => closeRebuildDialog() },
      }),
    ]),
    rebuildDialog.body,
    el('div', { className: 'modal-foot' }, [
      el('button', {
        className: 'btn', text: 'Скасувати',
        on: { click: () => closeRebuildDialog() },
      }),
      rebuildDialog.submit,
    ]),
  ]);
  rebuildDialog.root = el('div', {
    className: 'modal', attrs: { hidden: '' },
    on: { click: () => closeRebuildDialog() },
  }, [box]);
  document.body.appendChild(rebuildDialog.root);

  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && !rebuildDialog.root.hidden) closeRebuildDialog();
  });
}

function openRebuildDialog() {
  rebuildDialog.banks = pendingRebuilds().actionable;
  if (!rebuildDialog.banks.length) return;
  rebuildDialog.errorText = null;
  rebuildDialog.busy = false;
  rebuildDialog.root.hidden = false;
  renderRebuildDialog();
}

function closeRebuildDialog() {
  if (rebuildDialog.busy) return;
  rebuildDialog.root.hidden = true;
  rebuildDialog.banks = [];
  rebuildDialog.errorText = null;
}

function renderRebuildDialog() {
  clear(rebuildDialog.body);
  rebuildDialog.body.appendChild(el('p', {
    className: 'rm-lead',
    text: 'Повний реіндекс буде поставлено для ' + rebuildDialog.banks.length +
          ' банк(ів). Старі derived-індекси буде стерто й зібрано з .md заново.',
  }));

  const list = el('div', { className: 'set-stats' });
  for (const bank of rebuildDialog.banks) {
    list.appendChild(setStat(bank.name, bank.chunks + ' чанків', true));
  }
  rebuildDialog.body.appendChild(list);
  rebuildDialog.body.appendChild(el('p', {
    className: 'set-note',
    text: 'Файли .md не змінюються. Час пропорційний обсягу; конкретна ' +
          'швидкість залежить від бекенда й заліза цієї машини.',
  }));
  if (rebuildDialog.errorText) {
    rebuildDialog.body.appendChild(el('p', {
      className: 'modal-error', text: rebuildDialog.errorText,
    }));
  }
  rebuildDialog.submit.disabled = rebuildDialog.busy || !rebuildDialog.banks.length;
  rebuildDialog.submit.textContent = rebuildDialog.busy ? 'Ставимо в чергу…' : 'Перегенерувати';
}

async function submitPendingRebuilds() {
  const banks = rebuildDialog.banks.slice();
  if (!banks.length || rebuildDialog.busy) return;
  rebuildDialog.busy = true;
  rebuildDialog.errorText = null;
  renderRebuildDialog();

  const outcomes = await Promise.allSettled(
    banks.map((bank) => requestReindex(bank, { full: true }))
  );
  const failed = [];
  outcomes.forEach((outcome, index) => {
    const bank = banks[index];
    if (outcome.status === 'fulfilled') {
      const res = outcome.value;
      setNote(bank.id, 'поставлено: повний реіндекс · у черзі ' + res.queued +
                       ' · task ' + (res.task_ids || []).join(', '));
    } else {
      failed.push({ bank: bank, error: outcome.reason });
    }
  });

  const authFailure = failed.find((item) => isAuthError(item.error));
  if (authFailure) {
    rebuildDialog.busy = false;
    closeRebuildDialog();
    reportError(authFailure.error);
    return;
  }

  await Promise.all([loadBanks().catch(() => {}), loadStatus().catch(() => {})]);
  rebuildDialog.busy = false;
  renderRebuildNotice();
  if (!failed.length) {
    closeRebuildDialog();
    return;
  }
  rebuildDialog.banks = failed.map((item) => item.bank);
  rebuildDialog.errorText = failed.map((item) =>
    item.bank.name + ': ' + (item.error && item.error.message ? item.error.message : item.error)
  ).join(' · ');
  renderRebuildDialog();
}

// ---------------------------------------------------------------------------
// banks
// ---------------------------------------------------------------------------

function renderBanks() {
  const list = $('banks-list');
  clear(list);
  renderRebuildNotice();

  if (!state.banks.length) {
    list.appendChild(el('p', {
      className: 'empty-hint',
      text: 'Жодного банку не зареєстровано — «＋ Додати банк» у шапці вибирає теку з .md.',
    }));
    syncTicker();
    if (state.page === 'memory') renderHeader();
    updateSidebarCounts();
    return;
  }

  for (const bank of state.banks) {
    list.appendChild(bankCard(bank));
  }
  syncTicker();
  // The header's aggregate counts and the sidebar's bank count both read
  // `state.banks` — repaint them whenever the list changes.
  if (state.page === 'memory') renderHeader();
  updateSidebarCounts();
}

function bankCard(bank) {
  const selected = bank.id === state.selectedBankId;
  const classes = ['bank'];
  if (selected) classes.push('is-selected');
  if (bankState(bank) === 'disabled') classes.push('is-disabled');

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
  if (bankState(bank) === 'frozen') {
    // The warning IS the badge. A frozen bank keeps answering searches out of
    // an index that no longer follows the files, and nothing else on the card
    // says so — `status: ready` and a chunk count both look entirely healthy.
    badges.push(el('span', {
      className: 'badge badge-frozen',
      text: 'заморожено',
      title: 'Індекс не оновлюється — файли могли змінитись після ' +
             fmtDateTime(bank.last_indexed) +
             '. Пошук працює й відповідає за тим станом.',
    }));
  }
  if (bankState(bank) === 'disabled') {
    badges.push(el('span', { className: 'badge badge-off', text: 'вимкнено' }));
  }
  if (bank.exists === false) {
    badges.push(el('span', { className: 'badge badge-off', text: 'нема кореня' }));
  }

  // Human-facing address is the name, never the hash (lead amendment); the id
  // stays available as a tooltip for debugging.
  //
  // Every action lives in the menu at the end of this row. Name and badges
  // wrap together inside their own box; the menu button sits outside it and
  // cannot be pushed onto a line of its own.
  const head = el('div', { className: 'bank-row' }, [
    el('div', { className: 'bank-head' }, [
      el('span', {
        className: 'bank-name', text: bank.name, title: 'id: ' + bank.id,
      }),
      ...badges,
    ]),
    el('button', {
      className: 'btn btn-menu',
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
    }, [
      // The glyph is hidden from the accessible name, which is `aria-label`
      // alone: a screen reader that read both would announce "···, Дії над
      // банком" — visible text the accessible name does not contain, which
      // fails WCAG 2.5.3 (the mockup's own button already does this).
      el('span', { text: '···', attrs: { 'aria-hidden': 'true' } }),
    ]),
  ]);

  const stats = el('div', { className: 'bank-stats' }, [
    el('span', { text: 'файлів ' + bank.files }),
    el('span', { text: 'чанків ' + bank.chunks }),
    // An empty queue is the normal state, not a fact worth a stat of its own —
    // shown only once there is actually something in it.
    bank.queued ? el('span', { text: 'у черзі ' + bank.queued }) : null,
    el('span', { text: fmtBytes(bank.db_bytes), title: 'розмір індексу' }),
  ]);

  // `statusNote` earns its own line only when it says something the status
  // badge above does not — the plain "ready" reading is the same fact twice.
  // While actively indexing it gets the same warm `--busy` tint as the badge
  // and the live progress line below it, instead of the same flat grey as
  // "остання індексація" — everything temporary reads as one group, the one
  // durable fact (when the index last actually finished) reads as another.
  const noteRow = bank.status === 'ready' ? null : el('div', { className: 'bank-stats' }, [
    el('span', {
      className: bank.status === 'indexing' ? 'note-live' : 'muted',
      text: statusNote(bank),
    }),
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
      el('span', { className: 'muted', text: 'остання індексація: ' + fmtDateTime(bank.last_indexed) }),
    ]),
  ]);

  // Placed after the permanent facts above and right before the live
  // progress block below — everything that only describes "right now"
  // clusters at the bottom of the card instead of sitting between two
  // durable facts.
  if (noteRow) card.appendChild(noteRow);

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
  // REST is authoritative for this bank's pending set (contract: `api_tree`'s
  // `pending` field) — replaces rather than merges, so a file cleared or
  // queued while the tree pane was closed is picked up correctly on reopen.
  state.pendingFiles.set(bank.id, new Set(data.pending || []));
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

  sub.textContent =
    state.tree.files + ' ' + pluralizeUk(state.tree.files, ['файл', 'файли', 'файлів']) + ' · ' +
    state.tree.dirs + ' ' + pluralizeUk(state.tree.dirs, ['директорія', 'директорії', 'директорій']);

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
  // Keyed on the selected bank alone — a set for a different bank simply
  // isn't looked up here, so switching banks can never leak one bank's
  // highlights onto another's tree.
  const pending = state.pendingFiles.get(state.selectedBankId);
  if (pending && pending.has(node.path)) classes.push('is-pending');

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
  showPane('file');
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
  const button = $('file-reindex');
  clear(body);

  // `#file-title` stays the static "Вміст" caption from index.html — same
  // small-caps treatment as "Банки"/"Файли" above it. The path used to be
  // written in its place, which is why it rendered in a different (lowercase,
  // mono) style from the other two column headers; it now lives as its own
  // line inside `.file-meta`, below.
  const file = state.file;
  if (!file) {
    button.disabled = true;
    body.appendChild(el('p', { className: 'empty-hint', text: 'Оберіть файл у дереві.' }));
    return;
  }

  button.disabled = false;

  const chunks = (file.chunks || []).slice().sort((a, b) => a.start_char - b.start_char);

  body.appendChild(el('div', { className: 'file-meta' }, [
    el('div', { className: 'file-meta-path', text: file.path, title: file.path }),
    el('div', { className: 'file-meta-info' }, [
      el('span', { text: fmtBytes(file.size) }),
      el('span', { text: file.indexed ? 'в індексі' : 'не в індексі' }),
      el('span', { text: chunks.length + ' чанків' }),
      el('span', { text: 'sha256 ' + String(file.sha256 || '').slice(0, 12), title: file.sha256 }),
    ]),
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
  // raw number is the hit list in the journal, which stays 0-based on purpose
  // — it is a locator, not prose. So the two differ by one by design.
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
// resizable columns
// ---------------------------------------------------------------------------

const PANE_WIDTH_MIN = 180;
const PANE_WIDTH_MAX = 640;
const PANE_WIDTH_DEFAULT = [369, 484];

function clampPaneWidth(px) {
  return Math.min(PANE_WIDTH_MAX, Math.max(PANE_WIDTH_MIN, Math.round(px)));
}

/** The stored [Банки, Файли] widths, or the default when nothing was ever
 *  dragged (or the stored value is corrupt — same "fall through, don't
 *  throw" rule as every other localStorage read in this console). */
function loadPaneWidths() {
  try {
    const raw = JSON.parse(localStorage.getItem('mnemo_pane_widths'));
    if (Array.isArray(raw) && raw.length === 2 && Number.isFinite(raw[0]) && Number.isFinite(raw[1])) {
      return [clampPaneWidth(raw[0]), clampPaneWidth(raw[1])];
    }
  } catch (_) { /* no stored value, or a corrupt one */ }
  return PANE_WIDTH_DEFAULT.slice();
}

function applyPaneWidths(widths) {
  document.querySelector('.layout').style.gridTemplateColumns =
    widths[0] + 'px 6px ' + widths[1] + 'px 6px minmax(0, 1fr)';
}

/**
 * Wire both handles to drag-resize the Банки/Файли columns; Вміст always
 * takes what is left (`minmax(0, 1fr)`), so only two widths ever need
 * tracking or storing.
 *
 * Restored here rather than pre-paint (mirrors the `mnemo_sidebar` pattern,
 * `shell.js`): a column a few pixels off its saved width for one frame is a
 * minor resize, not the color flash a late theme switch would be.
 */
function wirePaneResizers() {
  const widths = loadPaneWidths();
  applyPaneWidths(widths);

  const wireOne = (id, index) => {
    let base = 0;
    wireColumnResizer($(id), {
      onStart: () => { base = widths[index]; },
      onDrag: (deltaX) => {
        widths[index] = clampPaneWidth(base + deltaX);
        applyPaneWidths(widths);
      },
      onCommit: () => localStorage.setItem('mnemo_pane_widths', JSON.stringify(widths)),
    });
  };
  wireOne('pane-resizer-1', 0);
  wireOne('pane-resizer-2', 1);
}

// ---------------------------------------------------------------------------
// wiring (static elements — this pane's markup never gets rebuilt wholesale)
// ---------------------------------------------------------------------------

$('banks-refresh').addEventListener('click', () => {
  loadBanks().catch(reportError);
  loadStatus().catch(() => {});
});

$('chunkviz-toggle').addEventListener('change', (ev) => {
  state.chunkViz = ev.target.checked;
  renderFile();
});

$('file-reindex').addEventListener('click', () => {
  const bank = bankById(state.selectedBankId);
  if (bank && state.filePath) reindex(bank, { path: state.filePath });
});

wirePaneResizers();

buildRebuildNotice();
buildRebuildDialog();
