# Admin Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Администратор получает ограниченный по времени и объёму безопасный ZIP
для разработчика из постоянного журнала ошибок или временной диагностической сессии.

**Architecture:** Отдельный структурированный recorder backend, ограниченный
spool Next.js, две таблицы управления и отдельный worker пакетов. Сырые сообщения
логгеров не экспортируются; разрешённые поля связываются request/operation ID.
UI и offline CLI используют один валидатор и один потоковый builder.

**Tech Stack:** Python 3.12, FastAPI/Starlette ASGI, SQLAlchemy 2/Alembic,
PostgreSQL/SQLite, stdlib logging/queue/zipfile, Next.js 16/React 19,
pytest и Node test runner; Docker Compose bundled/external.

**Spec:** [2026-09-27-admin-diagnostics-design.md](../specs/2026-09-27-admin-diagnostics-design.md).

**Status:** Только план, 2026-09-27. Код функции, миграции и тесты ещё не написаны
и не запускались. Значения ниже — целевые требования, не результаты измерений.

## Global Constraints

- Постоянный backend-журнал: 7 суток / 50 МиБ; frontend: 7 суток / 20 МиБ.
- Подробная сессия: 15 минут по умолчанию, диапазон 5–60, максимум 100 МиБ;
  одновременно одна; подробные данные живут 24 часа после остановки.
- Готовый ZIP: <=200 МиБ, TTL 24 часа; очередь 1 building + 2 queued;
  на администратора 1 незавершённый пакет и 3 создания в час.
- Общая файловая квота 500 МиБ = backend 480 + frontend 20, включая reservations,
  copies, temporary files, control metadata и deferred audit. Резерв диска 2 ГиБ.
- 1 МиБ = 1 048 576 байт; событие <=8 КиБ; backend queue 4096, Node queue 256;
  maintenance каждые 60 секунд; сегмент <=5 МиБ; stack <=32 кадров.
- Только admin управляет и читает диагностику. Browser ingest требует входа,
  отдельного opt-in и живой interface/system-сессии; не даёт доступа к журналам.
- Audit fail-closed для выдачи/старта/просмотра; stop и автоматические ограничения
  не блокируются отказом аудита. Пробелы записи явно видимы.
- Диагностика не пишет произвольные message/args/exception text, bodies, URLs,
  secrets, SQL, filenames, prompts, source text или историю чата.
- Существующие `data/debug` не экспортировать и автоматически не удалять.
- Один backend process и один Node writer; никаких дополнительных SaaS,
  Docker socket, публичных sourcemaps или новых прав на документы.
- Диагностический root находится вне DATA_DIR и обычного backup документов.
- Windows dev: 18000/16300; localhost для внутренних соединений не использовать.
  Внутри Docker порты остаются 8000/3000. npm shims локально не использовать.
- Не менять продуктовый код в ходе подготовки этого плана. При исполнении сначала
  заново проверить dirty working tree: сейчас уже изменены pipeline, llm_client,
  models, audit, api.js, i18n и SECURITY.md другой работой. Не откатывать её.
- Выполнение не означает автоматический commit/push/deploy. Коммиты — только
  при соответствующем поручении, точные файлы/hunks; никогда `git add .`.
- Изменения прав, журналирования, retention и deployment документировать в
  SECURITY.md в том же изменении, что и механизм/его отрицательные тесты.

## Review Focus

1. Ошибка LLM/SQL содержит текст документа и пароль: ни новый файл, ни ZIP,
   ни metadata не содержат canary. Тесты задач 1, 5, 8, 14.
2. NDJSON падает после HTTP 200 либо клиент обрывает ZIP: код обращения сохраняется,
   контекст и lease освобождаются. Задачи 4, 7, 9, 14.
3. Рестарт и скачок часов во время сессии: нет самовозобновления/продления,
   stale frontend control перестаёт действовать. Задачи 3, 6, 14.
4. Disk-full совпадает со сбоем БД и аудита: бизнес-операции не падают из-за recorder,
   stop/expiry выполняются, неполнота заметна. Задачи 2, 3, 8, 12, 14.
5. Скачивание пересекается с TTL, ротацией, ручным удалением и отзывом роли:
   нет выдачи чужих путей, повторной выдачи после отзыва или утечки leases.
   Задачи 2, 8, 9, 14.

## Карта файлов и границы ответственности

Все пути относительно корня репозитория. Новые имена — целевая структура.

| Файлы | Ответственность |
|---|---|
| `backend/app/services/diagnostics/schema.py`, `sanitize.py` | Типы событий, allowlists, безопасный стек |
| `.../context.py`, `.../middleware.py` | Request/operation context и pure ASGI |
| `.../store.py`, `.../recorder.py` | Сегменты, budgets/leases, bounded queue |
| `.../sessions.py`, `.../control.py` | TTL, activation, recovery, deferred control audit |
| `.../snapshot.py`, `.../bundle.py`, `.../bundle_queue.py` | Snapshot, ZIP, отдельная очередь |
| `backend/app/models/diagnostics.py`, `backend/app/api/diagnostics.py` | DTO и admin API |
| `backend/app/api/diagnostic_client.py` | Opt-in браузера и ограниченный ingest |
| `backend/app/db/models.py`, новая Alembic migration | Метаданные session/bundle |
| `backend/app/main.py`, `config.py`, `services/audit.py`, `error_codes.py` | Lifecycle и общие контракты |
| `pipeline.py`, `llm_client.py`, `vector_store.py`, `embedder.py`, `health.py` | Явные события предметных операций |
| `backend/app/api/chat.py`, `search.py`, `documents.py` | Инициализация контекста операций |
| `frontend/src/lib/diagnosticSchema.mjs`, `diagnosticServer.mjs` | Безопасные Node-события, spool |
| `frontend/src/instrumentation.js`, `lib/requestContext.mjs` | Next server hook и контекст |
| `frontend/src/app/api/[...path]/route.js`, `lib/backendFetch.js` | Корреляция/proxy failures без буферизации |
| `frontend/src/lib/diagnostics.mjs`, `diagnosticClient.mjs`, `lib/api.js` | UI-контракты, клиентские события, error ID |
| `frontend/src/components/DiagnosticsPanel.jsx`, `DiagnosticClientReporter.jsx` | Админский сценарий и opt-in reporter |
| `frontend/src/app/admin/diagnostics/page.js`, `components/AdminPanel.jsx` | Страница и вход из админки |
| `frontend/src/components/ErrorReference.jsx`, `Toast.jsx`, `ChatPanel.jsx` | Отображение/копирование кода обращения |
| `frontend/src/app/error.js`, `global-error.js`, `layout.js` | Error boundaries и подключение reporter |
| `backend/scripts/collect_diagnostics.py`, `scripts/production/collect-diagnostics.sh` | Offline сбор без приложения |
| `docker-compose.yml`, Dockerfile/entrypoint, production env samples | Volumes, ownership, ротация, build identity |
| `docs/ADMIN_DIAGNOSTICS.md`, `docs/PRODUCTION_DEPLOYMENT.md`, `SECURITY.md` | Runbook, ввод и откат |

Не извлекать прежний ExportQueue в общий framework: брать проверенные приёмы,
но новая очередь владеет только diagnostic_bundles. Не расширять generic JobQueue.

## Общий цикл исполнения и команды

Для каждой задачи: написать указанные тесты → получить ожидаемый RED по новому
контракту → реализовать → GREEN → локальный diff/review. Ошибка окружения не RED.
Ни один checkbox не отмечается по одному лишь наличию кода.

Backend-команды выполняются из `backend/` текущим проектным Python:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_diagnostics_schema.py -q
.\.venv\Scripts\python.exe -m ruff check app/services/diagnostics
```

В задачах ниже `python` означает этот интерпретатор; на Linux — Python окружения
CI. Не запускать несколько pytest с одним basetemp одновременно. Для изолированного
прогона задавать `--basetemp=../tests/tmp/pytest-runs/diagnostics-<task>`.

Frontend-команды из `frontend/`:

```powershell
node --test test/diagnostics.test.mjs
node node_modules/eslint/bin/eslint.js .
node node_modules/next/dist/bin/next build
```

CI использует рабочие npm scripts; локально прямой Node. До изменений Next
прочитать соответствующие installed docs в `node_modules/next/dist/docs/`.

## Task 1 — Безопасный формат, настройки и общий корпус тестовых данных

**Files:** создать `services/diagnostics/{__init__,schema,sanitize}.py`,
`backend/tests/test_diagnostics_schema.py`, `tests/fixtures/diagnostics/events-v1.json`;
изменить `backend/app/config.py`, `backend/tests/test_settings.py`.

**Interfaces:** `DiagnosticContext(request_id: str|None, operation_id: str|None,
doc_id: str|None, generation_id: str|None)` — immutable dataclass.
`sanitize_event(raw: Mapping[str, object]) -> dict | None`;
`safe_exception(exc: BaseException) -> dict`;
`encode_event(event: Mapping[str, object]) -> bytes` — JSONL UTF-8 <=8192 bytes.
Типы scope/filter и список event codes живут в schema.py; return None значит
отклонённое событие, recorder увеличивает счётчик.

- [ ] Написать `test_secret_values_never_reach_encoded_event`: подставить в message,
  nested args, exception cause, URL, filename, SQL parameters и locals разные canary;
  `assert all(secret not in output for secret in CANARIES)`; разрешённый error_code
  и repo-relative frame должны сохраниться.
- [ ] Добавить `test_unknown_fields_and_event_codes_rejected`,
  `test_utf8_size_limit_and_stack_cap`, `test_cycles_and_broken_repr_are_safe`:
  сообщение/`repr` не вызываются, `len(encoded)<=8192`, `len(frames)<=32`.
- [ ] Выполнить `python -m pytest tests/test_diagnostics_schema.py -q`, подтвердить RED.
- [ ] Реализовать allowlist по event_code, безопасный сериализатор и validated settings:
  `diagnostics_baseline_enabled=True`, `diagnostics_capture_enabled=False`,
  `diagnostics_bundle_enabled=False`, `diagnostics_download_enabled=False` до приёмки;
  root, quotas, TTL, reserve, queues и rate limits по Global Constraints.
  Не экспортировать эти настройки через общий публичный settings автоматически.
- [ ] Проверить противоречивую конфигурацию: backend+frontend != total,
  session > backend budget, segment > base budget, negative TTL — validation error.
- [ ] GREEN: schema + settings тесты; fixture не содержит реальных данных/секретов.

## Task 2 — Recorder, ротация, квота и независимость от БД

**Files:** создать `.../store.py`, `.../recorder.py`,
`backend/tests/test_diagnostics_store.py`, `test_diagnostics_recorder.py`.

**Interfaces:** `DiagnosticStore(root: Path, limits: DiagnosticLimits)`;
`reserve(bytes_required: int) -> Reservation` (`release()` идемпотентен);
`append(encoded: bytes, *, stream: str) -> bool`;
`snapshot(filter: EventFilter, cutoff_at: datetime) -> SnapshotLease`
(`paths`, `counts`, `release()`); `sweep(now: datetime) -> CleanupResult`.
`emit_event(event_code: str, *, context: DiagnosticContext|None=None,
exception: BaseException|None=None, fields: Mapping[str, object]|None=None) -> bool`.
`DiagnosticRecorder.start()/stop(timeout_seconds=5)` и `status() -> RecorderStatus`.

- [ ] RED-тесты `test_rotate_before_segment_limit`, `test_reservations_count_together`,
  `test_snapshot_copies_count_against_quota`, `test_queue_overflow_never_blocks`:
  на уменьшенных тестовых budgets `actual_bytes + reserved_bytes <= budget`,
  append при переполнении возвращает False и повышает dropped count.
- [ ] Добавить `test_disk_full_and_permission_error_do_not_escape`,
  `test_database_is_not_required`, `test_second_writer_refused`,
  `test_symlink_junction_and_path_escape_rejected`, `test_partial_last_line_ignored`.
- [ ] Выполнить `python -m pytest tests/test_diagnostics_store.py tests/test_diagnostics_recorder.py -q`.
- [ ] Реализовать один writer, bounded queue, byte accounting до записи,
  ротацию по размеру/возрасту, короткие locks, нет рекурсивного logging из handler.
  Writer lock кроссплатформенный, освобождается ОС при аварии; не делать вывод
  «stale» только из наличия файла. Для Windows использовать stdlib msvcrt,
  для Unix fcntl через маленький адаптер.
- [ ] Подключить безопасный handler существующих WARN/ERROR без `getMessage()`;
  дедуп fingerprint вычислять из code/frames/context, не из содержимого сообщения.
- [ ] Writer повторно проверяет deadline/scope перед записью detailed queue:
  события, оставшиеся в очереди после stop/expiry, не продлевают запись сессии;
  потери отражаются отдельным счётчиком, baseline error при этом допустим.
- [ ] GREEN; доказать возврат основного запроса при отказе записи и отсутствие
  захвата DB connections recorder-ом. Cleanup никогда не выходит за owned subtree.

## Task 3 — Метаданные, сессии, серверный таймер и аудит

**Files:** создать `.../sessions.py`, `.../control.py`,
`backend/app/models/diagnostics.py`, `backend/tests/test_diagnostics_sessions.py`,
`test_diagnostics_migration.py`; изменить `db/models.py`, `services/audit.py`,
`tests/test_audit.py`; создать Alembic revision `*_diagnostics_sessions_bundles.py`.
Точный revision ID/down_revision определить по `alembic heads` при исполнении,
не копировать сегодняшний head и не изменять существующие миграции.

**Interfaces:** `DiagnosticSessionService.start(scope: CaptureScope, minutes: int,
actor: User) -> SessionOut`; `stop(session_id: UUID, actor: User|None,
reason: StopReason) -> SessionOut`; `active_for(context, event_code) -> UUID|None`;
`recover(boot_id: UUID) -> RecoveryResult`; `tick(now_utc, now_monotonic) -> None`.
`write_control_event(event: ControlEvent) -> None` и `reconcile_control_events()`.
ControlEvent: UUID event_id, target UUID, action enum, reason enum, UTC;
actor metadata хранится отдельно от экспортируемого потока.

- [ ] RED: `test_start_requires_committed_audit`, `test_concurrent_start_only_one_wins`,
  `test_stop_is_idempotent`, `test_monotonic_deadline_survives_wall_clock_change`.
  Assertions: одна active, вторая заявка conflict; при audit failure ноль captured
  detailed events; после 900 секунд active_for возвращает None.
- [ ] RED: `test_restart_never_resumes_capture`, `test_expiry_with_database_down`,
  `test_deferred_audit_replay_is_idempotent`, `test_stop_with_full_disk_marks_gap`.
- [ ] Добавить обе таблицы с перечисленными в spec колонками; active_slot nullable
  unique (`1` только у starting/active) освобождается транзакционно. JSON-поля
  bounded; индекс по status/created_at/expires_at; metadata pruning через 7 суток.
- [ ] Реализовать SQL+audit commit → activation; на сбое filesystem/control
  остановить starting. Control journal ограничить 1 МиБ; receipts хранить в target
  metadata в той же транзакции, что audit. Отложенный terminal event сохраняется
  до reconciliation; строка не удаляется раньше него.
- [ ] Control projection для Next.js — отдельный безопасный JSON: boot_id,
  session_id, scope без doc_id для interface/system, deadline, lease_until.
  Обновлять каждые 5 секунд, lease 10 секунд; Node прекращает detailed capture
  при истечении lease даже если старый deadline ещё в будущем. До DB recovery
  на старте записать disabled projection. Session timer проверять также при emit.
- [ ] GREEN на SQLite и отдельной PostgreSQL test DB: upgrade, repeat upgrade,
  rollback только тестовой схемы, восстановление metadata без файлов. Основную
  dev/production БД миграциями теста не трогать.

## Task 4 — Корреляция HTTP, streaming и фоновых операций

**Files:** создать `.../context.py`, `.../middleware.py`,
`backend/tests/test_diagnostics_context.py`; изменить `main.py`,
`api/{documents,chat,search}.py`, `services/{pipeline,job_queue,export_queue}.py`.

**Interfaces:** `bind_context(context: DiagnosticContext) -> ContextManager`;
`current_context() -> DiagnosticContext`;
`new_operation(parent: DiagnosticContext, *, doc_id=None, generation_id=None) -> DiagnosticContext`;
`DiagnosticContextMiddleware(app)` — pure ASGI.

- [ ] RED: `test_parallel_requests_and_threads_do_not_share_context`,
  `test_uuid_header_validation`, `test_context_reset_after_cancellation`.
  Два одновременных документа имеют разные operation IDs и правильные doc IDs.
- [ ] RED: `test_stream_error_after_200_has_request_id`,
  `test_upload_and_zip_not_buffered`, `test_403_422_500_have_error_reference`.
  Не читать body для диагностики; NDJSON error event содержит request_id.
- [ ] Реализовать внешний ASGI context, безопасные route templates вместо raw
  URL, status/duration/exception; auth/CSRF failures покрываются тем же ID.
  Не писать один exception дважды из middleware и handler.
- [ ] Передавать immutable context в thread/task при постановке, не читать
  ContextVar случайного рабочего потока. Resume создаёт новую операцию;
  существующая generation identity используется, её семантика не меняется.
- [ ] GREEN: `python -m pytest tests/test_diagnostics_context.py tests/test_chat_stream.py -q`;
  документировать связь parent request → operation → generation/chunk.

## Task 5 — События пайплайна и устранение утечки через сырые LLM-дампы

**Files:** изменить `services/{pipeline,llm_client,vector_store,embedder,health}.py`,
`api/{chat,search}.py`, `config.py`; создать `backend/tests/test_diagnostics_pipeline.py`,
`test_diagnostics_privacy.py`; дополнить `tests/test_local_llm.py`.

**Interfaces:** только `emit_event` и DiagnosticContext предыдущих задач.
Event codes: `operation_started`, `stage_started`, `stage_finished`,
`dependency_call_finished`, `retry_scheduled`, `operation_failed`,
`operation_finished`, `dependency_status_changed`, `llm_parse_failed`.

- [ ] RED: на искусственном документе воспроизвести parse → generate → index →
  publish; проверить operation/generation/chunk IDs, длительность и счётчики,
  отсутствие исходного текста и filename во всех событиях.
- [ ] RED: `test_production_never_writes_llm_raw_dump`,
  `test_llm_parse_exception_has_no_response_prefix`,
  `test_document_scope_excludes_other_document`,
  `test_capture_started_mid_operation_records_only_new_events`.
- [ ] Запретить `_dump_debug_response` в production и убрать `{text[:200]!r}`
  из исключения. Development opt-in по умолчанию False, raw TTL 24h и quota
  20 МиБ отдельно; лимиты и механизм housekeeping покрыть отдельным тестом.
- [ ] Добавить structured hooks на границах вызовов/этапов без изменения retries,
  prompt, timeout, публикации, recovery и классификации existing problem codes.
  Provider status код классифицировать, его body/message никогда не передавать.
- [ ] GREEN: `python -m pytest tests/test_diagnostics_pipeline.py tests/test_diagnostics_privacy.py tests/test_local_llm.py tests/test_health_readiness.py -q`;
  регрессии pipeline запускать вместе с задачей 14 после интеграции.

## Task 6 — Next.js spool и безопасный серверный сбор

**Files:** создать `frontend/src/lib/{diagnosticSchema,diagnosticServer,requestContext}.mjs`,
`frontend/src/instrumentation.js`, `frontend/test/diagnosticServer.test.mjs`;
изменить `frontend/src/app/api/[...path]/route.js`, `lib/backendFetch.js`,
`frontend/src/proxy.js`, `frontend/test/api-proxy.test.js`.

**Interfaces:** `sanitizeServerEvent(raw) -> object|null`,
`emitServerEvent(eventCode, fields={}) -> boolean`,
`withRequestContext(context, callback) -> Promise`, `currentRequestId() -> string|null`.
Node import файлового recorder разрешён только в nodejs runtime;
instrumentation hook не сериализует err/request/context целиком.

- [ ] RED: общий events-v1 fixture одинаково отвергает секреты в Python и Node;
  `test_node_spool_rotates_at_20_mib`, `test_stale_control_lease_stops_capture`,
  `test_backend_down_still_records_proxy_failure`, `test_writer_failure_is_nonfatal`.
- [ ] Proxy генерирует UUID вместо внешнего ID; SSR proxy передаёт ID в request
  headers, backendFetch его наследует. При fetch rejection вернуть безопасный
  JSON 502/504 с code/request_id и записать техническое событие, без upstream URL.
- [ ] Сохранить passthrough upload/ZIP/NDJSON, AbortSignal, no-store и все Set-Cookie;
  отличать клиентский abort от ошибки зависимости. Успешные status/duration
  писать только при живой interface/system control lease.
- [ ] Node spool: один writer/lock, queue 256, cap 20 МиБ, TTL 7 суток, segment
  5 МиБ, ограничение скорости повторов; нельзя переподключить вывод всего console.
  В Node без native lock dependency использовать exclusive writer-marker и
  owner token; при неоднозначном владельце fail-safe без записи. Снимать stale
  marker только отдельным init/recovery шагом после доказанной остановки прежнего
  Node-сервиса (в Compose через pre-start init; в dev через контролируемый launcher).
  Не снимать marker по одному TTL. Проверить crash/restart и второй writer в тесте.
- [ ] GREEN: `node --test test/diagnosticServer.test.mjs test/api-proxy.test.js`;
  production build не включает server FS-модуль в client chunk.

## Task 7 — Opt-in браузера и код обращения в пользовательских ошибках

**Files:** создать `backend/app/api/diagnostic_client.py`,
`backend/tests/test_diagnostic_client_api.py`,
`frontend/src/lib/diagnosticClient.mjs`, `components/DiagnosticClientReporter.jsx`,
`components/ErrorReference.jsx`, `app/error.js`, `app/global-error.js`,
`frontend/test/diagnosticClient.test.mjs`; изменить `api.js`, `chatStream.mjs`,
`Toast.jsx`, `ChatPanel.jsx`, `layout.js`, `models/diagnostics.py`, `sessions.py`.

**Interfaces:** `ApiError.requestId: string|null`;
`attachDiagnosticBrowser(code: str, user: User) -> {expires_at, participation_id}`;
`leaveDiagnosticBrowser(user: User) -> None` — идемпотентно;
`ingest_client_event(event: ClientEvent, user: User) -> None`.
ClientEvent: fixed code, UUID event/request IDs, approved route key, build ID,
safe asset coordinates. Server заполняет component/origin и время получения.

- [ ] RED: `test_reader_can_report_but_cannot_read_diagnostics`,
  `test_expired_or_missing_optin_rejected`, `test_client_cannot_forge_server_fields`,
  `test_client_body_and_rate_limits`. Anonymous — 401; no opt-in — 403;
  >4096 bytes — 413; rate exceed — 429 с Retry-After.
- [ ] Присоединение только к interface/system-сессии: admin включает «Мой браузер»;
  для другого вошедшего пользователя одноразовый 128-bit random invitation,
  срок <= срока сессии, в БД только hash. Invitation показывается один раз,
  не попадает в URL/query/logs; ввод через POST с CSRF. Участие связано с user ID
  серверной сессии, имеет expiry; права на чтение не добавляются.
- [ ] Ограничить число участников 10/сессию и попыток join 5/мин на пользователя;
  отчёты 10/мин/user, 100/мин total. Не хранить user ID в exported events.
  Хранить invites/participants в bounded session JSON с транзакционной блокировкой;
  stop удаляет все memberships, leave удаляет только собственное участие.
- [ ] Reporter подключать после hydration и opt-in; handlers error/rejection
  превращают вход в fixed fields, не отправляют message/DOM/console. Memory queue
  максимум 20 событий/80 КиБ; никакой записи в localStorage; при истечении удалить.
- [ ] HTTP header/body и NDJSON error ID довести до ErrorReference с кнопкой
  копирования. Для сетевой ошибки без server response не выдумывать серверный ID:
  показывать локальный report ID с явной подписью. Не менять friendly error text.
- [ ] GREEN: `python -m pytest tests/test_diagnostic_client_api.py tests/test_chat_stream.py -q`;
  `node --test test/diagnosticClient.test.mjs`; error boundaries сохраняют CSP,
  корректный html/body у global-error и не отправляют повторно ошибку ingest.

## Task 8 — Снимок, безопасный ZIP и отдельный worker

**Files:** создать `.../{snapshot,bundle,bundle_queue}.py`,
`backend/tests/test_diagnostics_bundle.py`, `test_diagnostics_bundle_queue.py`.

**Interfaces:** `collect_snapshot(request: BundleRequest, *, cutoff_at: datetime)
-> BundleSnapshot`; `build_bundle(snapshot: BundleSnapshot, destination: Path,
limits: DiagnosticLimits) -> BuiltBundle`; `DiagnosticBundleQueue.submit(request,
actor) -> BundleOut`, `recover()`, `shutdown(timeout_seconds=5)`.
BundleRequest: `session_id?` XOR `from_utc/to_utc`; optional request/operation/doc IDs.
BuiltBundle: filename key, size_bytes, sha256, manifest; no arbitrary path in API.

- [ ] RED: `test_bundle_manifest_and_crc`, `test_safe_snapshot_when_db_unavailable`,
  `test_unknown_and_corrupt_lines_are_counted`, `test_cutoff_freezes_bundle`,
  `test_incompressible_input_reservation` и `test_restart_does_not_publish_partial`.
- [ ] Получать только whitelisted metadata операций, состояние зависимостей из
  существующего health cache/ограниченного probe. Никаких LLM completions,
  ensure_chunks, новых document transformations или raw errors в snapshot.
- [ ] Построить строго фиксированный состав ZIP из spec. Streams валидировать
  повторно; имена ZIP entry статические; не обходить data/debug или uploads.
  Готовить DEFLATE level1 по блокам, ограничивать и вход, и фактический выход.
- [ ] Admission резервирует полную оценку snapshot+ZIP+metadata; одновременно
  максимум 1 building и 2 queued, durable rate limit 3/hour/admin. Commit audit
  requested до постановки; publish ready + audit только после проверки ZIP.
- [ ] Ошибка компонента даёт partial manifest, превышение размера — failed с
  `diagnostic_bundle_too_large`. Rename без ready DB state не открывает download.
  Recovery удаляет/карантинирует orphan temporary в пределах budgets, не пытается
  повторно выполнить пользовательскую операцию.
- [ ] GREEN: `python -m pytest tests/test_diagnostics_bundle.py tests/test_diagnostics_bundle_queue.py -q`;
  утверждения `zip.testzip() is None`, no canaries, immutable cutoff, no >200 МиБ ZIP.

## Task 9 — Admin API, выдача и leases

**Files:** создать `backend/app/api/diagnostics.py`,
`backend/tests/test_diagnostics_api.py`; изменить `models/diagnostics.py`,
`error_codes.py`, `main.py`, `.../bundle_queue.py`.

**Interfaces / endpoints:**

| Method/path под `/api` | Контракт |
|---|---|
| GET `/admin/diagnostics/status` | capabilities, recorder status, quota, session; без текстов событий |
| POST `/admin/diagnostics/events/query` | filters, bounded cursor, audited view; до 100 событий |
| POST `/admin/diagnostics/sessions` | scope/minutes; 201, 409 если active |
| POST `/admin/diagnostics/sessions/{id}/stop` | идемпотентный stop |
| POST `/admin/diagnostics/sessions/{id}/invite` | одноразовый код, admin + audit |
| POST `/admin/diagnostics/bundles` | 202 BundleOut после audit/admission |
| GET `/admin/diagnostics/bundles` | страницы безопасных metadata |
| POST `/admin/diagnostics/bundles/{id}/preview` | audited manifest без raw log |
| GET `/admin/diagnostics/bundles/{id}/download` | audited FileResponse/no-store |
| DELETE `/admin/diagnostics/bundles/{id}` | 202 logical delete, физическое после lease |
| POST `/diagnostic-client/join` | authenticated opt-in, CSRF |
| POST `/diagnostic-client/leave` | authenticated идемпотентный opt-out, CSRF |
| POST `/diagnostic-client/events` | authenticated bounded ingest, CSRF, 204 |

`acquire_download(bundle_id: UUID, actor: User) -> DownloadLease(path, filename, release)`.
Invitation/revocation audit добавить как `diagnostic_browser_invited/joined/left`
без самого кода; эти действия дополняют базовый перечень spec.

- [ ] RED role matrix: anonymous 401; reader/editor/security 403; admin allowed;
  сброс admin-роли между preview и GET download -> 403. Каждый запрос проверяет
  текущую роль, URL/UUID не является capability.
- [ ] RED: bad CSRF ->403, malformed UUID/filter ->422, audit down ->503,
  not-ready ->409, expired/deleted ->410, quota ->507, throttled ->429.
  Коды `diagnostic_*` добавляются в общий словарь; messages не содержат paths.
- [ ] Реализовать leases и finally/background release при success/disconnect/error;
  sweep/DELETE помечают unavailable сразу, сохраняют открытый файл до release.
  Активная выдача максимум 10 минут; после TTL новый запрос (включая Range) запрещён.
  Для v1 отключить Range download, не обещать возобновление ZIP по частям.
- [ ] Отзыв роли блокирует новые запросы; уже отправленные байты не отозвать.
  Уже авторизованный stream заканчивается или timeout через 10 минут. Эту границу
  явно документировать; не заявлять мгновенный отзыв текущего stream.
- [ ] Query/preview — POST+CSRF, аудит один на действие, не на status poll.
  GET download пишет событие именно начала выдачи; Content-Disposition фиксирован,
  Cache-Control no-store и nosniff; не открывать static URL к директории.
- [ ] GREEN: `python -m pytest tests/test_diagnostics_api.py tests/test_authz.py tests/test_audit.py -q`;
  pending audit не замалчивается в status; disabled routes дают стабильный код.

## Task 10 — Lifespan, recovery, ранние ошибки и управление flags

**Files:** изменить `backend/app/main.py`, создать
`backend/app/diagnostic_entrypoint.py`, `backend/tests/test_diagnostics_lifecycle.py`;
изменить `backend/Dockerfile`, `scripts/start-all.ps1`, `scripts/start-all.sh`.

**Interfaces:** `initialize_diagnostics() -> DiagnosticRuntime`;
`DiagnosticRuntime.start()/stop()`; `diagnostic_entrypoint.main(argv: list[str]) -> int`.
Entry wrapper использует recorder без DB и запускает uvicorn с прежними аргументами.

- [ ] RED: приложение стартует при недоступной diagnostics dir; ранняя ошибка
  импорта/инициализации создаёт безопасный lifecycle event, если recorder доступен;
  второй lifespan не создаёт дубликат worker; shutdown bounded <=5 секунд.
- [ ] Инициализировать минимальный recorder перед импортом app.main в новом
  entrypoint; boot ID общий с lifespan. Обычный импорт app для unit tests остаётся
  возможным; никакие потоки/DB connect не запускаются на импорте schema/sanitize.
- [ ] Порядок lifespan: recorder → disabled control projection → DB/schema gate →
  sessions/bundles recovery → maintenance/worker. Shutdown: запрет admission →
  stop capture → drain bounded queues → release locks. Не задерживать бесконечно exit.
- [ ] Flags разделены: capture/bundle/download могут быть выключены независимо;
  baseline off прекращает recording, но не cleanup. Settings read через существующий
  механизм; изменение env вступает после управляемого restart, UI stop мгновенный.
- [ ] Сохранить startup scripts healthchecks, ownership PID/port и host-порты;
  проверить распознавание своего процесса после смены backend command line.
- [ ] GREEN: `python -m pytest tests/test_diagnostics_lifecycle.py tests/test_health_readiness.py -q`;
  crash recovery не затрагивает существующие очереди/генерации.

## Task 11 — Админский UI, локализация и browser acceptance

**Files:** создать `frontend/src/app/admin/diagnostics/page.js`,
`components/DiagnosticsPanel.jsx`, `lib/diagnostics.mjs`, `test/diagnostics.test.mjs`;
изменить `AdminPanel.jsx`, `api.js`, `globals.css`, locales ru/en,
`backend/app/i18n/ui_keys.json`, `ui_en.json`.

**Interfaces:** API helpers `getDiagnosticsStatus`, `queryDiagnosticEvents`,
`startDiagnosticSession`, `stopDiagnosticSession`, `createDiagnosticBundle`,
`listDiagnosticBundles`, `previewDiagnosticBundle`, `deleteDiagnosticBundle`.
Pure `diagnosticActions(status)`, `remainingSeconds(expiresAt, serverNow, elapsedMs)`
и `diagnosticStatusKey(code)` в diagnostics.mjs.

- [ ] RED Node tests: stopped/expired/degraded/unknown capabilities; countdown
  по server time, stale status не выглядит stopped; request failure не стирает
  текущий выбор scope; недоступный download flag скрывает ссылку.
- [ ] Страница RequireRole(admin), ссылка из AdminPanel. Блоки: статус/место;
  область+время+start/stop; выбор периода/кода; последние ошибки по кнопке обновления;
  пакеты с состоянием, составом, размером, сроком, download/delete.
- [ ] Poll status каждые 3 секунды только при видимой вкладке; список пакетов
  обновлять при pending; audit query не запускать автоматически по таймеру.
  Скачивание через обычную ссылку, без `fetch().blob()` на весь ZIP.
- [ ] Точные тексты: «Сбор остановится автоматически», «Данные собраны частично»,
  «Содержимое документов и переписки не включается», «Сбор недоступен: мало места»,
  «Журнал не содержит событий за часть выбранного периода». Английские эквиваленты.
- [ ] `node scripts/export-ui-keys.mjs`, затем
  `node --test test/diagnostics.test.mjs test/diagnosticClient.test.mjs test/i18n.test.mjs`.
- [ ] Пройти RU/EN, клавиатуру/focus, desktop/narrow viewport, SSR hydration,
  истечение сессии без открытой вкладки, две админские вкладки, ошибку download.
  Для browser acceptance использовать изолированные данные и известную авторизацию;
  отсутствие SSO-сессии не считать проверкой защищённого сценария.

## Task 12 — Offline CLI и аварийный пакет

**Files:** создать `backend/scripts/collect_diagnostics.py`,
`scripts/production/collect-diagnostics.sh`,
`backend/tests/test_collect_diagnostics.py`,
`scripts/production/tests/test_collect_diagnostics.sh`.

**Interfaces:** CLI `--root PATH --since ISO_UTC --until ISO_UTC --output PATH`
и `--dry-run`; output создаётся эксклюзивно. Возврат 0 — пакет создан, 2 —
невалидный ввод, 3 — writer active/нет доступа, 4 — quota/build failure.
Wrapper принимает явные runtime env file и Compose project; не использует корневой
`.env` вместо production env. Для `--help` и dry-run приложение не импортируется.

- [ ] RED: `test_cli_works_without_database_and_app_import`,
  `test_cli_refuses_live_backend_writer`, `test_cli_never_reads_env_or_raw_logs`,
  `test_output_exclusive_and_quota_checked`, `test_invalid_component_is_partial`.
- [ ] Offline snapshot/build использует те же schema и bundle функции, без
  session_scope/require_user/pipeline. OS-доступ — полномочие эксплуатационного
  администратора; export action оставляет control event в safe local journal,
  manifest указывает `collection_mode=offline` и статус audit reconciliation.
- [ ] Wrapper не останавливает стек автоматически. Если backend работает —
  направить оператора в UI/API; если остановлен — одноразовый контейнер той же
  версии без зависимостей, mounted safe roots, не запускающий entrypoint приложения.
- [ ] Получать только отдельные поля Docker state/image через format/JSON allowlist;
  не сохранять полный inspect/logs/config. Выход CLI и временные файлы тоже
  резервируются; свободное место проверяется и на output filesystem.
- [ ] GREEN: `python -m pytest tests/test_collect_diagnostics.py -q`;
  Linux `bash scripts/production/tests/test_collect_diagnostics.sh` с fixture Docker;
  отдельный реальный drill без БД выполняется в задаче 14.

## Task 13 — Deployment, права каталогов, документация и откат

**Files:** изменить `docker-compose.yml`,
`deploy/production/{bundled,external}.env.example`, `backend/docker-entrypoint.sh`,
`frontend/Dockerfile`, `.gitignore`, `SECURITY.md`, `docs/PRODUCTION_DEPLOYMENT.md`;
создать `docs/ADMIN_DIAGNOSTICS.md`, `backend/scripts/prepare_diagnostics_dirs.py`,
`backend/tests/test_diagnostics_deployment.py`.

- [ ] RED: compose contract test для bundled/external — нет Docker socket,
  новых host ports, DB credentials/frontend DATA_DIR; frontend не читает
  backend spool или bundles; backend frontend mount read-only.
- [ ] Добавить `OKF_DIAGNOSTICS_DIR` вне `OKF_DATA_DIR`. Bootstrap каталогов —
  явный шаг migrate/init до сервисов: backend subtree принадлежит APP_UID/GID;
  frontend subtree uid 1000 с общим APP_GID, mode 2770, файлы 0660;
  frontend supplementary group APP_GID. Control subtree backend монтируется
  frontend read-only. Процессы после init непривилегированные.
- [ ] Init отказывается следовать symlink/reparse и не делает recursive chown
  произвольного корня. На Windows проверить inherited ACL для выбранного каталога;
  отсутствие безопасного доступа — setup error, не попытка открыть каталог всем.
- [ ] Добавить build version/revision в обе сборки из CI аргументов; неизвестную
  локальную версию помечать `unknown`, не придумывать SHA. Backend schema version
  и frontend build ID должны попасть в manifest даже при смешанном релизе.
- [ ] Ротация json-file 10m/3 для backend/frontend/postgres/qdrant/migrate;
  отдельно объяснить её бюджет. Проверить отсутствие diagnostics в backup data.tar,
  а восстановленные SQL metadata без файлов дают gone/failed и stop active.
- [ ] Runbook: UI-воспроизведение, смысл coverage, ручная передача, TTL, reserve,
  offline сбор, отличие аудита, отсутствие logs внешних сервисов, известные пределы
  OOM/раннего crash/browser offline, разрешения, обращение со старыми raw dumps.
- [ ] Откат: сначала capture/bundle/download=False, stop; baseline по ситуации;
  cleanup оставить. Совместимые новые таблицы не удалять. Rollback image запрещён,
  если он вновь включает raw production LLM dump: использовать hotfix или сохранить
  исправление при откате. Downgrade миграции только на тестовом клоне/после решения
  владельца данных; пакетные файлы не удалять ради отката кода.
- [ ] SECURITY.md: механизм, boundary, негативный тест и реальные ссылки на отчёт;
  не описывать ещё не прошедшую проверку как выполненную.
- [ ] GREEN: deployment tests, Compose config и fixture backup/restore tests;
  diff не меняет порты/SSO redirects/политику внешней БД.

## Task 14 — Интеграционная, нагрузочная и релизная приёмка

**Files:** создать `backend/tests/test_diagnostics_integration.py`,
`backend/test_scripts/probe_diagnostics.py`,
`docs/superpowers/reports/2026-09-27-admin-diagnostics-acceptance.md`;
изменить `.github/workflows/ci.yml`. Measurements —
`tests/artifacts/diagnostics/`; временные логи/файлы — `tests/tmp/diagnostics/`.

- [ ] Синтетические canaries проходят через HTTP 500, LLM invalid JSON,
  SQL exception, chained exceptions, browser rejection, proxy failure и NDJSON
  error после 200. Проверить новые persisted files, SQL diagnostic metadata,
  API bodies и ZIP: отсутствие canaries, presence stable code/frames/IDs.
- [ ] Исследовать конфликты: concurrent start; capture stop vs append;
  snapshot vs rotation; delete/TTL vs download; disk-full mid-ZIP; DB down во время
  stop и ready audit; kill после rename до commit; frontend restart/backend down;
  corrupt line/unknown version; restored DB без spool; role revoke и CSRF.
- [ ] Error storm 10 000 событий/сек 60 секунд: память/диск bounded, drop counters
  видимы; бизнес health/search не зависают на recorder; writer не запускает
  рекурсивный storm. На уменьшенных квотах пройти каждую ветку eviction/rejection.
- [ ] На одном стенде снять 3 серии: baseline, capture, capture+build. Каждая —
  warmup 30 секунд и >=200 одинаковых API запросов; generation на stub LLM,
  отдельный реальный smoke без генерации confidential fixture наружу.
  p95 overhead <=10%/20%; RSS backend delta <=128 МиБ, Node <=32 МиБ;
  no new business 5xx, ZIP CRC и quotas проходят. Сохранить environment/build ID.
- [ ] Browser acceptance: admin start→reproduce→stop→preview→download→unzip;
  code reference matches; обычный пользователь join/report без read; RU/EN;
  backend down error в Node spool; после восстановления partial пакет;
  закрытая вкладка не продлевает срок; удалить во время скачивания.
- [ ] Linux: bundled и external Compose smoke, upgrade from previous schema,
  повторный startup/recovery, offline collection без PostgreSQL. Windows dev:
  пути с пробелом/кириллицей, file leases, rotation, junction rejection.
- [ ] CI: focused diagnostics suites входят в backend/frontend jobs; Linux Compose
  smoke для обеих топологий; небольшой Windows job для store/CLI/locks. Content
  checks писать через `git grep`, не предполагать `rg` на runner. Synthetic fixtures
  только; не публиковать реальные логи или исходники в CI artifacts.
- [ ] Полная регрессия после focused GREEN: backend `python -m ruff check .`,
  `python -m pytest tests/ -q`; frontend `node --test`, eslint, Next build;
  doc-parser tests при изменении parser boundary; existing audit/export/auth/health/
  generation suites обязательны. Каждый known failure зафиксировать отдельно;
  deselected suite не называть полной успешной проверкой.
- [ ] `git diff --check`, privacy inspection generated fixtures/artifacts, отчёт
  со свежими командами, counts, измерениями, ограничениями и rollback drill.
  Обновить SECURITY.md evidence только после реального результата.
- [ ] Включение на staging: baseline → capture → bundle/download; проверить
  обслуживание/очистку спустя TTL на ускоренных часах теста и реальный restart.
  Production-ввод отдельным действием владельца: migration backup, directory init,
  controlled service recreation, smoke admin/reader, проверка TTL и audit.

## Контроль покрытия и порядок

Зависимости: 1 → 2 → 3; 1–3 → 4–7; 2–5 → 8 → 9; 2–9 → 10–11;
8 → 12; 6/10/12 → 13; все → 14. Параллельная разработка допустима только после
фиксации общих schema/contracts и при явно выбранном способе исполнения.

| Требование | Задачи |
|---|---|
| Уже случившаяся ошибка и временный сбор | 1–6, 10–11 |
| Конфиденциальность до записи + при экспорте | 1, 5–8, 12, 14 |
| Scope/correlation/thread/NDJSON | 3–7 |
| TTL/quota/rotation/reserve и аварии | 2–3, 6, 8–10, 14 |
| RBAC/CSRF/audit/deferred stop | 3, 7, 9, 14 |
| UI, локализация, streaming download | 7, 9, 11 |
| Backend down / приложение не запускается | 6, 10, 12–14 |
| Deployment/backup/restore/rollback | 3, 10, 12–14 |

## Definition of Done

- [ ] Все 14 задач имеют проверенный результат и ссылки на evidence.
- [ ] Администратор получает понятный пакет без доступа разработчика к системе.
- [ ] Negative privacy/RBAC/disk-full/restart/streaming tests зелёные.
- [ ] No-content гарантия ограничена новым schema-based каналом; старые/raw логи
  не представлены как очищенные. Partial/missing evidence виден в manifest/UI.
- [ ] Производительность и дисковые пределы подтверждены измерениями.
- [ ] Runbook, SECURITY.md, миграция, release/rollback drill готовы.
- [ ] Текущие чужие изменения сохранены; deployment/commit/push не выполнены
  без отдельного поручения.

## Самопроверка плана

Проверены соответствие spec задачам, producer/consumer interfaces, границы
полномочий и failure paths. Все Review Focus привязаны к тестам. Точная ревизия
Alembic намеренно определяется по head на момент исполнения, поскольку сейчас
в рабочем дереве уже есть новая несвязанная миграция. Никакие тесты будущей
реализации этим документом не объявляются пройденными.
