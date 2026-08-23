# 2026-08-22 — install.ps1/install.sh: прогрес-індикатори на довгих кроках

Гілка `feat/installer-release-and-progress`, продовження `logs/2026-08-22-installer-release-based-bootstrap.md`.

`pip install -r requirements.txt` і `mnemo warmup` (~2.2 ГБ) раніше йшли мовчки — на повільному звʼязку виглядало як завислий скрипт.
Деталі механіки й код — у `topics/install-lifecycle.md`.

## Перевірено живцем

- `tests/test_install_windows.py` — **29/29**, включно з реальним `pip install -r requirements.txt` через новий heartbeat-хелпер (жодних змін тестів не знадобилось — heartbeat прозорий для існуючих асертів).
- `install.sh`'s `run_with_heartbeat` — ізольований bash-скрипт, три сценарії (успіх / падіння з captured output / bare-виклик коректно абортує під `set -e`) — усі три коректні.
- `Invoke-CheckedWithHeartbeat` (install.ps1) — ізольований `.ps1`, успіх + падіння з captured output — обидва коректні.
