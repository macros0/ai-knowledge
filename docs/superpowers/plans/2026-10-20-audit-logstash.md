# План реализации: актуальный main → необходимый аудит → Logstash

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Последовательное выполнение; старая Kubernetes-разработка не продолжается.

**Goal:** получить на актуальном main самостоятельный проверенный пакет
транзакционного audit/outbox и Logstash JSON в stdout.

**Architecture:** выборочно адаптировать ядро аудита из старого worktree к моделям,
сессиям, миграциям и зависимостям main. Сначала завершить native цепочку
операция → audit/outbox → stdout, затем подключить отдельный logger/formatter.
Доставка до OpenSearch принимается отдельно от локальной реализации.

**Tech Stack:** Python, SQLAlchemy/Alembic, SQLite/PostgreSQL, logging,
python-json-logger, pytest; узкие настройки существующего Helm chart.

**Spec:** [ТЗ, редакция 2](../specs/2026-10-20-audit-logstash-design.md).

20.10.2026: реализация выполняется в `codex/audit-logstash` от проверенного origin/main.
Основная БД/корпус и старый worktree сохранены; commit/push/deploy запрещены пользователем.
[Фактическая приёмка и OPEN](../reports/2026-10-20-audit-logstash-acceptance.md).

## Ход выполнения

- [x] Task 0: отдельная ветка от main, finite donor inventory и baseline 99 PASS.
- [x] Task 1: additive schema/catalog/store, fresh SQLite/PG и legacy upgrades.
- [x] Task 2: native поток, security transactions, Windows writer/cleanup, standalone CLI.
- [x] Task 3: Logstash formatter/strict logger, retries, actor/request context.
- [x] Task 4: Settings/Helm, subprocess proof, standalone runbook и RU/EN подписи auth action.
- [ ] Task 5: локальные проверки завершены, 4 full-run failures разобраны и повторены (64 PASS); Linux image/POSIX и домашний OpenSearch проверены; целевой OpenSearch OPEN.

Подробный checklist ниже отражает выполненные действия. Linux/target steps,
которые не выполнялись в доступной среде, оставлены открытыми.


## Global Constraints

- База — актуальный main. Старый worktree — источник отдельных участков кода.
- Не merge/cherry-pick старую Kubernetes-ветку целиком и не заменять общие файлы main.
- Не менять `scripts/kubernetes/lifecycle.py`, `postgres_tls.py`, `.superpowers/sdd/**`.
- Не продолжать private candidates, TLS/restore/admission/activation framework,
  source sealing, реплики и исторические очереди приёмки.
- Перенос audit runtime и additive migration входят в эту задачу.
- Текущие AuthSession, RBAC, CSRF, generation/reference editing, lock workflow
  и существующие domain action_type сохраняются.
- `AUDIT_STDOUT_MODE=disabled|durable`, default `disabled`.
- `AUDIT_STDOUT_FORMAT=native|logstash`, default `native`.
- `application="ai-knowledge"`, `logger_name="ai_knowledge.audit"`,
  `thread_name="audit-outbox"`, `@version="1"`, `audit_transport_version=1`.
- `@timestamp == timestamp`, `message == action`.
- success → INFO/20000; denied → WARN/30000; failure → ERROR/40000.
- Canonical payload <=16384 bytes; Logstash payload <=18432 bytes без framing.
- Framing: LF + JSON + LF; ошибка записи/formatter не даёт stdout_written.
- Нет backfill/replay legacy rows. Дедупликация downstream — по event_id.
- Только изолированные тестовые БД/DATA_DIR; основная .env/БД/корпус не используются.
- Каждый этап завершается указанным наблюдаемым результатом, а не новым helper-планом.

## Review Focus

1. Донор отстал от main: копирование моделей/auth целиком теряет новые функции (Tasks 0–2).
2. Старый каталог не содержит новых действий main: durable ломает штатные операции (Tasks 1–2).
3. DB rollback, marker failure и suppressed logging дают ложный успех (Tasks 1–3).
4. Migration/restore/cleanup могут потерять legacy данные или переиграть аудит (Tasks 1, 2, 5).
5. Локальный JSON PASS не подтверждает parsing/mapping/CRI в целевом индексе (Tasks 4–5).

## Карта переноса и файлов

Пути относительно нового execution checkout от main. Только перечисленные
назначения входят в работу; новые прямые зависимости обосновываются конкретной
ошибкой текущего сценария, а не пунктом старого плана.

| Файлы | Действие |
|---|---|
| `backend/app/services/audit_event.py`, `audit_catalog.py`, `audit_outbox.py` | Перенести/адаптировать ядро, каталог расширить до текущего ACTION_TYPES |
| `backend/app/services/audit.py` | Точечно расширить record_in_session и append; сохранить API main |
| `backend/app/db/models.py` | Добавить audit metadata и AuditOutbox, остальные модели сохранить |
| `backend/alembic/versions/<new_revision>_audit_outbox.py` | Новая additive revision от фактического head main; ID генерируется Alembic при реализации |
| `backend/app/services/audit_schema.py`, `audit_runtime.py`, `audit_metrics.py`, `audit_hold.py` | Узкие schema/preflight/status/счётчики/hold |
| `backend/app/services/audit_security.py` | Требуемые auth/denial события без смены OIDC-архитектуры |
| `backend/app/services/audit_denial_transport.py`, `audit_dns_probe.py`, `audit_file_materials.py`, `audit_file_probe.py` | Только необходимые прямые application-зависимости bounded denial; выбранный набор фиксируется в Task 0 |
| `backend/app/auth/service.py`, `auth/api.py`, `api/users.py`, `services/blocklist.py` | Точечные транзакционные audit hooks в текущем main |
| `backend/app/main.py`, `diagnostic_entrypoint.py`, `config.py` | Lifecycle writer, correlation/denial hooks, stderr routing, режим/формат |
| `backend/scripts/migrate_audit_schema.py`, `audit_delivery_status.py`, `audit_hold_restored_events.py` | Узкие standalone команды, без Kubernetes operator framework |
| `backend/app/services/audit_logstash.py` | Новый formatter, strict handler, private logger |
| `backend/requirements.txt`, `backend/requirements.lock.txt` | Workflow main; только необходимые dependency changes |
| `backend/tests/test_audit*.py`, `test_platform_audit_contract.py` | Выбранные audit tests, адаптированные к main, и новые тесты пакета |
| `backend/tests/fixtures/audit/logstash-v1.json` | Синтетический эталон JSON |
| `deploy/helm/ai-knowledge/values.yaml`, `values.schema.json`, `templates/config.yaml`, `templates/_helpers.tpl` | Только audit mode/format env/default/enum/защита от двойной настройки |
| `frontend/src/i18n/locales/ru.js`, `en.js`, `backend/app/i18n/ui_keys.json`, `ui_en.json` | Четыре подписи новых auth action и штатная генерация словарей (выявлено существующим тестом) |
| `.env.example`, `docs/AUDIT_DELIVERY.md`, `docs/AUDIT_EVENT_CATALOG.md` | Актуальная standalone инструкция и каталог, исторические результаты явно отделить |
| `docs/superpowers/reports/2026-10-20-audit-logstash-acceptance.md` | При реализации: baseline, конечный перечень переноса, результаты AC |

Текущие backend/auth/diagnostics tests являются регрессией. Старые Kubernetes/TLS
тесты не импортируются вместе с пакетом. `audit_event.py`/`audit_outbox.py` имеют
untracked-источник: git SHA донора не описывает их байты.

## Task 0: выбрать main и конечный состав переноса

**Вход:** актуальный main и read-only доступ к донору.
**Выход:** изолированная ветка от main, краткий перечень файлов/участков переноса
и исходный результат существующих tests main. Никакого ожидания old release.

- [x] Проверить AGENTS.md, main HEAD/status, удалённый main через fetch/read-only
  сравнение. При недоступном remote явно назвать используемый локальный SHA.
  Не reset/clean/rebase общий dirty checkout.
- [x] Создать или переиспользовать подходящий изолированный checkout на main,
  рабочая ветка с префиксом `codex/`. Не создавать ветку от Kubernetes-донора.
- [x] Записать в acceptance report SHA main и короткую таблицу переноса:
  источник, hash выбранного файла, нужные функции, адаптация к main, прямые
  зависимости. Это один inventory, не новый manifest/source-seal framework.
- [x] Проверить зависимости ядра и auth hooks. Выбрать только нужные audit helpers
  из карты; исключить OIDC provider rewrite, session recovery и Kubernetes code.
  Существующие helpers не копировать ради неиспользуемой возможности.
- [x] Статически сверить Alembic heads и ACTION_TYPES main. На проверенном локальном
  snapshot head `065b1c2d3e4f`; перед кодом перепроверить фактический main.
- [x] Запустить существующие tests main в test environment:

```powershell
# Из backend нового checkout
python -m pytest tests/test_audit.py tests/test_auth.py tests/test_auth_sso.py tests/test_authz.py -q
```

**Критерий:** известны база, фактические failures/skips и конечный состав переноса.
Не запускать старые 418 файлов, private582, restore/TLS матрицы. Уже известный
неотносящийся FAIL фиксируется отдельно; исправление чужой подсистемы не становится
автоматической задачей пакета.

## Task 1: schema, каталог и транзакционное audit/outbox ядро

**Files:** audit models/service/event/catalog/schema; новая Alembic revision;
`backend/scripts/migrate_audit_schema.py`; новые/адаптированные
`test_audit_schema.py`, `test_audit_event.py`, `test_audit_outbox_store.py`,
`test_audit_main_migration.py`.

**Interfaces:**

- `record_in_session(session, *, action_type, ...existing kwargs..., context=None)`:
  прежние параметры/возврат сохраняются; context — optional AuditContext.
- `build_event(entry, context: AuditContext) -> dict`, `encode_event(event: dict) -> bytes`.
- `AuditOutbox`: одна строка на audit_id, immutable payload, mutable delivery columns.
- `validate_audit_schema(connection) -> None`: точная проверка допустимой schema.

- [x] Написать тесты до переноса: legacy rows неизменны; metadata NULL; уникальный
  UUID новых событий; atomic audit+outbox commit, outer/savepoint rollback,
  ошибка INSERT/serialization не оставляет половину пары; disabled не создаёт outbox.
- [x] Добавить `test_catalog_covers_current_main_actions`: каталог равен набору
  ACTION_TYPES main плюс четырём выбранным auth actions; для каждого явный allowlist.
  Включить новые действия reference editing/обработки документов из актуального main.
- [x] Добавить проверки canonical limit/Unicode/NaN, запрещённых данных, неизвестного
  action, actor/target/outcome/request_id и безопасных old/new aggregates.
- [x] Запустить новые tests и зафиксировать предметный FAIL отсутствующего ядра.
- [x] Перенести ядро выборочно. Расширить существующий AuditLog и добавить AuditOutbox;
  не заменять models.py, config.py или audit.py старой версией целиком.
- [x] Создать новую revision от текущего head через Alembic. Не копировать donor
  revision/down_revision, не stamp существующую БД. Применять только к test DB.
- [x] Реализовать минимальный schema-repair для create_all drift: явный env,
  confirmed stopped writers, точная проверка исходной schema, отказ на unknown drift;
  никаких исправлений остальных таблиц/миграций.
- [x] Выполнить fresh/непустой upgrade на SQLite и disposable PostgreSQL;
  проверить старые строки/ID/время, новые таблицы main, одну голову Alembic,
  повторный запуск с уже установленной schema и отказ несовместимой schema.
- [x] Проверить сохранность текущего audit API и совместимость disabled; выполнить:

```powershell
python -m pytest tests/test_audit.py tests/test_audit_schema.py tests/test_audit_event.py tests/test_audit_outbox_store.py tests/test_audit_main_migration.py -q
```

**Критерий:** AC-11 и DB/catalog часть AC-12. Имеется реальная committed audit/outbox
пара на main; SQL migration является результатом этого этапа, не внешней предпосылкой.

## Task 2: закончить native цепочку на main

**Files:** audit_outbox/runtime/metrics/hold/security и выбранные helpers;
точечные hooks main/entrypoint/config/auth/users/blocklist; standalone status/hold
scripts; `test_audit_outbox_dispatcher.py`, `test_audit_process_faults.py`,
`test_platform_audit_contract.py`, `test_audit_http_contract.py`,
`test_audit_restore_contract.py`, `test_audit_operator_cli.py`,
`test_audit_main_entrypoint.py`.

**Interfaces:**

- `AuditDispatcher(settings, *, write_line=stdout_line, session_factory=None, clock=None)`;
  `start()`, `stop(timeout_seconds=5) -> bool`, `tick() -> int`, `status() -> dict`.
- `stdout_line(payload: bytes) -> None`: полный OS write с LF framing и исключением при сбое.
- Settings `audit_stdout_mode: Literal["disabled", "durable"] = "disabled"`.
- Status/hold используют текущий audit DB contract, не импортируют Kubernetes scripts.

- [x] Написать tests: только committed pending попадает в stdout; invalid payload
  held без блокирования остальных; short/zero/broken pipe/marker failure сохраняют
  retry; blocked sink не блокирует HTTP и не освобождает lock преждевременно.
- [x] Добавить auth tests: login/logout и block/unblock с audit+outbox в одной
  транзакции; audit failure откатывает mutation и не выдаёт cookie. Для остальных
  domain операций не заявлять атомарность шире существующих границ main.
- [x] Добавить denial tests: login/access/CSRF, не более одного события на запрос,
  исходные status/headers/redirect сохраняются, DB outage не открывает доступ.
  На supported faults проверить work <=4 s, cleanup <=0.5 s и HTTP <5 s.
  Не переносить OIDC providers/flow или создавать новый general executor.
- [x] Добавить тест server request UUID и его соответствия response/outbox;
  не доверять произвольному входящему X-Request-ID. Client IP остаётся null/unknown.
- [x] Добавить tests для startup при отсутствующей schema даже в disabled, прав
  writer, single-worker/OS lock, shutdown и повторного lifespan без дублирования.
- [x] Запустить новые tests, проверить RED, затем адаптировать donor участки к main.
  Main lifecycle должен явно preflight schema и start/stop dispatcher; не прятать
  ошибку обязательного preflight в существующем best-effort init_db.
- [x] В diagnostic_entrypoint настроить безопасный access/error stderr для durable;
  сохранить query/token redaction и нынешнюю диагностику main.
- [x] Перенести узкие status/hold CLI и проверить restore в новой test DB:
  pending становится held до первого writer start; UUID/time/payload неизменны,
  stdout_written/held не переигрываются. Никакого Kubernetes restore сценария.
- [x] Проверить существующий audit cleanup: новые pending/held не удаляются каскадом;
  новый retention/GC не добавлять. Для binary rollback документировать additive
  schema без downgrade и остановку writer/backup/hold/disabled.
- [x] Windows tests выполнены вместе с Task1; Linux/POSIX дополнительно проверены на VM103 (см. Linux/OpenSearch report). На Linux выполнены
  POSIX crash-after-write, partial frame и owner/SIGKILL tests; Windows skips
  не выдавать за Linux PASS.
- [x] **Предъявить первый конечный результат:** настоящий subprocess приложения
  с isolated DB/DATA_DIR, штатная synthetic операция → audit/outbox → одна native
  JSON-запись; повтор после injected write fault успешен. Выполнение не использует
  старый worktree, его venv, private sources или Kubernetes scripts.

**Критерий:** native milestone и AC-05/07/12/13. При сбое исправляется конкретная
цепочка этого теста; не создаётся новая очередь helper/proof/status работ.

## Task 3: Logstash formatter и strict logging handler

**Files:** новый `audit_logstash.py`, `test_audit_logstash.py`, fixture
`backend/tests/fixtures/audit/logstash-v1.json`; точечное подключение в outbox;
requirements и общий lock main.

**Interfaces:**

- `format_logstash_payload(payload: bytes) -> bytes` и `AuditLogstashFormatError(ValueError)`.
- `AuditLogstashFormatter(JsonFormatter)`; `MAX_LOGSTASH_BYTES = 18 * 1024`.
- `AuditLogstashHandler(logging.Handler)` с `emit(record: logging.LogRecord) -> None`.
- `make_logstash_sink(write_line: Callable[[bytes], None]) -> Callable[[bytes], None]`.
- Вход sink — validated canonical payload; результат — полный write или исключение.

- [x] Написать contract tests по точному набору 24 полей из ТЗ/fixture, всем outcome,
  типам, @timestamp, canonical projection, Unicode/CRLF/escaping, NaN и wire limit.
- [x] Добавить deterministic repeat tests после смены времени/потока/процесса;
  проверить overhead <=2048 bytes для всех action текущего каталога.
- [x] Добавить tests: root ERROR/disable/dictConfig/чужая LogRecordFactory не теряют
  и не дублируют audit; extra/traceback/path/secret canary не выходят в JSON.
- [x] Добавить tests ошибки formatter/write при обоих logging.raiseExceptions;
  `pending/stdout_failure`, marker не выставлен. Повторный marker fault выдаёт
  байт-в-байт тот же Logstash JSON, canonical payload в БД неизменен.
- [x] Запустить tests до реализации; подтвердить предметный RED.
- [x] Добавить `python-json-logger>=3,<5`, подобрать проверенный релиз и обновить
  lock штатным workflow main без общего upgrade:

```powershell
# Из корня нового checkout; версия uv — из актуального DEVELOPMENT_CHECKS
uv pip compile backend/requirements.txt doc-parser/pyproject.toml --universal --python-version 3.12 --generate-hashes --no-annotate -o backend/requirements.lock.txt
```

- [x] Установить hashed lock в disposable venv, parser с --no-deps; выполнить
  `python -m pip check` и dependency audit по DEVELOPMENT_CHECKS. Если для denial
  потребовалась новая версия psycopg, обосновать её отдельно и проверить также
  disabled DB path; старые donor pins не переносить целиком.
- [x] Реализовать allowlist formatter, private Logger.handle и один strict handler.
  Emit не скрывает исключения; stdout остаётся в одном dispatcher thread.
  Не использовать QueueHandler, root logger, sampling и произвольную сериализацию.
- [x] Подключить форматирование после `_validate_payload()`. Инъекция write_line
  остаётся конечным byte sink: native получает canonical, logstash — wire bytes.
- [x] Выполнить `python -m pytest tests/test_audit_logstash.py tests/test_audit_outbox_dispatcher.py -q`.
- [x] **Предъявить второй конечный результат:** тот же сценарий Task 2 с JSON
  Logstash, совпадением event_id/time и успешным повтором; старый worktree недоступен
  для runtime и не нужен для выполнения.

**Критерий:** AC-01…06; Logstash добавлен к уже работающему main-аудиту.

## Task 4: настройки, существующий chart и самостоятельная документация

**Files:** config/.env.example, четыре chart файла из карты, AUDIT_DELIVERY и
AUDIT_EVENT_CATALOG; `test_audit_logstash_config.py`, `test_audit_logstash_chart.py`,
`test_audit_main_entrypoint.py`.

**Interfaces:** Settings `audit_stdout_format: Literal["native", "logstash"] = "native"`;
`runtime.auditStdoutFormat` → `AUDIT_STDOUT_FORMAT`; mode независим от format.
Существующий chart уже имеет auditStdoutMode; обновлять только недостающие части.

- [x] Написать tests Settings mode/format matrix: disabled ничего не доставляет,
  native/logstash принимаются, неизвестное значение отклоняется при startup.
- [x] Написать Helm tests: default native, override logstash; schema enum и
  запрет AUDIT_STDOUT_MODE/FORMAT в runtime.config исключают двойную настройку.
- [x] Добавить switch test: pending после неудачной native попытки выходит в
  logstash; старый DB payload/ID/time прежние, held/stdout_written не тронуты.
- [x] После RED реализовать формат в Settings и env/chart. Конечный выбор sink
  происходит один раз при создании dispatcher; hot reload не добавляется.
- [x] Проверить весь matrix через реальный entrypoint: audit stdout, access/error
  stderr, disabled compatibility, безопасный startup failure и restart.
- [x] Обновить runbook под новый пакет main: migration/repair, defaults, safe samples,
  status/hold, ограниченные поддержанные DB profiles, rollback. Старые результаты
  и Kubernetes-поручения явно отделить; они не инструкция продолжать старую ветку.
- [x] Выполнить focused tests и `helm lint deploy/helm/ai-knowledge`; проверить
  `helm template` с durable/logstash через synthetic values существующих chart tests.
  Не менять release/lifecycle/TLS tooling и не запускать старый стенд.

**Критерий:** AC-07/08 и entrypoint часть AC-09; новый пакет самодостаточен.

## Task 5: конечная локальная приёмка и отдельная проверка OpenSearch

**Files:** acceptance report. Использовать сценарии Tasks 1–4; новый испытательный
framework и публичный debug endpoint не создавать.

- [x] Собрать backend image из новой ветки main с hashed lock. Выполнить isolated
  SQLite/PostgreSQL migration + native/Logstash smoke в Linux-контейнере.
- [x] Подтвердить физическую независимость: образ/тесты не используют пути/модули
  старого worktree или `.superpowers/sdd`, startup не требует Kubernetes operator.
- [x] Проверить 1000 synthetic events в test harness: native/logstash доставляют
  все события, без ошибок, с одинаковыми canonical данными. Записать время и размеры;
  не вводить новый production SLA и не менять существующие performance thresholds.
- [ ] Повторить полный backend suite (cwd `backend`) на окончательном snapshot.
  Исторический полный запуск: 4671 PASS/93 SKIP/4 FAIL; связанный повтор после
  исправлений: 64 PASS/7 SKIP. Project gate, Linux/POSIX/PG и Docker regression
  выполнены отдельно; их PASS не объявляется новым зелёным full suite.
  Прежние failures/skips сохранены в отчётах; старые TLS tests не импортировались.
- [ ] Закрыть локальные AC-01…09 и AC-11…13 по свежим доказательствам. Старые PASS
  донора не заменяют проверки переноса. При отсутствии Linux/PG фиксировать
  конкретный непроверенный пункт, не расширять исследование окружения бесконечно.
- [x] Предъявить локальный пакет и отчёт. AC-10 остаётся OPEN до целевой проверки;
  отсутствие платформенных доступов не задерживает выдачу локального результата.
- [ ] Для отдельного целевого сеанса получить collector contract из ТЗ: stdout/CRI
  parsing, mapping, INFO filtering, размер, index/access/retention/dedup и согласованное
  время ожидания. Использовать штатную поставку выбранного окружения без возобновления
  старой Kubernetes-разработки.
- [ ] После разрешённого развёртывания сопоставить synthetic success/denied/failure:
  operation/request → DB → stdout → индекс. Проверить typed fields, UTC, Unicode,
  near-limit/CRI, blank frames, parser errors и отсутствие секретных canary.
- [ ] Проверить controlled duplicate и форматный rollback logstash → native:
  очередь сохранна, повтор идентифицируется по event_id, stdout_written не считается
  ACK OpenSearch. Закрыть AC-10 только по фактическим документам индекса.

## Правила завершения и остановки расширения

- Конечные результаты: Task 1 — DB pair; Task 2 — native поток; Task 3 — Logstash
  поток; Task 4 — пригодный к запуску пакет; Task 5 — локальная приёмка и отдельный
  подтверждённый либо OPEN target status.
- Повторять проверки только после связанных изменений или конкретного failure.
  Не считать новую сводку статусов, число PASS или очередной hash конечным результатом.
- Если для текущего сценария внезапно требуется TLS operator/restore framework,
  это нарушение выбранной границы: остановить именно такое расширение, локализовать
  лишнюю зависимость и предложить минимальное решение. Старый план не возобновлять.
- Сохранить donor worktree как есть. Не удалять/архивировать его и не переносить
  экспериментальные каталоги в новый репозиторий в рамках этой задачи.
- Финальный diff — только перечисленные audit изменения. Перед разрешённым commit
  выполнить project checks; staging по файлам/участкам, не `git add .`.
  Commit, push, миграция основной БД и deploy требуют отдельного поручения.

## Матрица покрытия

| Критерии ТЗ | Задачи | Доказательство |
|---|---|---|
| AC-01/02/03 | 1, 3 | Canonical/Logstash fixture, поля/типы, allowlist, identity/time |
| AC-04/05/06 | 2, 3 | Full write, process faults, error propagation, logging isolation |
| AC-07 | 1, 2, 4, 5 | Legacy/disabled/native/switch/hold/rollback |
| AC-08 | 4 | Settings и Helm matrix |
| AC-09 | 2, 4, 5 | Entrypoint/image, Windows/Linux, hashed lock |
| AC-10 | 5, отдельный сеанс | Документы целевого индекса и collector contract |
| AC-11 | 1, 5 | Fresh/непустой upgrade SQLite/PG, main tables, один Alembic head |
| AC-12 | 1, 2 | Каталог main, security transactions, bounded denial |
| AC-13 | 0, 2, 3, 5 | Main baseline, manifest выбранных файлов, автономный process/image |

## Порядок и оценка

Порядок: 0 → 1 → 2 → 3 → 4 → 5. Конфигурацию format, необходимую для прогона
Task 3, добавить там минимально; полную validation/chart matrix закрыть в Task 4.
Последовательное выполнение одним исполнителем достаточно.

Перенос и адаптация аудита с migration/native proof: ориентир 2–4 ч.д.;
formatter/config/docs и локальная приёмка: 2–4 ч.д. Итого предварительно 4–8 ч.д.
Оценка включает перенос, который прежняя редакция оставляла внешней предпосылкой.
Она не включает ожидание платформы и изменение её collector. Готовность старого
Kubernetes release, TLS и restore не входит ни в оценку, ни в критический путь.

Текущий результат — реализованный пакет в отдельной ветке без commit/push.
Миграции выполнялись только на изолированных тестовых БД. Фактические проверки и
OPEN среды перечислены в отчёте; старые Kubernetes очереди не возобновляются.


Дополнительная Linux/home приёмка: [отчёт](../reports/2026-10-20-audit-logstash-linux-opensearch.md).
Проверен именно Docker json-file→Fluent Bit→OpenSearch; target CRI/доступы/retention остаются OPEN.
