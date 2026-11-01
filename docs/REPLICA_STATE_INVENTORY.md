# Состояние, требующее координации между репликами

Инвентарь P0, 31.10.2026. Пути в таблице относительно `backend/app/`, если не указано иное.
Рабочий runtime остаётся single-backend. В R1–R4 реализованы закрытые ветки
work ownership, admission/publication, роли и часть общей координации; включение distributed
по-прежнему запрещено. Ниже указаны также обязательные оставшиеся переносы.

| Путь | Состояние / текущий владелец | Повтор, отмена и перенос |
|---|---|---|
| main.py lifespan | combined recovery/startup threads; отдельная закрытая API ветка | R3: API lifecycle без global recovery, executors и purge; полный runtime acceptance ещё открыт |
| services/job_queue.py | combined queue; future Job + private bulk intent | R2: атомарный enqueue/approval/cancel, four-eyes; R3: bulk/item receipts, cooperative deferral и projection reconciliation; single-worker сценарий проверен локально |
| services/pipeline.py | combined executor; future document work | R2: enqueue/claim/cancel/recovery/publication; R3: API без executor, worker handler зарегистрирован; интегрированная приёмка ролей остаётся R5 |
| services/registry.py reset_stale_statuses | глобальная правка только combined | R2: distributed не сбрасывает чужие статусы; R3: lifecycle не запускает global recovery из API |
| services/staging.py | SQL manifest + chunk files, process locks | immutable artifacts S3, fenced checkpoint R2 |
| services/generation_store.py | SQL document locks, active/candidate pointer | R2: matching claim для work generation; distributed begin требует claim; отказ от чужого candidate только после координированной отмены |
| services/generation_publication.py | canonical commit + FS/Qdrant verification | R2: canonical + active pointer + work success вместе; R3: durable duplicate_refresh и checkpoint retirement с проверкой поколения/partial output |
| services/generation_files.py, generation_artifacts.py | generation dirs/checksums | BlobRef/materialize S1–S3 |
| services/generation_search.py, vector_store.py | exclusions, generation-aware IDs, clients | сохранить active filtering; stale writes R2 |
| services/generation_cleanup.py | удаление неактивных FS/Qdrant generations | R2: Work→Doc, exclusion identity/checkpoint protection; R3: sweep <=100 документов, pending coalescing и guarded per-document handler |
| services/processing_admission.py, processing_scope.py | review leases + frozen work input | R2: confirmation/work/audit atomic, queued work защищён от legacy recovery; R3: role lifecycle |
| services/processing_scope_jobs.py, update_workers.py | combined tokens/threads; future frozen scope Work | R3: новый candidate на claim/fence, renewed Work lease, cold source-cache reuse; trash/resume сохраняет принятую policy, cancel-update закрывает receipt; claim/publication проверены на actual PG, Pod/provider acceptance остаётся R5 |
| services/canonical_repair.py; fix_attachment_paths/backfill_comment_concepts CLI | combined repair; future trusted work | R3: frozen base/input/vector proof, per-fence generation, immutable accepted embedding cache, no provider call in SQL; CLI enqueue-only; bounded terminal candidate reconciliation; legacy CLI inventory ниже |
| services/processing_scope_cache.py, processing_scope_originals.py | source cache/original paths | portable durable refs S3; scratch может теряться |
| services/reference_concurrency.py | durable revision/edit lease | сохранить SQL serialization; проверить cross-process R4 |
| services/export_queue.py | combined local worker; закрытый durable export handler | R3: Job+intent admission; unique attempt scratch with process-death lock release; verified blobs+public completion+Work success atomic; transient retry and audited terminal reconciliation; reclaim/cold ZIP проверены локально |
| services/trash.py | combined purge loop; future durable sweep/purge/cleanup | R3: cursor/page100, exact deleted_at+current cutoff, SQL+audit+cleanup intent atomic; bytes удаляются отдельным guarded work |
| services/chat_history.py | SQL history + combined purge thread; future leased ChatRun | R4: unique actor/attempt→message, fenced sources/scenario/completion, partial draft only matching terminal owner; retention removes Run with history under resource→Run→Session order |
| services/chat_execution.py, api/chat.py; work/chat.py, chat_runtime.py | combined local inflight; future shared admission/cancel | R4: global chat capacity (including preview), request-local heartbeat, cross-API durable cancel, bounded25 expired-run reconciliation without LLM replay; actual independent API-process PostgreSQL proof, Pod/partition acceptance still R5 |
| services/rate_limiter.py | combined per-process windows; future shared rolling SQL | R4 foundation: hashed key, weighted events, operation/document budgets atomic; actual multiprocess PG admission tested |
| services/stopwords.py, ui_dictionary.py; frontend LocaleContext/tagDictionary | combined local cache; future shared references epoch | R4: creation/mutation + epoch + audit same SQL; stopword hits validate epoch, UI SQL; browser visible/focus refresh, no-store, stale reply fencing and bounded derived-count TTL; two warm API processes verified |
| services/llm_client.py, work/model_budget.py | local pools plus future global SQL capacity | R4 foundation: one shared completion budget, no provider admission in SQL; ambiguous completion/process loss quarantines capacity until trusted reconciliation; O2 reconciliation and provider qualification remain open |
| services/reference_translation.py, glossary/translations.py | combined sync backfill; future public Job + work | R3: input hash, bounded versioned cursors, model outside SQL, translation+audit+cursor atomic, CAS/human-edit guards, public failure reconciliation; R4 общий LLM budget подключён, provider qualification/reconciliation остаются открыты |
| main.py run_backfills / vector_store.py | combined startup fills; future index sweep/children | R3: cursor100, frozen per-document generation, read-lock recheck including after embedding, bounded retries; legacy NULL identities require O2 adoption before activation |
| services/diagnostics/bundle_queue.py, prepare_worker.py | combined local queue; future durable bundle Work | R3: bounded admission, unique attempt snapshot/part, verified blob + ready + audit + Work success atomic; cursor100 expiry/failure reconciliation/portable metadata TTL with pending-receipt protection; R4 cross-replica inbox ещё обязателен |
| services/diagnostics/control.py, runtime.py, store.py | capture control, spool, instance lifecycle | internal transport F1; inbox/artifacts/R4 |
| frontend/src/lib/diagnosticServer.mjs | frontend FS spool/control/recorder lock | bounded transport + instance IDs F1 |
| services/audit_outbox.py, audit_runtime.py | SQL outbox/held records; `.writer.lock` contains only a coordination byte | R4 dedicated PostgreSQL connection owns both advisory lock and queue transactions; standby never reads the queue; diagnostic deferred-control journal remains part of R4 diagnostics |
| auth/service.py, auth_sessions, blocklist.py | SQL sessions/identity/blocks | R4: DB-time expiry, per-request identity/groups/block read; actual other-process block/unblock/role change/logout verified; shared signing secret/runtime identity and cookie/IdP Pod acceptance remain O1/R5 |
| api/documents.py upload/download/delete | file paths, admission, auth | receipts S2; bindings/read leases S3; enqueue R2; R3 future delete/restore atomically enqueue latest-state visibility/badge intents |
| scripts/kubernetes/lifecycle.py (repo root) | one application workload backup/scale | all writers drain/epoch + intended counts O2 |

Перед R5 требуется повторная проверка всего списка и прямых FS/Qdrant/SQL writers:
неперенесённый singleton или global restart recovery запрещает distributed capability.

## Operator CLI inventory (R3, 01.11.2026)

Проверены 58 Python-файлов в `backend/scripts/`: 36 legacy writer entrypoints
получили `require_single_process_writer`; 2 repair-команды используют trusted Work,
2 reprocessing-команды — существующее durable admission. Остальные 18 файлов —
read-only inspection, экспорт результата в выбранный локальный файл, установка
локального runtime или общие модули без самостоятельного corpus writer.

| Группа | Команды / правила |
|---|---|
| Legacy index/cache/reference writers | backfill_attachment_tags, backfill_chunks, backfill_db_store, backfill_dedup, backfill_mail_scope, backfill_source_locale, backfill_translations, reindex, rebuild_sparse, rebuild_qdrant_v2, rebuild_tags, migrate_payload, migrate_tags_to_id, migrate_reference_origin, reference_editing_transition |
| Legacy schema/corpus migration and seeds | migrate_answer_scenarios, migrate_artifact_storage, migrate_glossary, migrate_glossary_identities, migrate_glossary_aliases, migrate_json_to_db, migrate_mail_schema, seed_attribute_values, seed_backup_fixture, seed_documents, seed_glossary, kubernetes_migrate, kubernetes_smoke |
| Legacy maintenance/backup/auth/audit | collect_artifacts --apply, create_qdrant_snapshot, restore_qdrant_snapshot, write_backup_manifest, rollback_bulk_exports, invalidate_all_auth_sessions, audit_hold_restored_events, migrate_audit_schema |
| Trusted Work repair | fix_attachment_paths, backfill_comment_concepts; future mode only admits work, without parsing/model calls in the CLI |
| Existing durable admission | reparse_mail_documents → Pipeline.regenerate; reprocess_table_documents → authenticated bulk Job |
| Read/helper | __init__, audit_delivery_status, audit_glossary_identities, audit_operator_common, backup_totals, check_archive_runtime, check_integrity, check_stuck_documents, collect_diagnostics, export_okf, generate_sbom, glossary_migration_metadata, inspect_attachment_scope, install_archive_runtime, prepare_diagnostics_dirs, probe_local_qwen, validate_storage_mode, wait_for_qdrant |

Guard выполняется до первого writer/provider effect и использует выбранный env-файл
или явно переданные Settings. `collect_artifacts` без `--apply` остаётся preview.
Включение replica runtime требует O2 replacements для maintenance/backup/restore
и verified adoption существующего NULL-generation корпуса; старые команды нельзя
разблокировать флагом обхода. Viewer GET не запускает legacy canonical backfill:
он возвращает `legacy_corpus_adoption_required` (409). Combined-поведение сохранено.

Export scratch — per-instance local storage, а не RWX artifact root. Worker
обходит не более 100 attempt-каталогов за tick с курсором; активный builder держит
kernel lock. Очистка после смерти процесса проверяет marker, host и containment,
не следует symlink/junction и не меняет SQL receipts/blob bindings. Diagnostic
subprocess scratch и cross-replica capture остаются отдельным R4 контрактом.
