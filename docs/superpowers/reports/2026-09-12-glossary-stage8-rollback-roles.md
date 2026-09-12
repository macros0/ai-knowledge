# Stage 8: rollback and access-control smoke

Дата: 12.09.2026. Статус: технический блок 8.7 проверен; выпуск не выполнялся.

## Отключение расширения

- `test_old_plan_is_immutable_when_term_is_disabled` подтверждает: после
  отключения термина новый запрос не применяет его, а уже созданный
  `QueryPlan` не изменяется; фикстура восстанавливает изолированное состояние.
- В новом процессе `Settings(_env_file=None)` без override прочитал
  `glossary_query_expansion_enabled=False`.
- В отдельном новом процессе с
  `GLOSSARY_QUERY_EXPANSION_ENABLED=true` прочитано `True`.
- Рабочий `.env` и глобальная конфигурация не изменялись; флаг остаётся
  выключенным.

## API и права

Команда:

```text
pytest backend/tests/test_glossary_api.py backend/tests/test_glossary_expansion.py::test_old_plan_is_immutable_when_term_is_disabled -q
```

Результат: `14 passed, 3 warnings`.

Покрыты роли admin/editor/user, CRUD и audit, admin-only source changes,
валидация aliases/version conflicts, preview для editor и запрет user,
pending/static routes, cookie-authenticated CSRF, переводческие права и
ограниченный backfill.

Полный backend suite после этих проверок: `1149 passed, 7 warnings`.

## Решение

Тестовый rollback и access-control контур 8.7 подтверждён. Реальный rollout,
включение флага и smoke после рестарта намеренно не выполнялись до утверждения
subject-matter labels, качества и полного performance-критерия этапа 8.
