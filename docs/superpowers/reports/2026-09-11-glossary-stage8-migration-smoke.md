# Stage 8: PostgreSQL migration smoke

Дата: 11.09.2026. Статус: частичная техническая проверка 8.7.

Отдельная БД: `okf_glossary_stage8_migration_20260911`. Рабочая БД не
использовалась.

- чистый `alembic upgrade head` завершён успешно;
- повторный `alembic upgrade head` не применил изменений и завершился успешно;
- `alembic_version` содержит одну head: `b2c3d4e5f6a7`;
- таблицы `chat_messages`, `domain_terms`, `domain_term_aliases`,
  `domain_term_translations` присутствуют;
- `scripts/migrate_glossary.py` в check-only режиме вернул пустые
  `missing_tables`, `missing_columns`, `missing_constraints` и
  `missing_history_column=false`.

На этом набор миграционных проверок для 8.7 закрыт; отдельная drift-БД
сохранена как диагностический стенд.

## Вторая проверка

На двух дополнительных чистых БД выполнены сценарии:

- `upgrade f1a2b3c4d5e6`, затем `upgrade head` → `b2c3d4e5f6a7`;
- `upgrade f8d9e0a1b2c3`, затем `upgrade head` → `b2c3d4e5f6a7`.

Обе исходные ветки успешно прошли merge и glossary revision.

На PostgreSQL после миграции выполнен конкурентный update одного термина пятью
клиентами с одной исходной версией: 1 успешное обновление, 4 корректных
`GlossaryVersionConflictError`, 0 прочих ошибок, финальная версия `2`, одна
audit-запись `GLOSSARY_SOURCE_UPDATE`. Данные и audit не потеряны.

## Companion при schema drift

На отдельной БД `okf_glossary_stage8_drift_20260911` после clean upgrade
намеренно удалена только тестовая колонка `domain_terms.original_description`.
Check-only вернул `missing_columns.domain_terms=["original_description"]` и
exit code `1`. Запуск с `--apply` отказался с явным RuntimeError о частично
созданной схеме и exit code `1`; автоматическое исправление не выполнялось.
