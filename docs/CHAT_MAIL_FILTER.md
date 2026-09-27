# Фильтр писем в чате

Режим `all` учитывает все найденные источники; `exclude` допускает только доказанные источники документов; `only` — письма и всех их потомков. Неопределённое происхождение доступно только в `all`. Фильтр не меняет права просмотра документов и не распознаёт вручную скопированный текст письма.

Классификация использует только канонический `parent_source_id`, `kind=mail` либо строго булево `metadata.mail=true`. Qdrant хранит производные `mail_scope` и `mail_scope_version=1`; строгий режим проверяет оба поля до limit во всех ветках, затем повторно проверяет SQL identities и каждый компонент контекста. Авторские вопросы перепроверяют поколение в новой locked SQL-сессии; locks освобождаются до LLM. Ошибка БД не разрешает ответ по payload, а пустой strict результат не повторяется в all.

Состояние выбора живёт в ChatProvider; новый чат его сохраняет, remount сбрасывает в all. Отправка фиксирует snapshot; повтор без словаря использует исходный режим, включая после ошибки. Старые сообщения без snapshot повторяются в all. История хранит mail_mode как необязательное поле retrieval_metadata v1.

## Проверка контура и миграция

Перед запуском сверить runtime env процесса: DATABASE_URL, QDRANT_URL, QDRANT_COLLECTION, KNOWLEDGE_PROFILE. Не выводить connection strings или секреты. Основной локальный тестовый контур использует корневой `.env`; измерительный — `tests/scripts/stage8/stage8-test-profile.ps1` и соответствующий runner. Это не production-рецепт: production получает выбранный runtime env явно через launcher/Compose.

Из `backend` выбранным Python:

```powershell
python scripts/backfill_mail_scope.py --dry-run --batch-size 256
python scripts/backfill_mail_scope.py --apply --batch-size 256
python scripts/backfill_mail_scope.py --dry-run --batch-size 256
```

Без флага выполняется dry-run. `--doc-id ID` ограничивает серверный scroll и обратный SQL-аудит. Batch size 1..1000. Dry-run не создаёт collection, indexes, tables или points. Apply обновляет только два mail-поля конкретных IDs, `wait=True`, с readback и проверкой реальной keyword/integer schema; векторы, source IDs/spans, chunks, файлы и история не меняются. Никаких parser/LLM/embedding/reindex вызовов.

Перед **каждым** apply остановить и дождаться writers данного контура: upload/queue, resume/regenerate, canonical repair/reindex, publication, purge, startup/backfills. Read-only chat допустим. Если writers нельзя остановить, эта процедура не применяется: online migration требует отдельного проекта. Сначала сохранить контрольные IDs/count/payload/vectors и поколения; после apply повторить dry-run в том же окне и требовать `would_change=0`, ноль операционных ошибок. Неактивные поколения учитываются как skipped и приводятся к v1 при публикации проверенного manifest.

JSON-отчёт содержит target fingerprint, время/версию, счётчики точек и причины unknown, failed/readback batches и `missing_active_points` из обратного постраничного SQL→Qdrant аудита. Scope-категории не пересекаются; причины могут пересекаться. Unknown и отсутствующие canonical points требуют объяснения полноты корпуса, даже при exit=0. Exit=0 означает завершённый проход без операционных ошибок, 1 — partial/dependency/index/readback failure, 2 — неправильные аргументы. При сбое сохранить отчёт, устранить причину и повторить apply с начала; совпадающие точки не пишутся повторно.

## Выпуск и откат

Порядок: backend со **всеми** writers/publication/search guards → отдельная миграция и приёмка каждого контура → UI. Новый UI со старым API недопустим. Ready candidates, созданные до обновления, получают v1 перед SQL commit; patch failure сохраняет прежнее активное поколение и ready candidate для retry.

Проверить новые upload/resume/regenerate/reindex/repair и публикацию старого ready; повторить после restart. Реальные Qdrant/PostgreSQL probes, Linux CI, браузерная матрица RU/EN и snapshot/retry, а также latency budget должны пройти до выпуска. Unit mocks не доказывают schema реального сервера или PostgreSQL locks. Known mixed/undocumented legacy provenance блокирует strict rollout; не ремонтировать её скрытым reparse/regenerate.

Для отката сначала убрать UI/возможность отправки strict. Совместимый backend обслуживает уже pending strict запросы до обновления клиентов. Явные only/exclude нельзя превращать в all. Полный backend rollback требует обновления/перезагрузки клиентов или временного закрытия чата. Mail payload/indexes можно оставить: старый all их игнорирует. SQL/историю/файлы/векторы не откатывать и корпус не удалять.

Фактический локальный тестовый выпуск 2026-09-27 завершён: [основной контур](superpowers/reports/2026-09-27-chat-mail-filter-main.md), [измерительный контур](superpowers/reports/2026-09-27-chat-mail-filter-measurement.md), [проверки и ограничения](superpowers/reports/2026-09-27-chat-mail-filter-validation.md). Оба apply/readback/repeat, новые writers/restart и браузерная приёмка прошли до общего UI. Старые записи без source-привязок остались unknown; их исправление требует отдельной provenance repair. Это не подтверждение выпуска на других production-серверах.
