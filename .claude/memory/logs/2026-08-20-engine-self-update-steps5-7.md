# 2026-08-20 (восьме) — самооновлення рушія: кроки 5–7 (unstaged)

Продовження `logs/2026-08-20-engine-self-update-steps0-4.md`.
Дизайн і план — `topics/engine-self-update-design.md`.
Нічого не закомічено.

## Крок 5 — `state/engine_version.json`

Новий `src/engine_update.py`: `default_state()`, `read_state()`/ `write_state()` (атомарно, tmp+replace — той самий патерн, що `service_ctl._write_identity`), `record_installed()`, `start_apply()`/ `finish_apply()`.
`read_state()` **ніколи не кидає** — відсутній файл, обрізаний чи структурно неправильний JSON усі падають назад у `default_state()` (перевірено тестами).
Нова секція в `config.py` (`--- self-update check (M) --- service-dev`): `UPDATE_CHECK_INTERVAL_S`, `GITHUB_REPO` (дефолт `DIMKA4621/mnemo`, читається з `git remote -v`), `UPDATE_CHECK_TIMEOUT_S`.

## Крок 6 — GitHub-check + фоновий таймер

`check_latest_release()` — неавторизований `GET .../releases/latest`, перевірено живцем (404 на mnemo — релізів іще нема; реальний репо з релізами; недосяжний хост `127.0.0.1:1`).
`record_check()` — м'яка відмова: помилка пише `at`/`error`, **не затирає** попередній `latest_tag`/`update_available` — перевірено живим провалом запиту.

Фоновий таймер — daemon `threading.Thread` (`while not stop.wait(interval)`), той самий патерн, що `servicelog.start_pruner()`/`watcher.py`, **не** asyncio-таск (`api.py`'s `_ping_loop` — свідомо інший вибір, аргументовано в доксстрінгу).
Одне навмисне відхилення від `start_pruner`: перша перевірка виконується **всередині** потоку, не синхронно перед поверненням `start_checker()`, бо мережевий виклик не має блокувати старт бекенда.
Вшито в `api.py`'s `lifespan`/`_shutdown` поруч із `servicelog.start_pruner()`.

## Крок 7 — пайплайн стейджингу

**Координаційне питання (виклик `Build-EngineVersion` з Python) розв'язане читанням, не вигадуванням:** докстрінг `Build-EngineVersion` уже називав саме цього викликача (крок 7, dot-source + виклик функції) — `install.ps1` міняти не довелось.

`stage_release(tag)`: tarball → `state/tmp/update-<tag>/download.tar.gz` → розпаковка з захистом від path-traversal (`_safe_members`, бо `tarfile.extractall(filter=...)` — лише 3.12+, підлога проєкту 3.10) → dot-source **розпакованого власного** `install.ps1` релізу → `Build-EngineVersion` через `powershell -NoProfile -Command` → `VERSION`-маркер (plain text) → атомарний фіналіз у `versions/<tag>/` (`os.replace`, фолбек на `shutil.move` для cross-device `MNEMO_STATE_DIR`).
Стейджинг-тека видаляється в `finally` завжди, успіх чи ні.
Прогрес — через наявний WS `Hub` (`hub.publish("update_progress", {...}, None)`), новий канал не заводився.
Windows-only (те саме свідоме обмеження, що й дизайн: POSIX не в цій ітерації, `install.sh` не має аналога `Build-EngineVersion`).

**Реальний наскрізний прогін:** оскільки `install.ps1` ще не в git (локальна робота цієї гілки), пайплайн доведено двома реальними шматками: (a) живе завантаження+розпаковка з GitHub codeload проти `master`, (b) повний пайплайн (завантаження по реальному локальному HTTP-сокету, розпаковка, dot-source реального `install.ps1`, справжній `pip install -r requirements.txt`, білд venv, генерація `mnemo.exe`/`mnemow.exe`, `VERSION`-маркер, атомарний фіналіз, порядок progress-подій, повторний-стейджинг-ідемпотентний) проти поточного робочого дерева репозиторію, запакованого так само, як GitHub архівує реліз.
Провальний шлях перевірено окремо: нічого не лишається під `versions/`, стейджинг прибирається.

## Перевірено

`tests/test_engine_update.py` — **52 passed, 0 failed**.
`tests/ test_service_ctl.py` — досі **89 passed, 0 failed** після вшивання `engine_update` у `lifespan` (реальний бекенд стартував/зупинявся з активним checker-потоком).
Реальний `~/.claude/mnemo` і його сервіс — не чіпались, усі тести редіректять `MNEMO_STATE_DIR`/`config.VERSIONS_DIR` у throwaway теки.

## Не в обсязі (свідомо, наступні кроки)

`/api/update/*` ендпоінти (крок 9), `update-apply` CLI і оркестрація stop→switch→start→health→rollback (крок 8, platform-dev) — `stage_release()` **не чіпає** `current` і не зупиняє сервіс, це саме той принцип "стара версія обслуговує запити весь час стейджингу".
