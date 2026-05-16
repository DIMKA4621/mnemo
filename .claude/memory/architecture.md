# Architecture

Огляд архітектури демо-проєкту. Three main services behind an API gateway.

## API Gateway
Єдина точка входу. Робить аутентифікацію (JWT), rate limiting та маршрутизацію
запитів до внутрішніх сервісів. The gateway never talks to the database
directly — it only calls the service layer.

## Core Service
Основна бізнес-логіка. Працює з PostgreSQL через шар репозиторіїв.
Транзакції тримаємо короткими; довгі операції виносимо у фонові задачі.

## Worker Service
Фоновий обробник. Reads jobs from a Redis queue and processes them
asynchronously. Concurrency is capped per node to keep memory bounded
on the Swarm cluster.

## Data flow
Клієнт → API Gateway → Core Service → PostgreSQL. Важкі задачі Core Service
кладе в Redis, а Worker Service їх підбирає й виконує поза запитом користувача.
