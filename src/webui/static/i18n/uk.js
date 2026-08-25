/* mnemo web console — Ukrainian dictionary (MN-10).
 *
 * Flat `key -> string` map, plus plural entries (`key -> {one, few, many}`)
 * consumed by `plural()` in app.js, using the same Slavic triad rule as the
 * existing `pluralizeUk()` helper (app.js). Keys mirror `en.js` one for one;
 * a key missing here falls back to English rather than breaking.
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

  // -- journal: static filter/list markup (index.html) ---------------------

  'journal.filter.bankLabel': 'Банк',
  'journal.filter.allBanks': 'Усі банки',
  'journal.filter.periodLabel': 'Період',
  'journal.filter.period24h': '24 години',
  'journal.filter.period1h': 'Остання година',
  'journal.filter.period7d': '7 днів',
  'journal.filter.period30d': '30 днів',
  'journal.list.newestFirst': 'нові спочатку',

  // -- settings: static footer markup (index.html) --------------------------

  'settings.btn.save': 'Зберегти',
};
