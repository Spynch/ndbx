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
