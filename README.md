# EventHub - NoSQL Database Project

[![EventHub](https://github.com/Spynch/ndbx/actions/workflows/eventhub.yml/badge.svg)](https://github.com/Spynch/ndbx/actions/workflows/eventhub.yml)

Backend-сервис платформы мероприятий для практического изучения NoSQL баз данных.

## С чего начать

1. **‼️ Настройте репозиторий** — проведите обязательную настройку контрибьюторов и защиты ветки (см. ниже)
2. **[Лабораторные работы](https://github.com/sitnikovik/ndbx/tree/main/docs/lab)** — технические задания для каждой лабораторной работы
3. **[CONTRIBUTING.md](CONTRIBUTING.md)** — требования к структуре проекта, процесс разработки и проверки
4. **[Документация курса](https://github.com/sitnikovik/ndbx)** — методические материалы и дополнительные ресурсы

> 💡 Не забудьте поменять `{your_username}` и `{your_repo}` в badge на ваши имя пользователя и название репозитория.

## Настройка репозитория

### Защита основной ветки

После создания репозитория из шаблона **обязательно настройте правила защиты для ветки `main`**:

1. Откройте **Settings** → **Branches** → **Add classic branch protection rule**
2. В поле **Branch name pattern** укажите: `main`
3. Включите следующие опции:
   - **Require a pull request before merging**
     - Require approvals: **1**: требует минимум одного одобрения перед слиянием
   - ***Require status checks to pass before merging***
     - Выберите *"autograder"*: проверит все лабораторные работы автоматически
     - ***Require branches to be up to date before merging*** (рекомендуется):
     требует, чтобы ветка PR была синхронизирована с последними изменениями из основной ветки перед слиянием
   - ***Lock branch***: запрещает прямые коммиты в основную ветку
   - ***Do not allow bypassing the above settings***: запрещает обход настроек защиты ветки
4. Нажмите **Create** или **Save changes**

> ⚠️ **Важно:** Без этих настроек автоматические проверки не будут блокировать PR с ошибками.

### Добавление коллабораторов

Чтобы преподаватели могли проводить код-ревью:

1. Откройте **Settings** → **Collaborators**
2. Нажмите **Add people**
3. Добавьте всех кто есть в списке ревьюеров в файле [CODEOWNERS](CODEOWNERS)
4. Выберите роль: **Write** (или выше), иначе ревьюер не сможет одобрить PR

## Помощь

Возникли вопросы? → [@sitnikovik](https://t.me/sitnikovik)

## Лабораторная работа №7: рекомендации мероприятий (Redis + MongoDB + Cassandra + Neo4j)

### Запуск

```bash
make run
```

Приложение будет доступно на `http://localhost:${APP_PORT}`.

### Конфигурация

Все настройки задаются в `.env.local`:

- `APP_HOST`
- `APP_PORT`
- `APP_USER_SESSION_TTL`
- `APP_LIKE_TTL`
- `APP_EVENT_REVIEWS_TTL`
- `APP_RECOMMENDATIONS_TTL`
- `APP_STARTUP_RETRY_ATTEMPTS`
- `APP_STARTUP_RETRY_DELAY_SECONDS`
- `REDIS_HOST`
- `REDIS_PORT`
- `REDIS_PASSWORD`
- `REDIS_DB`
- `MONGO_CFG_RS`
- `MONGO_CFG_1_PORT`
- `MONGO_CFG_2_PORT`
- `MONGO_CFG_3_PORT`
- `MONGO_SHARD1_RS`
- `MONGO_SHARD1_1_PORT`
- `MONGO_SHARD1_2_PORT`
- `MONGO_SHARD1_3_PORT`
- `MONGO_SHARD2_RS`
- `MONGO_SHARD2_1_PORT`
- `MONGO_SHARD2_2_PORT`
- `MONGO_SHARD2_3_PORT`
- `MONGODB_DATABASE`
- `MONGODB_USER`
- `MONGODB_PASSWORD`
- `MONGODB_HOST`
- `MONGODB_PORT`
- `CASSANDRA_HOSTS`
- `CASSANDRA_PORT`
- `CASSANDRA_USERNAME`
- `CASSANDRA_PASSWORD`
- `CASSANDRA_KEYSPACE`
- `CASSANDRA_CONSISTENCY`
- `CASSANDRA_REPLICATION_CLASS`
- `CASSANDRA_REPLICATION_FACTOR`
- `CASSANDRA_CLUSTER_NAME`
- `CASSANDRA_NUM_TOKENS`
- `CASSANDRA_MAX_HEAP_SIZE`
- `CASSANDRA_HEAP_NEWSIZE`
- `NEO4J_URL`
- `NEO4J_BOLT_PORT`
- `NEO4J_USERNAME`
- `NEO4J_PASSWORD`

### API

- `GET /health`
  - Всегда возвращает `{"status":"ok"}`
  - Не создаёт и не продлевает сессию в Redis
  - Если в запросе есть `X-Session-Id`, возвращает ту же cookie
- `POST /session`
  - Создаёт новую сессию при первом визите (`201 Created`)
  - Обновляет TTL существующей сессии (`200 OK`)
  - Сессии хранятся в Redis как Hash по ключу `sid:{session_id}`
- `POST /users`
  - Регистрирует нового пользователя
  - Сохраняет в MongoDB документ в коллекцию `users` с `password_hash` (bcrypt)
  - После успешной регистрации создаёт новую сессию и привязывает её к пользователю (`user_id` в Redis)
- `POST /auth/login`
  - Аутентифицирует пользователя по `username` и `password`
  - Привязывает текущую сессию к пользователю либо создаёт новую
- `POST /auth/logout`
  - Удаляет сессию в Redis
  - Удаляет cookie `X-Session-Id` (`Max-Age=0`)
- `POST /events`
  - Доступен только авторизованным пользователям
  - Создаёт событие в MongoDB (`events`) и возвращает `id` созданного события
- `GET /events`
  - Возвращает список событий
  - Поддерживает фильтрацию по `title` и пагинацию через `limit`/`offset`
- `GET /recommendations`
  - Доступен только авторизованным пользователям
  - Возвращает `{"events": [...]}` без поля `count`
  - Строит рекомендации по лайкам через Neo4j и кэширует список в Redis hash `user:{user_id}:recomms`
