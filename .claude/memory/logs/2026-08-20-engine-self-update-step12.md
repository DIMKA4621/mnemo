# 2026-08-20 (чотирнадцяте) — самооновлення рушія: крок 12, наскрізна перевірка

Продовження `logs/2026-08-20-engine-self-update-update-available-fix.md`.
**Три нові баги знайдені tester'ом, не полагоджені** (ескальовано, за тією самою дисципліною, що вся фіча) — це найважливіший результат кроку 12, важливіший за сам факт "6 сценаріїв пройдено".

## Баг A — КРИТИЧНИЙ: самооновлення ламає команду `mnemo` при кожному успіху

`engine_update._build_engine_version()` кличе `install.ps1`'s `Build-EngineVersion` з `-VersionDir` у **staging**-теку (`state/tmp/update-<tag>/build/`), тоді `_finalize_version_dir()` **переміщує** цю теку в `versions/<tag>/`.
Venv-python.exe толерує переміщення (relocatable за дизайном), але **pip-згенеровані console-script exe** (`mnemo.exe`, `mnemow.exe`, `pip.exe`, будь-що з `[project.scripts]`) зашивають **абсолютний shebang-шлях під час білда**.
Доведено побайтним витягом з реального exe:
```
#!E:\...\state\tmp\update-v1\build\.venv\Scripts\python.exe
```
Запуск такого exe (чи `pip.exe`, щоб виключити mnemo-специфіку) → `rc=1`, нуль stdout/stderr.

**Погіршує ситуацію:** `Publish-Launchers` (копіює стабільні exe в `bin\`) **ніколи не викликається в self-update пайплайні** (`grep` по `engine_update.py`/`cli.py`/`api.py` — нуль збігів).
Сам бекенд-сервіс не постраждав (`target_for_version()`/`windowless_python()` спавнять `pythonw.exe` напряму, не через ці exe), але **людська команда `mnemo`, функція в PowerShell-профілі й Task Scheduler autostart** — усі залежать від цього exe.

**Ширший корінь, ніж просто "staging+move ламає shebang":** навіть без staging-бага, `bin\mnemo.exe`'s shebang за дизайном вказує на конкретну версію (`versions/<tag>/.venv/Scripts/python.exe`) — а retention рано чи пізно **видаляє** ту версію.
Тобто `bin\mnemo.exe` мусить перевидаватись на кожному успішному switch (не тільки один раз при першому інсталі), інакше він неминуче зламається сам собою, коли джерельна версія випаде за retention — незалежно від staging-бага.

**Власник:** `src/engine_update.py` (service-dev, корінь — staging+move)
+ `src/cli.py`'s `_cmd_update_apply` (platform-dev, відсутній republish-виклик на кожен switch).

## Баг B — race у володінні процесом

Якщо процес `update-apply` вмирає між успішним `spawn_detached()` і записом `state/service.pid` (`_write_identity()` всередині `start()`) — новий бекенд піднімається й стає здоровим, але `service.pid` ніколи не пишеться.
`mnemo service status` каже `"not started by us"`, а `mnemo service stop` **відмовляється** зупиняти ("started by another launcher"), хоч цей бекенд легітимно піднятий самим mnemo self-update.

**Доведено живцем:** вбито реальний процес `update-apply` в точний момент запису `start_apply()` в `engine_version.json`; результуючий v7-бекенд відповідав коректно (`/health` → `3.0.0-v7`, healthy), `service.pid` справді відсутній, `mnemo service stop` видав `refusing to stop pid 51260 - started by another launcher` (rc=1), процес продовжив жити.
`install.ps1`'s refresh-флоу й `uninstall.ps1` мають force-fallback через `service.json`'s pid, тож це не постійно некерований стан машинно — але основна команда `mnemo service stop` не працює для цього бекенда, поки щось інше не перепише `service.pid`.

**Власник:** `src/service_ctl.py` (platform-dev).

## Баг C — `install.sh` мігрований лише наполовину, свіжий POSIX-інстал не працює взагалі

Тільки **шаблон** лаунчера в heredoc (`install.sh:345-358`) оновлено під версійну схему.
Усе інше, що реально **будує** рушій — старий плаский код: `PY_BIN="$MNEMO_HOME/.venv/bin/python"` (рядок 80), `--check` (рядок 138), `--deps-only` (214-221), `mkdir -p` розкладки (237-241, **не** створює `versions/`/`current`), rsync/cp мірор (289-296, напряму в `$MNEMO_HOME/src`), створення venv (302, `$MNEMO_HOME/.venv`).
**Ніде у файлі не створюється symlink `current`.** Тобто свіжий `install.sh` будує пласку розкладку, яку щойно переписаний `bin/mnemo`-лаунчер **ніколи не знайде** (`$HOME_DIR/current/.venv/bin/python` — такого файлу нема).
`install.sh` **нефункціональний сьогодні** для свіжого інсталу.

`tests/test_install_posix.py` спіймав би це миттєво на реальному прогоні — просто POSIX-машини не було під рукою жодного разу за всю фічу (підтверджено повторно в логах).
Перевірено кодрев'ю, не живим прогоном — як і домовлено.

**Власник:** `install.sh` (platform-dev).

## Успішно перевірені сценарії (6, живцем, throwaway `.claude/scratch/step12`)

1. **Повний цикл** v1→v5, через реальний `/api/update/apply`, свіжим оком (не довіряючи попереднім звітам наосліп) — усі переходи `apply.state` коректні.
2. **Паралельний пошук під час стейджингу** — стара версія відповідала на `/health` і реальний `mnemo search` (0.703с, коректні хіти) весь час, поки нова стейджилась.
3. **Retention на 4 послідовних апдейтах** — `versions/` після кожного: `[v1]`→`[v1,v2]`→`[v1,v2,v3]`→`[v2,v3,v4]`→`[v3,v4,v5]`.
   Точно 3, не раніше, не пізніше; видалені версії — повністю, разом з venv; `state/`/`model-cache/` не зачеплені.
4. **`doctor` після вбитого mid-apply.** Перша спроба (v6) програла гонку з WMI-поллінгом і вбила не той/уже мертвий PID — v6 завершився штатно (задокументовано чесно, не приховано).
   Друга спроба з точним instrumentation спіймала й убила реальний PID у момент `start_apply()`.
   **Реальний результуючий стан (не самозцілення):** новий v7-бекенд уже піднявся й був здоровим незалежно, але `record_installed`/ `finish_apply`/`prune_versions` не виконались (`engine_version.json. current` лишився `"v6"`, хоч реально служить v7), і `service.pid` не записаний (баг B).
   Після порогу `mnemo doctor` коректно показав `STUCK APPLY`, але **не згадав, що сервіс насправді здоровий на новому тегу** — людина, що піде за порадою "check service status", наткнеться на баг B і розгубиться без контексту.
5. **Автозапуск** — `autostart.launcher_path()`/`task_xml()` (чисті функції, реального Task Scheduler не чіпав) підтверджують: `<Command>` завжди резолвиться на стабільний неверсійний `USER_HOME/bin/mnemow.exe`, структурно незалежний від `current`/`versions`.
   Але через баг A "шлях стабільний" **не достатньо** — сам файл за цим шляхом зламаний з моменту першої self-update-публікації.
6. **POSIX** — код-рев'ю (немає POSIX-машини) → баг C, вище.

## Наступне

Три баги ескальовані, не полагоджені tester'ом (правильно — не його territory).
Роутинг: `engine_update.py`'s стейджинг (баг A, корінь) → service-dev; `cli.py`'s `_cmd_update_apply` (баг A, republish-виклик) + `service_ctl.py` (баг B) + `install.sh` (баг C) → platform-dev.
