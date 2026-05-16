# Onboarding

Як підняти проєкт локально за пів дня. Якщо довше — це баг онбордингу.

## Local setup
Потрібні: Python 3.12, Docker, `make`. `make bootstrap` піднімає
Postgres + Redis у docker-compose, ставить залежності, накатує міграції
й сідить демо-дані. Конфіг — з `.env.example`, реальні секрети не
потрібні для локалу.

## Common commands
`make run` — підняти всі сервіси локально; `make test` — уся пірамідa
тестів; `make lint` — ruff + mypy; `make migrate` — застосувати нові
міграції. Перед PR обов'язково `make lint test`.

## Who to ask
Архітектурні питання — дивись `architecture.md`. Деплой/інциденти —
`deployment-notes.md` і `observability.md`. Якщо щось не піднялось —
спершу `make doctor`, він перевіряє версії й порти.
