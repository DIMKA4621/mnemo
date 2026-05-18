# Deployment notes

Як ми деплоїмо на продакшн і що робити, коли реліз пішов не так.

## Production deploy
Продакшн — це Docker Swarm з 3 нод. Реліз робиться через
`docker stack deploy -c stack.yml app`. CI/CD pipeline (GitHub Actions)
збирає образ, пушить у registry і запускає деплой на менеджер-ноду.
Завжди перевіряй, на яке середовище деплоїш, перед запуском.

## Rollback strategy
Якщо реліз поламав продакшн — відкочуємось на попередній тег образу:
`docker service update --rollback app_core`. Swarm тримає попередню
версію сервісу, тож відкат майже миттєвий. Базу даних не чіпаємо при
rollback — міграції мають бути backward-compatible.

## Health checks
Кожен сервіс має `/healthz`. Swarm не перемикає трафік на новий
контейнер, поки health check не зелений. Це дає zero-downtime деплой.
