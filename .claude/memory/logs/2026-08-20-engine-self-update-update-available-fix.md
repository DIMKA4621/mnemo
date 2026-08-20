# 2026-08-20 (тринадцяте) — фікс: `update_available` протухав після switch

Продовження `logs/2026-08-20-engine-self-update-step11.md` (знахідка
ui-dev). Закомічено разом із цим логом.

## Фікс

`engine_update.py::record_installed()`: коли `status="active"` ставить
`state["current"] = tag`, `last_check.update_available` тепер
перераховується на місці за тією ж формулою, що й `record_check()`
(`bool(latest_tag) and latest_tag != tag`), не чекаючи наступного
фонового GitHub-check. `record_installed()` з не-`active` статусом (напр.
запис провалу) `last_check` не чіпає. На справжньому rollback (switch на
тег, що не є `latest_tag`) коректно лишається `true`.

## Перевірено

18/18 юніт-тестів (новий `test_update_available_clears_on_switch`).
Живий прогін того самого сценарію, що ui-dev відтворив (throwaway
`step9-home`): check → apply → одразу `GET /api/update/status` →
`update_available: false`. До фіксу те саме поле в попередньому прогоні
читалось `true` (доказ уже був, просто не було assert на ньому). 13/13
живого сценарію. Реальний рушій не чіпався.

## Підсумок: кроки 0–11 усі готові й закомічені, розрив закритий

Далі: крок 12 (наскрізна перевірка, tester), крок 13 (фінальна
синхронізація доків, docs-keeper).
