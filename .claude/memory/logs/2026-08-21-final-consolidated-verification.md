# 2026-08-21 (друге) — фінальна консолідована перевірка: усі три баги разом

Продовження `logs/2026-08-21-bugsBC-and-republish.md`. tester перевірив усі три фікси **комбіновано**, свіжим прогоном, не довіряючи попереднім звітам наосліп.

## Результат: усе чисто, нових багів немає

**Повний цикл v1→v3**, три інваріанти на кожному кроці: (a) shebang у `versions/<tag>/.venv/Scripts/{mnemo,mnemow}.exe` вказує сам на себе; (b) `bin\` SHA-256-збігається з активною версією; (c) `service.pid` пишеться одразу, `probe().managed == True`.
Усі PASS на v2 і v3.
Живий `bin\ mnemo.exe --help` → rc=0 (був rc=1, баг A), `service status` → без "started by another launcher" (баг B), реальний `search` повертає коректні хіти.

**Комбінований forced-fail:** зламано venv v4, `update-apply` з v3 → rollback на v3, сервіс здоровий, `last_apply.result: "rolled_back"`.
**`bin\`'s SHA-256 не змінився** — підтверджує, що `publish_launchers()` кличеться лише на `rc == EXIT_OK`, ніколи на rollback, точно як заявлено.

**Повна регресія, свіжий прогін:** `test_service_ctl.py` 89, `test_autostart.py` 32, `test_engine_update.py` 62, `test_platform.py` 316, `test_install_windows.py` 29, `test_install_refresh.py` 8 — **536 passed, 0 failed** сумарно (507 з попереднього звіту platform-dev + 29 власних).
`test_search.py`: recall@3/ recall@5 = 1.00.

**`install.sh` повторний рев'ю:** нічого не знайдено проти вже застосованого фіксу.

## Знайдено, не полагоджено (поза скоупом цієї задачі)

`tests/test_install_posix.py` **не був зачеплений** жодним з фіксів — досі перевіряє стару пласку розкладку (`engine / ".venv" / "bin" / "python"`, рядки 159/312).
Сам `install.sh` уже виправлений (баг C), тест — ні.
Той самий клас застарілості, що `test_install_windows.py` мав до фіксу в кроці 12. Закрито окремим невеликим фіксом одразу після цього запису.

## Реальний рушій

Підтверджено не чіпаний протягом усієї перевірки — той самий `started_at`, що на старті сесії.
