# 2026-08-20 (дев'яте) — самооновлення рушія: крок 8, `update-apply` (unstaged)

Продовження `logs/2026-08-20-engine-self-update-steps5-7.md`.
Нічого не закомічено.

## Підтверджений ризик: застиглий `sys.executable` після switch

`update-apply` виконується у процесі, який диспетчер запустив **до** `switch_current()` — той самий Python-процес старої версії, `sys.executable` зафіксований на старті.
**Підтверджено реальним прогоном:** `service_ctl.start()` без явного target бере саме цей застиглий шлях — `service.json`'s `"python"` показував `versions/v-old/...` навіть після того, як `current` уже вказував на `v-new`.
Мовчазна поломка, точно як і підозрювалось.

**Виправлення — на місці виклику, не зміна глобального `_default_target()`:** новий `service_ctl.target_for_version(version_dir)` — чиста функція, що рахує serve-argv для **явної** теки версії, незалежно від `sys.executable` будь-якого процесу.
`update-apply` користується нею і для switch, і для rollback.
Свідомо **не** self-respawn через `bin\mnemo.exe` (розбило б один атомарний крок на два процеси з новим failure mode: батько виходить раніше, ніж дитина візьме на себе health-wait) і **не** зміна дефолту в `_default_target()` (усі інші виклики `start()` — shell, Task Scheduler, інсталятор — вже коректно вирівняні, бо щойно пройшли через `current`; зміна дефолту була б no-op для них і реальним ризиком для `test_service_ctl.py`, який активно користується `windowless_python()` на dev-checkout без `versions/`/`current` взагалі).
Попередження задокументоване в докстрінгу `windowless_python()`.

## Другий баг, знайдений під час побудови forced-rollback тесту

`service_ctl.start()` не ловив `OSError` від `spawn_detached()` — коли цільового інтерпретатора реально нема (видалена/битий venv, точна форма "поганого" self-update білда), `subprocess.Popen` кидає `FileNotFoundError` неперехопленим.
Перший прогін rollback-тесту зламав `update-apply` посеред оркестрації: switch на биту версію вже стався, виняток вилетів до будь-якого rollback-коду, `current` лишився на битій версії, сервіс down, `last_apply` завис з `finished_at: null`.
Виправлено в `service_ctl.start()` (ловить `OSError` навколо `spawn_detached`, повертає `EXIT_DOWN`) — закриває єдину прогалину в контракті "невдача старту — це код повернення, ніколи виняток", який `start()` і так гарантує для інших режимів відмови.

## Перевірено живцем

- **Успіх:** реальний `mnemo update-apply` через реальний диспетчер, сервіс під `v-old`, застейджений `v-new` → `current -> v-new`, `doctor` підтверджує сервінг під venv нової версії, `engine_version.json`: `v-old` → `previous`, `v-new` → `active`, `last_apply.result = "applied"`.
  Exit 0.
- **Форс-rollback:** python.exe/pythonw.exe у `versions/v-new/.venv` видалені до застосування → `update-apply` ловить провал старту (rc=3 завдяки `OSError`-фіксу), відкочує на `v-old`, рестартує, підтверджує здоровим — `last_apply.result = "rolled_back"`.
  Exit 1. **Сервіс лишився живим** — це й була мета вправи.
- Реальний рушій і сервіс (той самий PID до/після) — не чіпались; усе на throwaway `.claude/scratch/step8-home`, ізольовані порти `188xx`.
- Повна суїта після обох фіксів: **89 passed, 0 failed**, регресій немає.

## Exit codes `update-apply`

`0` = застосовано, здоровий · `1` = apply впав, rollback вдався (сервіс здоровий на старому тегу) · `2` = нічого не застейджено/готово · `3` = і apply, і rollback впали, сервіс down.

## Інференс, не рішення — прийнято координатором для кроку 9

Ні `engine_update.py`, ні дизайн не називають явного поля "застейджено й готово до застосування".
Платформ-dev вивів: тег готовий, коли `last_check.update_available == true` (тег — `last_check.latest_tag`) **і** `versions/<тег з latest_tag>/VERSION` існує й збігається.
Маркер-перевірка робить подвійну роботу — і sanity-check, і "чи справді застейджено" gate.
**Прийнято як контракт** для кроку 9 (API-хендлер `/api/update/apply` має читати готовність саме так, не вигадувати окреме поле).

## Не зроблено, свідомо не розширено

Симетрична гілка "rollback теж не підняв health" перевірена інспекцією коду, не живим прогоном — конструювання рігу, де `v-old` ламається саме між stop і rollback-рестартом без поламки базової лінії тесту, визнано зайвою машинерією; код структурно ідентичний вже доведеній rollback-гілці, просто повертає `EXIT_DOWN` замість повторної спроби.

## Файли

`src/cli.py` (`_cmd_update_apply`, прихована підкоманда), `src/service_ctl.py` (`target_for_version`, `current_tag`, фікс `OSError`, докстрінг-попередження), `mnemo_bootstrap.py` (`update-apply` у `_BACKGROUND_ONLY`).
