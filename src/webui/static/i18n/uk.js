/* mnemo web console — Ukrainian dictionary (MN-10).
 *
 * Flat `key -> string` map, plus plural entries (`key -> {one, few, many}`)
 * consumed by `plural()` in app.js, using the standard Slavic count-noun
 * triad rule. Keys mirror `en.js` one for one; a key missing here falls back
 * to English rather than breaking.
 */
'use strict';

window.MNEMO_I18N = window.MNEMO_I18N || {};
window.MNEMO_I18N.uk = {
  // -- common: shared chrome (app.js) --------------------------------------

  'common.gate.missing.title': 'Потрібен токен доступу',
  'common.gate.missing.text': 'Щоб відкрити консоль, потрібен токен. ' +
    'Команда друкує посилання з чинним токеном і відкриває його:',
  'common.gate.rejected.title': 'Токен не підійшов',
  'common.gate.rejected.text': 'Сервіс відхилив наданий токен (HTTP 401). Найімовірніше він ' +
    'застарілий або скопійований не повністю — актуальний токен видає сама команда.',
  'common.gate.rejected.lead': 'Команда друкує готове посилання з чинним токеном і відкриває його:',
  'common.gate.rejected.note': 'Токен відхилено сервісом.',
  'common.gate.tokenPlaceholder': '48 шістнадцяткових символів',
  'common.gate.manualLabel': 'Або вставте токен вручну:',
  'common.gate.submit': 'Увійти',
  'common.gate.enterToken': 'Введіть токен.',
  'common.gate.idle': 'не автентифіковано',

  'common.error.unreachable': 'бекенд недоступний: {message}',
  'common.error.invalidJson': 'невалідний JSON у відповіді',

  'common.status.ready': 'готово',
  'common.status.indexing': 'індексується',
  'common.status.empty': 'порожньо',

  'common.taskKind.file': 'файл',
  'common.taskKind.bulk': 'синхронізація індексу',
  'common.taskKind.rebuild': 'повний реіндекс',
  'common.taskKind.prune': 'зняття з індексу',
  'common.taskKind.default': 'задача',

  'common.unit.sec': 'с',
  'common.unit.min': 'хв',

  'common.progress.batch': 'батч',
  'common.progress.chunks': 'чанків',
  'common.progress.yielded': 'витіснено',
  'common.progress.approxTitle': 'час відколи консоль побачила цю задачу — вона почалася раніше',
  'common.progress.exactTitle': 'час від початку задачі',

  'common.reindex.queuedNote': 'поставлено: {what} · у черзі {n} · task {ids}',

  'common.bankMenu.stateNote': 'стан: {state}',
  'common.bankMenu.sync': 'Синхронізація індексу',
  'common.bankMenu.syncTitle': 'Переіндексує лише файли, що змінилися, і знімає з індексу видалені',
  'common.bankMenu.rebuild': 'Повний реіндекс',
  'common.bankMenu.rebuildTitle': 'Стирає індекс і збирає його заново — довго, пропорційно розміру банку',
  'common.bankMenu.mcpTitle': 'Токен цього банку і готовий фрагмент конфігурації для проєкту',
  'common.bankMenu.stateLabel': 'Стан',
  'common.bankMenu.remove': 'Прибрати банк',
  'common.bankMenu.removeTitle': 'Зняти банк з реєстру; .md не чіпаються',

  'common.btn.cancel': 'Скасувати',
  'common.btn.close': 'Закрити',
  'common.btn.closeEsc': 'Закрити (Esc)',
  'common.btn.copy': 'копіювати',
  'common.btn.copied': 'скопійовано',

  'common.resizerTitle': 'Перетягніть, щоб змінити ширину',

  // -- common.picker: folder picker (add-bank dialog) ----------------------

  'common.picker.title': 'Додати банк',
  'common.picker.ariaLabel': 'Додати банк',
  'common.picker.pathLabel': 'Директорія з .md — вона стане коренем банку',
  'common.picker.pathPlaceholder': 'або вставте шлях',
  'common.picker.nameLabel': 'Назва банку (необов’язково)',
  'common.picker.namePlaceholder': 'вгадається з назви директорії',
  'common.picker.createStructure': 'Створити структуру пам’яті тут (.claude/memory)',
  'common.picker.connectMcp': 'Одразу підключити проєкт (MCP)',
  'common.picker.addDir': 'Додати цю директорію',
  'common.picker.reading': 'читаю…',
  'common.picker.home': 'дім',
  'common.picker.bankBadge': 'банк',
  'common.picker.noSubdirs': 'жодної піддиректорії',
  'common.picker.truncated': 'показано перші {n} директорій — решту вставте шляхом',
  'common.picker.withSubdirs': '(з піддиректоріями)',
  'common.picker.mdCount': 'у цій директорії {count} .md{nested}',
  'common.picker.noMd': 'у цій директорії немає .md — індексувати буде нічого',
  'common.picker.countTruncatedTitle': 'рахунок обірвано за часом — файлів щонайменше стільки, ' +
    'індексуватися будуть усі',
  'common.picker.excludesTitle': 'без .git, .venv, node_modules — так само, як їх пропускає індексатор',
  'common.picker.alreadyRegistered': 'уже зареєстрована як «{name}»',
  'common.picker.alreadyBankTitle': 'уже банк: {name}',
  'common.picker.hint.alreadyBank': 'директорія вже є банком пам’яті',
  'common.picker.hint.hasNestedMemory': 'директорія вже має структуру .claude/memory',
  'common.picker.hint.willBecome': 'Банком стане: {root}',
  'common.picker.hint.project': 'Проєкт: {root}',
  'common.picker.hint.willConnect': 'Буде підключено після створення структури',
  'common.picker.hint.projectOnly': 'доступно лише для банку в «<проєкт>/.claude/memory»',
  'common.picker.mcpSkipped': 'MCP не підключено',
  'common.picker.mcpConnected': 'MCP підключено',
  'common.picker.mcpFailed': 'підключення MCP не вдалося',
  'common.picker.addedNote': 'банк додано · індексація стала в чергу',

  // -- common.token: MCP access panel ---------------------------------------

  'common.token.title': 'Доступ MCP',
  'common.token.titleFor': 'Доступ MCP — {name}',
  'common.token.ariaLabel': 'Доступ MCP до банку',
  'common.token.regen': 'Перегенерувати',
  'common.token.regenTitle': 'Видати банку новий токен; старий одразу перестане діяти',
  'common.token.regenConfirm': 'Перегенерувати токен банку «{name}»? Старий перестане діяти негайно: ' +
    'кожен конфіг, який його вже містить — ~/.claude.json, .mcp.env інших проєктів — більше ' +
    'не підключиться, доки ви не впишете туди новий токен.',
  'common.token.regenYes': 'Так, перегенерувати',
  'common.token.regeneratedNote': 'Токен перегенеровано. Конфіги зі старим токеном більше не ' +
    'підключаться — впишіть у них новий.',
  'common.token.bankTokenLabel': 'Токен банку',
  'common.token.hide': 'сховати',
  'common.token.show': 'показати',
  'common.token.hideTitle': 'Прибрати значення з екрана',
  'common.token.showTitle': 'Показати значення на екрані',
  'common.token.copyTokenTitle': 'Скопіювати токен, не показуючи його',
  'common.token.copyToClipboard': 'Скопіювати у буфер',
  'common.token.copyFailed': 'Не вдалося скопіювати — виділіть текст і скопіюйте вручну.',
  'common.token.scopeNote': 'Відкриває лише банк «{name}». Службовий токен, яким відкрито цю ' +
    'консоль, ширший — у конфіг проєкту він не потрібен.',
  'common.token.entryLabel': 'Назва запису в конфігурації',
  'common.token.entryHint.base': 'За нею запис видно серед інших mcp-серверів; вона ж стає ' +
    'префіксом імен інструментів — mcp__{entry}__search.',
  'common.token.entryHint.own': ' Від неї ж походить {var}: токен належить одному банку, тож ' +
    'другий банк у тому самому проєкті не переписує токен першого. MNEMO_HOST і MNEMO_PORT ' +
    'спільні — це адреса служби, не банку.',
  'common.token.scope.literal': 'зі значеннями · .mcp.json або ~/.claude.json',
  'common.token.scope.template': 'з плейсхолдерами · .mcp.json.template',
  'common.token.scopeHint': 'Друга — якщо в проєкті є .mcp.json.template і mcp-setup.sh: там ' +
    'значення підставляються з .mcp.env, а в git їде тільки шаблон. Інакше перша: .mcp.json ' +
    'тримає значення прямо і лежить у .gitignore.',
  'common.token.caption.literal': 'Для .mcp.json проєкту або ~/.claude.json — злити з «mcpServers»',
  'common.token.caption.template': 'Для .mcp.json.template — злити з наявним «mcpServers»',
  'common.token.caption.env': 'Рядки для .mcp.env',
  'common.token.templateLead.part1': 'Усі три файли заповнює ',
  'common.token.templateLead.part2': ' — фрагмент у .mcp.json.template, змінні у .mcp.env, рядки ' +
    'підстановки в mcp-setup.sh. Сам .mcp.env він не створює: це файл із секретами, тож спершу ',
  'common.token.templateLead.part3': ', потім init ще раз, і в кінці ',
  'common.token.templateLead.part4': ' — він і збирає .mcp.json зі значеннями. Нижче — те саме, ' +
    'що запише init: щоб побачити наперед або вписати руками, якщо запустити його в цьому ' +
    'проєкті не можна.',
  'common.token.manualPaste.part1': 'Якщо вписуєте руками, додайте до виклику ',
  'common.token.manualPaste.part2': ' у mcp-setup.sh рядок ',
  'common.token.manualPaste.part3': '. Без нього плейсхолдер потрапляє в .mcp.json дослівно, а ' +
    'скрипт усе одно звітує про успіх — і поломка виявиться аж тоді, коли сервер мовчки не ' +
    'підключиться.',
  'common.token.generatedFileNote': '.mcp.json — згенерований файл: він у .gitignore, і ' +
    'mcp-setup.sh переписує його з шаблону. Запис має лежати в .mcp.json.template, інакше ' +
    'наступний запуск скрипта його зітре.',

  // -- common.removal: remove-bank dialog -----------------------------------

  'common.removal.submit': 'Прибрати',
  'common.removal.busy': 'Прибираю…',
  'common.removal.title': 'Прибрати банк',
  'common.removal.ariaLabel': 'Прибрати банк',
  'common.removal.leadPrefix': 'Банк ',
  'common.removal.leadSuffix': ' перестане існувати для цієї машини.',
  'common.removal.goneForever': 'Зникає назавжди',
  'common.removal.goneForeverText': 'Реєстрація банку та його токен. Токен видається випадково і ' +
    'не відтворюється: кожен .mcp.json, який ним підключається, перестане працювати, і ' +
    'повернути той самий токен неможливо.',
  'common.removal.untouched': 'Лишається недоторканим',
  'common.removal.untouchedPrefix': 'Усі .md за шляхом ',
  'common.removal.untouchedSuffix': '. Кабінет не видаляє вміст банку — тільки те, що з нього ' +
    'виведено.',
  'common.removal.dropIndex': 'видалити також індекс ({bytes}) — відновлюваний повним реіндексом',
  'common.removal.stripMcpPrefix': 'видалити MCP-підключення (',
  'common.removal.noMcpJson': 'у корені проєкту немає .mcp.json',
  'common.removal.confirmLabel': 'Введіть назву банку, щоб підтвердити',

  // -- shell: sidebar, header, WebSocket connection state (shell.js) -------

  'shell.nav.ariaLabel': 'Розділи консолі',
  'shell.nav.memory': 'Памʼять',
  'shell.nav.journal': 'Журнал',
  'shell.nav.settings': 'Налаштування',
  'shell.sidebar.collapse': 'Згорнути навігацію',
  'shell.sidebar.expand': 'Розгорнути навігацію',
  'shell.footTitle': 'провайдер {provider} · версія {version}',
  'shell.conn.connecting': 'підключення…',
  'shell.conn.live': 'наживо',
  'shell.conn.dropped': 'розірвано',
  'shell.conn.error': 'помилка',
  'shell.event.done': 'готово: {what} · {n} чанків · {took}',
  'shell.event.error': 'помилка: {what} — {error}',
  'shell.event.pruned': 'знято з індексу: {n}',

  // -- memory: static Памʼять pane markup (index.html) ----------------------

  'memory.pane.banks': 'Банки',
  'memory.pane.refreshTitle': 'Оновити список',
  'memory.pane.files': 'Файли',
  'memory.pane.selectBankHint': 'Оберіть банк ліворуч.',
  'memory.pane.content': 'Вміст',
  'memory.pane.chunkVizTitle': 'Показати межі чанків так, як вони лежать в індексі',
  'memory.pane.chunkVizLabel': 'Межі чанків',
  'memory.pane.reindexFileBtn': 'Переіндексувати файл',
  'memory.pane.selectFileHint': 'Оберіть файл у дереві.',

  // -- memory: Памʼять page — banks, tree, file view (page-memory.js) -------

  'memory.header.panelAriaLabel': 'Панель',
  'memory.header.addBank': '＋ Додати банк',
  'memory.header.addBankTitle': 'Зареєструвати нову директорію з .md як банк',

  'memory.count.banks': { one: '{n} банк', few: '{n} банки', many: '{n} банків' },
  'memory.count.files': { one: '{n} файл', few: '{n} файли', many: '{n} файлів' },
  'memory.count.chunks': { one: '{n} чанк', few: '{n} чанки', many: '{n} чанків' },
  'memory.count.dirs': { one: '{n} директорія', few: '{n} директорії', many: '{n} директорій' },

  'memory.banks.emptyHint': 'Жодного банку не зареєстровано — «＋ Додати банк» у шапці вибирає ' +
    'директорію з .md.',

  'memory.bankState.enabled.label': 'Активний',
  'memory.bankState.enabled.note': 'Стежимо за файлами, індекс оновлюється сам, пошук працює.',
  'memory.bankState.frozen.label': 'Заморожений',
  'memory.bankState.frozen.note': 'За файлами не стежимо — індекс лишається як є, але пошук працює. ' +
    'Це те, що рятує від повної перебудови при зміні моделі.',
  'memory.bankState.disabled.label': 'Вимкнений',
  'memory.bankState.disabled.note': 'Не стежимо й не шукаємо. Банк лишається в реєстрі.',

  'memory.statusNote.indexingHasChunks': 'база є, свіжі зміни доїжджають',
  'memory.statusNote.indexingEmpty': 'перший білд у процесі — ще порожньо',
  'memory.statusNote.emptyQueued': 'порожньо, задачі в черзі',
  'memory.statusNote.emptyIdle': 'справді порожньо, нічого не заплановано',
  'memory.statusNote.ready': 'індекс готовий',

  'memory.bank.menuBtnTitle': 'Дії над банком',
  'memory.bank.filesStat': 'файлів {n}',
  'memory.bank.chunksStat': 'чанків {n}',
  'memory.bank.queuedStat': 'у черзі {n}',
  'memory.bank.dbSizeTitle': 'розмір індексу',
  'memory.bank.lastIndexed': 'остання індексація: {date}',
  'memory.bank.frozenBadge': 'заморожено',
  'memory.bank.frozenBadgeTitle': 'Індекс не оновлюється — файли могли змінитись після {date}. ' +
    'Пошук працює й відповідає за тим станом.',
  'memory.bank.disabledBadge': 'вимкнено',
  'memory.bank.noRootBadge': 'нема кореня',

  'memory.indexedState.yes': 'в індексі',
  'memory.indexedState.no': 'не в індексі',

  'memory.tree.selectBankHint': 'Оберіть банк ліворуч.',
  'memory.tree.loading': 'Завантаження…',
  'memory.tree.emptyMd': 'У цьому банку немає .md файлів.',

  'memory.chunk.gap': '· поза чанками ·',
  'memory.chunk.end': { one: 'кінець · {n} символ', few: 'кінець · {n} символи', many: 'кінець · {n} символів' },

  'memory.rebuild.action': 'Перегенерувати',
  'memory.rebuild.queuing': 'Ставимо в чергу…',
  'memory.rebuild.dialogTitle': 'Перегенерувати індекси',
  'memory.rebuild.dialogAriaLabel': 'Перегенерувати індекси',
  'memory.rebuild.notice.actionable': '{n} банк(и) мають індекс від попередньої моделі',
  'memory.rebuild.notice.running': '{n} вже перегенеровуються',
  'memory.rebuild.notice.disabled': '{n} вимкнено — спершу їх треба увімкнути',
  'memory.rebuild.notice.suffix': '. Пошук по застарілих векторах відмовляє, а не змішує два простори.',
  'memory.rebuild.dialog.lead': 'Повний реіндекс буде поставлено для {n} банк(ів). Старі ' +
    'derived-індекси буде стерто й зібрано з .md заново.',
  'memory.rebuild.dialog.chunksLabel': '{n} чанків',
  'memory.rebuild.dialog.note': 'Файли .md не змінюються. Час пропорційний обсягу; конкретна ' +
    'швидкість залежить від бекенда й заліза цієї машини.',

  // -- journal: static filter/list markup (index.html) ---------------------

  'journal.filter.bankLabel': 'Банк',
  'journal.filter.allBanks': 'Усі банки',
  'journal.filter.periodLabel': 'Період',
  'journal.filter.period24h': '24 години',
  'journal.filter.period1h': 'Остання година',
  'journal.filter.period7d': '7 днів',
  'journal.filter.period30d': '30 днів',
  'journal.list.newestFirst': 'нові спочатку',

  // -- journal: Журнал page — header, list, detail (page-journal.js) -------

  'journal.header.segQuery': 'Запити',
  'journal.header.segIndex': 'Індексація',
  'journal.header.refreshTitle': 'Оновити журнал',

  'journal.list.shownOf': 'Показано {shown} із {total}',
  'journal.list.empty': 'Порожньо',
  'journal.list.noEvents': 'Подій не знайдено.',

  'journal.event.rebuildTitle': 'Повна перебудова банку',
  'journal.event.pruneTitle': 'Зняття з індексу',
  'journal.event.syncTitle': 'Синхронізація індексу',
  'journal.event.errorStatus': 'помилка',

  'journal.hit.openFile': 'Відкрити файл',
  'journal.hit.showMore': 'показати повністю',
  'journal.hit.collapse': 'згорнути',
  'journal.hit.chunkLabel': '{heading} · чанк {n}',

  'journal.detail.queryKicker': 'запит · #{id}',
  'journal.detail.indexKicker': 'індексація · #{id}',
  'journal.detail.bank': 'банк',
  'journal.detail.face': 'обличчя',
  'journal.detail.prefix': 'префікс',
  'journal.detail.hits': 'хітів',
  'journal.detail.tookMs': 'час, мс',
  'journal.detail.when': 'коли',
  'journal.detail.kind': 'вид',
  'journal.detail.trigger': 'тригер',
  'journal.detail.resultsLabel': 'Результати',
  'journal.detail.resultsOrderNote': 'у точному ранговому порядку',
  'journal.detail.noHits': 'Жодного влучення.',
  'journal.detail.filesIndexed': 'файлів',
  'journal.detail.chunksIndexed': 'чанків',
  'journal.detail.filesPruned': 'знято',
  'journal.detail.duration': 'тривалість',
  'journal.detail.errorLabel': 'Помилка',
  'journal.detail.fileSection': 'Файл',
  'journal.detail.currentFileOf': 'поточний файл банку {bank}',
  'journal.detail.selectHint': 'Оберіть подію ліворуч.',

  // -- settings: static footer markup (index.html) --------------------------

  'settings.btn.save': 'Зберегти',

  // -- settings: Settings page — header, tabs, ledes (page-settings.js) -----

  'settings.header.title': 'Налаштування',
  'settings.header.sub': 'стосується цієї машини, не окремого банку',

  'settings.tabs.general': 'Загальні',
  'settings.tabs.embed': 'Модель ембедингу',
  'settings.tabs.maint': 'Обслуговування',

  'settings.lede.general': 'Те, що стосується самої консолі й машини в цілому — не окремого ' +
    'банку і не бекенда ембедингу.',
  'settings.lede.embed': 'Який бекенд рахує вектори для пошуку в банках і скільки оперативної ' +
    'памʼяті він для цього займає.',
  'settings.lede.maint': 'Той самий структурований doctor report, який CLI показує текстом. ' +
    'Перевірки запускаються лише при відкритті цього розділу.',

  'settings.loading': 'Завантаження…',
  'settings.messages.nothingChanged': 'Нічого не змінено.',
  'settings.overrideNote': 'перекрито змінною {var} — збережене тут не подіє, доки вона виставлена',
  'settings.notSavedToggle': 'не збережено — зараз {state}; натисніть «Зберегти», щоб застосувати',
  'settings.state.on': 'увімкнено',
  'settings.state.off': 'вимкнено',
  'settings.toggle.on': 'Увімкнено',
  'settings.toggle.off': 'Вимкнено',

  // -- settings.general: theme, language, autostart, auto-update, require-login, status --

  'settings.general.theme.label': 'Тема консолі',
  'settings.general.theme.note': 'Вибір цього браузера — тому застосовується одразу й не чекає «Зберегти».',
  'settings.general.theme.dark': 'Темна',
  'settings.general.theme.light': 'Світла',

  'settings.general.language.label': 'Мова',
  'settings.general.language.note': 'Вибір цього браузера — тому застосовується одразу й не чекає «Зберегти».',

  'settings.general.autostart.label': 'Запускати службу при вході в систему',
  'settings.general.autostart.note': 'Реєструється як {mechanism}{named}. Діє з наступного входу; ' +
    'те, що працює зараз, не зачіпає.',
  'settings.general.autostart.namedSuffix': ' — «{name}»',
  'settings.general.autostart.notFetched': 'стан не отримано',
  'settings.general.autostart.unsupported': 'на цій системі не підтримується',
  'settings.general.autostart.savedOn': 'Автозапуск: служба підніматиметься при вході в систему.',
  'settings.general.autostart.savedOff': 'Автозапуск: вимкнено, службу доведеться піднімати самому.',

  'settings.general.autoUpdate.label': 'Автоматичне оновлення',
  'settings.general.autoUpdate.note': 'Придатний реліз застосовується сам — з коротким відліком і ' +
    'кнопкою «Скасувати» просто в консолі. Вимкнено — лишається тільки банер і ручне ' +
    'підтвердження, як і раніше.',
  'settings.general.autoUpdate.savedOn': 'Автооновлення: увімкнено.',
  'settings.general.autoUpdate.savedOff': 'Автооновлення: вимкнено.',
  'settings.general.autoUpdate.checking': 'Перевіряємо…',
  'settings.general.autoUpdate.checkBtn': 'Перевірити оновлення',
  'settings.general.autoUpdate.upToDate': 'Актуальна версія.',

  'settings.general.requireLogin.label': 'Вимагати токен для входу в кабінет',
  'settings.general.requireLogin.noteOff': 'Вимкнено (типово): «/api» (кабінет і CLI) відкритий ' +
    'на loopback, як зараз, — без токена.',
  'settings.general.requireLogin.noteOn': 'Увімкнено: кабінету й CLI потрібен сервісний токен ' +
    'для доступу.',
  'settings.general.requireLogin.savedOn': 'Вхід у кабінет тепер вимагає токен.',
  'settings.general.requireLogin.savedOff': 'Кабінет знову відкритий на loopback без токена.',
  'settings.general.requireLogin.tokenLabel': 'Сервісний токен',
  'settings.general.requireLogin.tokenNote': 'Показано один раз — GET /api/settings більше його не поверне. ' +
    'Консоль уже підставила цей токен у поточну сесію, тож входити ' +
    'заново не потрібно. Якщо він загубиться — той самий файл лежить ' +
    'на диску (шлях і mnemo doctor покажуть його), або вимкніть цю ' +
    'опцію ще раз, щоб зняти вимогу токена.',

  'settings.general.serviceNotLoaded': 'Стан служби ще не отримано.',
  'settings.general.statusLabel': 'Стан',
  'settings.general.stat.version': 'Версія',
  'settings.general.stat.pid': 'PID',
  'settings.general.stat.address': 'Адреса',
  'settings.general.stat.provider': 'Провайдер',
  'settings.general.stat.uptime': 'Працює',
  'settings.general.stat.priorityQueue': 'Черга пріоритетів',
  'settings.general.stat.priorityOn': 'увімкнена',
  'settings.general.stat.priorityOff': 'вимкнена',
  'settings.general.aboutLabel': 'Про проект',

  'settings.uptime.hoursMinutes': '{h} год {m} хв',
  'settings.uptime.minutesSeconds': '{m} хв {s} с',
  'settings.uptime.seconds': '{s} с',

  // -- settings.embed: backend, model, endpoint, key, memory ----------------

  'settings.embed.warn.line1': 'Зміна моделі або ширини — це новий ключ перебудови.',
  'settings.embed.warn.line2': 'Конфігурація діє після збереження налаштувань.',
  'settings.embed.warn.line3': 'Старі індекси отримають REBUILD PENDING і пошук по них ' +
    'відмовить, доки їх не перегенерувати.',
  'settings.embed.backendLabel': 'Бекенд ембедингу',
  'settings.embed.notSavedBackend': 'не збережено — зараз активний «{active}»; натисніть ' +
    '«Зберегти», щоб перемкнути на «{target}»',
  'settings.embed.local.lead': 'Вектори рахує резидент на цій машині — ',
  'settings.embed.local.dimsSuffix': ' ({dim} вимірів). ',
  'settings.embed.local.noDimSuffix': '. ',
  'settings.embed.local.tail': 'Жоден байт памʼяті не залишає машину.',
  'settings.embed.modelLabel': 'Модель',
  'settings.embed.model.prefixedNote': 'ця модель тренована з маркерами — mnemo підставить їх сама',
  'settings.embed.model.notInCatalog': ' (не з довідника)',
  'settings.embed.urlLabel': 'Адреса',
  'settings.embed.dimLabel': 'Вимірів',
  'settings.embed.timeoutLabel': 'Таймаут, с',
  'settings.embed.dimNote': 'Ширина підставлена з довідника, але останнє слово за самим ' +
    'ендпоінтом: mnemo звіряє її з першим отриманим вектором і ' +
    'відмовиться писати індекс, якщо вони розійшлися.',
  'settings.embed.keyLabel': 'Ключ API',
  'settings.embed.keyNote': 'Зберігається у settings.json на цій машині. Назад не показується — ' +
    'сторінка, яка друкує секрет, друкує його і в скриншот.',
  'settings.embed.key.placeholderStored': 'збережений — введіть новий, щоб замінити',

  'settings.embed.mem.hold.loaded': 'у памʼяті',
  'settings.embed.mem.hold.unloaded': 'не в памʼяті',
  'settings.embed.mem.hold.na': 'нічого не тримає',
  'settings.embed.mem.hold.unknown': 'невідомо',
  'settings.embed.mem.label': 'Оперативна памʼять',
  'settings.embed.mem.notFetched': 'Стан ще не отримано.',
  'settings.embed.mem.introStrong': 'Модель піднімається сама, коли потрібна для пошуку чи індексації',
  'settings.embed.mem.introRest': ' — «не в памʼяті» це нормальний стан, не помилка. ' +
    '«Вивантажити» звільняє памʼять одразу, замість тримати модель ' +
    'постійно завантаженою про запас.',
  'settings.embed.mem.statusCaption': 'Статус:',
  'settings.embed.mem.modelCaption': 'Модель:',
  'settings.embed.mem.idleCaption': 'Автовивантаження:',
  'settings.embed.mem.unloadBtn': 'Вивантажити',
  'settings.embed.mem.wakeBtn': 'Підняти в памʼять',
  'settings.embed.mem.probeEndpointBtn': 'Перевірити ендпоінт',
  'settings.embed.mem.probeBtn': 'Перевірити',
  'settings.embed.mem.downloading': 'Завантаження моделі…',
  'settings.embed.mem.downloadBtn': 'Завантажити модель на диск (2.2 ГБ)',
  'settings.embed.mem.note.wakeSoon': 'Підніметься назад за кілька секунд.',
  'settings.embed.mem.note.naHosted': 'Цей ендпоінт не тримає нічого на цій машині — модель живе ' +
    'на боці постачальника, тож звільняти нічого.',
  'settings.embed.mem.note.naProbeCost': '«Перевірити ендпоінт» зробить один embedding request. Для ' +
    'тарифікованого API це може бути платний виклик.',
  'settings.embed.mem.note.expiresAt': 'Бекенд тримає її до {when}.',
  'settings.embed.mem.note.othersHeld': 'Там же ще {n} модел(і/ей) — не наші, їх не чіпаємо.',
  'settings.embed.mem.note.downloadFailed': 'Завантаження не вдалося — спробуйте ще раз.',
  'settings.embed.mem.unloadedNote': 'Памʼять звільнено. Модель повернеться сама при наступному пошуку.',
  'settings.embed.mem.probeOkBase': 'Ендпоінт відповів',
  'settings.embed.mem.probeOkDimSuffix': ' — пробний вектор має {dim} вимірів.',
  'settings.embed.mem.loadedNote': 'Бекенд відповів — модель у памʼяті.',
  'settings.embed.mem.busyError': 'Черга ще працює через цей бекенд — почекайте, доки вона ' +
    'звільниться, і спробуйте ще раз.',

  'settings.embed.errors.missingUrl': 'Вкажіть адресу ендпоінта.',
  'settings.embed.errors.dimNotPositive': 'Вимірів має бути додатним числом.',
  'settings.embed.saved.restartRequired': 'Збережено. Набере чинності після перезапуску служби.',
  'settings.embed.saved.appliedNoPending': 'Збережено й застосовано. Перевірте бекенд кнопкою вище.',
  'settings.embed.saved.appliedPending': 'Збережено й застосовано. Перевірте бекенд кнопкою вище, ' +
    'потім перегенеруйте банки з REBUILD PENDING на головному екрані.',
  'settings.embed.errors.refreshFailed': 'Налаштування збережено, але не всі стани вдалося ' +
    'перечитати. Оновіть сторінку — повторно зберігати не потрібно.',

  // -- settings.maint: diagnostics + orphan cleanup --------------------------

  'settings.maint.refreshing': 'Оновлюємо…',
  'settings.maint.refreshBtn': 'Оновити діагностику',
  'settings.maint.collecting': 'Збираємо діагностику…',
  'settings.maint.notFetched': 'Звіт ще не отримано.',
  'settings.maint.engineLabel': 'Рушій',
  'settings.maint.embedLabel': 'Ембединг',
  'settings.maint.providerLabel': 'Провайдер',
  'settings.maint.localModelLabel': 'Локальна модель',
  'settings.maint.residentLabel': 'Резидент',
  'settings.maint.serviceLabel': 'Служба',
  'settings.maint.queueLabel': 'Черга',
  'settings.maint.registryLabel': 'Реєстр',
  'settings.maint.unknown': 'невідомо',
  'settings.maint.unknownTitle': 'Невідомо',
  'settings.maint.genericError': 'помилка',
  'settings.maint.model.cachedFull': 'кеш повний',
  'settings.maint.model.notLoaded': 'НЕ ЗАВАНТАЖЕНА',
  'settings.maint.model.cachedNotNeeded': 'є, але не потрібна',
  'settings.maint.model.notNeeded': 'не потрібна',
  'settings.maint.unavailable': 'НЕДОСТУПНИЙ',
  'settings.maint.resident.up': 'працює',
  'settings.maint.resident.down': 'не завантажений',
  'settings.maint.resident.portSuffix': ' · машинний порт',
  'settings.maint.resident.na': 'n/a для цього провайдера',
  'settings.maint.endpoint.dimsUnit': 'вимірів',
  'settings.maint.endpoint.notConfigured': 'НЕ НАЛАШТОВАНО — {error}',
  'settings.maint.backend.upSummary': 'працює · pid {pid} · машинний порт',
  'settings.maint.backend.down': 'НЕ ДОСТУПНИЙ — {error}',
  'settings.maint.token.notSet': 'не встановлено · /api відкритий на loopback за замовчуванням',
  'settings.maint.registryUnreadable': 'НЕЧИТАНИЙ — {error}',
  'settings.maint.registryReadable': 'Реєстр читається.',
  'settings.maint.registry.noRoot': 'нема кореня',
  'settings.maint.count.projects': { one: '{n} проєкт', few: '{n} проєкти', many: '{n} проєктів' },
  'settings.maint.wiring.allCurrent': 'усі актуальні',

  'settings.maint.orphans.unavailableTitle': 'Список недоступний',
  'settings.maint.orphans.deletionForbidden': 'Видалення заборонене: {reason}',
  'settings.maint.orphans.registryUncheckable': 'реєстр не можна перевірити',
  'settings.maint.orphans.noneTitle': 'Сиріт немає',
  'settings.maint.orphans.noneNote': 'Кожен index належить банку.',
  'settings.maint.orphans.unreadable': 'не читається — {error}',
  'settings.maint.orphans.preV3NoRoot': 'pre-v3 index — root не записаний',
  'settings.maint.orphans.noRoot': 'root не записаний',
  'settings.maint.orphans.rootStillOnDisk': ' · root досі є на диску',
  'settings.maint.orphans.unknownFiles': '? файлів',
  'settings.maint.orphans.sectionLabel': 'Індекси-сироти',
  'settings.maint.orphans.sectionNote': 'Doctor лише показує. Прибирає тільки окрема підтверджена ' +
    'дія — ніколи автоматично й ніколи разом із діагностикою.',
  'settings.maint.orphans.cleanupBtn': 'Прибрати сироти',
  'settings.maint.orphans.confirmText': 'Буде видалено тільки ці показані derived index id: {ids}. ' +
    'Перед кожним видаленням реєстр перевіряється знову; .md не чіпаються.',
  'settings.maint.orphans.cleaning': 'Прибираємо…',
  'settings.maint.orphans.deleteBtn': 'Видалити {n}',
  'settings.maint.orphans.result.removed': 'видалено {removed} з {total}',
  'settings.maint.orphans.result.freed': 'звільнено {bytes}',
  'settings.maint.orphans.result.skipped': 'пропущено {n}',
  'settings.maint.orphans.result.locked': 'locked {n}',
  'settings.maint.orphans.lockedError': 'Не всі файли видалено: {list}',

  // -- update: self-update banner + modal (update.js) -----------------------

  'update.modal.title': 'Оновлення mnemo',
  'update.steps.download': 'Завантаження з GitHub',
  'update.steps.venv': 'Встановлення пакетів',
  'update.steps.switching': 'Перемикання версії та перезапуск',

  'update.confirm.currentLabel': 'Поточна версія: ',
  'update.confirm.newLabel': 'Нова версія: ',
  'update.confirm.warning': 'Службу mnemo буде зупинено й перезапущено на новій версії. На ' +
    'цей час пошук та індексація недоступні. Після «OK» дію не можна скасувати — прогрес ' +
    'показуватиметься до завершення.',
  'update.confirm.okBtn': 'OK',
  'update.confirm.staleTarget': 'Показана версія застаріла (актуальна: {tag}). Закрийте ' +
    'вікно й спробуйте ще раз.',

  'update.autoPending.leadPrefix': 'Автоматичне оновлення до ',
  'update.autoPending.leadMiddle': ' почнеться через ',
  'update.autoPending.leadSuffix': ' с.',
  'update.autoPending.note': 'Якщо нічого не натиснути, оновлення застосується автоматично. ' +
    '«Скасувати» лише відкладає його — ту саму версію може бути запропоновано знову під час ' +
    'наступної перевірки.',

  'update.progress.title': 'Оновлення до {tag}…',
  'update.progress.switchingNote': 'Служба перезапускається — сторінка на кілька секунд ' +
    'втратить з’єднання. Це очікувано: результат стане відомий одразу після відновлення ' +
    'зв’язку.',

  'update.timeout.text': 'Не вдалося дізнатися результат оновлення за відведений час. Служба ' +
    'могла ще перезапускатися, або консоль тимчасово не звʼязується з нею. Перевірте вручну ' +
    '(mnemo doctor) або спробуйте ще раз.',
  'update.timeout.retryBtn': 'Спробувати ще',

  'update.terminal.done': 'Оновлено до {tag}.',
  'update.terminal.rolledBack': 'Проблема під час оновлення до {tag} — відкотили назад на {current}.',
  'update.terminal.errorSuffix': ' ({error})',
  'update.terminal.failedBase': 'Оновлення не вдалося',
  'update.terminal.failedWithError': ': {error}',
  'update.terminal.failedNoError': '.',
  'update.terminal.unknownState': ' Стан служби може бути невизначеним — перевірте mnemo doctor.',
  'update.terminal.unchanged': ' Поточна версія не змінювалась.',
  'update.terminal.autoClosePrefix': 'Закриється автоматично через ',
  'update.terminal.autoCloseSuffix': ' с.',

  'update.banner.busy': 'Оновлення mnemo триває…',
  'update.banner.autoPending': 'Автооновлення до {tag} очікує підтвердження',
  'update.banner.available': 'Доступна нова версія {tag}',
};
