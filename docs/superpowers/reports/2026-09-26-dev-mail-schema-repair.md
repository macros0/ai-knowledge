# Исправление схемы пользовательской dev-базы

Пользователь разрешил миграцию запущенной собственной базы и второй
«измерительной» базы. Имя второй базы уточняется отдельно: несколько тестовых
и измерительных баз существуют на этой машине. Изменения применены только
к подтверждённой основной `okf_knowledge` на PostgreSQL17 :5432 и её
специально восстановленной проверочной копии.

## Причина ошибки

Runtime подтвердил «Основной контур», основной DATABASE_URL и данные в `data/`.
Журнал backend показывал UndefinedColumn:
`document_staging.generation_id does not exist`. Health200 проверял доступность
БД, но не совместимость полной схемы.

Read-only mail schema audit выявил:

- нет `document_staging.generation_id`;
- нет `document_generations.publication_hash`;
- `document_chunks.created_at` и `okf_attachments.created_at` nullable,
  хотя модели требуют NOT NULL; NULL rows=0.

Дополнительная сверка всей metadata выявила такое же старое расхождение
`attribute_values.sort_order`: шесть строк, NULL rows=0.
Alembic revision `f0a1b2c3d4e5` не соответствует истории create_all-managed
dev-схемы; вслепую upgrade/stamp не выполнялся.

## Backup, проверка и изменения

Подтверждённый uvicorn backend на :18000 был остановлен на время обслуживания.
Другие сервисы, PG10 :5433, Qdrant и исходные файлы документов не менялись.

Custom pg_dump размером1578220 bytes восстановлен через pg_restore
в новую `codex_mail_repair_20260926` на том же локальном PG17. Все38 таблиц
проверены по количеству строк и SHA256 содержимого каждой существовавшей
колонки. Восстановленная копия совпала с источником. Backup SHA256:
`0575c79f94c4ebcd5b712f148d7242e5c7c42ec30081b6ed2cd0dea9a0af1d63`.

Сначала на копии, затем на основной базе транзакционно выполнены адресные
изменения с lock_timeout5s и statement_timeout30s:

```sql
ALTER TABLE document_chunks ALTER COLUMN created_at SET NOT NULL;
ALTER TABLE okf_attachments ALTER COLUMN created_at SET NOT NULL;
ALTER TABLE document_generations
  ADD COLUMN IF NOT EXISTS publication_hash VARCHAR(64);
ALTER TABLE document_staging
  ADD COLUMN IF NOT EXISTS generation_id VARCHAR(32);
```

После mail repair общая metadata проверена дополнительно. Пятое изменение
также отдельно проверено на копии, затем применено с остановленными writers:

```sql
ALTER TABLE attribute_values ALTER COLUMN sort_order SET NOT NULL;
```

Существующие строки всех38 таблиц сохранили прежние SHA/counts на каждом шаге.
Новые nullable-поля добавлены без выдуманных значений. Alembic revision
не изменялся; прежние бинарные файлы и векторы не перегенерировались.

Backup сохранён в постоянном ignored каталоге
`data/backups/mail-schema-20260926/okf_knowledge-before.dump`;
проверочная копия и первоначальный dump оставлены для восстановления.
Private dump/logs/hash snapshots не включены в Git или внешние запросы.

## Финальная проверка

- `compare_metadata` всей основной БД: никаких различий.
- `scripts.migrate_mail_schema` dry-run: ready=true, missing/conflicts пусты.
- Полное ORM-чтение staging/generations, включая новые поля, без исключения.
- Backend восстановлен штатным скрытым helper; launcher PID28260, PID-файл
  штатного запуска обновлён. GET127.0.0.1:18000/health200,
  profile «Основной контур», database=ok.
- Статусы существующих документов не используются как доказательство
  автоматического восстановления неудачных задач; новый LLM regenerate
  не запускался.

Артефакты: `tests/tmp/mail-schema-repair-20260926/` — initial errors,
main-before/main-after, main-repair-report, full-schema-repair-report,
final-verification.json и одноразовые команды. Первичный full-schema-after
отчёт с одним расхождением сохраняет состояние после первого этапа,
final-verification подтверждает успешный второй этап.

Для `mail_http_acceptance` на :25432 почтовая схема уже ready=true,
revision010b1c2d3e4f, изменений не требовалось. Полная metadata также видит
три дополнительных CHECK constraints glossary_infotype_rules из тестовой
схемы; они не являются причиной отсутствующих почтовых колонок и не удалялись.
Не подтверждённые пользователем другие измерительные базы не изменялись.

Браузерная проверка существующей вкладки была отклонена политикой browser URL
protocol. Обход не выполнялся; результат доказан восстановлением backup,
сравнением схемы/данных, ORM и HTTP health. Отсутствие браузерных ошибок после
миграции этим отчётом не заявляется.
