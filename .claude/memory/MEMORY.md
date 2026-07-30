# mnemo project memory

Bank root: `.claude/memory` (this folder), searchable through the `mnemo` MCP
server — `memory_search`. This file is an **index**: links + quick facts only;
detail lives in `topics/`, day notes in `logs/`.

## Architecture

- [Native Windows support](topics/windows-native-support.md) — PowerShell 5.1
  installer, canonical launcher contract, portable wiring, and verification.

## Quick facts

- Сторінці **не можна** дізнатися шлях до вибраної теки (`webkitdirectory` —
  лише відносні імена; `showDirectoryPicker()` шлях приховує навмисно). Тому
  вибір теки в кабінеті обходить ФС **на боці бекенда** — `GET /api/fs/dirs`.
  Нативний системний діалог відкинуто (contracts §14, п.5).
- Памʼять цього проєкту **переїхала в репозиторій** (`.claude/memory/`) з
  `~/.claude/projects/E--work-projects-other-mnemo/memory/`. Наслідок:
  нативного автозавантаження `MEMORY.md` більше немає — памʼять знаходиться
  **пошуком** через MCP, або хуком-насінням `memory-hook` (SessionStart), якщо
  його увімкнути. Стара тека лишилась **застиглим бекапом**.
- Три поверхні HTTP: `/api/*` — приватний канал кабінету (прихований з
  OpenAPI); `/mcp/<bank>` — MCP для агентів (JSON-RPC); `/mcp-tools/<tool_name>`
  — дзеркало тих самих тулів звичайним HTTP для людини, видне у Swagger
  (`/docs`), віддає байт-у-байт той самий текст.

## Logs

- [2026-07-30](logs/2026-07-30.md) — кнопка «＋ додати банк» + браузер тек;
  перейменування кнопок реіндексу; ширина колонки банків; поворот на
  MCP-first: дзеркало `/mcp-tools`, хуки-насіння, переїзд памʼяті в репо.
- [2026-07-22](logs/2026-07-22.md)
