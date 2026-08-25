/* mnemo web console — English dictionary (MN-10).
 *
 * Flat `key -> string` map, plus plural entries (`key -> {one, other}`)
 * consumed by `plural()` in app.js. Namespaced by owning file/section:
 * `common.*` (app.js shared chrome), `shell.*` (shell.js), `memory.*` /
 * `journal.*` / `settings.*` (each page's own strings, including the static
 * markup in index.html that belongs to it) and `update.*` (update.js).
 *
 * English is the default language (MNEMO-10): this is the dictionary read
 * when a key is missing from `uk.js`, and the one `index.html`'s markup is
 * baked in.
 */
'use strict';

window.MNEMO_I18N = window.MNEMO_I18N || {};
window.MNEMO_I18N.en = {
  // -- common: shared chrome (app.js) --------------------------------------

  'common.gate.missing.title': 'Access token required',
  'common.gate.missing.text': 'A token is needed to open the console. ' +
    'The command prints a link with the current token and opens it:',
  'common.gate.rejected.title': 'Token rejected',
  'common.gate.rejected.text': 'The service rejected the token you supplied (HTTP 401). ' +
    'It is most likely stale, or was copied incompletely — the current token ' +
    'is printed by the command itself.',
  'common.gate.rejected.lead': 'The command prints a ready link with the current token and opens it:',
  'common.gate.rejected.note': 'The token was rejected by the service.',
  'common.gate.tokenPlaceholder': '48 hex characters',
  'common.gate.manualLabel': 'Or paste the token manually:',
  'common.gate.submit': 'Sign in',
  'common.gate.enterToken': 'Enter a token.',
  'common.gate.idle': 'not authenticated',

  'common.error.unreachable': 'backend unreachable: {message}',
  'common.error.invalidJson': 'invalid JSON in response',

  'common.status.ready': 'ready',
  'common.status.indexing': 'indexing',
  'common.status.empty': 'empty',

  'common.taskKind.file': 'file',
  'common.taskKind.bulk': 'index sync',
  'common.taskKind.rebuild': 'full reindex',
  'common.taskKind.prune': 'index prune',
  'common.taskKind.default': 'task',

  'common.unit.sec': 's',
  'common.unit.min': 'min',

  'common.progress.batch': 'batch',
  'common.progress.chunks': 'chunks',
  'common.progress.yielded': 'preempted',
  'common.progress.approxTitle': 'time since the console saw this task — it started earlier',
  'common.progress.exactTitle': 'time since the task started',

  'common.reindex.queuedNote': 'queued: {what} · in queue {n} · task {ids}',

  'common.bankMenu.stateNote': 'state: {state}',
  'common.bankMenu.sync': 'Sync index',
  'common.bankMenu.syncTitle': 'Reindexes only changed files and removes deleted ones from the index',
  'common.bankMenu.rebuild': 'Full reindex',
  'common.bankMenu.rebuildTitle': "Wipes the index and rebuilds it from scratch — slow, proportional to the bank's size",
  'common.bankMenu.mcpTitle': "This bank's token and a ready-made config fragment for a project",
  'common.bankMenu.stateLabel': 'State',
  'common.bankMenu.remove': 'Remove bank',
  'common.bankMenu.removeTitle': 'Unregister the bank; .md files are untouched',

  'common.btn.cancel': 'Cancel',
  'common.btn.close': 'Close',
  'common.btn.closeEsc': 'Close (Esc)',
  'common.btn.copy': 'copy',
  'common.btn.copied': 'copied',

  'common.resizerTitle': 'Drag to resize',

  // -- common.picker: folder picker (add-bank dialog) ----------------------

  'common.picker.title': 'Add bank',
  'common.picker.ariaLabel': 'Add bank',
  'common.picker.pathLabel': "Directory with .md — it becomes the bank's root",
  'common.picker.pathPlaceholder': 'or paste a path',
  'common.picker.nameLabel': 'Bank name (optional)',
  'common.picker.namePlaceholder': 'guessed from the folder name',
  'common.picker.createStructure': 'Create the memory structure here (.claude/memory)',
  'common.picker.connectMcp': 'Connect the project (MCP) right away',
  'common.picker.addDir': 'Add this directory',
  'common.picker.reading': 'reading…',
  'common.picker.home': 'home',
  'common.picker.bankBadge': 'bank',
  'common.picker.noSubdirs': 'no subdirectories',
  'common.picker.truncated': 'showing the first {n} directories — paste the rest as a path',
  'common.picker.withSubdirs': '(with subdirectories)',
  'common.picker.mdCount': 'this directory has {count} .md{nested}',
  'common.picker.noMd': 'this directory has no .md — nothing to index',
  'common.picker.countTruncatedTitle': 'the count timed out — there are at least this many files, all of them will be indexed',
  'common.picker.excludesTitle': 'excludes .git, .venv, node_modules — same as what the indexer skips',
  'common.picker.alreadyRegistered': 'already registered as «{name}»',
  'common.picker.alreadyBankTitle': 'already a bank: {name}',
  'common.picker.hint.alreadyBank': 'this directory is already a memory bank',
  'common.picker.hint.hasNestedMemory': 'this directory already has a .claude/memory structure',
  'common.picker.hint.willBecome': 'Will become the bank: {root}',
  'common.picker.hint.project': 'Project: {root}',
  'common.picker.hint.willConnect': 'Will be connected once the structure is created',
  'common.picker.hint.projectOnly': 'available only for a bank at «<project>/.claude/memory»',
  'common.picker.mcpSkipped': 'MCP not connected',
  'common.picker.mcpConnected': 'MCP connected',
  'common.picker.mcpFailed': 'MCP connection failed',
  'common.picker.addedNote': 'bank added · indexing queued',

  // -- common.token: MCP access panel ---------------------------------------

  'common.token.title': 'MCP access',
  'common.token.titleFor': 'MCP access — {name}',
  'common.token.ariaLabel': 'MCP access to bank',
  'common.token.regen': 'Regenerate',
  'common.token.regenTitle': 'Issue the bank a new token; the old one stops working immediately',
  'common.token.regenConfirm': 'Regenerate the token for bank «{name}»? The old one stops working ' +
    "immediately: every config that already holds it — ~/.claude.json, another " +
    "project's .mcp.env — will stop connecting until you put the new one in.",
  'common.token.regenYes': 'Yes, regenerate',
  'common.token.regeneratedNote': 'Token regenerated. Configs with the old token will no longer connect ' +
    '— put the new one in them.',
  'common.token.bankTokenLabel': 'Bank token',
  'common.token.hide': 'hide',
  'common.token.show': 'show',
  'common.token.hideTitle': 'Remove the value from the screen',
  'common.token.showTitle': 'Show the value on screen',
  'common.token.copyTokenTitle': 'Copy the token without showing it',
  'common.token.copyToClipboard': 'Copy to clipboard',
  'common.token.copyFailed': "Couldn't copy — select the text and copy it manually.",
  'common.token.scopeNote': 'Opens only the bank «{name}». The service token this console is open ' +
    'with is broader — it does not belong in a project config.',
  'common.token.entryLabel': 'Config entry name',
  'common.token.entryHint.base': 'This is how the entry shows up among other MCP servers; it also ' +
    'becomes the prefix of the tool names — mcp__{entry}__search.',
  'common.token.entryHint.own': ' It is also where {var} comes from: the token belongs to one bank, ' +
    'so a second bank in the same project does not overwrite the first one’s token. ' +
    'MNEMO_HOST and MNEMO_PORT are shared — they name the service, not the bank.',
  'common.token.scope.literal': 'with values · .mcp.json or ~/.claude.json',
  'common.token.scope.template': 'with placeholders · .mcp.json.template',
  'common.token.scopeHint': 'The second — if the project has .mcp.json.template and mcp-setup.sh: ' +
    'values there are substituted from .mcp.env, and only the template rides in git. ' +
    'Otherwise the first: .mcp.json holds the values directly and is in .gitignore.',
  'common.token.caption.literal': "For the project's .mcp.json or ~/.claude.json — merge into «mcpServers»",
  'common.token.caption.template': 'For .mcp.json.template — merge into the existing «mcpServers»',
  'common.token.caption.env': 'Lines for .mcp.env',
  'common.token.templateLead.part1': 'All three files are filled in by ',
  'common.token.templateLead.part2': ' — the fragment into .mcp.json.template, the variables into ' +
    '.mcp.env, the substitution lines into mcp-setup.sh. It does not create .mcp.env itself: ' +
    'that is a file with secrets, so first run ',
  'common.token.templateLead.part3': ', then init again, and finally ',
  'common.token.templateLead.part4': " — that is what assembles .mcp.json with the actual values. " +
    "Below is exactly what init will write: to preview it, or to fill it in by hand if " +
    "init cannot be run in this project.",
  'common.token.manualPaste.part1': 'If you fill it in by hand, add to the ',
  'common.token.manualPaste.part2': ' call in mcp-setup.sh the line ',
  'common.token.manualPaste.part3': '. Without it the placeholder lands in .mcp.json verbatim, and the ' +
    'script still reports success — so the failure only shows up when the server silently ' +
    'fails to connect.',
  'common.token.generatedFileNote': '.mcp.json is a generated file: it is in .gitignore, and ' +
    'mcp-setup.sh rewrites it from the template. The entry must live in .mcp.json.template, ' +
    'or the next run of the script will wipe it.',

  // -- common.removal: remove-bank dialog -----------------------------------

  'common.removal.submit': 'Remove',
  'common.removal.busy': 'Removing…',
  'common.removal.title': 'Remove bank',
  'common.removal.ariaLabel': 'Remove bank',
  'common.removal.leadPrefix': 'Bank ',
  'common.removal.leadSuffix': ' will stop existing for this machine.',
  'common.removal.goneForever': 'Gone forever',
  'common.removal.goneForeverText': "The bank's registration and its token. The token is issued at " +
    'random and cannot be recreated: every .mcp.json that connects with it stops working, ' +
    'and the same token can never be recovered.',
  'common.removal.untouched': 'Left untouched',
  'common.removal.untouchedPrefix': 'All .md under ',
  'common.removal.untouchedSuffix': '. The console does not delete the bank’s contents — ' +
    'only what was derived from it.',
  'common.removal.dropIndex': 'also delete the index ({bytes}) — recoverable with a full reindex',
  'common.removal.stripMcpPrefix': 'also remove the MCP wiring (',
  'common.removal.noMcpJson': 'no .mcp.json at the project root',
  'common.removal.confirmLabel': 'Type the bank name to confirm',

  // -- shell: sidebar, header, WebSocket connection state (shell.js) -------

  'shell.nav.ariaLabel': 'Console sections',
  'shell.nav.memory': 'Memory',
  'shell.nav.journal': 'Journal',
  'shell.nav.settings': 'Settings',
  'shell.sidebar.collapse': 'Collapse navigation',
  'shell.sidebar.expand': 'Expand navigation',
  'shell.footTitle': 'provider {provider} · version {version}',
  'shell.conn.connecting': 'connecting…',
  'shell.conn.live': 'live',
  'shell.conn.dropped': 'disconnected',
  'shell.conn.error': 'error',
  'shell.event.done': 'done: {what} · {n} chunks · {took}',
  'shell.event.error': 'error: {what} — {error}',
  'shell.event.pruned': 'removed from index: {n}',

  // -- memory: static Памʼять/Memory pane markup (index.html) --------------

  'memory.pane.banks': 'Banks',
  'memory.pane.refreshTitle': 'Refresh list',
  'memory.pane.files': 'Files',
  'memory.pane.selectBankHint': 'Select a bank on the left.',
  'memory.pane.content': 'Content',
  'memory.pane.chunkVizTitle': 'Show chunk boundaries as they lie in the index',
  'memory.pane.chunkVizLabel': 'Chunk boundaries',
  'memory.pane.reindexFileBtn': 'Reindex file',
  'memory.pane.selectFileHint': 'Select a file in the tree.',

  // -- journal: static filter/list markup (index.html) ---------------------

  'journal.filter.bankLabel': 'Bank',
  'journal.filter.allBanks': 'All banks',
  'journal.filter.periodLabel': 'Period',
  'journal.filter.period24h': '24 hours',
  'journal.filter.period1h': 'Last hour',
  'journal.filter.period7d': '7 days',
  'journal.filter.period30d': '30 days',
  'journal.list.newestFirst': 'newest first',

  // -- settings: static footer markup (index.html) --------------------------

  'settings.btn.save': 'Save',
};
