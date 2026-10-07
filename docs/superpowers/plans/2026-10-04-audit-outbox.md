# Audit Outbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax. Исполнение inline; делегирование, commit, merge и публикация только по отдельному указанию пользователя.

**Goal:** сохранить журнал БД и обеспечить повторяемую локальную доставку безопасного audit JSON в stdout после commit.

**Architecture:** audit row и immutable outbox payload создаются одной транзакцией. Один dispatcher компактного backend читает только committed pending, пишет одну JSON строку и flush, затем отдельной транзакцией отмечает stdout_written. Crash между flush и marker повторяет тот же event_id. Trusted-IP relay и downstream ACK — отдельные пакеты.

**Tech Stack:** существующие FastAPI/SQLAlchemy 2/Alembic/PostgreSQL 17/SQLite, Python thread/Event, существующие Helm/lifecycle и DB audit API. Новые брокер, Redis и OpenSearch SDK не вводятся.

**Spec:** [Audit delivery design](../specs/2026-10-04-audit-delivery-design.md).

**Историческая приёмка ядра 04–05.10.2026 (последующий DNS gate — FAIL, см. Task 3a):** ядро durable outbox реализовано в рабочем дереве `codex/kubernetes-rollout`, без commit/merge. Проверены атомарность, повтор, HTTP auth/CSRF, PostgreSQL и восстановление непустого dump в отдельную DB. Новый Alembic head — `b6c7d8e9f0a1`, parent `a5b6c7d8e9f0`; общий head/model drift suite прошёл. Проверенный образ ядра `audit-v5` (до исправления deadline): 270 packaged Python files (180 app) совпали с source; прежний полный backend 3370 PASS / 36 SKIP / 0 FAIL, coverage 88% при gate 85%; текущий targeted PostgreSQL fault набор 31 PASS / 0 SKIP (9 прежних + 22 новых cases), без изменения backend source. Домашние confidential/basic/PKCE none и public/none/S256/UserInfo-only HTTPS backup/restore прошли: 67 и 36 immutable rows соответственно, fresh PVC/Secrets/key, old-cookie/flow refusal, новый DB/stdout event; source/target copies и off-VM completed backups сохранены. Из-за RAM предыдущая собственная confidential restored копия остановлена после нового backup; public restored копия работает. Next correlation defect исправлен, frontend 375 PASS/1 SKIP и build/lint PASS. Непроверенные окруженческие условия перечислены ниже. [Отчёт](../reports/2026-10-04-audit-outbox-acceptance.md), [runbook](../../AUDIT_DELIVERY.md). Копия документов в main не означает интеграцию runtime.

## Global Constraints

- `AUDIT_STDOUT_MODE=disabled|durable`, default disabled сохраняет старые local/Compose способы запуска. Kubernetes durable включается явно; это выбор проекта, не внешнее универсальное требование.
- При durable security mutations/login требуют записи audit+outbox до commit. Ошибка БД не выдаёт новую auth cookie и не подтверждает block/unblock. Отказ доступа остаётся отказом даже при недоступном audit store.
- Event schema 1, UUID event_id, одна UTF-8 JSON строка не больше **16 KiB**. Actor/time/action/target/reason/UUID не обрезаются для уменьшения строки. Oversize обязательного поля — безопасный отказ до mutation; list summary имеет count и truncated.
- В новом event/outbox запрещены token/cookie/code/state/nonce/verifier/password, headers, DB URL, signed URL, document/prompt/retrieval text и произвольная exception string. Per-action allowlist применяется до сохранения payload. Существующие domain old/new/meta в DB и ответы audit UI сохраняют контракт; formatter не переписывает их и не переносит неизвестные поля в stdout. Новые auth поля проверяются до записи в DB.
- Старые audit IDs/action names/API filters и чтение исторических строк сохраняются. Новая nullable schema не превращает старые строки в новые события и не запускает historical replay.
- Batches **100**, одна попытка записи в работе; retry **1, 2, 4…60 s**, далее 60 s без удаления pending. Нет DB transaction во время stdout I/O и неограниченного payload buffer.
- `stdout_written` означает только локальные write+flush. Exactly-once и ACK внешнего индекса не обещаются. Дубликат имеет прежние event_id/time/payload.
- Compact/Recreate/replicas=1/workers=1 сохраняются. Durable при более чем одном backend worker отказывается до startup; distributed claiming/leases — будущий этап реплик.
- Plain forwarded headers не принимаются. В этом пакете client_ip=null/ip_source=unknown; legacy socket peer остаётся отдельно в DB и не выдаётся за IP браузера. Direct/relay profile подключает отдельный adapter plan; proxy header stripping сохраняется.
- Pending/held и stdout_written без downstream подтверждения не удаляются автоматически. Новая retention/replay процедура — отдельный операторский пакет. Исторические события восстановленного backup не отправляются автоматически.
- Все примеры обезличены. Runtime env и дампы приватны; тесты работают только с disposable corpus. CI и внешняя инфраструктура не становятся зелёными по unit tests.
- `disabled` выключает и enqueue, и dispatcher; новые DB audit rows сохраняются без outbox. Старые pending не меняют статус. Возврат к durable возобновляет их доставку; события периода disabled не backfill-ятся. Это явный перерыв гарантии stdout, а не обход fail-closed политики. Изменение режима требует restart, rollback/restore используют hold отдельно.
- При недоступной DB нельзя гарантировать сохранение denial: доступ остаётся закрытым, потеря audit фиксируется доступной локальной диагностикой без обещания durable записи. Гарантия повтора начинается только с committed outbox row.

## Review Focus

1. Rollback/savepoint/failed session commit не оставляет audit/outbox success и cookie — Tasks 1/3.
2. Crash после flush до marker сохраняет ID/time; broken pipe/зависший stdout не блокирует API и не плодит writers — Task 2.
3. Restore/old binary rollback не воспроизводит исторический login success — Task 5.
4. Audit DB unavailable при protected denial не даёт 200 и не пишет фиктивный committed event — Task 3.
5. Dynamic legacy meta, Unicode/oversize и поддельные headers не раскрывают данные и не подменяют identity/IP — Tasks 0/1/3/4.

Для текущего остатка Task 3a: implicit file inputs, startup до readiness, замена Secret во время запроса, cancellation при чтении и постороннее исключение в fault fixture имеют отдельные критерии в 3a.5. Успешный connect-only probe не закрывает HTTP/lifecycle gate.

## Карта файлов и интерфейсов

| Область | Файлы / интерфейс |
|---|---|
| Schema/catalog | Новые `backend/app/services/audit_event.py`, `audit_catalog.py`; frozen `AuditContext`, `build_event(entry: AuditLog, context: AuditContext) -> dict`, `encode_event(event: dict) -> bytes` |
| Persistence | `backend/app/services/audit.py`, `backend/app/db/models.py`; расширение `record_in_session(..., event_context: AuditContext | None = None) -> dict`, прежние append/record/query |
| Migration | Новый `backend/alembic/versions/b6c7d8e9f0a1_audit_outbox.py`, down_revision `a5b6c7d8e9f0`; nullable audit fields + отдельная outbox table |
| Dispatcher | Новый `backend/app/services/audit_outbox.py`; `AuditDispatcher.start()`, `stop(timeout_seconds=5)`, `status() -> dict`; injectable `write_line(bytes) -> None` |
| Security | `backend/app/auth/{api.py,service.py}`, `backend/app/services/blocklist.py`, `backend/app/api/users.py`, общий API denial handler |
| Runtime boundaries | `backend/app/main.py` (включая `CsrfMiddleware`), `backend/app/diagnostic_entrypoint.py`, `backend/app/services/diagnostics/middleware.py`; auth providers logout/simulation, общий серверный request ID |
| Operations | `backend/scripts/audit_delivery_status.py`, `audit_hold_restored_events.py`, новый `migrate_audit_schema.py`, `scripts/kubernetes/lifecycle.py`, `scripts/production/restore-bundled.sh` |
| Config/docs | `backend/app/config.py`, Helm schema/env examples, CI, `SECURITY.md`, новый `docs/AUDIT_DELIVERY.md` |

`AuditContext` — frozen dataclass: actor_type=user/system/anonymous/unknown,
outcome=success/denied/failure, optional method/reason/request_id; в этом пакете
client_ip=None и ip_source=unknown. Actor ID/name берутся из verified caller в
существующих audit user_id/username, method/reason сверяются с catalog действия.
`build_event` нормализует DB created_at в UTC ISO-8601, берёт ровно один event_id
из записи и возвращает schema/actor/target/old/new/meta по allowlist. UUID/time
не пересоздаются dispatcher. Error context не содержит Request/exception object.

### Task 0: Зафиксировать inventory и безопасный catalog

**Files:** новый `docs/AUDIT_EVENT_CATALOG.md`, `audit_catalog.py`; tests `backend/tests/test_audit_event.py`.
**Interfaces:** для каждого текущего ACTION_TYPES указаны разрешённые old/new/meta keys, actor/target/outcome и transaction owner. Callers с отдельной audit transaction помечены legacy-after-action; их атомарность с FS/Qdrant не обещается.

- [x] Read-only inventory всех `record`, `append`, `record_in_session` и dynamic wrappers: action, caller, meta keys, real commit boundary. Инвентарь не содержит values корпуса.
- [x] RED `test_catalog_covers_all_existing_actions`: все ACTION_TYPES имеют явный allowlist; неизвестное action не публикуется. RED `test_catalog_drops_unknown_and_sensitive_fields`: canary secret/text/header отсутствует в encoded payload.
- [x] Реализовать catalog без permissive fallback. Auth actions: `auth_login_success`, `auth_login_denied`, `auth_access_denied`, `auth_logout`; existing domain names сохраняются.
- [x] Для каждого разрешённого поля определить тип, предел размера и источник. Разрешённый ключ не разрешает произвольный текст: reason блокировки, имена файлов, glossary values, exception message не копируются в event только по имени ключа. Тесты включают canary в разрешённом поле и вложенном массиве. Классификация actor явная: `SystemUser` → system, `public_user` → anonymous, verified User → user, неизвестный wrapper → unknown; username не определяет доверие. `simulation` маркируется отдельно от `oidc`, `disabled` не создаёт фиктивного login.
- [x] Run `python -m pytest tests/test_audit_event.py -q`; Expected: PASS. Не менять произвольные старые domain callers этим шагом.

### Task 1: Atomic audit/outbox schema и legacy compatibility

**Files:** `audit_event.py`, `audit.py`, models, migration; tests `test_audit_outbox_store.py`, прежний `test_audit.py`.
**Interfaces:** audit nullable fields event_id/schema_version/actor_type/method/reason/outcome/request_id/ip_source; outbox audit_id unique, immutable payload, table `audit_outbox`, status pending/held/stdout_written, attempt_count/next_attempt_at/last_error_code/stdout_written_at. AuditContext передаётся проверенным caller, raw Request/headers туда не входят.

- [ ] RED `test_commit_creates_audit_and_outbox_same_event`, `test_outer_and_savepoint_rollback_publish_nothing`, `test_legacy_reader_preserves_ids_and_filters`, `test_unicode_event_limit_and_summary` (16 KiB, count/truncated).
- [ ] Run `python -m pytest tests/test_audit_outbox_store.py tests/test_audit.py -q`; Expected RED: новые table/API отсутствуют.
- [x] Добавить additive migration. Существующие строки: новые поля nullable, outbox rows не создаются автоматически. Новый event получает UUID/time один раз; return API прежних callers сохраняется.
- [x] Nullable event_id уникален для новых записей. Payload хранится как уже encoded UTF-8 строка: dispatcher не пересериализует JSON/JSONB. Ограничения status/attempt_count и индекс `(status, next_attempt_at, audit_id)` входят в migration; audit_id уникален, удаление audit row не каскадирует outbox. Не вводить FK на пользователя. Проверить idempotency metadata introspection и несовпадающий migration head до изменений.
- [x] `record_in_session` только flush, без commit/stdout. `append` делает собственную transaction; outbox insert находится в ней. Ошибка serialization/outbox insert откатывает обе записи.
- [x] Существующий `init_db/create_all` не добавляет колонки в непустую локальную DB. Новый `migrate_audit_schema.py --runtime-env-file <path> --confirm-stopped` применяет только проверенный additive audit delta для локальной DB с drift от create_all, без общего stamp/Alembic upgrade и без изменения чужих таблиц. Неизвестная промежуточная схема — отказ. Fresh DB создаётся штатно; versioned Compose/Kubernetes проходят обычный Alembic. Документировать backup, остановку writer и точную команду обновления Windows/macOS/Linux до перезапуска.
- [x] Общая introspection для audit delta и Alembic различает ровно прежнюю/полную новую схему; повторное применение к полностью совместимой схеме — noop, частичный drift — отказ. Проверить совпадение типов/constraints/indexes, а не только наличие table. Это не исправляет остальные отставшие migrations локальной DB и не разрешает общий stamp. `record` и `append` также принимают optional event_context и передают его без потери в `record_in_session`; отсутствие context не присваивает доверенную identity.
- [ ] RED `test_nonempty_legacy_sqlite_and_postgres_upgrade_preserves_audit_ui`, `test_disabled_durable_disabled_transition`, `test_null_legacy_event_ids_and_unique_new_ids`, `test_payload_bytes_survive_database_roundtrip`. Использовать SQLite на файле с разными соединениями/потоками, а не общий in-memory connection для доказательства commit visibility.
- [ ] Повторить tests на SQLite и disposable PostgreSQL 17; Expected PASS/0 skips, migration old→new и обратно только на fixture. Настоящий head/model drift проверить существующим suite.

### Task 2: Bounded dispatcher и crash recovery

**Files:** `audit_outbox.py`, Settings/main; tests `test_audit_outbox_dispatcher.py`.
**Interfaces:** thread читает eligible pending партиями 100, закрывает read transaction до I/O; marker/update выполняет отдельно. Clock/write_line/session factory injectables только для тестов. start идемпотентен, stop не теряет pending.

- [ ] RED `test_only_committed_pending_is_written`, `test_flush_then_marker_preserves_exact_payload`, `test_crash_after_flush_retries_same_event_id`, `test_broken_pipe_uses_bounded_backoff`, `test_blocked_writer_does_not_block_api_or_spawn_second_writer`.
- [ ] Run `python -m pytest tests/test_audit_outbox_dispatcher.py -q`; Expected RED по отсутствующему dispatcher.
- [x] Реализовать один writer thread, одну in-flight строку и retry 1/2/4…60 s. Исключение stdout/marker сохраняет pending и безопасный fixed error code. Не логировать payload или exception text.
- [x] Не держать DB transaction во время write/flush. Зависший writer не создаёт replacement thread; API продолжает работать, health показывает stalled. Stop ждёт максимум 5 s; повторный start при живом writer запрещён.
- [ ] Рабочий sink пишет полную UTF-8 строку с newline и учитывает short write. В durable mode application/Uvicorn access/error logs направлены в stderr; audit sink не проходит через logging formatter/diagnostic capture. После возможного обрыва начальный newline отделяет неполный хвост, затем повторяется полный исходный payload. Blank/partial frame не считается event. RED `test_partial_write_restart_recovers_complete_json`, `test_concurrent_access_logs_do_not_interleave_audit`, CR/LF/Unicode в значениях экранируются; проверить реальные stdout bytes, не только mock writer. CRI reassembly остаётся внешним gate.
- [x] Eligible pending выбираются по `(next_attempt_at, audit_id)`; глобальный commit order не обещается. Persist attempt_count до попытки; next_attempt_at — UTC, ожидание loop — interruptible monotonic timer. Ошибки SELECT/attempt update/marker получают bounded backoff, не hot loop. Повреждённый payload/неизвестная schema переводится в held с фиксированным error code, без пересборки или удаления, и не блокирует остальные строки. RED `test_database_outage_recovers_without_hot_loop`, `test_invalid_payload_is_held_without_starving_valid_rows`.
- [x] Writer удерживает отдельный OS file lock `DATA_DIR/audit/.writer.lock` до фактического завершения потока/процесса (паттерн `diagnostics/store.py`, без зависимости от включения diagnostics). Второй процесс с тем же DATA_DIR отказывается; lock не удаляется ради обхода живого владельца. SIGKILL освобождает OS lock. Это защита compact shared DATA_DIR, не distributed lease для нескольких PVC/узлов. RED `test_second_process_refused`, `test_shutdown_timeout_retains_lock`, `test_sigkill_allows_restart`; заблокированный daemon writer не удерживает завершение процесса через buffered-stdio finalizer.
- [x] Проверить restart до write, после write до marker и marker commit failure; Expected PASS на обеих БД. Graceful shutdown не заявляет успешную доставку после timeout.

### Task 3: Security mutations и auth denials

**Files:** auth service/API, blocklist/users, API error boundary; tests `test_platform_audit_contract.py`, существующие auth/authz/user suites.
**Interfaces:** `Blocklist.block_in_session(session, ...) -> dict`, `unblock_in_session(session, external_id) -> int`; старые wrappers используют их. Identity из verified server-side User/ID token; метод oidc, safe reason enum из catalog.

- [ ] RED `test_login_audit_and_session_commit_together`, `test_login_audit_failure_sets_no_cookie`, `test_logout_audit_and_revoke_commit_together`, `test_block_and_unblock_audit_failure_roll_back_mutation`.
- [ ] RED `test_one_denial_event_per_request`, `test_denial_audit_failure_remains_denied`, `test_unknown_session_does_not_claim_expiry`, `test_poll_and_probe_paths_do_not_create_auth_failures`.
- [ ] Run `python -m pytest tests/test_platform_audit_contract.py -q`; Expected RED по отсутствующим auth events/atomic blocklist.
- [x] В durable mode security mutation + audit/outbox объединить в одну DB transaction. Удаление прежней session при login rotation тоже откатывается при audit failure; logout cookie сохраняется при failed revoke commit.
- [x] Для login denial/userinfo no-role не доверять browser username/error query. Protected denial записывается ровно один раз: auth middleware/API handler имеют общий request.state marker. Исключить health, OPTIONS, `/api/auth/me` polling и успешное чтение audit UI.
- [x] Audit failure на denial сохраняет исходный 401/403 и отдельный bounded `audit_unavailable` signal без утверждения committed event. Внешний RP logout failure не отменяет committed local revoke.
- [x] Общий `emit_auth_denial_once(request, reason, ...)` вызывается также из прямого ответа `CsrfMiddleware` и OIDC redirect error paths: исключение не обязательно попадает в API handler. Сохраняются status/redirect, `WWW-Authenticate`, CSRF и cookie semantics. Исключение audit UI касается успешного чтения; попытка запрещённого доступа к нему журналируется. Причина определяется кодом отказа, не HTTP status или browser error string. Повторный logout отсутствующей/удалённой session не создаёт ложный успешный revoke.
- [ ] Audit persistence в async routes/middleware выполняется через threadpool с ограниченными connection/pool/statement/lock timeout, целевой суммарный бюджет одной записи denial ≤5 s. Это отдельный audit timeout contract; не менять глобальные timeouts pipeline. Отмена await не останавливает работающий SQL: DB timeout/rollback обязательны, detached retries не создаются. Emergency signal использует существующий nonblocking diagnostics recorder и фиксированный счётчик при его недоступности, без синхронного stdout/stderr/DB I/O из request path и без повторного audit самого audit failure. RED `test_csrf_and_redirect_denial_once`, `test_audit_ui_denial_recorded`, `test_denial_db_timeout_is_bounded`, `test_denial_with_blocked_stdout_returns`, `test_repeated_logout_has_no_phantom_revoke`.
- [ ] Serialization/validation/store failures в auth mutation переводятся в безопасный dependency failure так же, как SQLAlchemyError: без cookie или частичного commit и без raw exception text в API/log. Проверить rollback прежней session, DB-full, lock timeout и блокировку/разблокировку одновременно из двух запросов; audit содержит фактическое число изменённых rows. Ошибка audit после уже совершённого legacy domain action не маскируется как откат самой операции.
- [x] В durable mode общий `request.state.request_id` генерируется сервером в `DiagnosticContextMiddleware`, даже если diagnostics disabled. Входящий X-Request-ID не становится audit ID; audit, diagnostics и response используют один новый UUID. Disabled mode сохраняет прежнюю корреляцию. RED на forged/duplicate header, совпадение response/event/diagnostics и отсутствие чтения upload/SSE body. Отдельный audit request ID поверх уже существующего не создавать.
- [x] Повторить real HTTP OIDC fixture, auth/authz/user suites; Expected PASS, JWT/state/CSRF/cookie protections не ослаблены.
- [x] Окруженческие faults на отдельном PostgreSQL 17: statement timeout 57014 + concurrent liveness response, refused/handshake-stalled connect с HTTP 401 и бюджетом <5 s; rollback и recovery DB/stdout подтверждены.
- [x] Реальный outbox tablespace disk-full 53100 на guarded 4 MiB tmpfs: пять security mutations сохраняют session/cookie/block rows при HTTP 503; protected denial остаётся 401; recovery создаёт правильное новое событие.
- [x] Реальные lock/statement timeout при fresh/rotated login, logout, block и unblock: 10 cases, SQLSTATE55P03/57014, безопасный 503, прежние session/cookie/block rows, recovery и exact new event. Timeouts заданы в test PG session options; global runtime deadline этим не вводится.
- [x] Mixed block/unblock: block-first/unblock-first/overlap, actual row count совпадает с committed audit и итоговым block state.
- [x] Полное PGDATA/WAL exhaustion проверено 05.10.2026 на owned tmpfs 128/64 MiB: 36 HTTP probes, cold-start refusal с повторной проверкой ENOSPC после stop, три отказа реального FastAPI lifespan, 24 CLI invocations включая nonempty hold, сохранность committed facts и exact stdout после recovery. Runner `scripts/kubernetes/tests/audit_cluster_fault_acceptance.py`; 9 ownership/mount guards. Preflight/schema status на живом full PGDATA могут работать: это не проверка свободного места.
- [x] Воспроизвести реальные DNS/multihost faults: 8 cases, **5 PASS / 3 FAIL** 05.10.2026; silent DNS, silent-name → healthy-IP и three-stall hosts превысили 5 s. Liveness/fail-closed/rollback/recovery подтверждены; исторический audit-v5 deadline gate остаётся FAIL; новый результат ниже в Task 3a.
- [x] Исправить общий DNS/connect/SQL deadline для выбранного Linux профиля по Task 3a и повторить неизменный <5 s gate: 20 PASS/0 SKIP. Полный составной Task 3a остаётся открыт.
- [ ] Production load matrix и реальные StorageClass faults: single-endpoint, tmpfs и DNS fixture proof их не подтверждают.


### Task 3a: общий deadline PostgreSQL denial — предлагаемый bounded repair

**Статус реализации 05.10.2026:** транспорт реализован inline в рабочей ветке. Исходный audit-v5 **5 PASS / 3 FAIL** сохранён. Новый Linux image audit-v6 проходит **20 PASS / 0 SKIP** реальных DNS/multihost/TLS/COMMIT cases при неизменном HTTP **<5 s**; отдельные PG **31 PASS / 0 SKIP** и whole PGDATA/WAL recovery также повторены. Последующее продолжение подготовило audit-v7: Linux network/lifecycle **24 PASS/0 SKIP**, native Windows PG **35 PASS/0 SKIP**, focused Windows **35 PASS**, steady burst/cancellation и реальный Linux SIGTERM пройдены. Full-v7: 3408 PASS/83 SKIP/0 FAIL, coverage 88% при прежнем gate 85%; новый home upgrade/HTTPS/correlation proof PASS, 272/272 running hashes и 102 immutable rows сохранены. Новый nonempty restore audit-v7 подтверждён: 136 immutable rows, четыре fresh PVC/новые ключи, pending→held/no replay, old-cookie/flow refusal, полный HTTPS smoke и два off-VM archives. Hosted CI остаётся отдельным gate. Дополнительные auth/file profiles, production load/CSI и hosted CI остаются открыты; свежий file-profile gate13 PASS/3 FAIL описан в3a.5; Task 3a целиком не объявляется закрытым.

**Область:** только durable PostgreSQL denial. Security mutations сохраняют существующую общую транзакцию; schema/catalog/outbox payload, dispatcher и глобальные pipeline timeouts не меняются. `disabled`, SQLite, локальный запуск и Compose требуют регрессии. Исполнение inline, без автоматического commit/deploy.

**Files / обязанности:**

| Файлы | Изменение |
|---|---|
| `backend/app/services/audit_security.py` | Один attempt на request; deadline и admission до передачи работы в поток; сохранение HTTP/identity/failure-counter контракта |
| `backend/app/services/audit_denial_transport.py` | Эффективные параметры подключения, owned DB connection, bounded connect/SQL/cleanup; без изменения глобального engine |
| `backend/app/services/audit_dns_probe.py` | Изолированное системное разрешение имён и ограниченный IPC; без импорта settings/БД и без SQL |
| `backend/app/services/audit_runtime.py`, dependency constraints/backend image recipe | Scoped preflight совместимости; воспроизводимая проверенная версия драйвера и упаковка helper |
| `backend/tests/test_audit_denial_transport.py`, существующие audit/PG/network/configuration tests | Budget/admission, параметры/ошибки, lifecycle, реальные faults и обратная совместимость |
| `scripts/kubernetes/tests/audit_network_fault_acceptance.py`, его guard tests, `.github/workflows/ci.yml` | Расширенная обязательная matrix, evidence и очистка только собственных ресурсов |
| `docs/AUDIT_DELIVERY.md`, `SECURITY.md`, этот план/spec/отчёт | Поддерживаемые профили, ограничения, результаты и процедура отката |

Новые файлы реализованы; публичные denial API по-прежнему возвращают None. Абсолютный monotonic deadline передаётся в owned worker. Внутренний bool обозначает только завершённую очистку для освобождения admission slot, не подтверждение записи. Подтверждённый commit, отсутствие записи и неизвестный COMMIT различаются проверкой фактов БД и root-commit hooks; counters вручную не увеличиваются.

**Подтверждённые параметры и остаток работ:**

- Admission: максимум 4 attempts/helpers на процесс, без очереди при занятых слотах; отдельный AnyIO limiter. Deadline создаётся до offload. Pending cancellation не запускает SQL; начавший работу worker сохраняет слот до cleanup. При неприбранном helper слот закрывается, а не выдаётся следующему writer.
- DNS: до 8 hosts и 8 уникальных IP на host, stdin до 4096 bytes, stdout protocol до 32 KiB. Один helper, ограниченные resolver threads, allowlist env без application/PG secrets, isolated Python, Windows CREATE_NO_WINDOW. Cleanup kill/reap до 0,5 s. Literal IPv4/IPv6, hostaddr и Unix socket обходят DNS; реальный Linux Unix-socket SCRAM/SQL/commit proof получен в 3a.5. На Windows этот профиль не проверен.
- Endpoint quantum: минимум из 1 s и остатка общего бюджета, делённого на число оставшихся кандидатов. Проверены четыре TCP stalls → здоровый пятый; исходные hostname/port сохраняются для TLS identity. Auth refusal не переключается на следующий host. target_session_attrs проверяется bounded read-only SQL, prefer-standby имеет fallback any; SQLAlchemy AdaptContext.adapters сохранены отдельно от libpq kwargs.
- Scoped durable PG preflight допускает только проверенные пары **Linux/libpq 18.6 (180006)** и **Windows/libpq 18.4 (180004)** при psycopg **3.3.6** и deadline-capable wait; requirements pin-ит psycopg 3.3.6. Native Windows PostgreSQL 17.11/SCRAM/TLS/SQL proof пройден, SQLAlchemy там 2.0.52; Linux packaged SQLAlchemy — 2.1.3. Другие OS/build combinations требуют отдельной приёмки. Отказ service из URL проверен; environment service пока может заблокировать defaults до отказа (FAIL в 3a.5). Main venv не обновлялся. Disabled и SQLite не требуют нового adapter.
- Linux nonroot/read-only proof: 2 CPU, 1536 MiB, 256 PID, /tmp 256 MiB. Реальные IPv6, частичный DNS, TLS verify-full/ошибки имени и CA, COMMIT-before/ACK-loss проходят. Windows helper/actual interpreter cleanup и выбранный native PG профиль выполнены. Regular passfile проверен на Linux в 3a.5; там же три file-blocking сценария остаются FAIL. GSS/SSPI, mTLS, успешный service profile и production load не доказаны. Нет общей гарантии real-time для зависшей ОС/native auth или произвольного профиля.
- Unknown COMMIT: 4,070/4,074 s в финальном proof, один audit_unavailable, нет ложного memory commit counter или повторной INSERT. До COMMIT rows=0; потерянный ACK оставляет ровно один committed event, который штатный dispatcher выводит с исходным UUID/payload. После восстановления проверено исчезновение pg_stat_activity session.
- Focused 27 PASS (17 transport + 10 HTTP/config), PostgreSQL 31 PASS/0 SKIP, network 20 PASS/0 SKIP, whole PGDATA/WAL повторены. Общий project gate PASS: npm audit 0 vulnerabilities, frontend 393 PASS/1 SKIP, ESLint/Ruff PASS. Full backend: 3401 PASS/78 SKIP/0 FAIL, coverage 88% при gate 85%; home/hosted CI этим не заменяются.

#### 3a.1. Проверка реализуемости и конфигурационного контракта

- [x] Проверить короткий дизайн до product code. Реализован synchronous PostgreSQL adapter в ограниченном AnyIO offload, системный resolver в короткоживущем процессе, libpq connect polling и SQL waits. Альтернативы `wait_for` над существующим потоком или обязательный hostaddr сами по себе не решают исходный общий контракт. Глобальный Windows event-loop policy не менять.
- [ ] В disposable proof на packaged версиях проверить все потенциально блокирующие переходы: resolver, connect/auth/TLS, SQLAlchemy dialect initialization, SQL/commit и error cleanup. Сейчас image содержит **psycopg 3.3.6 / libpq 18.6 / SQLAlchemy 2.1.3**, локальный venv — psycopg 3.3.4. Не считать сигнатуру driver wait стабильной только по номеру версии; проверить capability и аварийные ветки, зафиксировать поддерживаемый диапазон/constraints. Несовместимый durable PG profile отказывается до readiness с безопасной ошибкой; disabled/SQLite не требуют нового PG adapter. Не обновлять общий локальный venv как побочный эффект.
- [ ] Составить support matrix эффективных SQLAlchemy/libpq параметров: URL/query/connect args и применимые PG environment/service/passfile defaults; host/hostaddr/port lists, пустые элементы, IPv4/IPv6, Unix sockets, несколько адресов одного имени, credentials, options/search_path, target_session_attrs и load_balance_hosts. Сохранить исходный hostname для TLS/GSS и password-file matching. Проверить приоритет источников, порядок/политику failover и отличие сетевого отказа от отказа аутентификации. Неизвестный профиль явно отклоняется scoped preflight, не преобразуется и не переходит молча на старый медленный путь. Ограничение ранее допустимого профиля требует отдельного решения, а не скрытого сужения общего решения.
- [ ] TLS/GSS/SSPI, service/auth helpers и локальное чтение файлов включить в проверку блокировок применимого профиля; `hostaddr` не является доказательством bounded поведения всей аутентификации. Не отключать SSL/GSS и не менять доверие CA ради теста. Если требуемый профиль не укладывается в deadline без небезопасных private hooks или оставленного writer, остановить эту ветку реализации и уточнить дизайн; gate остаётся FAIL.

Основание: [libpq 18 connection control](https://www.postgresql.org/docs/18/libpq-connect.html) требует исключить DNS из polling, повторно получать актуальный socket и самостоятельно контролировать срок; `connect_timeout` не ограничивает `PQconnectPoll`. Это основание выбора polling; реализация и границы proof перечислены ниже.

#### 3a.2. Admission, единый бюджет и DNS helper

- [x] Начинать deadline в async denial boundary **до ожидания admission/offload**, а не внутри worker. Реализованный бюджет: до 4 s от этой точки на admission/DNS/connect/SQL, DNS до 1 s внутри него, до 0,5 s cleanup; остаток до HTTP <5 s — запас на возврат ответа. Ни фаза, ни следующий host не получают новый полный срок. HTTP измерять от начала запроса до ответа; отдельно измерять denial interval, чтобы не скрывать очередь и middleware. Это gate перечисленных faults при работающем scheduler, не гарантия при остановленной VM/ОС.
- [x] Выделить ограниченную admission capacity, независимую от занятого общего AnyIO limiter, без неограниченной очереди. До load matrix зафиксировать численные лимиты attempts/helpers/hosts/addresses/IPC и правило отказа при переполнении. При отсутствии слота или истёкшем сроке не запускать writer: сохранить отказ доступа и выдать один безопасный `audit_unavailable`; отсутствие audit row не выдавать за доставку. Once-marker и исключения OPTIONS/health/auth-me применить до offload, чтобы конкурирующие callbacks не создавали две попытки. RED: `test_deadline_includes_admission`, `test_shared_limiter_saturation_preserves_denial_and_liveness`, `test_overload_starts_no_writer`, `test_request_attempt_is_claimed_once`.
- [x] DNS helper использует системный resolver с /etc/hosts/search/ndots и правилами ОС; literal IP/явный hostaddr/Unix socket обходят его. Один общий DNS deadline на набор имён, ограниченное параллельное разрешение и частичные результаты: зависшее первое имя не задерживает готовые последующие имена/IP. Сохранить привязку каждого адреса к исходным host/port, проверять формат, размер, число и порядок результатов; не создавать постоянный DNS cache. RED: `test_silent_name_does_not_starve_ready_hostname`, `test_partial_dns_results_preserve_host_port_pairs`, `test_dns_bypass_profiles`.
- [ ] В child передавать только необходимые имена/параметры resolver через bounded stdin; env по allowlist, без наследования DB URL/password/APP_SECRET_KEY, identity или SQL. Не импортировать application bootstrap, не наследовать DB sockets и лишние descriptors; Windows запуск скрытый. IPC чтение/запись и spawn входят в deadline, stderr не публикует произвольный вывод. Обработать spawn/PID-limit failure, обрыв/oversize IPC и смерть helper. Проверить отсутствие секретов в argv/env/logs через synthetic markers. RED: `test_resolver_child_has_no_application_secrets`, `test_dns_ipc_and_spawn_fail_closed`.
- [ ] Один владелец освобождает connection, pipe, helper и admission token на success/error/timeout/client cancellation/shutdown. Terminate/kill/reap ограничены cleanup budget; нельзя вернуть token и начать новые helpers поверх неубранного процесса. Неочищенный ресурс — явная ошибка приёмки и закрытый слот с bounded lifecycle учётом, а не бесконечные retries или «успешная очистка». Серия timeout/cancel не увеличивает число потоков/PID/fd и не оставляет локальный writer; SIGTERM проверяется в пределах настроенного termination grace. RED: `test_cancel_and_shutdown_reap_owned_helpers`, `test_repeated_faults_recover_resource_capacity`.

#### 3a.3. Подключение транспорта и границы транзакции

- [ ] Адаптер использует host/hostaddr/port пары и остаток общего срока. Отдельно ограничить попытку одного endpoint, чтобы первый TCP stall не съедал весь бюджет рабочего следующего host; распределение времени и порядок фиксируются в proof. Проверить silent-name → healthy-IP, stall-IP → healthy-IP, несколько A/AAAA, target_session_attrs и auth-failure stop semantics. Создание SQLAlchemy connection и dialect setup также внутри срока; не копировать отключённые SSL/GSS из synthetic fixture в runtime.
- [ ] Тот же deadline распространяется на BEGIN, обе записи audit/outbox, COMMIT, ROLLBACK и pool reset/dispose. Statement/lock limits — дополнительные ограничения, а не замена общего срока. SQL timeout не должен запускать стандартный многосекундный cancel/wait; owned connection закрывается без автоматического переподключения/повтора INSERT. Сохраняются SQLSTATE, типы данных, search_path, root-commit/savepoint hooks. RED: `test_dialect_setup_and_cleanup_share_deadline`, `test_socket_blackhole_during_insert_and_commit`, плюс существующие lock/statement/atomicity regressions.
- [x] При потере COMMIT ACK исход **неизвестен**: не обещать rollback, не повторять INSERT и не считать успех подтверждённым. Durable outbox доставит реально committed запись обычным dispatcher. Проверить обе стороны границы через отдельную здоровую connection после снятия fault, с прежними event UUID/payload bytes; unconfirmed attempt даёт ровно один failure signal, не фиктивный commit counter. Возврат HTTP и отсутствие локального writer не доказывают мгновенное прекращение серверной транзакции при сетевом разрыве: отдельно проверить её завершение/освобождение locks после восстановления. RED: `test_lost_commit_ack_preserves_single_event`, `test_disconnect_before_commit_leaves_no_partial_event`.
- [ ] Сохранить 401/Bearer, 403/CSRF и существующие OIDC/cookie/redirect contracts: audit fault никогда не разрешает доступ и не выдаёт новую auth cookie. В диагностике только bounded failure signal/фиксированные причины, без host/URI/actor labels, traceback или синхронной stdout записи в request. Профили disabled/SQLite не используют helper и сохраняют прежнее поведение. RED: `test_audit_timeout_preserves_denial_http_contracts`, `test_disabled_and_sqlite_do_not_load_pg_transport`.

**Дополнение Windows/lifecycle 05.10.2026:** native профиль проверен на отдельном новом PostgreSQL 17.11 с SCRAM, TLS и loopback port; рабочие БД не изменялись, собственный postmaster остановлен. Реальный системный localhost resolver → TLS → SQL прошёл за 1,140 s; проверено завершение самого interpreter при timeout. Native матрица — 6 store/preflight, 16 fault/rollback/race и 13 multihost/TLS/COMMIT/burst cases, без пропусков. POSIX SIGTERM и Linux /etc/resolv.conf/IPv6 fixture не приписываются Windows. Linux final-v7 — 24 cases, в том числе 2×3 bursts по 24 requests и SIGTERM при DNS/SQL: shutdown 1,113/1,107 s при grace 8 s, zero owned helper/DB sessions, четыре immutable bootstrap events сохранены. Базовый общий AnyIO pool прогревается первым burst; последующие два не увеличивают threads/handles, без удержания test instrumentation. Нет queue/лишнего SQL при overflow; cancelled worker возвращает слот после cleanup. Это fault/admission proof, не production load matrix.

#### 3a.4. Приёмка, упаковка и откат

- [x] Проверить выбранные Windows/Linux build пары: Linux final-v7 network/lifecycle 24 PASS, native Windows PostgreSQL 35 PASS и Windows focused 35 PASS; helper interpreter и admission cleanup подтверждены. Не считать POSIX/DNS fixture тестами Windows.
- [x] Повторить full backend/coverage на frozen audit-v7: 3491 collected, 3408 PASS/83 SKIP/0 FAIL, coverage 88% при gate 85%; обязательные network24/PG31/whole-storage2 без пропусков. Общий project gate PASS, dependency audit 0 vulnerabilities; 272 packaged files совпадают.
- [x] Выполнить home managed upgrade с coherent backup и прежними digests; actual running272 hashes/revision совпали, HTTPS/OIDC/roles/CSRF/upload/generation/search/chat и response→DB→stdout PASS; 102 исторических immutable rows сохранены без replay.
- [x] Создать coherent completed backup нового audit-v7, вернуть приложение Ready, перенести архив вне VM и проверить manifest/6 artifacts; исходный pre-upgrade backup сохранить.
- [x] Повторить nonempty restore нового audit-v7 backup: 136 immutable rows, четыре fresh PVC, новые APP_SECRET_KEY/PG password, одно добавленное pending→held до start, ранее held сохранён, pending=0/no historical replay. Старые cookie/flow отказаны, новый login response→DB→stdout и полный HTTPS smoke PASS. Source paused/PVC retained после coherent backup; completed restore backup и source backup проверены вне VM. Этот single-node proof не закрывает restore_without_source_vm/production RPO/RTO.

- [ ] Все исходные **8 real DNS/multihost cases** становятся PASS при неизменном HTTP <5 s, без skip/xfail. Добавить случаи из 3a.2–3a.3, TLS verify-full positive/hostname mismatch/untrusted CA, IPv4/IPv6 и реальные Windows/Linux paths; не ограничиться mocks resolver/poll. В required job отсутствие нужного test backend/профиля — ошибка, не зелёный skip. Старые 31 PG cases, SQLite/HTTP, stdout-stall и storage/atomicity regressions сохраняются.
- [ ] Новый backend image должен включать helper и dependency constraints. Проверить запуск nonroot/read-only с реально заявленными PID/CPU/RAM/tmp limits; fault burst совмещать с `/health/live` и обычным лёгким API. Зафиксировать число попыток/одновременность, HTTP latency, resources до/после и zero leftover owned resources. Не объявлять это production load/CSI приёмкой.
- [ ] Из корня worktree выполнить focused unit/config tests, затем существующий runner: `python scripts/kubernetes/tests/audit_network_fault_acceptance.py --backend-image <new-local-image> --evidence-directory <new-private-directory>`. Сохранить исходный RED; записать новый source/image digest, driver versions, JUnit и cleanup evidence. Далее full backend/coverage, dependency/project gate и релевантный home HTTPS/restore smoke на том же snapshot. Проверки ОС и hosted required CI отмечаются отдельно; локальный PASS их не заменяет.
- [ ] Перед домашним upgrade проверить свободную память и сохранить coherent backup/старые image digests. Откат образа без изменения схемы возвращает прежний известный DNS defect: это восстановление прежней версии, не GREEN release. Не отключать durable audit и не переходить на legacy transport автоматически. Продвижение возможно только после проверки всех обязательных gates на фактическом release snapshot; audit-v5 и старая домашняя приёмка новый транспорт не подтверждают.

**Review Focus 3a:** очередь/once-marker — 3a.2; секреты и lifecycle helper — 3a.2; DSN/TLS/failover — 3a.1/3a.3; неизвестный COMMIT и отсутствие повторной записи — 3a.3; реальные версии, ресурсы и отсутствие ложного GREEN — 3a.4. Выполненные части отмечены; составные checkboxes с незакрытыми OS/auth/load/home/CI условиями остаются открыты.

#### 3a.5. Файловый этап конфигурации/connect — следующий scoped repair

**Исходный gate 05.10.2026:** новый runner `scripts/kubernetes/tests/audit_profile_acceptance.py`, default `--scope all`: **13 PASS / 3 FAIL / 0 SKIP** на frozen audit-v7. Обычный SCRAM/passfile и real Unix socket пройдены; FIFO passfile, TLS CA и environment service-file превышают прежний <5 s. Новый required шаг GitHub audit-capacity job остаётся RED до исправления. `--scope regular`/`blocked` — только явно записанные диагностические выборки; required CI использует all и не объявляет частичный proof полным. Исходный source272/272 и home deployment не менялись.

| Профиль | Свежая проверка | Статус |
|---|---|---|
| Explicit/PGPASSFILE regular file, mode0600, escaped colon/backslash | SCRAM, original hostname при hostaddr, TLS verify-full, SQL/commit, response→DB→stdout; URL-over-env, password precedence/first matching entry | PASS выбранного Linux profile |
| Wrong first wildcard, mode0644, missing passfile | Denial401/Bearer, один failure signal, без частичных строк, healthy recovery | PASS отказа; не успешный auth profile |
| Numeric hostaddr without hostname | SCRAM + explicit sslmode=require, без DNS; это отдельный encryption-only profile, не verify-full identity proof | PASS указанной конфигурации |
| Unix socket, explicit password | Реальный shared owned socket, SCRAM/SQL/commit, inet_server_addr NULL, без DNS | PASS Linux; Windows socket proof отсутствует |
| Service из URL | Scoped refusal до service-file I/O | PASS отказа; service profile не поддерживается |
| PGSERVICE + PGSERVICEFILE FIFO | `PQconndefaults` читает service-файл прежде существующего guard | FAIL общего deadline |
| Passfile FIFO / TLS CA FIFO | Native connect/SSL-file phase не возвращает управление за5s; killed/reaped child, zero extra server sessions | FAIL общего deadline |
| Default home passfile, service success, GSS/SSPI/mTLS/system trust/CRL directory, blocked regular filesystem | Свежего proof нет | OPEN, не переносить результаты обычного файла |

**Область repair:** сохранить действующие schema/event/transaction/admission contracts. Не обходить проблему ослаблением TLS, глобальной очисткой PG environment, отключением durable или заменой приложения/IdP. Сначала фиксировать условия профиля и короткий дизайн файлового слоя; единичный `stat` перед `fopen` не подтверждает bounded чтение обычного файла/TOCTOU/NFS.

**Первый repair 05.10.2026 — выполнен только service-guard:** `reject_service_profile(engine)` отказывает при наличии service в SQLAlchemy URL/dialect kwargs или PGSERVICE, включая пустые значения, до native defaults. `validate_audit_transport_profile(settings, *, engine=None)` вызывается в lifespan до init_db/seed/shared DB операций; disabled/SQLite не вызывают PostgreSQL guard. Подготовлен локальный кандидат `audit-v8-service-startup`: исходные16 profiles **14 PASS / 2 FAIL**, расширенная required matrix16+14service/startup+7protocol = **35 PASS / 2 FAIL / 0 SKIP**; focused native Windows **57 PASS** плюс отдельные существующие startup/health/HTTP contracts **29 PASS**; packaged source272/272 совпадает с новым candidate. Исходные13/3 и промежуточные fixture/driver failures сохранены. Strict child protocol теперь принимает только ожидаемый DenialTimeout terminal result, exit0 и точные frames; broad unexpected exception не закрывает gate.

На момент service-only snapshot полный B не был закрыт; последующий file-material repair и текущие ограничения приведены ниже. Изолированный ранний service refusal выполнен раньше решения A, потому что не зависит от способа подготовки passfile/TLS материалов; ruling сохранён в private ledger. В service-only snapshot helper/limits ещё не были выбраны; текущий выбор и реализация приведены ниже. Реальный lifespan test использует подменённые external-dependency/diagnostics hooks, но настоящий lifespan/HTTP и sentinel seed/dispatcher; это не запуск полного production stack. Домашний audit-v7 и его restore proof относятся к предыдущему snapshot. Новый полный backend/coverage, project/Compose/network/storage gate, home upgrade/restore и hosted CI не выполнены для этого кандидата.

**Файлы и границы интерфейсов:**

| Владелец | Изменение / потребитель |
|---|---|
| `backend/app/services/audit_denial_transport.py` | `connection_parameters(engine, *, deadline)` получает абсолютный monotonic deadline; проверка service предшествует native defaults. `connect(params, deadline)` сохраняет подпись и SQLAlchemy adapters. Файловые материалы принадлежат одной попытке до закрытия её соединения |
| `backend/app/services/audit_security.py` | `_record_denial` передаёт исходный deadline; cleanup материалов включён в существующее решение об освобождении/quarantine slot. Публичные denial API и transaction owner не меняются |
| `backend/app/services/audit_runtime.py`, `backend/app/main.py` | Ранний `validate_audit_transport_profile` уже стоит до seed; последующий `preflight_audit` передаёт отдельный deadline для file/config validation до общего `engine.connect()`. Отказ профиля происходит до readiness/dispatcher; общий startup DB timeout этим этапом не объявляется исправленным |
| `backend/tests/test_audit_denial_transport.py`, `test_audit_configuration.py`, `test_audit_postgres_profiles.py`, `test_audit_postgres_network.py` | Unit precedence/ownership, startup, настоящий HTTP и Linux/Windows fault lifecycle |
| `scripts/kubernetes/tests/audit_profile_acceptance.py`, `backend/tests/test_kubernetes_audit_profile_runner.py`, `.github/workflows/ci.yml` | Строгий протокол результата, required matrix, private evidence и cleanup fixture |
| `docs/AUDIT_DELIVERY.md`, `SECURITY.md`, spec/этот план/отчёт | Таблица поддерживаемых профилей, численные пределы, обновление/откат и подтверждённый snapshot |

**Порядок исполнения:** A → B → C → D → E. Старые unchecked RED briefs в Tasks 1–6 — исторические требования, а не указание заново реализовать готовое ядро. Ранний service-guard и строгий test protocol выполнены в указанном выше объёме; составные критерии A–E остаются открытыми.

- [ ] **A. Закрыть решения дизайна до product code.** В разделе file-profile spec зафиксировать для каждого explicit/env/implicit input: поддержка либо ранний отказ; способ bounded чтения; численные лимиты bytes/files/IPC и время жизни материалов; внутренний интерфейс их владельца и cleanup. Включить default home passfile и TLS files, `sslrootcert=system`, `sslcrldir`, client cert/key, encrypted key и Windows paths/ACL. Даже явный URL password не является доказательством отсутствия остальных файловых inputs. Непроверенный профиль не должен молча переходить к native read. GSS/SSPI/LDAP/service success остаются отдельными профилями. Выбран helper: `audit_file_probe.py` и controller `audit_file_materials.py`, tests `test_audit_file_materials.py`/`test_audit_file_lifecycle.py`. Design/limits/lifetime записаны в spec и ledger; Windows ACL/system-provider coverage ещё открыты. Отдельный прежний test_audit_file_probe.py не требуется: helper protocol/materials покрываются указанными файлами.
- [ ] **B. Ранний service отказ и bounded config — scoped path выполнен.** Перед `PQconndefaults` проверить URL/PGSERVICE, в том числе при явных URL credentials. Закрепить тестом `test_service_precedence_refuses_before_native_defaults`: параметризованные absent/empty/nonempty значения, разрешение приоритета без изменения process environment; sentinel доказывает, что unsafe defaults не вызваны. Существующий `test_service_profile_refused_before_file_or_connect[env]` становится PASS именно по ожидаемой категории отказа. Передать deadline обоим production callers из таблицы, обновить прямые test callers. `test_file_profile_preflight_refuses_before_readiness` проверяет реальный lifespan: отказ file/config этапа в пределах 4 s + cleanup ≤0,5 s, без запуска dispatcher и без соединения через общий engine после отказа. Disabled/SQLite сохраняют прежний путь.
- [ ] **C. Материалы и доверие.** Реализовать выбранный в A механизм; сохранить passfile matching/escaping/permissions, original hostname/port и per-host credentials при failover, TLS hostname/CA/client identity, SQLAlchemy adapters. Проверить env/default paths, symlink/rotation/size/TOCTOU, missing/unreadable/oversize и блокирующее metadata/read. `test_projected_secret_rotation_preserves_attempt_materials` покрывает read-only projected Secret с symlink-заменой: одна попытка использует согласованный набор cert/key/CA либо безопасно отказывает; новая попытка после ротации использует новые материалы. Ни stale cache, ни fallback на старые credentials/TLS не допускаются без отдельно описанного контракта. Проверить ошибки имени/CA, не принимать один лишь успешный TLS handshake за identity proof. Mode0444 projected passfile не считать автоматически эквивалентом ранее проверенного mode0600: поддержка и безопасные права временной копии определяются в A; silent bypass проверки прав запрещён.
- [ ] **C.1. Если выбран helper:** только local config/file work, **без SQL/INSERT** и без импорта app settings. Allowlist env, private bounded stdin/stdout protocol, никаких credentials/keys в argv/stderr/log/evidence. Ограничения bytes проверяются во время чтения, не после неограниченного `communicate()`; `test_file_protocol_limits_and_redaction` проверяет N/N+1, malformed/truncated output, exit≠0, canary secrets и pipe stall. AdaptersMap/live connection не сериализуются. Private files не попадают в backups/diagnostics; cleanup проверяется на success/error/cancel, а остатки после SIGKILL удаляются безопасно только по подтверждённому owner. DNS helper остаётся secret-free; общий лимит helpers и памяти относится к попытке, а не отдельно к каждому helper.
- [ ] **D. Полный HTTP/lifecycle gate.** Deadline создаётся **до admission/offload**, а file/config, DNS, connect и SQL последовательно расходуют один WORK_SECONDS=4,0 s; у каждой фазы нет нового четырёхсекундного бюджета. Cleanup ≤0,5 s, HTTP <5 s. Startup file/config получает отдельный такой же бюджет без заимствования request slot. `test_file_fault_http_denial_and_recovery` проходит через настоящий protected HTTP endpoint для service/passfile/CA faults: неизменные status/Bearer/request UUID, без новой cookie, один failure signal, zero partial facts; после снятия fault — ровно один committed event и exact stdout. FIFO/service отказ до DB не запускает SQL. `test_file_fault_burst_cancel_and_sigterm` проверяет saturation/cancellation/SIGTERM и здоровый соседний API: admission max4, без очереди/новых SQL attempts, живой worker держит slot до cleanup, incomplete cleanup quarantines slot. Процессы/FD/handles/private files/DB sessions возвращаются к baseline; поздних INSERT нет. Внешний kill/reap тестового ребёнка не считается очисткой приложения. Зависшая ОС не получает обещания hard real-time.
- [ ] **E. Строгая приёмка и выпуск.** Ужесточить существующий CHILD protocol в `test_audit_postgres_profiles.py`: broad `except Exception → bounded-refusal` и один `attempt-started` не доказывают правильный отказ. `test_profile_protocol_rejects_unexpected_exception` требует ожидаемую фиксированную категорию отказа, exit=0 и конечный маркер; TypeError/import failure/crash/пустой output/`connect-returned` на блокирующем FIFO — FAIL. Все 16 исходных cases остаются в all-scope matrix, новые добавляются с явным inventory и новым expected count runner; не замораживать число16 после добавления тестов. Required fixtures отсутствуют → FAIL, без skip/xfail/continue-on-error. Сохранить исходные13/3 и180s watchdog failure.

**Команды и достаточное доказательство:**

1. Из `backend/` выполнить `python -m pytest tests/test_audit_denial_transport.py tests/test_audit_configuration.py tests/test_kubernetes_audit_profile_runner.py -q` и выбранные в A helper tests. Ожидание: PASS; platform-only cases перечислены отдельно и выполнены на своей ОС.
2. Из корня Linux/Docker выполнить `python scripts/kubernetes/tests/audit_profile_acceptance.py --backend-image <new-image-id> --evidence-directory <new-private-directory>` без `--scope`. Ожидание: все исходные16 + новые cases PASS, 0 SKIP, exit0; HTTP/startup/cleanup измерены отдельно. Затем required network24/PG31/storage2, полный backend с coverage ≥85% и `node scripts/check-project.mjs`. Existing runner flags брать из `--help`; старые evidence directories не перезаписывать.
3. Native Windows повторяет поддерживаемые passfile/env/ACL и lifecycle cases на проверенной паре драйвер/libpq; POSIX FIFO не заменяется фиктивным Windows PASS. Для native blocked-file case нужен эквивалентный owned fixture либо явное ограничение поддержки. macOS для durable PG этим этапом не обещается; legacy disabled/local/Compose bundled/external сохраняются и проходят релевантную регрессию.
4. Новый source/image freeze связывает код, tests, driver/libpq/OS, image digest и matrix. Домашний compact/nonroot/read-only стенд получает projected Secret/trust mounts, startup refusal и HTTP после ротации; затем HTTPS/restore с immutable rows и no replay. Перед запуском — capacity/backup/exact ownership check. Откат сохраняет данные, но возврат к audit-v7 возвращает известный file-profile дефект; это recovery, не зелёная приёмка repair. Hosted required CI проверяет фактический release snapshot отдельно; GitLab consumer в окруженческой копии должен получать ту же обязательную all-scope проверку или проверяемый результат на точно этих digests. Commit/merge/push отдельно.

**Основание:** [libpq18 password file rules](https://www.postgresql.org/docs/18/libpq-pgpass.html) задаёт matching/permissions; [fe-connect.c](https://github.com/postgres/postgres/blob/REL_18_STABLE/src/interfaces/libpq/fe-connect.c) открывает passfile через `fopen` перед `fstat`, а `PQconndefaults` вызывает defaults/service parsing. Свежие faults подтверждают, что polling/DNS repair не охватывает файловый этап. Это дефект доступности scoped denial/config transport, не разрешение доступа и не proof downstream delivery.

### Task 4: Runtime configuration и delivery status

**Files:** Settings/main, status CLI, Helm/env examples, `SECURITY.md`, runbook; tests `test_audit_configuration.py`, `test_kubernetes_chart.py`.
**Interfaces:** mode disabled/durable; singleton lifecycle после проверки schema; `audit_delivery_status.py --runtime-env-file <private path>` возвращает DB counts/oldest pending age/held reasons и время наблюдения. Без URL/credentials/event/actor/IP labels. Отдельный процесс CLI не видит runtime counters/writer state: возвращает `runtime_state=unknown`, не предполагает healthy.

- [ ] RED `test_disabled_preserves_legacy_startup`, `test_durable_refuses_multiple_workers`, `test_operation_job_inherits_audit_mode_without_starting_dispatcher`, `test_status_has_no_payload_or_identity`.
- [x] В Settings валидировать mode/worker limit; startup подключает dispatcher после schema, shutdown останавливает его. Maintenance Jobs импортируют store, но не запускают lifespan writer.
- [ ] Durable startup проверяет требуемые таблицы/колонки/индексы и права чтения/записи вне существующего best-effort `try init_db` в main. Неприменённая migration/нет обязательных прав — отказ до readiness; проверка прав не добавляет фиктивных audit событий. Проверить реальные `--workers` в entrypoint и OS lock из Task 2. Initial durable profile не поддерживает hot reload; если он потребуется, reload shutdown/start — отдельный lifecycle gate. При runtime stdout stall liveness/readiness не создают restart loop, доставка диагностируется отдельно. RED `test_missing_audit_schema_fails_startup`, `test_read_only_outbox_role_refused`, `test_cli_worker_override_refused`.
- [x] Передать enum через application/operation rendered env; сохранять config schema 2, secret guards и компактную topology. Runbook описывает backlog/DB-full/stdout stall и manual investigation.
- [x] Operator CLI использует существующий явный runtime env contract: один защищённый выбранный файл, без fallback на корневой `.env`, без паролей в argv/output. Job с injected environment не требует host path; путь задаётся только при прямом запуске CLI. Операция hold требует привилегий UPDATE outbox, status — SELECT; права проверяются без фиктивного события.
- [x] Counters recorded/denied/db_failure/stdout_failure/retry и writer state доступны только in-process `AuditDispatcher.status()`; они сбрасываются после restart. Recorded/denied увеличиваются только после root commit, включая committed savepoints и исключая rollback. db_failure здесь считает неудачные DB cycles dispatcher; audit_unavailable у denial имеет отдельный bounded failure_count, это не счётчик committed событий. CLI сообщает только проверяемые DB aggregates; DB failure → безопасный nonzero exit, не нулевой backlog. Автоматический живой мониторинг этих counters требует следующего adapter и не считается выполненным. Runbook задаёт операторскую проверку backlog/возраста/места, действия при росте, стоимость хранения и запрет auto-delete pending/held/stdout_written. RED `test_cli_does_not_invent_runtime_state`, `test_cli_database_failure_is_not_empty_queue`.
- [ ] Run configuration/chart/auth regressions и `node scripts/check-project.mjs`; Expected PASS для новых contracts, общий gate остаётся red при dependency failure. `SECURITY.md` обновляется в том же наборе изменений.

### Task 5: Backup/restore hold и old binary rollback

**Files:** hold CLI, lifecycle, `scripts/production/restore-bundled.sh`, runbook; tests `test_audit_restore_contract.py`, `test_kubernetes_lifecycle.py`, `scripts/production/tests/test_backup_scripts.sh`; owned home lab.
**Interfaces:** `audit_hold_restored_events.py --runtime-env-file <path> --confirm-hold-restored-events` транзакционно переводит restored pending в held. Событие/UUID/time/payload неизменны; audit rows и stdout_written не переписываются. Historical replay CLI в этот пакет не входит.

- [ ] RED `test_hold_works_with_old_binary_and_new_table`,
  `test_absent_table_is_noop_and_unknown_schema_refused`,
  `test_restore_holds_pending_before_first_application_start`, `test_held_event_never_enters_dispatcher`, `test_new_event_after_restore_is_pending`, `test_hold_does_not_rewrite_audit_history`.
- [x] В restore maintenance Job, после SQL import и до запуска приложения, выполнить hold всех restored pending фиксированным operator command в saved
  toolbox image; не разрешать произвольный runner override. Если hold недоступен/ошибочен, приложение остаётся остановленным и lock сохраняется.
- [x] Старый binary, не умеющий durable outbox, запускается только с transport disabled; перед rollback остановить writer, сохранить coherent backup и явно удержать pending. Это не разрешает SQL downgrade/stamp.
- [x] Actual nonempty backup: создать success/denial/pending, остановить writer, backup→fresh PVC/Secrets/new APP_SECRET_KEY→restore→hold до первого start. Старые cookie/flow отказаны, исторические pending не выходят в stdout, новый login даёт новый event.
- [x] Обычный restart без restore сохраняет pending и повторяет прежний UUID. Exact marker duplicates подтверждаются сравнением DB/stdout. Backup kit и исходные namespace не переписываются; independent private kit сохраняется до cleanup.
- [x] Compose bundled restore выполняет тот же hold после импорта и до `compose up backend`; external/manual SQLite/PostgreSQL restore имеет обязательную остановку всех writer, hold и проверку результата до старта. Само приложение не отличает ручное копирование DB от restart, поэтому такую защиту нельзя обещать без операционной процедуры. RED `test_compose_restore_hold_precedes_backend_start`, `test_hold_failure_keeps_all_writers_stopped`.
- [x] `audit_outbox_hold_command() -> list[str]` формируется текущим lifecycle из фиксированного Python/SQL и выполняется в проверенном saved image: не импортирует новый app model/скрипт, которого нет в старом образе. При отсутствующей outbox table — noop; неизвестных schema/status — отказ. Hold идемпотентен и выполняется независимо от режима target. Различать причину restored_backup / binary_rollback / invalid_payload; повторный hold не перезаписывает её. После rollback и повторного upgrade held остаются held.
- [x] Существующий `backup_totals.py` считает документы/векторы и не подтверждает сохранность аудита. В приёмке отдельно сверить количество audit/outbox rows, UUID и hash immutable payload до/после restore; изменение pending→held допустимо, изменение фактов — нет. Для старого backup без outbox проверить upgrade с пустой очередью. Полную проверку выполнять для nonempty нового backup и старого saved image; не подменять её одним mock SQL.

### Task 6: Полная приёмка и handoff

**Files:** CI suites/PostgreSQL service, `docs/superpowers/reports/2026-10-04-audit-outbox-acceptance.md`, общий roadmap.

- [x] Локальные SQLite + PostgreSQL 17 migration/store/transaction/dispatcher/security tests выполнены в frozen final image; текущий PG набор 31 PASS/0 SKIP включает 22 новых timeout/race/capacity cases. Исторические v5 hashes 270/270 сохранены; новый v6 freeze — 272/272, новый full suite 3401 PASS/78 SKIP/0 FAIL, coverage 88%.
- [ ] Повторить required CI на фактическом release snapshot; отсутствие DB-service — failure, не skip.
- [x] Frozen backend собран, source/packaged hashes и actual running image identity проверены. Домашние confidential/basic/PKCE none и public/none/S256/UserInfo-only HTTPS flows + role/CSRF/upload/search/chat прошли; successful callback и protected denial сверены response→DB→stdout.
- [ ] Повторить выбранный OIDC профиль на целевой платформе после release freeze; прочие комбинации не объявлять покрытыми домашними двумя профилями.
- [x] Проверить killed dispatcher/marker failure/stdout stall/restart и restore hold; сохранить первоначальные failures. Запись в stdout не объявлять доставкой в внешний индекс.
- [ ] Полный backend/coverage CI, frontend/lint/parser/Compose contracts; red gates не менять thresholds/omissions ради допуска. Независимый review только при отдельной авторизации.
- [ ] Report: exact source/image versions, counts/skips, migration/restore/rollback, duplicates, failed attempts, operator status и незакрытые downstream/relay/retention gates. Cleanup только owned UID/run resources после verify backup.
- [x] Домашняя VM: перед запуском проверить доступность, RAM/диск и существующие workloads; использовать согласованный training стенд с synthetic corpus и отдельными PVC. При нехватке RAM останавливать только ранее согласованную тестовую БД после точной идентификации; состав запущенных ресурсов и порядок обратного запуска сохранить приватно. Docker/локальные проверки не заменяют фактический home Kubernetes прогон; отсутствие доступа отмечается открытым gate.

## Следующие отдельные пакеты и review

Trusted-IP relay/HMAC, credential-protected metrics endpoint/monitor route,
collector parser/dedup/ACK, historical replay/retention и multi-replica claiming
не входят в эту реализацию. Task 5а roadmap целиком остаётся открытой до проверки
применимых adapters. Отсутствие collector SDK не является proof downstream.

Self-review: schema/allowlist→0/1; atomic writes→1/3; bounded retry/crash→2;
configuration/status→4; restore/replay boundary→5; actual lab/CI→6. Все пять
Review Focus имеют owning tests. План сохраняет inline execution; review
документа предшествует новой архитектурной реализации. Коммит не часть handoff.

Исходные проблемы, учтённые при реализации: `init_db` не обновляет legacy columns;
`DiagnosticContextMiddleware` принимает входящий UUID; `CsrfMiddleware` возвращает
403 напрямую; `api/users.py` разделяет audit и mutation; Compose restore сразу
стартует backend; `backup_totals.py` не считает audit. Соответствующие изменения и локальные failure tests реализованы; оставшиеся gates перечислены ниже. Незакрытый составной checkbox не означает отсутствия всего кода: его полный критерий ещё не доказан.


## Сверка реализации с критериями приёмки

| Пакет | Подтверждено локально | Осталось проверить |
|---|---|---|
| 0 | 77 actions, 95 статических call sites/wrappers; typed allowlist, enums, canary, Unicode/summary | Применимый внешний event profile, внешние IdP account события |
| 1 | Atomic insert и serialization-before-INSERT; root/savepoint rollback; UNIQUE и failed INSERT; strict FK/check/type/index drift; additive SQLite/PG и SERIAL legacy fixture; empty SQLite downgrade; head/model drift | Production rollout migration; fixture-only PostgreSQL downgrade, если нужен отдельный proof (binary rollback не делает downgrade) |
| 2 | Marker retry/short writes/broken pipe/backoff; отдельные процессы crash/partial/SIGKILL/second-owner на SQLite и PG; bounded stop сохраняет lock; реальные Uvicorn stdout/stderr streams | CRI/collector reassembly и downstream dedup; глобальный commit order не обещается |
| 3 | Atomic security/cookie/session rollback; OIDC replay/CSRF/401/UUID; SQLite/PG denial lock budget с сохранённым search_path; HTTP при blocked stdout; concurrent unblock [0,1]; реальный outbox tmpfs full 53100/security rollback/recovery, denial statement57014/concurrent liveness, mutation lock55P03/statement57014 10cases, refused/stalled connect, mixed block/unblock3schedules; whole PGDATA128/WAL64 MiB tmpfs, cold-start/actual lifespan refusal, nonempty maintenance hold и exact recovery | Дополнительные auth/file profiles, production load/CSI и hosted CI; выбранные Linux24/Windows35, home upgrade и новый136-row restore подтверждены |
| 4 | Mode/worker guards; strict schema, реальный SELECT/INSERT/sequence/UPDATE всех delivery columns; CLI failures/root-env isolation; chart env; root-commit counters | Protected live adapter; reload, если потребуется отдельный профиль; локальный project gate PASS 05.10.2026 после scoped tinyglobby migration; внешний required CI открыт |
| 5 | Nonempty pg_dump→fresh DB→old-image hold, immutable digest; final 6 rows/pending=0; K8S/Compose ordering; full home confidential67/public36 restore, fresh PVC/Secrets/key/no replay и два off-VM archives | Дополнительные OIDC комбинации, реальные capacity/RPO/RTO и restore_without_source_vm; old-image hold подтверждён отдельным DB proof |
| 6 | Исторический Final-v5 source/image hashes: 270 Python/180 app files; 3370 PASS/36 SKIP; coverage 88% (gate 85%); targeted PG31PASS/0SKIP (9+22); Ruff PASS; frontend375PASS/1SKIP, lint/build PASS; home confidential/public restore; новый v6: 272 matched files, full3401PASS/78SKIP/88%, network20PASS/0SKIP, PG31PASS/0SKIP, project gate PASS; report и private evidence | Final-v7: 3408 PASS/83 SKIP/88%, Linux24/Windows35; home upgrade/correlation и 102 immutable rows подтверждены. Home-v7 restore136 rows/new PVC/keys/no replay и off-VM backups подтверждены; external CI/publication, дополнительные OIDC комбинации/target cluster и adapters; source freeze не является commit |

Домашняя VM и два выбранных confidential/public Kubernetes/PVC/Secrets/HTTPS restore proof из Task 5 подтверждены. Выбранная outbox capacity/timeout/mixed-race matrix также подтверждена. Полное PGDATA/WAL и выбранные cold-start/lifespan/maintenance/recovery probes подтверждены 05.10.2026. Следующие gates: остаток Task 3a (дополнительные auth/file profiles, production load/home) и external CI; Linux DNS/multihost gate исправлен с сохранением исходного FAIL; dependency remediation локально подтверждён 05.10.2026. Повторное подключение начинается с current Guest Agent address и resource check, а не старого IP.
Локальные schema/process/CLI и выбранные capacity/timeout/race пробелы прежней матрицы, включая whole PGDATA/WAL, закрыты;
Полная совместимость Task 3a и внешний release/CI gate остаются отдельными
незакрытыми проверками; выбранная Linux network matrix теперь GREEN. Compound checkboxes не отмечаются выполненными только
на основании частичного локального proof. RED списки выше — исходные test briefs;
фактически выполненные regression checks и их границы указаны в отчёте.
Общий rollout и задача 5а остаются открытыми.


### Уточнение dependency gate после public home приёмки

Свежий audit 04.10.2026: 5 high в цепочке braces→micromatch→fast-glob→Next ESLint, 0 critical. [Advisory](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm) пока не содержит patched braces version. `npm audit fix --force` с downgrade ESLint config до Next 14 не является проверенным исправлением для Next 16.

- [x] Проверить совместимый upstream ESLint/Next пакет, устраняющий уязвимую цепочку; сначала построить candidate lock в изолированной папке и сравнить dependency graph. Если готового обновления нет, подготовить отдельное решение для цепочки линтинга, не менять runtime framework автоматически.
- [x] Для выбранного изменения получить свежий audit без high/critical, выполнить frontend tests, lint, production build и полный project check. Новый frontend image и повторный home smoke требуются, если изменился deployable artifact. Сохранить исходный FAIL; не использовать исключения, скрытие dev dependencies или ослабление gate.

Fault matrix продолжать только на disposable PostgreSQL/container storage: не заполнять диск домашней VM или действующих БД. Capacity fault, connect/statement timeout и смешанный concurrent block/unblock фиксируются раздельными критериями; каждый отказ должен сохранять deny/cookie/transaction contract и проверяться после recovery.


### Handoff fault acceptance

Поставляемый `scripts/kubernetes/tests/audit_fault_acceptance.py` создаёт только свои internal/no-port Docker resources, использует pinned PostgreSQL image и read-only test mount. Окончательный прогон 31 PASS/0 SKIP, non-root/read-only backend test container; resource identity и private logs сохранены. `.github/workflows/ci.yml`: ordinary PG job содержит 25 portable cases, новый capacity job — полный отдельный 31-case runner. External CI и required-check settings ещё не подтверждены. `SECURITY.md` обновлён; product backend не менялся.


### Dependency handoff (05.10.2026)

Scoped tinyglobby adapter реализован и проверен в рабочей ветке. Windows:
393 PASS/1 прежний SKIP; Linux:394 PASS/0 SKIP; rootDir regressions18/18.
Effective ESLint67rules совпали, lint0errors/40oldwarnings; audit0vulnerabilities,
штатный полный project check и production build PASS. Новый frontend audit-v3
прошёл домашний managed upgrade с backup, running compiled hashes308/308,
полный HTTPS/OIDC smoke и response→DB→stdout correlation. Новый completed
backup проверен вне VM. Исходный FAIL и промежуточные candidates сохранены;
порог/состав gate не менялись. Runtime backend270source hashes не менялись.
Локальный dependency gate закрыт; release CI, main integration/publication и
прочие fault/adapters/replica gates остаются открыты. Детали — в отчёте приёмки.


#### Текущий checkpoint 3a.5: audit-v16-cleanup-budget

Проверены short/partial marker writes, marker open/close/unlink/rmdir faults, symlink/FIFO marker refusal, нулевой абсолютный cleanup deadline и IPC close failure. Incomplete cleanup удерживает exact owner/slot; timeout после удаления marker оставляет markerless directory в quarantine, повторная автоматическая очистка отсутствует. Controlled POSIX parent SIGKILL подтверждает сохранение snapshot и exact fixture-owned recovery; production orphan recovery этим не реализован.

Required Linux profiles120/120, network24/24, PG31/31, whole-storage2/2 PASS без пропусков; native Windows144PASS/12POSIX SKIP. На том же образе и2CPU выполнены10 последовательных прогонов26 cleanup/SIGKILL/file-phase cases:260PASS/0SKIP. Packaged274/274, scoped Ruff PASS. [Proof, исходные FAIL и ограничения](../reports/2026-10-04-audit-outbox-acceptance.md#cleanup-edges-и-budget-05102026--scoped-proof-release-open).

В нагрузке v15 выявлен преждевременный cleanup timeout: .25s рабочей части при общем лимите .5s. В v16 общий лимит не повышен, разделение изменено на .4s work/.1s reap. Цена: меньше резерв на reap; если завершение не подтверждено, сохраняются quarantine/registry и failed capacity gate. Work4s, defaults/create/prepare phase1s, HTTP<5s и admission4/no queue прежние. V15 failures115PASS/4FAIL и116PASS/3FAIL сохранены; один focused6PASS не заменял обязательный прогон.

Selected actual home file proof добавлен без изменения v16: projected CA/CRL explicit/default/rotation/old bytes/missing-present и actual emptyDir container-restart/new-Pod lifetime PASS. Direct0440 passfile ignored, test-only0600 staging принимает synthetic bytes. Работающее приложение не обновлено. [Новый proof и границы](../reports/2026-10-04-audit-outbox-acceptance.md#actual-projected-secret-и-emptydir-05102026--selected-file-proof).

Следующие узкие пакеты в порядке зависимости:

1. **C/C.1 — приёмка выбранного product профиля.** Temp layout уже реализован: отдельные disk emptyDir /tmp, resources и наследование writer Jobs; render/lifecycle proof не заменяет actual application proof и sizing. Проверить active/quarantined/orphan snapshots, parser/PDF/export, frontend cache, Job /work+logs, container restart и Pod replacement. Password остаётся runtime Secret env; direct fsGroup0440 passfile не становится поддержанным credential synchronizer. Для установки без PG TLS выполнить её собственные runtime/backup/release gates; новый TLS-пакет не является обязательным prerequisite без требования среды. При выборе TLS отдельно выполнить весь [T1–T6](2026-10-05-kubernetes-postgres-tls.md), включая server cert/key/HBA, app/Jobs, legacy unknown, trust transition, valid native crypto и полный lifecycle. File-only Secret proof не доказывает этот профиль. Home app остаётся v7; реальные CSI/NFS и uninterruptible I/O — отдельные проверки.
2. **D — operational parent-death recovery.** Контролируемый SIGKILL test имеет внешний fixture witness PID/start identity + UUID/exact directory и отказывает при active/foreign owner; продукт такого durable witness не имеет. До implementation определить защищённое ownership evidence, lifetime/Pod UID/process identity, авторизацию оператора и bounded cleanup procedure. Owning tests: active owner, foreign owner, PID reuse, markerless/partial marker, symlink/junction, concurrent recovery и исчезновение каталога; source files неизменны. Имя/возраст пути не доказывают владение. Не вводить recursive sweep общего Temp или автоматическое освобождение quarantine slot без proof. После удаления marker и failed rmdir стандартный helper безопасно отказывает; восстановление marker в fixture — не production recipe.
3. **C/C.1 — оставшиеся native profiles.** Windows service-account/default-file/auth/crypto и дополнительные GSS/SSPI/LDAP/OpenSSL-provider комбинации отдельно. Native144 относится к выбранным account/interpreter/libpq, не hosted Windows CI. При настоящем uninterruptible I/O bounded отказ не доказывает завершение child: owner/registry сохраняются, capacity gate остаётся failed.
4. **E — release.** После оставшихся runtime изменений новый freeze и required all120 (расширять, не исключать cases), network24/PG31/storage2; полный backend/coverage≥85%, project/Compose/dependency checks на этом snapshot. Затем resource/capacity check, coherent backup и домашний projected-Secret/HTTPS/restore immutable rows/no replay; hosted required CI и target platform отдельно. Выбранные10×26 не закрывают формальную стабильность всех новых сценариев или release CI. V7 full/home и v16 scoped proof не переносить на новый snapshot.

A/B реализованы в выбранном объёме; составные C/C.1/D/E остаются OPEN. Home runtime остаётся v7; main получает только docs mirror. Локальная проверка не является integration/publication. Реплики backend/frontend и внешний collector остаются отдельными этапами; commit/push/deploy не выполнены.

#### Product temp layout 05.10.2026 — selected package

Generic chart получил отдельные bounded disk emptyDir /tmp для backend/frontend,
TMPDIR/TEMP/TMP и ephemeral resources; backend contract наследуется writer Jobs,
legacy backup старым template сохранён. Начальные1Gi/256Mi — не measured sizing.
Password остаётся runtime Secret env. Продуктовый CA/CRL profile application/Jobs/
restore, native TLS/auth/revocation, disk-pressure и orphan ownership остаются OPEN.
App home v7 не обновлено, v16 app sources неизменны. [Приёмка и границы](../reports/2026-10-04-audit-outbox-acceptance.md#product-temporary-storage-05102026).

#### PostgreSQL CA/CRL: найденный prerequisite 05.10.2026

По исходникам bundled PostgreSQL ещё не включает TLS и server cert/key/HBA.
Preflight запрещает query в DATABASE_URL; Jobs не переносят PG trust identity.
pg_dump/pg_restore используют local socket внутри PG Pod и не подтверждают TCP
TLS. Поэтому клиентский CA/CRL mount сам по себе не является работающим профилем.

Подготовлен [проект полного opt-in bundled TLS](../specs/2026-10-05-kubernetes-postgres-tls-design.md):
server certificate/key и hostssl SCRAM, client verify-full и optional CRL,
immutable version-named Secrets, общий app/Job contract, schema3 operator kit
с явным PEM parser, backup format3 и legacy2=unknown с отдельным disabled adoption.
Не объявлять TLS обязательным внешним требованием; disabled сохраняет первый запуск.

Письменный дизайн исправлен при проверке; исполняемый план T1–T6 добавлен.
При реализации пройти последовательно operator validation → chart/server/Jobs →
backup/restore compatibility → valid native crypto/projected Secret proof →
полный одноразовый lifecycle и ресурсную приёмку. File-only v16, prior regular-file
native profiles и локальные render tests не закрывают эти новые gates.
Это проектирование; runtime/chart/toolbox/backup format не изменены этим пакетом.
Home upgrade, целевая платформа, реплики, orphan cleanup и dependency gates OPEN.

#### Проверка плана 05.10.2026 — приоритет и закрытые пробелы проектирования

Подробный [план PostgreSQL TLS](2026-10-05-kubernetes-postgres-tls.md) разделён на
T1 profile/kit, T2 chart/Jobs, T3 lifecycle/recovery, T4 backup/restore,
T5 native projected crypto proof и T6 full release. Это предстоящая реализация.

Opt-in TLS не блокирует первый disabled bundled запуск, если выбранная среда
не требует PG TLS; его собственные audit/backup/resources/dependency/CI gates
остаются открытыми до проверки. TLS release требует весь T1–T6. Неиспользуемые
native auth profiles не добавляются молча в поддерживаемую поставку: зафиксировать
поддерживаемую матрицу и ранний отказ остальных; наличие лабораторного profile
не означает его production support.

Уточнены: legacy backup2=unknown и explicit adoption; restore с истёкшей старой
PKI через привязанный trust transition; actual first initdb/VSO ordering;
immutable versioned HBA; новые Pod/connection proofs при rotation; checkpoint и
ручной recovery до/после migration; отсутствие automatic downgrade; exact schema2/
schema3 recovery kits; PG GSS/SCRAM/defaults; срок жизни ключей/CRL и реальные RPO/RTO.
Runtime в этой проверке не менялся; документирование не закрывает implementation
или platform gates. Предшествующие checkpoint ниже/выше — исторические результаты.
