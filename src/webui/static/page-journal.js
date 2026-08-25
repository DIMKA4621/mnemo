/* Журнал: master-detail — event cards on the left, full detail on the
 * right. New page (the mockup's one genuine rewrite); driven by the same
 * `GET /api/logs` (contract 9.5) the old table view used, no backend change.
 *
 * Dropped from the mockup on purpose (no backend filter exists for either,
 * confirmed with the user): the `status` select and the free-text `jq`
 * search. `bank` and `period` stay — the API already accepts `bank`/`since`.
 *
 * The mockup's quoted "snapshot" text under each hit (`.hit-snap`) is landed:
 * each hit's `content` (contract 7.2) is a per-event snapshot — rows written
 * before the field existed simply lack it, which reads the same as `null`.
 * No "text unavailable" placeholder for that case either: that branch is
 * still not drawn, only omitted, not faked.
 */
'use strict';

function bankLabel(bankId) {
  const bank = bankById(bankId);
  return bank ? bank.name : (bankId || '—');
}

// ---------------------------------------------------------------------------
// header + filters
// ---------------------------------------------------------------------------

function journalHeaderHtml() {
  const seg = (id, label) =>
    '<button class="seg' + (state.logKind === id ? ' is-active' : '') + '" data-kind="' + id + '">' +
    label + '</button>';
  const refreshTitle = t('journal.header.refreshTitle');
  return '<span class="page-title">' + t('shell.nav.journal') + '</span>' +
    '<div class="segmented" id="jkind">' +
      seg('query', t('journal.header.segQuery')) + seg('index', t('journal.header.segIndex')) +
    '</div>' +
    '<div class="grow"></div>' +
    '<button class="btn btn-ghost btn-icon btn-sm" id="journal-refresh" ' +
      'title="' + refreshTitle + '" aria-label="' + refreshTitle + '">↻</button>';
}

function setLogKind(kind) {
  if (state.logKind === kind) return;
  state.logKind = kind;
  renderHeader();
  loadLogs().catch(reportError);
}

/** The bank options mirror `state.banks` — refreshed on every entry to this
 *  page, since that is cheap and a newly-added bank should show up here. */
function populateBankFilter() {
  const select = $('jbank');
  const current = select.value;
  clear(select);
  select.appendChild(el('option', { text: t('journal.filter.allBanks'), attrs: { value: '' } }));
  for (const bank of state.banks) {
    select.appendChild(el('option', { text: bank.name, attrs: { value: bank.name } }));
  }
  if ([...select.options].some((o) => o.value === current)) select.value = current;
}

const PERIOD_HOURS = { '1h': 1, '24h': 24, '7d': 24 * 7, '30d': 24 * 30 };

function periodSinceIso(period) {
  const hours = PERIOD_HOURS[period] || 24;
  return new Date(Date.now() - hours * 3600 * 1000).toISOString();
}

$('jbank').addEventListener('change', (ev) => {
  state.logBank = ev.target.value;
  loadLogs().catch(reportError);
});

$('jperiod').addEventListener('change', (ev) => {
  state.logPeriod = ev.target.value;
  loadLogs().catch(reportError);
});

// ---------------------------------------------------------------------------
// data
// ---------------------------------------------------------------------------

async function loadLogs() {
  const params = new URLSearchParams({
    kind: state.logKind, limit: '200', offset: '0',
    since: periodSinceIso(state.logPeriod),
  });
  if (state.logBank) params.set('bank', state.logBank);
  const data = await api('/api/logs?' + params.toString());
  state.logRows = data.events || [];
  state.logTotal = data.total || 0;
  renderJournal();
  updateSidebarCounts();
}

/** A live WS event is prepended only when the current filter would include it. */
function pushLiveLog(kind, row) {
  if (state.logKind !== kind) return;
  if (state.logBank && bankLabel(row.bank_id) !== state.logBank) return;
  row.hits = row.hits || [];
  state.logRows.unshift(row);
  if (state.logRows.length > 200) state.logRows.pop();
  state.logTotal += 1;
  renderJournal();
  updateSidebarCounts();
}

// ---------------------------------------------------------------------------
// list
// ---------------------------------------------------------------------------

function indexEventTitle(ev) {
  if (ev.path) return ev.path;
  if (ev.kind === 'rebuild') return t('journal.event.rebuildTitle');
  if (ev.kind === 'prune') return t('journal.event.pruneTitle');
  return t('journal.event.syncTitle');
}

/**
 * A `.badge` pill, not the old dot-plus-word: in `.ev-bot`/`.d-kick` the
 * status sat in the same weight as the bank name, the trigger and the
 * timestamp next to it — four identical grey mono spans in a row read as
 * one blurred line, and status is the one fact worth seeing first.
 */
function evStatusBadgeClass(ev) {
  if (state.logKind === 'query') {
    if (ev.status === 'indexing') return 'badge-indexing';
    if (ev.status === 'empty') return 'badge-empty';
    return 'badge-ready';
  }
  if (ev.result === 'error') return 'badge-off';
  if (ev.result === 'skipped') return 'badge-empty';
  return 'badge-ready';
}

function evStatusWord(ev) {
  if (state.logKind === 'query') return statusLabel(ev.status);
  return ev.result === 'error' ? t('journal.event.errorStatus') : ev.result;
}

function evStatusBadge(ev) {
  return el('span', { className: 'badge ' + evStatusBadgeClass(ev), text: evStatusWord(ev) });
}

function evCard(ev, selected) {
  const isQuery = state.logKind === 'query';
  const title = isQuery ? ev.query : indexEventTitle(ev);
  const n = isQuery ? ev.n_hits : ev.chunks_indexed;
  const metaWord = isQuery ? ev.face : ev.trigger;

  return el('button', {
    className: 'ev' + (selected ? ' is-selected' : ''),
    attrs: { 'data-ev': ev.id },
    on: {
      click: () => {
        state.logSelected[state.logKind] = ev.id;
        renderList();
        renderDetail();
      },
    },
  }, [
    el('div', { className: 'ev-top' }, [
      el('span', { className: 'ev-q', text: title }),
      el('span', { className: 'ev-n' }, [
        document.createTextNode(String(n)),
        el('small', { text: fmtMs(ev.took_ms) }),
      ]),
    ]),
    el('div', { className: 'ev-bot' }, [
      evStatusBadge(ev),
      el('span', { text: bankLabel(ev.bank_id) }),
      el('span', { text: metaWord || '—' }),
      el('span', { className: 't', text: fmtDateTime(ev.ts) }),
    ]),
  ]);
}

function renderList() {
  const listEl = $('jlist');
  const countEl = $('jcount');
  clear(listEl);
  countEl.textContent = state.logRows.length
    ? t('journal.list.shownOf', { shown: state.logRows.length, total: state.logTotal })
    : t('journal.list.empty');

  if (!state.logRows.length) {
    listEl.appendChild(el('p', { className: 'empty-hint', text: t('journal.list.noEvents') }));
    return;
  }

  const selectedId = state.logSelected[state.logKind];
  for (const ev of state.logRows) {
    listEl.appendChild(evCard(ev, ev.id === selectedId));
  }
}

// ---------------------------------------------------------------------------
// detail
// ---------------------------------------------------------------------------

function factsRow(pairs) {
  return el('div', { className: 'd-facts' }, pairs.map(([label, value]) => el('div', {}, [
    el('div', { className: 'd-k', text: label }),
    el('div', { className: 'd-v', text: value }),
  ])));
}

function numBox(value, label) {
  return el('div', {}, [
    el('div', { className: 'num', text: String(value) }),
    el('div', { className: 'num-k', text: label }),
  ]);
}

function fmtScore(v) {
  return v == null ? '—' : Number(v).toFixed(4);
}

/** "Відкрити файл" switches both the bank and the page, then opens the
 *  file — reuses `selectBank()`/`openFile()` from page-memory.js, which is
 *  exactly why that file loads before this one. It lands on Памʼять either
 *  way, so the button says what it does (opens the file), not where it
 *  happens to land. */
function openInMemory(bankId, path) {
  const bank = bankById(bankId);
  if (!bank) return;
  selectBank(bank.id);
  setPage('memory');
  openFile(path);
}

/**
 * Splits on sentence-ending punctuation followed by whitespace. Good enough
 * for a preview cutoff — this is not a parser, and a chunk that never hits
 * `. `/`? `/`! ` (a bullet list, a code block) just comes back as one
 * "sentence", which correctly skips the preview below.
 */
function splitSentences(text) {
  return text.match(/[^.!?…]+[.!?…]+(\s+|$)|[^.!?…]+$/g) || [text];
}

const HIT_SNAP_PREVIEW_SENTENCES = 2;

/**
 * The snapshot block: first two sentences, with a toggle that expands to
 * the full chunk **in the same block** rather than a separate area — the
 * mockup's "show more" jumping the reader to a fresh box elsewhere read as
 * navigating away from what they were just reading.
 */
function hitSnapBlock(content) {
  const trimmed = content.trim();
  const sentences = splitSentences(trimmed);
  const box = el('div', { className: 'hit-snap' });
  if (sentences.length <= HIT_SNAP_PREVIEW_SENTENCES) {
    box.textContent = trimmed;
    return box;
  }
  const preview = sentences.slice(0, HIT_SNAP_PREVIEW_SENTENCES).join('').trim();
  const textEl = el('span', { text: preview });
  const toggle = el('button', {
    className: 'hit-snap-more',
    text: t('journal.hit.showMore'),
    on: {
      click: () => {
        const expanded = box.classList.toggle('is-expanded');
        textEl.textContent = expanded ? trimmed : preview;
        toggle.textContent = expanded ? t('journal.hit.collapse') : t('journal.hit.showMore');
      },
    },
  });
  box.appendChild(textEl);
  box.appendChild(document.createTextNode(' '));
  box.appendChild(toggle);
  return box;
}

function hitRow(hit, index, bankId) {
  const children = [
    el('div', { className: 'hit-top' }, [
      el('span', { className: 'hit-r', text: String(index + 1) }),
      el('div', { className: 'hit-l' }, [
        el('div', { className: 'hit-p', text: hit.path }),
        el('div', {
          className: 'hit-h',
          text: t('journal.hit.chunkLabel', { heading: hit.heading || '—', n: hit.chunk_index }),
        }),
      ]),
      el('span', { className: 'hit-s' }, [
        document.createTextNode('score ' + fmtScore(hit.score)),
        el('br'),
        document.createTextNode('sim ' + fmtScore(hit.sim)),
      ]),
    ]),
  ];
  if (hit.content) children.push(hitSnapBlock(hit.content));
  children.push(
    el('div', { className: 'hit-foot' }, [
      el('button', {
        className: 'btn btn-sm',
        text: t('journal.hit.openFile'),
        on: { click: () => openInMemory(bankId, hit.path) },
      }),
    ]),
  );
  return el('article', { className: 'hit' }, children);
}

function renderQueryDetail(box, ev) {
  box.appendChild(el('div', { className: 'd-kick' }, [
    el('span', { text: t('journal.detail.queryKicker', { id: ev.id }) }),
    evStatusBadge(ev),
  ]));
  box.appendChild(el('h2', { className: 'd-h', text: ev.query }));
  box.appendChild(factsRow([
    [t('journal.detail.bank'), bankLabel(ev.bank_id)],
    [t('journal.detail.face'), ev.face],
    [t('journal.detail.prefix'), ev.path_prefix || '—'],
    [t('journal.detail.hits'), String(ev.n_hits)],
    [t('journal.detail.tookMs'), fmtMs(ev.took_ms)],
    [t('journal.detail.when'), fmtDateTime(ev.ts)],
  ]));
  box.appendChild(el('div', { className: 'd-sec' }, [
    document.createTextNode(t('journal.detail.resultsLabel') + ' '),
    el('span', { className: 'muted', text: t('journal.detail.resultsOrderNote') }),
  ]));

  const hits = ev.hits || [];
  if (!hits.length) {
    box.appendChild(el('p', { className: 'empty-hint', text: t('journal.detail.noHits') }));
    return;
  }
  hits.forEach((hit, index) => box.appendChild(hitRow(hit, index, ev.bank_id)));
}

function renderIndexDetail(box, ev) {
  box.appendChild(el('div', { className: 'd-kick' }, [
    el('span', { text: t('journal.detail.indexKicker', { id: ev.id }) }),
    evStatusBadge(ev),
  ]));
  box.appendChild(el('h2', { className: 'd-h', text: indexEventTitle(ev) }));
  box.appendChild(factsRow([
    [t('journal.detail.bank'), bankLabel(ev.bank_id)],
    [t('journal.detail.kind'), ev.kind],
    [t('journal.detail.trigger'), ev.trigger],
    [t('journal.detail.when'), fmtDateTime(ev.ts)],
  ]));
  box.appendChild(el('div', { className: 'nums' }, [
    numBox(ev.files_indexed, t('journal.detail.filesIndexed')),
    numBox(ev.chunks_indexed, t('journal.detail.chunksIndexed')),
    numBox(ev.files_pruned, t('journal.detail.filesPruned')),
    numBox(fmtMs(ev.took_ms), t('journal.detail.duration')),
  ]));

  if (ev.error) {
    box.appendChild(el('div', { className: 'note is-err' }, [
      el('strong', { text: t('journal.detail.errorLabel') }),
      el('br'),
      document.createTextNode(ev.error),
    ]));
  }

  if (ev.path) {
    box.appendChild(el('div', { className: 'd-sec', text: t('journal.detail.fileSection') }));
    box.appendChild(el('article', { className: 'hit' }, [
      el('div', { className: 'hit-top' }, [
        el('span', { className: 'hit-r', text: '·' }),
        el('div', { className: 'hit-l' }, [
          el('div', { className: 'hit-p', text: ev.path }),
          el('div', {
            className: 'hit-h',
            text: t('journal.detail.currentFileOf', { bank: bankLabel(ev.bank_id) }),
          }),
        ]),
      ]),
      el('div', { className: 'hit-foot' }, [
        el('button', {
          className: 'btn btn-sm',
          text: t('journal.hit.openFile'),
          on: { click: () => openInMemory(ev.bank_id, ev.path) },
        }),
      ]),
    ]));
  }
}

function renderDetail() {
  const box = $('jdetail');
  clear(box);
  const selectedId = state.logSelected[state.logKind];
  const ev = state.logRows.find((row) => row.id === selectedId) || state.logRows[0] || null;
  if (!ev) {
    box.appendChild(el('p', { className: 'empty-hint', text: t('journal.detail.selectHint') }));
    return;
  }
  state.logSelected[state.logKind] = ev.id;
  if (state.logKind === 'query') renderQueryDetail(box, ev);
  else renderIndexDetail(box, ev);
}

function renderJournal() {
  populateBankFilter();
  renderList();
  renderDetail();
}

// ---------------------------------------------------------------------------
// resizable events/detail split — same handle and drag mechanics as Памʼять
// (`wireColumnResizer`, app.js), one width instead of two.
// ---------------------------------------------------------------------------

const JOURNAL_WIDTH_MIN = 260;
const JOURNAL_WIDTH_MAX = 720;
const JOURNAL_WIDTH_DEFAULT = 400;
const JOURNAL_STACK_BREAKPOINT = 940;

function clampJournalWidth(px) {
  return Math.min(JOURNAL_WIDTH_MAX, Math.max(JOURNAL_WIDTH_MIN, Math.round(px)));
}

function loadJournalWidth() {
  const raw = Number(localStorage.getItem('mnemo_journal_width'));
  return Number.isFinite(raw) && raw > 0 ? clampJournalWidth(raw) : JOURNAL_WIDTH_DEFAULT;
}

/**
 * Below `JOURNAL_STACK_BREAKPOINT` the stylesheet switches `.jl` from two
 * columns to one column of two stacked rows (`page-journal.css`) — an inline
 * `grid-template-columns` would win over that media query at any width, so
 * it is only set above the breakpoint; `removeProperty` below it lets the
 * media query rule again, same as a column a mouse never touched.
 */
function applyJournalWidth(px) {
  const jl = document.querySelector('.jl');
  if (window.innerWidth > JOURNAL_STACK_BREAKPOINT) {
    jl.style.gridTemplateColumns = px + 'px 6px minmax(0, 1fr)';
  } else {
    jl.style.removeProperty('grid-template-columns');
  }
}

function wireJournalResizer() {
  let width = loadJournalWidth();
  applyJournalWidth(width);
  let base = 0;
  wireColumnResizer($('journal-resizer'), {
    onStart: () => { base = width; },
    onDrag: (deltaX) => {
      width = clampJournalWidth(base + deltaX);
      applyJournalWidth(width);
    },
    onCommit: () => localStorage.setItem('mnemo_journal_width', String(width)),
  });
  window.addEventListener('resize', () => applyJournalWidth(width));
}

wireJournalResizer();
