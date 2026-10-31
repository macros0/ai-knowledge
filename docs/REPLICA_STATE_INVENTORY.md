# Состояние, требующее координации между репликами

Инвентарь P0, 31.10.2026. Пути в таблице относительно `backend/app/`, если не указано иное.
Все механизмы пока работают в прежнем single-backend runtime; столбец «Перенос»
описывает задачу, а не выполненную миграцию. Детали проверяются тестами каждой задачи.

| Путь | Состояние / текущий владелец | Повтор, отмена и перенос |
|---|---|---|
| main.py lifespan | recovery всех running/processing; startup threads | owner-aware recovery R2/R3; убрать из API |
| services/job_queue.py | in-memory queue/admission lock; Job в SQL | bulk four-eyes сохранить; enqueue/recovery R1/R2 |
| services/pipeline.py | executor, semaphore, abort events, chunk locks | durable claim/cancel/checkpoints, R2/R3 |
| services/registry.py reset_stale_statuses | глобальная правка статусов при старте | только просроченные owners R2 |
| services/staging.py | SQL manifest + chunk files, process locks | immutable artifacts S3, fenced checkpoint R2 |
| services/generation_store.py | SQL document locks, active/candidate pointer | сохранить; добавить ownership guard R2 |
| services/generation_publication.py | canonical commit + FS/Qdrant verification | immutable artifacts S3, claim transaction R2 |
| services/generation_files.py, generation_artifacts.py | generation dirs/checksums | BlobRef/materialize S1–S3 |
| services/generation_search.py, vector_store.py | exclusions, generation-aware IDs, clients | сохранить active filtering; stale writes R2 |
| services/generation_cleanup.py | удаление неактивных FS/Qdrant generations | coordinated GC, read leases, tombstones S3/R2 |
| services/processing_scope_jobs.py, update_workers.py | SQL owner token/lease/cancel + threads | переиспользовать; общий WorkClaim R2/R3 |
| services/processing_scope_cache.py, processing_scope_originals.py | source cache/original paths | portable durable refs S3; scratch может теряться |
| services/reference_concurrency.py | durable revision/edit lease | сохранить SQL serialization; проверить cross-process R4 |
| services/export_queue.py | local worker/locks, SQL public state, files/downloads | durable jobs R3, artifacts/leases S3 |
| services/trash.py | purge loop, FS/SQL/Qdrant deletes | scheduler dedup, guarded delete R3 |
| services/chat_history.py | SQL history + purge thread | completed run uniqueness R4; purge R3 |
| services/chat_execution.py, api/chat.py | active request/cancel, model clients | ChatRun/capacity/cancel R4; SSE interruption допустим |
| services/rate_limiter.py | per-process sliding windows | atomic PG limits R4 |
| services/stopwords.py, ui_dictionary.py | local cache/invalidation | transactional ConfigRevision R4 |
| services/reference_translation.py, glossary/translations.py | model calls / backfill | versioned jobs/cursors R3, global model budget R4 |
| main.py run_backfills / vector_store.py | sparse/relations/chunks background fill | durable coordinated jobs R3 |
| services/diagnostics/bundle_queue.py, prepare_worker.py | local queue/subprocess lifecycle | explicit role/work ownership R3 |
| services/diagnostics/control.py, runtime.py, store.py | capture control, spool, instance lifecycle | internal transport F1; inbox/artifacts/R4 |
| frontend/src/lib/diagnosticServer.mjs | frontend FS spool/control/recorder lock | bounded transport + instance IDs F1 |
| services/audit_outbox.py, audit_runtime.py | SQL outbox + filesystem writer lock/hold | coordinated dispatcher R4; audit guarantees сохранить |
| auth/service.py, auth_sessions | SQL sessions/rights, cleanup | shared secret/revocation on each API R4 |
| api/documents.py upload/download/delete | file paths, admission, auth | receipts S2; bindings/read leases S3; enqueue R2 |
| scripts/kubernetes/lifecycle.py (repo root) | one application workload backup/scale | all writers drain/epoch + intended counts O2 |

Перед R5 требуется повторная проверка всего списка и прямых FS/Qdrant/SQL writers:
неперенесённый singleton или global restart recovery запрещает distributed capability.
