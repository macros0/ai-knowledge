# Аудит: БД, outbox и Logstash JSON в stdout

Реализация этого пакета основана на main. Старый Kubernetes/TLS/restore проект
не требуется для сборки или запуска. Поле `stdout_written` означает полную
локальную запись; получение OpenSearch принимается отдельно.

## Установка и миграция

Новый backend требует additive audit schema **даже при выключенном stdout**.
Перед обновлением сохранить backup и остановить backend writers. На versioned
БД выполнить штатный Alembic upgrade до `066b1c2d3e4f` (после текущего head
`065b1c2d3e4f`). Migration добавляет audit metadata и audit_outbox; старые rows
сохраняются, исторического replay нет. Fresh development DB создаётся init_db.

Для существующей dev/create_all БД с drift использовать узкий инструмент:

```powershell
# Из backend; файл содержит явные DATABASE_URL/DATA_DIR, приложение остановлено
python scripts/migrate_audit_schema.py --runtime-env-file <private-runtime.env> --confirm-stopped
```

Инструмент не stamp-ит историю Alembic и не исправляет другие таблицы.
Неизвестная schema отвергается. Проверять на копии/отдельной БД до работы с
целевыми данными. В разработке этого пакета основная пользовательская БД не меняется.

## Режимы

| Переменная | Значения | Default |
|---|---|---|
| AUDIT_STDOUT_MODE | disabled, durable | disabled |
| AUDIT_STDOUT_FORMAT | native, logstash | native |

Disabled сохраняет прежний DB audit: новых outbox нет и dispatcher не запускается.
Durable записывает audit + sanitized immutable payload в одной SQL транзакции.
Один dispatcher читает committed pending, выводит строку и отдельной транзакцией
ставит stdout_written. При сбое между записью и marker возможен повтор.

Запускать через `python -m app.diagnostic_entrypoint --host 0.0.0.0 --port 8000`
с одним worker без hot reload. Для локального host выбирать принятый в окружении
порт; контейнерный backend по-прежнему 8000. Durable access/error logs идут в
stderr; audit JSON — в stdout. Некорректная schema/права/profile и второй owner
отвергаются до запуска mutating workers; startup failure закрывает ресурсы.

Helm: `runtime.auditStdoutMode: durable`, `runtime.auditStdoutFormat: logstash`.
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

В durable login/logout/block/unblock атомарны с audit/outbox; обязательный audit
failure откатывает security mutation и не выдаёт новую cookie. Disabled logout
сохраняет прежнее best-effort очищение браузерной сессии при ошибке DB.
Domain операции сохраняют свои существующие transaction boundaries main:
атомарность audit+outbox не делает всю бизнес-операцию автоматически атомарной.

Denial сохраняет 401/403/redirect и фиксируется не более одного раза на запрос.
Успешные health/auth-me polls не создают отказов. При недоступной DB denial audit
не гарантируется: fixed audit_unavailable в диагностике/счётчике памяти, доступ
остаётся закрыт. Выделенный bounded DB path: work 4s, cleanup 0.5s, ограниченная
admission. PostgreSQL durable требует поддержанный psycopg/libpq build из lock;
неизвестный service-file profile отвергается до defaults/DB I/O. Дополнительные
NFS/CSI/OS/crypto profiles не подтверждаются результатами обычного локального PG.

В durable используется серверный UUID request ID; произвольный входящий
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

Для отката только формата вернуть native штатным restart. Для отката binary:
остановить writer, backup, hold pending с reason binary_rollback через standalone
operator API `hold_pending(engine, reason="binary_rollback")`, выбрать disabled,
вернуть предыдущий binary. Не удалять additive schema и не stamp-ить БД.
Возврат durable продолжает оставшиеся pending; новые события периода disabled
не backfill-ятся. Не снимать held ради освобождения места и не удалять строки
с pending/held или неподтверждённой доставкой. Новый retention/GC не добавлен.

DB права writer: SELECT/INSERT audit_log и sequence USAGE, SELECT/INSERT outbox,
UPDATE delivery columns. UPDATE/DELETE audit_log для writer не нужны. Status —
SELECT; hold operator — UPDATE outbox. Existing retention/удаление допускается
только отдельной процедурой, учитывающей outbox и факт внешней доставки.

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
