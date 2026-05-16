# Memory Index — demo project

Тонкий індекс загальної памʼяті проєкту. Деталі — у topic-файлах.

## Quick facts
- Стек: Python 3.12 backend, PostgreSQL 16, Redis для черг і кешу.
- Деплой: Docker Swarm, 3 ноди, продакшн (production cluster).
- CI/CD: GitHub Actions → build → scan → push → `docker stack deploy`.

## Topics
- [Architecture](architecture.md) — сервіси, потоки даних, межі модулів
- [Database](database.md) — схема, міграції, пулінг, бекапи
- [Caching](caching.md) — Redis ключі, TTL, інвалідація, stampede
- [Security](security.md) — JWT, секрети, rate limiting, сканування
- [Observability](observability.md) — логи, метрики, трасування, on-call
- [API contracts](api-contracts.md) — версіонування, помилки, пагінація
- [Deployment notes](deployment-notes.md) — реліз і rollback
- [Onboarding](onboarding.md) — як підняти проєкт локально
