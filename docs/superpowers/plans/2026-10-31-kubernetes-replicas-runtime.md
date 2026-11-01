# Distributed API and workers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Track steps with checkboxes.

**Goal:** Запустить несколько API/workers с общей очередью, безопасной отменой и восстановлением.
**Architecture:** WorkItem ledger в PostgreSQL отделён от публичных bulk Job records. Durable ownership связывается с существующими generations и portable artifacts; фоновые задачи имеют явные роли.
**Tech Stack:** SQLAlchemy 2, psycopg/PostgreSQL 17, FastAPI, Alembic, existing Qdrant clients.
**Spec:** [Согласованный дизайн](../specs/2026-10-31-kubernetes-replicas-storage-design.md).

Статус 01.11.2026: R1 foundation реализован (rpl02): caller-owned transactions,
SKIP LOCKED, DB UTC leases, fencing/cancel/dedupe, bounded explicit worker и
kind/version registry. Import не запускает executor. Публичные Job/four-eyes не
изменены. R2–R5 и активация distributed ролей остаются открытыми; глобальную
ёмкость и maintenance epoch этот foundation не обещает.

## Global Constraints

Ограничения [основного плана](2026-10-31-kubernetes-replicas.md) обязательны.
Нельзя тестировать SKIP LOCKED/DB time/concurrent claims только на SQLite.
Долгий LLM-вызов не удерживает SQL transaction. Внешние вызовы могут повторяться;
активная версия и канонические строки не могут быть опубликованы старым владельцем.

## Review Focus

DB partition и возобновление процесса; потерянный ACK; cancel/delete/publish;
лимиты/права на разных API; audit duplicate/loss. R1–R5 проверяют каждый сценарий.

## Task R1: транзакционная очередь и leases

**Files:** создать пакет `backend/app/services/work/` (`models.py`, `store.py`,
`worker.py`, `handlers.py`, `__init__.py`),
`backend/alembic/versions/rpl02_work_items.py`, `backend/tests/test_work_store.py`,
`backend/tests/test_work_store_postgres.py`, `backend/tests/support/replica_postgres.py`;
изменить `backend/app/db/models.py`, `backend/app/config.py`.

**Interfaces:** SQL model `WorkItem`: id, kind, payload_version, payload_json,
status, dedupe_key unique, doc_id nullable, owner_id, fence bigint, lease_until,
cancel_requested, attempts, available_at, created_at, finished_at, error_code.
Статусы queued/running/succeeded/failed/cancelled; неизвестная payload_version
остаётся queued, показывается как unsupported и не зацикливает claim.
Публичный `Job` сохраняет semantics/four-eyes; связанный work_id хранится отдельно.

DTO в work/models.py: `WorkSpec(kind: str, payload_version: int, payload: dict,
dedupe_key: str, doc_id: str | None)`; `WorkClaim(work_id: str, owner_id: str,
fence: int, lease_until: datetime, spec: WorkSpec, maintenance_epoch: int = 0)`.
Epoch в R1 всегда 0; O2 добавляет authoritative MaintenanceState и persisted epoch
в WorkItem. До O2 это поле не обещает fencing всей установки.
Store интерфейсы (SQLAlchemy Session, caller commits):

```python
enqueue(session, spec: WorkSpec) -> str
claim(session, *, owner_id: str, supported: dict[str, set[int]], lease_seconds: int) -> WorkClaim | None
renew(session, claim: WorkClaim, *, lease_seconds: int) -> WorkClaim
assert_owner(session, claim: WorkClaim) -> None
finish(session, claim: WorkClaim, *, outcome: str, error_code: str | None = None) -> None
cancel(session, work_id: str) -> bool
```

`LostOwnership` — typed exception. Claim атомарен через SKIP LOCKED и UPDATE,
DB UTC time; reclaim increment fence. Стартовые settings: lease 120 s, heartbeat
30 s, attempts 3 для классифицированных transient errors, backoff 5/30/120 s;
validation/unsupported не retries. Значения проверяются измерениями R5.

- [x] Тесты `test_two_processes_claim_once`, `test_dedupe_in_same_transaction`, `test_old_fence_cannot_renew_finish`, `test_unknown_version_not_claimed`, `test_cancel_prevents_publish_guard`: assert разные workers не получают один live claim; rollback не оставляет work; old token raises LostOwnership.
- [x] RED: `python -m pytest tests/test_work_store.py -q`; PG через runner runtime, не current dev DB. Harness создаёт/удаляет только run schema/database по P0 safety contract.
- [x] Реализовать store/DTO/migration. Lease не освобождает global capacity до атомарной передачи/истечения. Worker polling bounded; нет бесконечного цикла на PG outage. Wall clock worker не определяет ownership.
- [x] GREEN unit + PG multiprocess; crash после claim/before ACK и продление рядом с expiry проверяются детерминированными барьерами, не sleep-only tests. Migration непустой Job таблицы не меняет её статусы.
- [x] Commit `feat: add PostgreSQL work ownership and recovery`.

Проверки R1: queue/worker/schema + S4 core51 passed; settings/legacy queue/
repository/chart/generation/export77 passed/11 environment-skipped. Actual
PostgreSQL7 passed: spawned claims и dedupe, stale-owner return, expired renew,
nonempty Job upgrade/downgrade refusal, process crash и DB-time expiry после
подтверждённого row-lock wait. Mandatory gate: audit0, frontend740 passed/1 skipped,
ESLint/Ruff passed. Это foundation, а не полный runtime/provider acceptance runner.

## Task R2: document jobs, admission и fencing публикации

**Files:** изменить `backend/app/api/documents.py`, `services/pipeline.py`,
`services/job_queue.py`, `services/bulk_generation.py`, `services/registry.py`,
`services/generation_store.py`, `services/generation_publication.py`,
`services/generation_files.py`, `services/generation_cleanup.py`, `services/generation_search.py`,
`services/vector_store.py`, `services/staging.py`, `services/document_update.py`,
`services/processing_scope_jobs.py`, `services/update_workers.py`;
создать `backend/tests/test_work_document_publication.py`,
`backend/tests/test_work_recovery_postgres.py`, `backend/app/services/work/document_handler.py`.

**Interfaces:** `run_document_job(claim: WorkClaim) -> None` в document_handler.py.
`publish_prepared_document(settings, vector_store, doc_id, generation_id, *,
claim: WorkClaim | None = None) -> bool` сохраняет legacy callers только combined;
distributed требует claim. `assert_owner` и existing document lock выполняются
в той же transaction, что canonical rows + active pointer + work success.
Единый порядок блокировок: WorkItem → Document → generation/state → canonical rows;
cancel/delete/admission соблюдают его. Многодокументные операции берут doc IDs по порядку.

Каждый новый claim после потери владельца получает НОВОЕ candidate generation ID:
старое abandoned, immutable проверенные checkpoints можно перенести ссылками в
новый manifest с перепроверкой source/parser/scope signature. Нельзя продолжить
запись в тот же generation prefix/Qdrant IDs, доступный старому worker. Старые
generation tombstones сохраняются, пока нельзя исключить поздние записи; hydration
fail-closed для отсутствующего/неактивного generation. GC не удаляет active IDs.
Существующие point IDs уже включают generation_id — использовать их.

- [x] Тесты `test_old_worker_returns_after_reclaim`, `test_cancel_delete_publish_race`, `test_upload_commit_enqueues_atomically`, `test_late_qdrant_write_never_visible`, `test_checkpoint_reuse_requires_matching_signature`. Assertions: old publication raises LostOwnership; active generation только нового claim; one upload receipt→one work; late upsert не попал в retrieval/download; delete не воскрешён.
- [x] RED: `python -m pytest tests/test_work_document_publication.py -q`; barriers в двух процессах через `test_work_recovery_postgres.py`.
- [x] Подключить enqueue к SQL transaction S2, global bounded admission к upload/resume/regenerate и existing four-eyes bulk Job. Заменить global reset_stale_statuses/recover all running на owner-aware recovery только distributed. Внешние writes перед publish исключительно в unique attempt/generation namespace. Все прямые writers из P0 либо проходят guard, либо вынесены в coordinated work; список пустых исключений проверить до R5.
- [ ] GREEN плюс `python -m pytest tests/test_generation_store.py tests/test_generation_search.py tests/test_generation_publication_service.py tests/test_pipeline_queue.py -q`; runner runtime реальный Qdrant + PG + shared storage, kill/partition/return старого worker.
- [x] Commit `feat: fence document execution and publication`.

Implementation milestone R2 (01.11.2026): `rpl02a_document_work`, transactional
document/bulk admission, frozen processing policy, claim-bound generation writers,
atomic publication/work success, checkpoint recovery and late-write tombstones.
Core80 passed; affected regression initially393 passed/1 platform skipped and one
checkpoint-retirement precondition updated, then its publication/cleanup/staging
group74 passed. Actual PostgreSQL + server Qdrant: all5 R2 scenarios passed,
including spawned old-process return through the real heartbeat worker, two
cancel/delete barriers, global budget and schema preservation/downgrade refusal.
R1's7 actual PostgreSQL cases also passed in the extended run. Mandatory gate:
audit0, frontend740 passed/1 skipped, ESLint/Ruff. Soft delete preserves resumable
draft checkpoints; explicit cancel-update discards/restores that draft.

Ruling: full role/Pod/partition runner depends on R3/R4 and stays an R5 gate.
These probes share a host filesystem adapter root, so they do not certify RWX/CSI
or S3 durability. R3 still must register bulk, duplicate-refresh, cleanup and
scope/repair handlers from the inventory. Direct distributed generation writers
without a claim are rejected. No distributed capability is enabled by this milestone.

## Task R3: роли и все фоновые механизмы

**Files:** создать `backend/app/worker.py`, `backend/app/services/work/runtime.py`,
`backend/app/services/work/scheduler.py`, `backend/tests/test_runtime_roles.py`,
`backend/tests/test_background_work_migration.py`;
изменить `backend/app/main.py`, `backend/Dockerfile`, `services/export_queue.py`,
`services/trash.py`, `services/chat_history.py`, `services/reference_translation.py`,
`services/glossary/translations.py`, `services/diagnostics/bundle_queue.py`,
`services/diagnostics/prepare_worker.py`, `services/work/handlers.py`, `SECURITY.md`.

**Interfaces:** `start_runtime(settings) -> RuntimeHandle`, `RuntimeHandle.stop(timeout: float) -> bool`.
API запускает HTTP и request-local services, но не job executors/recovery/purge.
Worker command `python -m app.worker`; отдельный лёгкий HTTP health server на
port 8001 в том же процессе без импорта HTTP app и её startup hooks.
`/health/live` проверяет свежесть main-loop heartbeat (не старше 90 s),
`/health/ready` — готовность получать задания и доступность обязательного storage;
LLM outage не делает процесс мёртвым. Долгий handler выполняется отдельно от
main-loop heartbeat. Этот port не публикуется Gateway/пользовательским Service;
HTTP backend readiness нельзя копировать на worker.
Combined сохраняет прежний режим, пока явно не выбран replica schema.
Handler registry: kind/version → callable `(WorkClaim) -> None`, payload содержит
IDs, не секреты/произвольные filesystem paths/callable names.

Планировщик формирует durable work с dedupe key `(kind, scope, schedule_slot)`.
Exports/bundles сохраняют существующие public records/statuses, используют blobs
и global read leases. Completion без artifact ref запрещён. Подзадачи bulk не
занимают worker, синхронно ожидающий выполнения дочернего work той же очередью.

- [ ] `test_api_import_starts_no_executor`, `test_workers_do_not_reset_foreign_processing`, `test_scheduler_dedupes_slot`, `test_export_resume_after_worker_loss`, `test_bulk_does_not_deadlock_single_worker`. Assertions: в API отсутствуют worker threads/subprocesses; один slot→один work; 1 worker завершает bulk; доступный export имеет проверенный blob.
- [ ] RED: `python -m pytest tests/test_runtime_roles.py tests/test_background_work_migration.py -q`.
- [ ] Перенести все позиции P0 inventory, не только документную очередь. Для translation/backfill payload version и cursor/checkpoint сохраняются; очистка идемпотентна и координирована. На SIGTERM прекратить claim, сохранить завершённые checkpoints; при истечении grace lease позволяет recovery без ложного done. Audit/diagnostic inbox переносит R4 до включения distributed.
- [ ] GREEN плюс `python -m pytest tests/test_export_queue.py tests/test_diagnostics_bundle_queue.py -q`; runner runtime на 1 API + 1 worker, restart API не останавливает worker.
- [ ] Commit `feat: separate API and background runtime roles`.

R3 software milestone, 01.11.2026: closed role lifecycles, periodic handlers,
scope/repair/translation work, portable export/bundle completion, retention,
versioned cursors and legacy-writer guards implemented. Final affected group:
86 passed; export queue/scratch group:45 passed,1 Windows symlink permission skip;
role/scratch group:24 passed,1 same skip. Actual isolated PostgreSQL runtime
group:4 passed (220.44s), including independent-process fencing, nonempty schema
upgrade, JSON coordinators and heartbeat-backed scope publication. Root check:
npm audit0 vulnerabilities, frontend747 passed/1 skipped, ESLint and Ruff passed.
These are scoped checks, not the entire backend suite or provider certification.

Dependency ruling: commit the reviewed software milestone while keeping R3 open.
The actual1 API+1 worker/restart runner needs R4's audit/diagnostic coordination
and R5's role manifests/capability. R3 checkboxes and activation remain closed
until that integrated acceptance. Legacy NULL-generation corpus adoption and
new maintenance/backup entrypoints remain O2 prerequisites; no env bypass is
introduced. Diagnostic child-process scratch lifecycle belongs to R4; the R3
local scratch cleaner owns only in-process export attempts.

## Task R4: общие лимиты, конфигурация, чат и аудит

**Files:** создать `backend/app/services/work/coordination.py`,
`backend/alembic/versions/rpl03_replica_coordination.py`,
`backend/tests/test_replica_coordination.py`, `backend/tests/test_replica_audit.py`;
изменить `services/rate_limiter.py`, `services/chat_execution.py`, `api/chat.py`,
`services/chat_history.py`, `services/stopwords.py`, `services/ui_dictionary.py`,
`services/audit_outbox.py`, `services/audit_runtime.py`, `services/diagnostics/ingest.py`,
`services/diagnostics/runtime.py`, `app/auth/service.py`, `app/db/models.py`, `SECURITY.md`.

**Interfaces:** `reserve_capacity(session, *, resource: str, owner_id: str,
limit: int, ttl_seconds: int) -> CapacityLease | None`; `CapacityLease(id, resource,
owner_id, fence, expires_at)`; renew/release require full token. `consume_rate(session,
*, key: str, limit: int, window_seconds: int) -> bool`; атомарные DB slots/windows.
Global LLM budget применяется chat + generation + translation; локальные executor
лимиты остаются вторичной защитой. Если provider не умеет отменять запрос после
partition, слот не разрешает новый вызов до известного hard timeout старого плюс
grace; невозможность гарантировать внешний hard cap отражается как ограничение,
не маскируется новым lease.

`ConfigRevision(domain, revision)` обновляется в одной transaction с изменением;
на request/job boundary проверяется версия; auth rights/revocation всегда из
авторитетной БД. ChatRun record хранит owner/lease/cancel/status, историю завершённых
ответов связывает уникальный run ID; cancel на любом API меняет durable flag.
При обрыве SSE нет автоматического повтора LLM и нет обещания продолжения текста.

Audit: сохранить existing outbox payload/status/delivery semantics и event ID.
Для distributed заменить `.writer.lock` на dedicated PostgreSQL advisory-lock
connection у dispatcher; без lock/new healthy connection не читать/маркировать
очередь. Потеря соединения останавливает dispatcher; возможный повтор stdout
между write и mark сохраняет тот же event ID. Не обещать exactly-once внешнему sink.
Audit spool/hold files инвентаризировать: незавершённые security records не могут
существовать только на ephemeral disk; выбрать durable DB/blob representation
с сохранением действующих audit tests. Диагностический inbox и чтение сегментов
получают instance namespaces и durable refs, coverage gaps остаются явными.

- [x] `test_two_apis_share_capacity_and_rate`, `test_revocation_visible_on_second_api`, `test_cancel_chat_from_other_api`, `test_config_revision_invalidates_both`, `test_audit_failover_replays_same_event_id`. Assertions: суммарный admission ≤ limit; revoked actor получает 403; cancel сохранён; новая версия словаря на обоих; commit audit не теряется, duplicate имеет исходный ID.
- [x] RED/GREEN: coordination/audit и остальные R4 suites; multiprocess PG cases выполнены через safe disposable-schema harness. Запуск тех же механизмов в Pods через общий runner относится к R5 и остаётся отдельным gate.
- [x] Реализовать coordination, обновить API/settings/glossary/client cache paths из P0 inventory, вернуть подробные причины degraded без credentials. Distributed readiness не становится ready при ошибке обязательного audit preflight.
- [x] GREEN плюс `python -m pytest tests/test_authz.py tests/test_audit_outbox_dispatcher.py tests/test_audit_storage_modes.py tests/test_chat_stream.py -q`; проверить все DB-only/external-only/both audit modes. Не ослаблять strict audit schema для новой очереди.
- [x] Commit `feat: coordinate state across backend replicas` (заключительный R4 commit после отдельных проверенных milestones).

R4 foundation milestone, 01.11.2026: caller-owned capacity/rate/revision schema,
shared RateLimiter, localization revision invalidation and LLMClient global
provider admission implemented behind the closed capability. Weighted events
keep bulk admission bounded without per-document SQL round trips. Uncertain
external calls retain capacity until trusted completion/reconciliation; TTL alone
does not establish that the model stopped. O2 must implement the audited recovery
path before activation. Existing local pools remain secondary limits.
Scoped checks:23 coordination/model/rate/schema tests passed;92 LLM/local-profile/
assessment tests passed;83 localization/schema regressions passed. Isolated
PostgreSQL:6 passed, including four-process limit contention and migration with
nonempty Job/downgrade refusal. Root check: audit0 vulnerabilities, frontend747
passed/1 skipped, ESLint/Ruff passed. Alembic now preserves percent-escaped URLs
through ConfigParser, covered offline and on actual PostgreSQL.
This intermediate commit does not complete R4. ChatRun/global chat admission,
cross-API cancel/history identity, auth/revocation acceptance, frontend cache
boundaries, audit advisory-lock dispatcher and durable diagnostic instances/inbox
remain open. Integrated R3/R5 role acceptance and production gates remain closed.

R4 chat milestone, 01.11.2026: `rpl03a_chat_runs` adds unique actor/attempt→history
ownership, DB-time leases/fences, shared chat admission (including preview),
cross-API cancellation and request-local heartbeat. Private owner tokens guard
history/source/scenario writes; completion and slot release commit together.
Soft deletion cancels runs; retention removes them with history. Bounded25-row
reconciliation stops expired requests without LLM replay. Partial drafts remain
limited to the matching owner's empty stopped/failed message. Direct legacy
history admission is closed in future mode. Rechecks after history locks and
before/after model reservation prevent late owners from publishing or starting
another provider request. Combined paths and existing public permissions remain.
Scoped final checks:94 chat/model/coordination/LLM/schema tests passed;82 chat,
history, streaming and preview regressions passed before the final model guard.
Actual isolated PostgreSQL:3 passed, including independent API-process duplicate
admission/cancel, legacy-history migration and expiry during a held history-row
lock. SQLite migration roundtrip preserves history and rejects loss of completed
Run identities. These checks do not certify multi-Pod or customer providers.
The positive managed API→LLMClient→history path also passed with a controlled
provider response. Final root check: npm audit0 vulnerabilities, frontend747
passed/1 skipped, ESLint and Ruff passed. No full backend/provider claim.
Auth/revocation acceptance, frontend cache boundaries, audit dispatcher failover
and diagnostic durable instances/inbox remain open. R4 and activation stay gated.

R4 auth/cache milestone, 01.11.2026: future session/block expiry uses DB time;
identity/groups/revocation are reloaded on each request under the shared deployment
role mapping. Catalog creation and edits publish one `references` epoch with their
existing SQL/audit transaction. No old revision rows are deleted. Stopword cache
hits validate that epoch. The authenticated, noncached revision endpoint exposes
only known opaque counters; legacy probes do not query future schema.
Visible-browser metadata checks, foreground refresh and aborted/coalesced requests
refresh UI overrides/tag lookups/language options. Transient failures retry, stale
generations are ignored and derived tag counts have bounded TTL. Controlled editor
selections retain their values. Existing glossary snapshot revision logic is reused.
Checks:159 affected auth/reference/translation/localization tests passed;62 focused
frontend tests passed. PostgreSQL:3 process/cache probes passed, plus the extended
auth probe passed after adding changed-group/missing-role cases. Both warmed API
caches observe remote glossary edits. Root gate: audit0 vulnerabilities,
frontend756 passed/1 skipped, ESLint/Ruff passed. Cookie/IdP/multi-Pod acceptance,
common runtime/signing identity and all provider qualifications remain O1/R5.
At this cache milestone, diagnostic control/instances/inbox remained open; the
final diagnostic software result is recorded below. Deployment remains gated.

R4 audit dispatcher software milestone (2026-11-01): API and worker participate
in session advisory leadership scoped to the current database/schema. The owned
connection executes all queue transactions; standby does not read the queue,
lost connections cannot mark a previous write through a fresh connection, and
stopped owners cannot reacquire. Candidate/owner connections are physically
discarded on release. Preflight requires ownership access while accepting a busy
healthy peer. Direct PostgreSQL or session pooling is required; transaction
pooling is unsupported. Existing outbox/held state remains SQL, with no schema
change; immutable payload/event ID and all sink modes remain intact. Future new
event/eligibility/retry/marker timestamps use DB time. No downstream exactly-once
guarantee is introduced.
Checks:5 explicit isolated PostgreSQL cases passed, including another API process,
standby without queue reads, killed owner connection between write and marker,
same-ID replay in both stdout modes and a skewed host clock. Audit schema, denial
transport and storage-mode group:98 passed/3 skipped; role/lifecycle/storage-mode
group:55 passed; entrypoint/restore/Logstash compatibility:64 passed. Root check:
audit0 vulnerabilities, frontend756 passed/1 skipped, ESLint/Ruff passed.
At this dispatcher milestone, diagnostic control/instances/inbox were still open.
The final diagnostic result follows. Legacy journal adoption remains O2.

R4 diagnostic software completion (2026-11-01): SQL-authoritative capture control
and security audit commit together; failed stops are not acknowledged as local
journal intents. Short selector leases fail closed, including delayed SQL reads;
one replica stopping does not end the shared capture. Browser admission/membership
uses shared budgets and authoritative post-lock checks. Bounded immutable SQL
segments carry canonical hashes and instance/stream identities. Shared quotas,
frontend idempotency and segment/receipt publication are serialized; admitted
batches survive API disk loss and replacement maintenance workers retry them.
Previously admitted capture data can finish after stop; fresh stale writers cannot.
Known loss and unknown historical frontend counters remain explicit. Retention is
bounded and preserves pending/legacy audit evidence. Read views and supervised
prepare/ZIP children use verified private inputs. Same-host scratch cleanup checks
ownership, containment, links and recorder/parent/child OS locks; child watchdogs
survive parent death. Offline collection marks shared SQL events unavailable.
Unadopted legacy deferred journals are preserved and refuse the future runtime.
Checks:402 diagnostic/role/worker/offline tests passed,2 skipped (host symlink
privilege and an optional legacy PostgreSQL target). Explicit owned PostgreSQL:
3 passed, including competing API receipt admission and nonempty schema upgrade.
Required auth/chat/audit regression:77 passed. A cold API's local instance was
removed before a replacement worker built the ZIP and another API downloaded all
backend/frontend events;1 roundtrip test passed. Root check: audit0 vulnerabilities,
frontend756 passed/1 skipped, ESLint/Ruff passed. Software R4 is complete; actual
multi-Pod runtime/provider/maintenance qualification remains R5/O1–O3. The build's
distributed activation gates remain closed until those requirements are met.

## Task R5: distributed chart и приёмка нескольких исполнителей

**Files:** создать `deploy/helm/ai-knowledge/templates/api.yaml`, `templates/worker.yaml`,
`deploy/kubernetes/profiles/distributed-basic.values.example.yaml`,
`backend/tests/test_distributed_chart.py`;
изменить `values.yaml`, `values.schema.json`, `templates/services.yaml`,
`templates/network.yaml`, `templates/config.yaml`, `scripts/kubernetes/lifecycle.py`,
`backend/test_scripts/run_replica_acceptance.py`, `docs/KUBERNETES_DEPLOYMENT.md`,
`docs/PRODUCTION_DEPLOYMENT.md`, `SECURITY.md`.

**Interfaces:** Deployment frontend/api/worker; Service backend выбирает только api.
Все роли подключают одинаковые SQL/Qdrant/artifact identities и APP_SECRET_KEY.
Topological spread по реальному failure domain, probes отдельных ролей, bounded
termination grace. Initial acceptance 2 frontend + 2 API + 2 workers, один Uvicorn
на Pod; intended counts сохраняются maintenance receipt. Режим basic не обещает HA storage.

- [x] `test_api_service_excludes_workers`, `test_distributed_requires_shared_storage`, `test_role_probes_and_identity`, `test_legacy_single_process_guard_retained`. Assertions: endpoints только API; local rejected; worker не использует отсутствующий HTTP main; compact multiworker отвергнут.
- [x] RED: `python -m pytest tests/test_distributed_chart.py -q` с Helm.
- [ ] Добавить templates/capability, снять запрет replicas только после R1–R4. Не добавлять HPA. Метрики queue age/depth, claim/retry, lease loss, model saturation, per-role readiness; resources определить по небольшому corpus/load probe.
- [ ] GREEN chart + runner runtime: 1→2 API, 1→2 workers, kill API/worker, partition PG, возврат старого процесса, concurrent cancel/delete, exhausted LLM capacity. Проверить отсутствие duplicates/loss, лимиты, стабильный auth. Записать latency/throughput и не объявлять линейное ускорение.
- [ ] Commit `feat: enable verified API and worker replicas`.

R5 foundation milestone (2026-11-01): separate role Deployments, API-only Service,
bounded private scratch, manual counts/spread, private worker health/metrics and
role NetworkPolicies are implemented. A namespace-bound keyed common configuration
and source-build identity must match the explicitly installed SQL/storage identity
and exact Alembic revision `rpl04_runtime_identity`. Startup has no bootstrap/env
bypass; SQL boundaries/readiness fence retired configurations. ConfigMap authority
and explicit stub endpoints prevent Secret overrides of chart-controlled settings.
Shared SQL queue/model gauges and process claim/retry/lease-loss counters are
implemented. Root checks passed (audit0, frontend756/1skip, ESLint/Ruff); focused
foundation107 passed, final chart5 passed, metrics/auth/worker19 passed; explicit
owned PostgreSQL identity3 passed. These overlapping cohorts are not additive and
are not full backend CI. O1 bootstrap/storage wiring and O2 maintenance are required
before actual multi-Pod failure/load acceptance. R5 remains open, including runner,
resource measurements and rollout qualification; the basic example keeps app off.

## Ключевые assertions для тестов задач

R5 owned basic runtime milestone (2026-11-01): exact allowlisted backend/frontend
Linux images built and a fresh TLS PG/Qdrant/S3 installation bootstrapped inside
the backend image. Actual2 frontend/2 API/2 worker became Ready; shared sessions
worked on distinct API Pods, and synthetic DOCX reached done/problem=None with
BM25 visibility and SHA-256-identical source downloads. Manual API/worker scale
2→1→2 and normal idle Pod replacement passed; document processing passed again
after replacement. Initial lab API512Mi OOM led to1Gi; chart defaults retained.
The runner now repeats owned-scope/identity/session/storage-boundary/document
checks, records image/source digests and memory, and optionally performs idle
replacement. Active worker loss, PG partition/stale return, concurrent cancel/
delete and model exhaustion remain required pending gates; O2 recovery is still
required. This milestone does not close R5 or certify production resources/SSO,
live models, alternate provider combinations, node failover or independent restore.

R5 active-publication/capacity milestone (2026-11-01): actual owned restored
managed-S3 runtime passed cancel and delete during real synthetic model requests.
Late worker output did not replace the published generation/content; exactly one
publication remained, cancelled work became terminal, cancelled content stayed
searchable/downloadable and deleted content stayed hidden. The instrumented model
reported actual active/peak counters without retaining inputs. With two document
workers running and one occupied global model slot, each of the two independent
API processes received immediate busy admission without sending another provider
request. Actual peak stayed1; both documents later completed with matching source
hashes and search. The30s delay was deliberate fault injection. All application
roles returned Ready2/2/2, model owners/calls0, fixture success configuration
restored. Public result: `../reports/2026-11-01-replica-runtime-active-publication.json`.
The runner now supports explicit `--active-faults`, binds namespace UID before
mutation and removes only performed fault checks from pending. Active worker
loss, PostgreSQL partition, stale-worker return and resource/load qualification
remain open. These two checks do not close R5 or certify real provider/node failover.

R5 active-worker-loss milestone (2026-11-01): the two exact owned worker CRI
containers were stopped while the instrumented provider still reported one actual
in-flight request. The original reservation remained owned after provider finish
and DB-clock expiry; both independent APIs returned busy without another provider
request. Replacement workers ran but could not bypass quarantine. The same held
recovery operation at epoch5 stopped all roles/provider normally; fresh Kubernetes
and CRI guards confirmed no unknown/orphan writer, unchanged Ready node identities
and zero provider endpoints before and after exact-token audited reconciliation.
A same-image release Job verified held corpus/work hashes. The interrupted DOCX
automatically completed with fence1→3, problemNone, one active publication and
matching original SHA/search. All9 prior documents retained content, active
generations, original bytes and delete visibility;2/2/2 ready0restarts. Report:
`../reports/2026-11-01-replica-active-worker-loss.json`. The standalone coordinator
records this check; the combined runner still conservatively lists it pending
until O3 integrates the phase-controlled recipe. PostgreSQL partition, stale
worker return and resource/load qualification remain open. This single-node
synthetic recipe does not qualify real providers or node/host failover.

R5 PostgreSQL-partition/stale-return milestone (2026-11-01): one exact worker
Pod was isolated from managed PostgreSQL on TCP5432 by an owned Cilium deny rule;
Qdrant/model stayed reachable and the peer retained PG access. The old container
stayed live, became unready and did not restart. The healthy peer reclaimed the
unfinished document at fence2 instead of1 and abandoned generation1. Policy/label
propagation exceeded the initial observation window; the same live fault was
confirmed without re-injection. The first confirmed network sample followed the
provider's server-side finish, while work/publication remained unfinished; no
claim is made that the server was still active at that network sample.
After exact-policy removal the original container resumed claims/completions.
Its live provider client acknowledged a confirmed terminal reply normally; model
peak stayed1 and capacity returned idle without offline reconciliation or a new
maintenance epoch. The original-token publication guard probe in the old Pod
was rejected even with a future client lease; its transaction rolled back and
persisted generations were unchanged. Only generation/fence2 published. The
process lease-loss counter remained0 (the disconnected heartbeat did not take
that counter branch), so it is not used as the completion oracle. Normal worker
startup/success fixture restored,2/2/2 Ready0restarts, source SHA/search passed and
all10 earlier documents retained content/generation/source/delete state. Report:
`../reports/2026-11-01-replica-postgres-partition-stale-return.json`.
The test wrapper changes only runtime UUID selection to Pod UID for exact owner
mapping; production queue/handler/heartbeat/fencing code is unchanged. Resource/
load qualification and O3 integration into the combined runner remain open;
standalone fault receipts do not certify external providers or node/host loss.

R1: `assert len(live_claims_for_same_work) == 1`;
`assert new_claim.fence > old_claim.fence`; старое finish проверяется через
`pytest.raises(LostOwnership)`.
R2: `assert active_generation_id == replacement_generation_id`;
`assert stale_generation_id not in retrieved_generation_ids` после позднего upsert;
`assert committed_upload_work_count == 1` после retry.
R3: `assert api_executor_threads == []`; `assert scheduled_work_count_for_slot == 1`;
`assert bulk_status == "completed"` при одном worker.
R4: `assert total_active_reservations <= configured_limit`;
`assert revoked_response.status_code == 403`; `assert cancelled_run.cancel_requested`;
`assert set(replayed_event_ids) == {original_event_id}` при повторе audit event.
R5: `assert backend_endpoint_roles == {"api"}`;
`assert completed_work_ids == accepted_work_ids` после recovery контрольного corpus;
`assert duplicate_active_publications == 0`.
