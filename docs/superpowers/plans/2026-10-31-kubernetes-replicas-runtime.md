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

- [ ] `test_two_apis_share_capacity_and_rate`, `test_revocation_visible_on_second_api`, `test_cancel_chat_from_other_api`, `test_config_revision_invalidates_both`, `test_audit_failover_replays_same_event_id`. Assertions: суммарный admission ≤ limit; revoked actor получает 403; cancel сохранён; новая версия словаря на обоих; commit audit не теряется, duplicate имеет исходный ID.
- [ ] RED: `python -m pytest tests/test_replica_coordination.py tests/test_replica_audit.py -q`; multiprocess PG cases через runner runtime.
- [ ] Реализовать coordination, обновить API/settings/glossary/client cache paths из P0 inventory, вернуть подробные причины degraded без credentials. Distributed readiness не становится ready при ошибке обязательного audit preflight.
- [ ] GREEN плюс `python -m pytest tests/test_authz.py tests/test_audit_outbox_dispatcher.py tests/test_audit_storage_modes.py tests/test_chat_stream.py -q`; проверить все DB-only/external-only/both audit modes. Не ослаблять strict audit schema для новой очереди.
- [ ] Commit `feat: coordinate state across backend replicas`.

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

- [ ] `test_api_service_excludes_workers`, `test_distributed_requires_shared_storage`, `test_role_probes_and_identity`, `test_legacy_single_process_guard_retained`. Assertions: endpoints только API; local rejected; worker не использует отсутствующий HTTP main; compact multiworker отвергнут.
- [ ] RED: `python -m pytest tests/test_distributed_chart.py -q` с Helm.
- [ ] Добавить templates/capability, снять запрет replicas только после R1–R4. Не добавлять HPA. Метрики queue age/depth, claim/retry, lease loss, model saturation, per-role readiness; resources определить по небольшому corpus/load probe.
- [ ] GREEN chart + runner runtime: 1→2 API, 1→2 workers, kill API/worker, partition PG, возврат старого процесса, concurrent cancel/delete, exhausted LLM capacity. Проверить отсутствие duplicates/loss, лимиты, стабильный auth. Записать latency/throughput и не объявлять линейное ускорение.
- [ ] Commit `feat: enable verified API and worker replicas`.

## Ключевые assertions для тестов задач

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
