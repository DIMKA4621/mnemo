# 2026-08-20 (сьоме) — самооновлення рушія: кроки 0–4 (unstaged)

Дизайн і повний план — `topics/engine-self-update-design.md`. Нічого з
нижченаведеного не закомічено; усе — робоче дерево репозиторію.

## Крок 0 — спайк диспетчеризації лаунчера

Підтвердив архітектурне припущення: `mnemo_bootstrap.py` може спавнити
subprocess (`current/.venv/Scripts/(python|pythonw).exe -m src.cli <argv>`)
замість in-process імпорту `src.cli`, і `service_ctl.py` при цьому не
потребує жодних правок — `windowless_python()`/`_default_target()`
автоматично резолвлять версійний інтерпретатор, бо диспетчеризація вже
відбулась до їхнього виконання.

**Знахідка, не очевидна з дизайну:** `sys.prefix`/`sys.executable`/`__file__`
для пошуку `ENGINE_HOME` непридатні — усі прив'язані до venv, в якому
`pip install --no-deps` зібрав exe **на етапі білда**, не до місця
реального запуску (перевірено копіюванням exe в іншу теку). Придатний лише
`sys.argv[0]`. Записано в тему дизайну.

**Відкрито:** Ctrl-C на foreground-виклику не перевірено емпірично (немає
інтерактивного TTY в середовищі спайку) — аргументовано з семантики Windows
process groups, потребує ручної перевірки колись перед випуском.

## Кроки 1–4 — константи, лаунчер, `service_ctl.py`, інсталятори

- `config.py`: `VERSIONS_DIR`, `CURRENT_LINK`, `UPDATE_RETENTION_COUNT=3`
  (env-керований) у секції platform-dev (блок L). Таймінги перевірки
  (`UPDATE_CHECK_INTERVAL_S`/`GITHUB_REPO`/`UPDATE_CHECK_TIMEOUT_S`) свідомо
  не додані — чужа секція, лишено коментар для service-dev.
- `install.sh`: POSIX-лаунчер отримав ту саму зміну без "зашитого на білді"
  венv-шляху (bash-скрипт резолвить `HOME_DIR` заново щоразу) —
  `$HOME_DIR/current/.venv/bin/python`, окремий `PYTHONPATH=$CURRENT_DIR`,
  `MNEMO_HOME` лишився неверсійним.
- `service_ctl.py`: нові `versions_dir()`, `current_link()`,
  `switch_current(tag)` (Windows: junction repoint `.new` → rmdir →
  rename; POSIX: справжній atomic `os.replace` symlink), `update_lock()`
  (перевикористав `_exclusive_start()`'s locking, винесений у спільний
  `_exclusive_file_lock()`), `prune_versions(keep=, active=)`. 15 нових
  тестів у `tests/test_service_ctl.py`, ізольовані патчем `config`-констант,
  реальний рушій не чіпають. Повна суїта: **89 passed**.
- `install.ps1`: **рішення платформ-dev** — новий PowerShell-функція
  `Build-EngineVersion` (не новий скрипт, не новий прапор), бо `install.ps1`
  уже організований як dot-sourceable бібліотека функцій + `Invoke-Install`
  driver (той самий механізм, що вже використовує `test_platform.py`).
  Замінила `Sync-EngineCode`+`Install-Launcher`; параметризована цільовою
  текою — знадобиться і для першого інсталу (`versions/local/`), і пізніше
  для стейджингу нового релізу (крок 7). Плюс `Set-CurrentVersion`
  (PowerShell-native, бо має відпрацювати до існування будь-якого venv) і
  `Publish-Launchers`. `uninstall.ps1`: виправлено survey/report-рядки під
  нову розкладку — сама логіка видалення правки не потребувала (вона й так
  вимітає «усе, крім залишених» generically).
  **Перевірено на throwaway `--home`:** свіжий інстал кладе
  `versions\local\{src,.venv}` + `current` як справжню Windows-junction, без
  пласкої `src\`/`.venv\` у корені; повторний запуск ідемпотентний (одна
  тека `local`, не дві); `-Check`/`-DepsOnly`/`service start|status|stop`/
  `doctor`/`uninstall.ps1 -DryRun`/повне знесення — усе працює. Реальний
  `C:\Users\dima\.claude\mnemo` і його сервіс — не чіпались (ті самі PID до
  й після).

## Розриви, підняті, а не проігноровані — обидва закриті того ж дня

1. **`docs/Memory-contracts-v3.md` §15.4 бреше.** Написано «bootstrap
   знаходить engine home від `sys.prefix`... імпортує `src.cli:main`» — це
   вже не так. Не виправлено платформ-dev (не його файл, і рішення ще
   формально не влягло в `Memory-design-v3.md` §13) — піде через
   `docs-keeper`.
   **Закрито:** `docs-keeper` додав рішення **#33** в `Memory-design-v3.md`
   §13, переписав §15.4 під реальний контракт, доповнив §1 (новий рядок
   `mnemo_bootstrap.py`, доповнений рядок `service_ctl.py`), оновив
   "Installed engine" в `CLAUDE.md`. Заразом підчистив сусідню застарілу
   заяву про `mnemow.exe` як "phase 5 add-on" (уже реалізовано, знайшов
   через `pyproject.toml`+`git log`) — не з нашого diff, але лежало поруч.
2. **`tests/test_install_windows.py` (~400 рядків) масово впаде** на новій
   версійній розкладці (жорстко очікує `engine / ".venv" / ...`,
   `engine / "src" / ...`). Не було в обсязі кроку 4 — territory `tester`.
   **Закрито:** 4 точкові виправлення шляхів (`.venv`→`current/.venv`,
   `src`→`current/src`, і один тест, що мовчки нічого не перевіряв —
   `not (engine / ".venv").exists()` на шлях, який за новою розкладкою й
   так ніколи не існує — замінено на реальну перевірку відсутності
   `versions/`/`current`). Жодного кейса не послаблено й не викинуто — усі
   чотири правки суто переносять шлях, поведінка, яка б архітектурно
   зникла, не знайдена. **29 passed, 0 failed** (`test_install_windows.py`),
   **89 passed, 0 failed** (`test_service_ctl.py`), реальний сервіс
   (`pid 26636`) не чіпався і лишився на старій пласкій розкладці — він
   ще не переінстальований новим кодом.

## Пам'ять, виправлена цим сеансом

`topics/windows-native-support.md` — дві заяви про `sys.prefix`-резолюцію й
пласку `src`/`.venv` розкладку були фактично невірні щодо коду в робочому
дереві; виправлено на місці з поміткою «unstaged, self-update».
