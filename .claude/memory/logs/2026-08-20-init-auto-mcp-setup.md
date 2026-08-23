# 2026-08-20 (п'яте) — `init` сам запускає `mcp-setup`, без ручного кроку

`feat(init): run mcp-setup itself, so .mcp.json is ready without a manual step`

## Запит

Користувач: `mnemo init` мав закінчуватись на друкованій інструкції «regenerate .mcp.json with either of: bash mcp-setup.sh / powershell ... mcp-setup.ps1» — і людині доводилось виконувати команду вручну.
Просив продумати, щоб `init` сам виконав скрипт останнім кроком («бам-бам»), і нагадати edge cases наявного MCP-wiring.

Спершу `EnterPlanMode` (архітектурна розвилка — торкається інваріанту «`.mcp.json` ніколи не пишеться планувальником»), план затверджено, потім реалізовано.

## Edge cases наявного wiring (нічого з цього не змінено)

Відповідь на друге питання користувача — все вже враховано в `_plan_wiring`/`_bootstrap_layer`/`_plan_setup_scripts`/`_setup_state`: відсутній шаблон → `_bootstrap_layer` сіє (або переносить наявні сервери з `.mcp.json` — `carried`); шаблон уже є → мердж; старий ключ `mnemo` (HTTP) → звичайний `init` перейменовує, stdio-покоління → лише `--migrate`; чужий сервер `mnemo` → не чіпається; `mcp-setup.*` відсутні → сіються; маркер mnemo + стара редакція → `stale`, оновлюється; маркер mnemo + чужі байти → `edited`, лишається як є; маркера нема взагалі → лише дописуються `sed -e` рядки; `.mcp.json`/`.mcp.env` git-tracked → відмова з `git rm --cached`.

## Що зроблено

`src/scaffold.py`:
- `_run_setup_script(proj) -> (ok, lines, script)` — обирає раннер за платформою (`os.name == "nt"` → PowerShell на `mcp-setup.ps1`, інакше — `mcp-setup.sh` напряму через shebang, БЕЗ пошуку `bash` на PATH), запускає з таймаутом 30с, повертає stdout скрипта на успіх або коротку діагностику на невдачу.
  Ніколи не кидає — невдала регенерація не валить `init`.
- `init_project()`'s фінальний блок: успіх → «ran mcp-setup.<sh|ps1> — .mcp.json is ready» + рядки скрипта; невдача → та сама стара інструкція («regenerate .mcp.json by hand with either of: ...») як фолбек, плюс діагностика чому.
  `init` і далі повертає `0` — сама проводка вдалась, автозапуск лише зручність.
- Windows-специфічний `creationflags=0x08000000` (CREATE_NO_WINDOW) на виклик `subprocess.run` — знайдено САМ під час рев'ю, не в запиті: `init` (і, отже, цей крок) також викликається зсередини FastAPI/uvicorn без консолі (кабінетний чекбокс «одразу підключити проєкт (MCP)», див. `logs/2026-08-20-cabinet-followups.md`), і без прапорця там блиснуло б вікно PowerShell.
  Число власне в `scaffold.py`, не імпортоване з `service_ctl._CREATE_NO_WINDOW` (модуль-приватне, крос-модульно не тягнеться).

**Інваріант не порушено:** `_plan_wiring()` і далі ніколи не кладе `.mcp.json` у свій план записів (тест `.mcp.json itself is never written` не чіпався й далі зелений) — новий крок стартує вже після запису файлів, поза рівнем планування.

## Тести

`tests/test_platform.py` — **перші наскрізні тести `init_project()` в суїті** (до цього все тестувало `_plan_wiring`/`_plan_mcp_template` напряму, `grep init_project( tests/*.py` був порожній):
- `test_init_runs_setup_script` — реальний `init_project()`, реальний subprocess-запуск щойно написаного скрипта; `.mcp.json` з'являється без жодного плейсхолдера, з реальним loopback URL і 48-hex токеном.
- `test_init_setup_script_failure_falls_back` — перший `init` успішний, потім скрипт зламано вручну (лишаючи маркер mnemo → `edited`-стан), другий `init` все одно повертає `0`, друкує і діагностику, і ручний фолбек.

Обидва запускались насправді на цій Windows-машині (PowerShell branch, а не пропуск) — 316 passed, 0 failed, разом з рештою суїти.

**Безпечний патерн для тестування `init_project()`/`_register_bank`** (знайдено при дизайні тестів, до того як хтось наступний повторив би пастку з `logs/2026-08-20-cabinet-followups.md`): мокати `src.client.Client` **цілком** (`patch("src.client.Client")`, `.add_bank.side_effect = ServiceDown()`), а не вказувати недосяжний порт — порт з `autostart=True` все одно намагається підняти реальний сервіс, підмінений клас узагалі не йде в мережу.
Записано і в `topics/project-wiring.md`, і в `topics/deferred.md`.

## Верифікація

- `python -c "import ast; ast.parse(...)"` на обох файлах.
- `pytest`-стиль ручний прогін (`python tests/test_platform.py`): 316 passed, 0 failed, включно з двома новими тестами (12 під-перевірок разом).
- Живої переперевірки через встановлений `mnemo.exe` НЕ робилось — рушій (`~/.claude/mnemo/`) досі старий mirror, і `python -m src.cli init` напряму ризикував би або зачепити реальний сервіс (8918), або автоспавнути новий (та сама пастка вище).
  Наскрізні тести вже виконують реальний subprocess-запуск щойно написаного скрипта — те саме, що й живий `mnemo init`, лише з ізольованою реєстрацією банку.
