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

  // -- memory: Памʼять/Memory page — banks, tree, file view (page-memory.js) --

  'memory.header.panelAriaLabel': 'Panel',
  'memory.header.addBank': '＋ Add bank',
  'memory.header.addBankTitle': 'Register a new directory with .md as a bank',

  'memory.count.banks': { one: '{n} bank', other: '{n} banks' },
  'memory.count.files': { one: '{n} file', other: '{n} files' },
  'memory.count.chunks': { one: '{n} chunk', other: '{n} chunks' },
  'memory.count.dirs': { one: '{n} directory', other: '{n} directories' },

  'memory.banks.emptyHint': 'No banks registered yet — "＋ Add bank" in the header picks a directory with .md.',

  'memory.bankState.enabled.label': 'Active',
  'memory.bankState.enabled.note': 'Watching the files, the index updates itself, search works.',
  'memory.bankState.frozen.label': 'Frozen',
  'memory.bankState.frozen.note': 'Not watching the files — the index stays as-is, but search still ' +
    'works. This is what saves a full rebuild when the model changes.',
  'memory.bankState.disabled.label': 'Disabled',
  'memory.bankState.disabled.note': 'Not watching, not searching. The bank stays in the registry.',

  'memory.statusNote.indexingHasChunks': 'the base exists, fresh changes are catching up',
  'memory.statusNote.indexingEmpty': 'first build in progress — still empty',
  'memory.statusNote.emptyQueued': 'empty, tasks queued',
  'memory.statusNote.emptyIdle': 'genuinely empty, nothing scheduled',
  'memory.statusNote.ready': 'index ready',

  'memory.bank.menuBtnTitle': 'Bank actions',
  'memory.bank.filesStat': '{n} files',
  'memory.bank.chunksStat': '{n} chunks',
  'memory.bank.queuedStat': 'queued {n}',
  'memory.bank.dbSizeTitle': 'index size',
  'memory.bank.lastIndexed': 'last indexed: {date}',
  'memory.bank.frozenBadge': 'frozen',
  'memory.bank.frozenBadgeTitle': 'The index is not updating — files may have changed since {date}. ' +
    'Search still works and answers from that state.',
  'memory.bank.disabledBadge': 'disabled',
  'memory.bank.noRootBadge': 'root missing',

  'memory.indexedState.yes': 'indexed',
  'memory.indexedState.no': 'not indexed',

  'memory.tree.selectBankHint': 'Select a bank on the left.',
  'memory.tree.loading': 'Loading…',
  'memory.tree.emptyMd': 'This bank has no .md files.',

  'memory.chunk.gap': '· outside chunks ·',
  'memory.chunk.end': { one: 'end · {n} character', other: 'end · {n} characters' },

  'memory.rebuild.action': 'Rebuild',
  'memory.rebuild.queuing': 'Queuing…',
  'memory.rebuild.dialogTitle': 'Rebuild indexes',
  'memory.rebuild.dialogAriaLabel': 'Rebuild indexes',
  'memory.rebuild.notice.actionable': '{n} bank(s) have an index from a previous model',
  'memory.rebuild.notice.running': '{n} already rebuilding',
  'memory.rebuild.notice.disabled': '{n} disabled — enable them first',
  'memory.rebuild.notice.suffix': '. Search over stale vectors refuses rather than mixing two spaces.',
  'memory.rebuild.dialog.lead': 'A full reindex will be queued for {n} bank(s). Old derived indexes ' +
    'will be wiped and rebuilt from the .md from scratch.',
  'memory.rebuild.dialog.chunksLabel': '{n} chunks',
  'memory.rebuild.dialog.note': ".md files are not changed. Time is proportional to size; the exact " +
    "speed depends on this machine's backend and hardware.",

  // -- journal: static filter/list markup (index.html) ---------------------

  'journal.filter.bankLabel': 'Bank',
  'journal.filter.allBanks': 'All banks',
  'journal.filter.periodLabel': 'Period',
  'journal.filter.period24h': '24 hours',
  'journal.filter.period1h': 'Last hour',
  'journal.filter.period7d': '7 days',
  'journal.filter.period30d': '30 days',
  'journal.list.newestFirst': 'newest first',

  // -- journal: Журнал page — header, list, detail (page-journal.js) -------

  'journal.header.segQuery': 'Queries',
  'journal.header.segIndex': 'Indexing',
  'journal.header.refreshTitle': 'Refresh journal',

  'journal.list.shownOf': 'Showing {shown} of {total}',
  'journal.list.empty': 'Empty',
  'journal.list.noEvents': 'No events found.',

  'journal.event.rebuildTitle': 'Full bank rebuild',
  'journal.event.pruneTitle': 'Removed from index',
  'journal.event.syncTitle': 'Index sync',
  'journal.event.errorStatus': 'error',

  'journal.hit.openFile': 'Open file',
  'journal.hit.showMore': 'show more',
  'journal.hit.collapse': 'collapse',
  'journal.hit.chunkLabel': '{heading} · chunk {n}',

  'journal.detail.queryKicker': 'query · #{id}',
  'journal.detail.indexKicker': 'indexing · #{id}',
  'journal.detail.bank': 'bank',
  'journal.detail.face': 'face',
  'journal.detail.prefix': 'prefix',
  'journal.detail.hits': 'hits',
  'journal.detail.tookMs': 'time, ms',
  'journal.detail.when': 'when',
  'journal.detail.kind': 'kind',
  'journal.detail.trigger': 'trigger',
  'journal.detail.resultsLabel': 'Results',
  'journal.detail.resultsOrderNote': 'in exact rank order',
  'journal.detail.noHits': 'No hits.',
  'journal.detail.filesIndexed': 'files',
  'journal.detail.chunksIndexed': 'chunks',
  'journal.detail.filesPruned': 'removed',
  'journal.detail.duration': 'duration',
  'journal.detail.errorLabel': 'Error',
  'journal.detail.fileSection': 'File',
  'journal.detail.currentFileOf': 'current file of bank {bank}',
  'journal.detail.selectHint': 'Select an event on the left.',

  // -- settings: static footer markup (index.html) --------------------------

  'settings.btn.save': 'Save',

  // -- settings: Settings page — header, tabs, ledes (page-settings.js) -----

  'settings.header.title': 'Settings',
  'settings.header.sub': 'applies to this machine, not one bank',

  'settings.tabs.general': 'General',
  'settings.tabs.embed': 'Embedding model',
  'settings.tabs.maint': 'Maintenance',

  'settings.lede.general': "What concerns the console and the machine as a whole — not one bank, and not the embedding backend.",
  'settings.lede.embed': 'Which backend computes vectors for search across banks, and how much RAM it takes for that.',
  'settings.lede.maint': 'The same structured doctor report the CLI prints as text. Checks run only when this section is opened.',

  'settings.loading': 'Loading…',
  'settings.messages.nothingChanged': 'Nothing changed.',
  'settings.overrideNote': 'overridden by the {var} environment variable — what is saved here has no effect while it is set',
  'settings.notSavedToggle': 'not saved — currently {state}; press "Save" to apply',
  'settings.state.on': 'on',
  'settings.state.off': 'off',
  'settings.toggle.on': 'On',
  'settings.toggle.off': 'Off',

  // -- settings.general: theme, language, autostart, auto-update, require-login, status --

  'settings.general.theme.label': 'Console theme',
  'settings.general.theme.note': 'This browser\'s own choice — applies immediately, does not wait for "Save".',
  'settings.general.theme.dark': 'Dark',
  'settings.general.theme.light': 'Light',

  'settings.general.language.label': 'Language',
  'settings.general.language.note': 'This browser\'s own choice — applies immediately, does not wait for "Save".',

  'settings.general.autostart.label': 'Start the service at logon',
  'settings.general.autostart.note': 'Registers as {mechanism}{named}. Applies from the next logon; what is running now is untouched.',
  'settings.general.autostart.namedSuffix': ' — "{name}"',
  'settings.general.autostart.notFetched': 'state not received',
  'settings.general.autostart.unsupported': 'not supported on this system',
  'settings.general.autostart.savedOn': 'Autostart: the service will come up at logon.',
  'settings.general.autostart.savedOff': 'Autostart: off, you will need to start the service yourself.',

  'settings.general.autoUpdate.label': 'Automatic update',
  'settings.general.autoUpdate.note': 'A fit release applies itself — with a short countdown and a "Cancel" button right in the console. Off — only the banner and manual confirmation remain, as before.',
  'settings.general.autoUpdate.savedOn': 'Auto-update: on.',
  'settings.general.autoUpdate.savedOff': 'Auto-update: off.',
  'settings.general.autoUpdate.checking': 'Checking…',
  'settings.general.autoUpdate.checkBtn': 'Check for updates',
  'settings.general.autoUpdate.upToDate': 'Up to date.',

  'settings.general.requireLogin.label': 'Require a token to sign in',
  'settings.general.requireLogin.noteOff': 'Off (default): "/api" (console and CLI) is open on loopback, as now — no token.',
  'settings.general.requireLogin.noteOn': 'On: the console and CLI need a service token to access.',
  'settings.general.requireLogin.savedOn': 'Signing in now requires a token.',
  'settings.general.requireLogin.savedOff': 'The console is open on loopback again, no token.',
  'settings.general.requireLogin.tokenLabel': 'Service token',
  'settings.general.requireLogin.tokenNote': "Shown once — GET /api/settings will not return it again. The console has already adopted this token for the current session, so signing in again is not needed. If it's lost — the same file is on disk (the path and mnemo doctor will show it), or turn this option off again to drop the token requirement.",

  'settings.general.serviceNotLoaded': 'Service state not received yet.',
  'settings.general.statusLabel': 'State',
  'settings.general.stat.version': 'Version',
  'settings.general.stat.pid': 'PID',
  'settings.general.stat.address': 'Address',
  'settings.general.stat.provider': 'Provider',
  'settings.general.stat.uptime': 'Uptime',
  'settings.general.stat.priorityQueue': 'Priority queue',
  'settings.general.stat.priorityOn': 'enabled',
  'settings.general.stat.priorityOff': 'disabled',
  'settings.general.aboutLabel': 'About the project',

  'settings.uptime.hoursMinutes': '{h} h {m} min',
  'settings.uptime.minutesSeconds': '{m} min {s} s',
  'settings.uptime.seconds': '{s} s',

  // -- settings.embed: backend, model, endpoint, key, memory ----------------

  'settings.embed.warn.line1': 'Changing the model or width is a new rebuild key.',
  'settings.embed.warn.line2': 'The configuration takes effect after saving settings.',
  'settings.embed.warn.line3': 'Old indexes get REBUILD PENDING and search over them refuses until they are regenerated.',
  'settings.embed.backendLabel': 'Embedding backend',
  'settings.embed.notSavedBackend': 'not saved — currently active is "{active}"; press "Save" to switch to "{target}"',
  'settings.embed.local.lead': 'The resident on this machine computes vectors — ',
  'settings.embed.local.dimsSuffix': ' ({dim} dimensions). ',
  'settings.embed.local.noDimSuffix': '. ',
  'settings.embed.local.tail': 'No byte of memory leaves the machine.',
  'settings.embed.modelLabel': 'Model',
  'settings.embed.model.prefixedNote': 'this model is trained with markers — mnemo will add them itself',
  'settings.embed.model.notInCatalog': ' (not in catalogue)',
  'settings.embed.urlLabel': 'Address',
  'settings.embed.dimLabel': 'Dimensions',
  'settings.embed.timeoutLabel': 'Timeout, s',
  'settings.embed.dimNote': "The width is filled from the catalogue, but the endpoint has the final word: mnemo checks it against the first vector received and refuses to write the index if they disagree.",
  'settings.embed.keyLabel': 'API key',
  'settings.embed.keyNote': 'Stored in settings.json on this machine. Never shown back — a page that prints a secret prints it into a screenshot too.',
  'settings.embed.key.placeholderStored': 'stored — type a new one to replace it',

  'settings.embed.mem.hold.loaded': 'in memory',
  'settings.embed.mem.hold.unloaded': 'not in memory',
  'settings.embed.mem.hold.na': 'holds nothing',
  'settings.embed.mem.hold.unknown': 'unknown',
  'settings.embed.mem.label': 'RAM',
  'settings.embed.mem.notFetched': 'State not received yet.',
  'settings.embed.mem.introStrong': 'The model comes up on its own when needed for search or indexing',
  'settings.embed.mem.introRest': ' — "not in memory" is a normal state, not an error. "Unload" frees the memory right away, instead of keeping the model loaded just in case.',
  'settings.embed.mem.statusCaption': 'Status:',
  'settings.embed.mem.modelCaption': 'Model:',
  'settings.embed.mem.idleCaption': 'Auto-unload:',
  'settings.embed.mem.unloadBtn': 'Unload',
  'settings.embed.mem.wakeBtn': 'Bring into memory',
  'settings.embed.mem.probeEndpointBtn': 'Check endpoint',
  'settings.embed.mem.probeBtn': 'Check',
  'settings.embed.mem.downloading': 'Downloading the model…',
  'settings.embed.mem.downloadBtn': 'Download the model to disk (2.2 GB)',
  'settings.embed.mem.note.wakeSoon': 'It will come back in a few seconds.',
  'settings.embed.mem.note.naHosted': "This endpoint holds nothing on this machine — the model lives on the provider's side, so there is nothing to free.",
  'settings.embed.mem.note.naProbeCost': '"Check endpoint" makes one embedding request. For a metered API this can be a paid call.',
  'settings.embed.mem.note.expiresAt': 'The backend holds it until {when}.',
  'settings.embed.mem.note.othersHeld': 'There are also {n} other model(s) there — not ours, we leave them alone.',
  'settings.embed.mem.note.downloadFailed': 'The download failed — try again.',
  'settings.embed.mem.unloadedNote': 'Memory freed. The model will come back on its own on the next search.',
  'settings.embed.mem.probeOkBase': 'The endpoint responded',
  'settings.embed.mem.probeOkDimSuffix': ' — the probe vector has {dim} dimensions.',
  'settings.embed.mem.loadedNote': 'The backend responded — the model is in memory.',
  'settings.embed.mem.busyError': 'The queue is still working through this backend — wait until it clears and try again.',

  'settings.embed.errors.missingUrl': 'Enter the endpoint address.',
  'settings.embed.errors.dimNotPositive': 'Dimensions must be a positive number.',
  'settings.embed.saved.restartRequired': 'Saved. Takes effect after the service restarts.',
  'settings.embed.saved.appliedNoPending': 'Saved and applied. Check the backend with the button above.',
  'settings.embed.saved.appliedPending': 'Saved and applied. Check the backend with the button above, then regenerate the banks with REBUILD PENDING on the main screen.',
  'settings.embed.errors.refreshFailed': 'Settings saved, but not every state could be re-read. Refresh the page — no need to save again.',

  // -- settings.maint: diagnostics + orphan cleanup --------------------------

  'settings.maint.refreshing': 'Refreshing…',
  'settings.maint.refreshBtn': 'Refresh diagnostics',
  'settings.maint.collecting': 'Collecting diagnostics…',
  'settings.maint.notFetched': 'The report has not been received yet.',
  'settings.maint.engineLabel': 'Engine',
  'settings.maint.embedLabel': 'Embedding',
  'settings.maint.providerLabel': 'Provider',
  'settings.maint.localModelLabel': 'Local model',
  'settings.maint.residentLabel': 'Resident',
  'settings.maint.serviceLabel': 'Service',
  'settings.maint.queueLabel': 'Queue',
  'settings.maint.registryLabel': 'Registry',
  'settings.maint.unknown': 'unknown',
  'settings.maint.unknownTitle': 'Unknown',
  'settings.maint.genericError': 'error',
  'settings.maint.model.cachedFull': 'cache complete',
  'settings.maint.model.notLoaded': 'NOT LOADED',
  'settings.maint.model.cachedNotNeeded': 'present but not needed',
  'settings.maint.model.notNeeded': 'not needed',
  'settings.maint.unavailable': 'UNAVAILABLE',
  'settings.maint.resident.up': 'running',
  'settings.maint.resident.down': 'not loaded',
  'settings.maint.resident.portSuffix': ' · machine port',
  'settings.maint.resident.na': 'n/a for this provider',
  'settings.maint.endpoint.dimsUnit': 'dimensions',
  'settings.maint.endpoint.notConfigured': 'NOT CONFIGURED — {error}',
  'settings.maint.backend.upSummary': 'running · pid {pid} · machine port',
  'settings.maint.backend.down': 'UNAVAILABLE — {error}',
  'settings.maint.token.notSet': 'not set · /api is open on loopback by default',
  'settings.maint.registryUnreadable': 'UNREADABLE — {error}',
  'settings.maint.registryReadable': 'The registry reads fine.',
  'settings.maint.registry.noRoot': 'root missing',
  'settings.maint.count.projects': { one: '{n} project', other: '{n} projects' },
  'settings.maint.wiring.allCurrent': 'all current',

  'settings.maint.orphans.unavailableTitle': 'List unavailable',
  'settings.maint.orphans.deletionForbidden': 'Deletion is refused: {reason}',
  'settings.maint.orphans.registryUncheckable': 'the registry cannot be checked',
  'settings.maint.orphans.noneTitle': 'No orphans',
  'settings.maint.orphans.noneNote': 'Every index belongs to a bank.',
  'settings.maint.orphans.unreadable': 'unreadable — {error}',
  'settings.maint.orphans.preV3NoRoot': 'pre-v3 index — root not recorded',
  'settings.maint.orphans.noRoot': 'root not recorded',
  'settings.maint.orphans.rootStillOnDisk': ' · root still on disk',
  'settings.maint.orphans.unknownFiles': '? files',
  'settings.maint.orphans.sectionLabel': 'Orphan indexes',
  'settings.maint.orphans.sectionNote': 'Doctor only shows them. Cleanup happens only as a separate confirmed action — never automatically, never together with diagnostics.',
  'settings.maint.orphans.cleanupBtn': 'Clean up orphans',
  'settings.maint.orphans.confirmText': 'Only these shown derived index ids will be deleted: {ids}. The registry is re-checked before each deletion; .md files are untouched.',
  'settings.maint.orphans.cleaning': 'Cleaning up…',
  'settings.maint.orphans.deleteBtn': 'Delete {n}',
  'settings.maint.orphans.result.removed': 'removed {removed} of {total}',
  'settings.maint.orphans.result.freed': 'freed {bytes}',
  'settings.maint.orphans.result.skipped': 'skipped {n}',
  'settings.maint.orphans.result.locked': 'locked {n}',
  'settings.maint.orphans.lockedError': 'Not all files were deleted: {list}',

  // -- update: self-update banner + modal (update.js) -----------------------

  'update.modal.title': 'mnemo update',
  'update.steps.download': 'Downloading from GitHub',
  'update.steps.venv': 'Installing packages',
  'update.steps.switching': 'Switching version and restarting',

  'update.confirm.currentLabel': 'Current version: ',
  'update.confirm.newLabel': 'New version: ',
  'update.confirm.warning': 'The mnemo service will be stopped and restarted on the new ' +
    'version. Search and indexing are unavailable while this happens. Once you press "OK" ' +
    'the action cannot be cancelled — progress is shown until it finishes.',
  'update.confirm.okBtn': 'OK',
  'update.confirm.staleTarget': 'The version shown is stale (current: {tag}). Close this ' +
    'window and try again.',

  'update.autoPending.leadPrefix': 'Automatic update to ',
  'update.autoPending.leadMiddle': ' starts in ',
  'update.autoPending.leadSuffix': ' s.',
  'update.autoPending.note': 'If nothing is clicked, the update applies automatically. ' +
    '"Cancel" only postpones it — the same version may be offered again on the next check.',

  'update.progress.title': 'Updating to {tag}…',
  'update.progress.switchingNote': 'The service is restarting — the page will lose its ' +
    'connection for a few seconds. This is expected: the result becomes known as soon as ' +
    'the connection recovers.',

  'update.timeout.text': "Couldn't learn the update's outcome within the allotted time. " +
    "The service may still be restarting, or the console can't reach it right now. Check " +
    'manually (mnemo doctor) or try again.',
  'update.timeout.retryBtn': 'Try again',

  'update.terminal.done': 'Updated to {tag}.',
  'update.terminal.rolledBack': 'A problem occurred updating to {tag} — rolled back to {current}.',
  'update.terminal.errorSuffix': ' ({error})',
  'update.terminal.failedBase': 'Update failed',
  'update.terminal.failedWithError': ': {error}',
  'update.terminal.failedNoError': '.',
  'update.terminal.unknownState': ' Service state may be undefined — check mnemo doctor.',
  'update.terminal.unchanged': ' The current version was not changed.',
  'update.terminal.autoClosePrefix': 'Closes automatically in ',
  'update.terminal.autoCloseSuffix': ' s.',

  'update.banner.busy': 'mnemo update in progress…',
  'update.banner.autoPending': 'Auto-update to {tag} awaiting confirmation',
  'update.banner.available': 'New version {tag} available',
};
