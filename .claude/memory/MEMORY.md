# Memory Index — demo project

Тонкий індекс загальної памʼяті проєкту. Деталі — у topic-файлах.

## Quick facts
- Стек: Python 3.12 backend, PostgreSQL 16, Redis для черг.
- Деплой: Docker Swarm, 3 ноди, продакшн (production cluster).
- CI/CD: GitHub Actions → build → push → `docker stack deploy`.

## Topics
- [Architecture](architecture.md) — сервіси, потоки даних, межі модулів
- [Deployment notes](deployment-notes.md) — як ми релізимо й робимо rollback
