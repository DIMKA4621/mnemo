# 2026-08-22 — get.ps1/get.sh: дефолт — останній реліз, не master

Гілка `feat/installer-release-and-progress` (з `master`, після мержу
`feat/v3` — `c1c882e`).

Деталі й код у `topics/install-lifecycle.md`. Коротко: резолв
`releases/latest` → `refs/tags/<tag>`, фолбек на `master` якщо lookup впав.
Заразом виправлено застарілий `test_check_latest_release_real_no_releases_yet`
у `tests/test_engine_update.py` — тепер `DIMKA4621/mnemo` має реальний реліз
(v3.0.0), і той тест уже реально ловив би 404-твердження, яке більше не
правда. Перейменовано на `test_check_latest_release_real_own_repo`, асерти
інвертовано.

Новий тест `test_get_ps1_resolves_release_tag` у `tests/test_get_bootstrap.py`
— локальний фікстур-сервер для самого lookup, реальний (але швидкий, 404)
запит до codeload для підробленого тегу — підтверджує, що резолвлений тег
доходить до завантаження незміненим.

## Перевірено живцем

- `tests/test_get_bootstrap.py` — **15/15**, включно з новим тестом резолву
  релізу.
- `tests/test_engine_update.py` — **80/80** після фіксу застарілого тесту.
- Ручна перевірка всіх трьох гілок `Resolve-MnemoArchiveUrl` (недоступний
  API → fallback master; реальний живий lookup → правильно резолвив v3.0.0;
  `MNEMO_GET_REF` оверрайд).

## Небезпечний інцидент: `get.ps1` без `-InstallHome` зачепив реальний `~/.mnemo`

Ad hoc перевірка fallback-гілки (release lookup впав → мав повернутись на
`master`) була запущена **без** `-InstallHome`-ізоляції, з розрахунком, що
короткий таймаут захистить. Не захистив: `install.ps1` встиг реально
зупинити справжній сервіс, перебудувати `versions/local/` зі свіжозавантаженого
GitHub `master` і перемкнути `current` на нього — на **реальній машині
користувача**, поки self-update-стан (`state/engine_version.json`) лишався
на `v3.0.0`. Дані (`state/`, `model-cache/`, реєстр банків) не постраждали —
це гарантія install.ps1, і вона підтвердилась, — але `current` розійшовся з
тим, що self-update вважав активним. Виправлено вручну через той самий
`service_ctl.switch_current("v3.0.0")`, яким користується реальний
update-apply (stop → switch_current → start), не переінсталяцією.
Підтверджено `doctor`/`status` після фіксу: 4 банки, усі ready.

Повний розбір і урок ("жоден живий запуск без явної ізоляції") — у
`topics/install-lifecycle.md`, секція «Небезпечний інцидент».
