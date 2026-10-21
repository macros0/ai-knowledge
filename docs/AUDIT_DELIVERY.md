# Аудит: БД, outbox и Logstash JSON в stdout

Реализация этого пакета основана на main. Старый Kubernetes/TLS/restore проект
не требуется для сборки или запуска. Поле `stdout_written` означает полную
локальную запись; получение OpenSearch принимается отдельно.

## Установка и миграция

Новый backend требует audit schema v2 **во всех трёх режимах**.
Перед обновлением сохранить backup и остановить всех writers; старый binary
со схемой v2 несовместим. На versioned
БД выполнить штатный Alembic upgrade до `067b1c2d3e4f` (после `066b1c2d3e4f`). Migration отделяет
outbox от audit_log, переносит существующие payload/state без replay и создаёт
рабочие snapshots импорта стоп-слов и receipts миграций глоссария. Старые
строки журнала и history IDs сохраняются. Неизвестная схема/неполная история
отвергается до преобразования; существующий create_all не заменяет миграцию. Fresh development DB создаётся init_db.

Для существующей dev/create_all БД с drift использовать узкий инструмент:

```powershell
# Из backend; файл содержит явные DATABASE_URL/DATA_DIR, приложение остановлено
python scripts/migrate_audit_schema.py --runtime-env-file <private-runtime.env> --confirm-stopped
```

Инструмент не stamp-ит историю Alembic и не исправляет другие таблицы.
Неизвестная schema отвергается. Проверять на копии/отдельной БД до работы с
целевыми данными. В разработке этого пакета основная пользовательская БД не меняется.

## Режимы

| Режим | AUDIT_DB_ENABLED | AUDIT_STDOUT_MODE |
|---|---|---|
| Только БД (default) | true | disabled |
| Только внешний аудит | false | durable |
| Оба канала | true | durable |

`false + disabled` запрещено при загрузке Settings и Helm render. Выбор
`AUDIT_STDOUT_FORMAT=native|logstash` независим от получателей, default native.
Внешний аудит использует техническую очередь в БД, даже когда постоянный журнал
отключён. Полное отключение аудита не поддерживается.

Во всех режимах один каталог событий, включая вход/выход/отказы доступа.
В DB-only новые события идут только в audit_log. В external-only — только
в audit_outbox; both атомарно сохраняет обе копии с одним event_id/time.
Outbox имеет собственные id/event_id/created_at, не зависит от строки журнала.
После commit один dispatcher выводит JSON и отдельной транзакцией отмечает
stdout_written. Сбой между выводом и marker может повторить событие.

Смена режима применяется после restart только к новым событиям. Старая история
остаётся доступной security; события external-only задним числом в журнал не
переносятся. При disabled накопленные pending приостанавливаются; durable
продолжает их с исходной identity. Held не снимаются, written не переигрываются.
Объём БД в external-only продолжает расти: автоматической очистки outbox нет.
Политика retention/GC — отдельный проект, stdout_written не является внешним ACK.

Snapshot history/rollback стоп-слов и receipts идемпотентной миграции глоссария
сохраняются в отдельных рабочих таблицах во всех режимах. Это данные приложения,
не скрытые записи постоянного журнала.

Запускать через `python -m app.diagnostic_entrypoint --host 0.0.0.0 --port 8000`
с одним worker без hot reload. Для локального host выбирать принятый в окружении
порт; контейнерный backend по-прежнему 8000. Durable access/error logs идут в
stderr; audit JSON — в stdout. Некорректная schema/права/profile и второй owner
отвергаются до запуска mutating workers; startup failure закрывает ресурсы.

Helm: `runtime.auditDbEnabled: false` + `runtime.auditStdoutMode: durable`
для external-only; true + durable для both; true + disabled для DB-only.
`runtime.auditStdoutFormat: logstash` выбирает оболочку JSON.
Не задавать эти env через runtime.config: chart отклоняет двойную настройку.
Перезапуск применяет выбор формата; dynamic reload отсутствует.

## Поля и доставка

Canonical schema 1: event_id, timestamp (UTC), action, actor/target, outcome,
method/reason, request_id, client_ip/ip_source, old_value/new_value, meta.
Все текущие действия main и четыре auth-события входят в явный allowlist:
[каталог](AUDIT_EVENT_CATALOG.md). Native выводит canonical payload.

Logstash добавляет @timestamp (=timestamp), @version="1", message (=action),
logger_name="ai_knowledge.audit", thread_name="audit-outbox",
application="ai-knowledge", audit_transport_version=1. Success: INFO/20000,
denied: WARN/30000, failure: ERROR/40000. Canonical поля остаются на верхнем
уровне с исходными типами; содержимое не помещается JSON-строкой в message.
Синтетический пример: `backend/tests/fixtures/audit/logstash-v1.json`.

Вывод UTF-8, компактный JSON без BOM/NaN; внутренние переводы строк экранируются.
Canonical limit 16384 bytes, Logstash limit 18432 bytes без framing.
Физический framing — LF + JSON + LF: пустые frames пропускаются, LF перед полной
повторной строкой отделяет незавершённый хвост погибшего writer. Collector должен
собирать CRI fragments длинной строки и разбирать JSON ровно один раз.

Повтор одного payload в одном формате сохраняет bytes, ID и время. При смене
формата pending выводится в новом формате, при этом canonical payload в БД
не меняется. Downstream dedup по event_id; exactly-once и глобального порядка нет.
Нельзя считать запись в stdout подтверждением доставки индексу.

## Ошибки и атомарность

Ошибка stdout/formatter оставляет pending с stdout_failure и ограниченным retry.
Ошибка marker оставляет pending/marker_failure. Некорректный canonical payload
получает held/invalid_payload, не мешая другим событиям. Held и stdout_written
автоматически не переигрываются. Logging levels, sampling и handleError не могут
скрыть ошибку audit writer. Заблокированный stdout занимает один writer, не HTTP
request thread; stalled writer не следует лечить автоматическим restart storm.

Во всех режимах login/logout/block/unblock атомарны с выбранными получателями; обязательный audit
failure откатывает security mutation и не выдаёт новую cookie. DB-only также требует успешного commit обязательного аудита; это намеренное
усиление прежнего best-effort logout. При ошибке DB успех выхода не подтверждается.
Domain операции сохраняют свои существующие transaction boundaries main:
атомарность audit+outbox не делает всю бизнес-операцию автоматически атомарной.

Denial сохраняет 401/403/redirect и фиксируется не более одного раза на запрос.
Успешные health/auth-me polls не создают отказов. При недоступной DB denial audit
не гарантируется: fixed audit_unavailable в диагностике/счётчике памяти, доступ
остаётся закрыт. Выделенный bounded DB path: work 4s, cleanup 0.5s, ограниченная
admission. PostgreSQL durable требует поддержанный psycopg/libpq build из lock;
неизвестный service-file profile отвергается до defaults/DB I/O. Дополнительные
NFS/CSI/OS/crypto profiles не подтверждаются результатами обычного локального PG.

Во всех режимах используется серверный UUID request ID; произвольный входящий
X-Request-ID его не заменяет. Client IP остаётся null/ip_source=unknown; доверенный
proxy relay в этот пакет не входит. Существующие разрешённые actor ID/name могут
быть персональными данными. Headers, cookies, токены, OAuth state/code, тексты
документов, traceback и свободные ошибки в JSON-оболочку не добавляются.

## Status, restore и rollback

```powershell
python scripts/audit_delivery_status.py --runtime-env-file <private-runtime.env>
```

Команда показывает pending/held/stdout_written, oldest pending age и fixed error
counts без payload. Runtime_state=unknown у отдельной CLI: память backend ей
недоступна. Ошибка DB даёт nonzero exit. Счётчики процесса обнуляются при restart;
recorded/denied учитывают root commit, не rollback/savepoint rollback.

При restore: остановить writers, восстановить backup в отдельную DB/data directory,
**до первого запуска backend** выполнить:

```powershell
python scripts/audit_hold_restored_events.py --runtime-env-file <restored-runtime.env> --confirm-hold-restored-events
```

Проверить ID/time/payload и counts. Команда идемпотентна, удерживает restored pending;
сама подмена DB приложением не распознаётся. Kubernetes restore framework не нужен.

Для отката только формата вернуть native штатным restart. Для смены режима
выбрать одну из трёх допустимых комбинаций; pending сохраняются при disabled.

**Старый binary несовместим со схемой v2 даже при disabled.** Автоматический
schema downgrade запрещён. До первого запуска новой версии возврат выполняется
проверенным восстановлением backup до миграции в отдельную БД/data directory.
После новых записей предпочтительно исправление вперёд; откат backup может
потерять новые данные и требует отдельного решения/переноса. Не создавать
фиктивные AuditLog ради старого FK и не удалять queue. Restore hold tool
распознаёт строгие v1/v2; runtime требует v2 и рабочие history/receipt таблицы.

Writer DB-only: INSERT/SELECT audit_log и USAGE его sequence. External-only:
INSERT/SELECT outbox и USAGE её sequence, UPDATE только delivery columns;
INSERT audit_log не нужен. Both объединяет эти права. Для просмотра сохранённой
истории нужен SELECT audit_log. Рабочие domain-таблицы имеют отдельные права
приложения. UPDATE/DELETE audit_log и DELETE outbox для writer не нужны.
Status — SELECT, hold operator — UPDATE outbox. Не очищать pending/held либо
stdout_written без отдельно согласованного retention/replay контракта.

`GET /api/audit/config` доступен роли security, Cache-Control: no-store;
возвращает режим и format, не подтверждает доступность OpenSearch. UI показывает
режим, сохранённую локальную историю и ограничение CSV/агрегатов локальными
записями. Ошибка чтения конфигурации показывается как неизвестный режим.

## Проверка OpenSearch

Нужны фактические parser/mapping, index/access/retention, правила INFO filtering,
CRI reassembly/blank frames, лимит >=18432 bytes плюс container overhead, buffering,
retry/dedup и согласованное окно ожидания. Развернуть проверенный пакет штатным
способом в тестовом окружении. Для synthetic success/denied/failure сопоставить
request → DB → stdout → индекс по event_id; проверить типы полей, UTC, Unicode,
near-limit строку и отсутствие parser errors/secret canary. Только это закрывает
AC-10. Локальные PASS и библиотека python-json-logger этого не доказывают.

Актуальные результаты: [отчёт](superpowers/reports/2026-10-20-audit-logstash-acceptance.md).


Домашний Linux/OpenSearch proof: [отчёт](superpowers/reports/2026-10-20-audit-logstash-linux-opensearch.md).
В проверенном Fluent Bit: JSON из Docker `log`, пустые строки пропускаются,
`Logstash_Format Off` сохраняет timestamp приложения, `Suppress_Type_Name On`,
`Id_Key event_id` + `Write_Operation index` обеспечивают проверенный replay dedup.
Эти настройки необходимо сопоставить с реальным сборщиком заказчика; они не
включаются автоматически выбором `AUDIT_STDOUT_FORMAT=logstash`.

Режимы v2 и границы текущей проверки: [отчёт](superpowers/reports/2026-10-21-audit-storage-modes-acceptance.md).
