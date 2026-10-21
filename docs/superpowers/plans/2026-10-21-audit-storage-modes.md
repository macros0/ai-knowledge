# Audit storage modes Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for integration and superpowers:dispatching-parallel-agents for independent file scopes. Steps use checkbox syntax for tracking.

**Goal:** Implement DB-only, external-only and both, retaining SQL outbox and identical audit coverage.
**Architecture:** A shared effective-mode resolver selects sinks after event creation. Independent outbox v2 stores delivery identity without an AuditLog FK. Domain snapshots and migration receipts preserve features currently reading audit history.
**Tech Stack:** Python/FastAPI, SQLAlchemy/Alembic, SQLite/PostgreSQL, Next/React, Helm.
**Spec:** docs/superpowers/specs/2026-10-21-audit-storage-modes-design.md

## Global Constraints

- AUDIT_DB_ENABLED=true and AUDIT_STDOUT_MODE=disabled remain defaults. false+disabled fails before workers.
- SQL technical queue remains; no GC, historical backfill, direct OpenSearch client, main DB migration, deploy, push or merge.
- Same event catalog in three modes; mandatory security audit fails closed, denial preserves denial.
- Preserve canonical bytes/identity, pending/held/written states, strict schema checks, bounded denial profiles, single writer.
- Work only in codex/audit-storage-modes worktree; unrelated main changes stay untouched.
- User explicitly approved plan and autonomous implementation; no further implementation-plan approval round.

## Review Focus

- Old 066 migration importing mutable runtime helpers must remain reproducible; fresh v1→v2 chain test.
- Existing stopword history IDs must still target same snapshots after migration; import/rollback test.
- external-only must never insert AuditLog through processing_scope or auth; scenario tests.
- DB-only auth now uses bounded denial path; permission/profile checks must run before mutating startup.
- Both→DB with pending must pause without deleting/marking sent; re-enable resumes unchanged identity.

## Interfaces and Ownership

Task 1 owns models.py, audit_schema*, audit_hold*, new Alembic version and schema tests. Public upgrade_audit_schema(connection) upgrades legacy/v1/v2 to v2. validate_audit_schema(connection) requires v2. Old 066 imports frozen audit_schema_v1 helper. validate_outbox_contract(connection) recognizes exact v1/v2 for hold; runtime explicitly requires v2. Outbox fields: id:int PK, event_id:str unique nonnull, created_at:datetime nonnull, existing payload/status/attempt_count/next_attempt_at/last_error_code/stdout_written_at; no audit_id/FK.
Domain models: StopwordImportSnapshot(id:int PK, created_at, username, locale:str, kind:str, old_words:JSON list, new_words:JSON list, import_meta:JSON nullable). GlossaryMigrationReceipt(checksum:str PK, result_checksum:str, created_at). No FK to audit_log. Shared model names/fields are fixed for tasks 1/2.
Task 2 owns locale_service.py, migrate_glossary_identities.py, glossary_migration_metadata.py and focused domain tests. Uses models above; task 1 handles historical backfill.
Task 3 owns frontend UI/api helper/i18n and Helm/env config, tests. Backend GET /audit/config returns effective_mode=db|external|both, db_enabled:bool, stdout_mode, stdout_format. Existing list API unchanged.
Task 4 (controller) owns audit.py, audit_modes.py, config.py, audit_runtime.py/outbox.py/security.py, auth/users/main/context hooks, processing_scope.py, API route, scripts and integration tests. get_audit_mode(settings) exposes db_enabled/stdout_enabled/effective_mode. Domain snapshots unaffected by sink choice.

### Task 1: Independent queue and versioned migration

- [x] Write tests: exact v1 upgrade preserves bytes/all states/IDs, standalone v2 without log, unknown drift fails unchanged, repeat upgrade, snapshots preserve historical IDs, receipts choose last valid result.
- [x] Run RED on new cases with isolated SQLite DBs.
- [x] Implement independent models and frozen v1 schema/066 helper; add v2 migration after current head; update dev migration path.
- [x] Implement strict v2 schema checks and model-free v1/v2 hold, sequence repair for PostgreSQL and independent snapshots/receipts backfill. Validate source rows before changing schema.
- [x] Run GREEN: schema/migration/restore tests; PostgreSQL on isolated runtime if available.

### Task 2: Domain working history independent of audit

- [x] Write RED: external-only stopword import/history/rollback and repeated glossary migration; include old history IDs.
- [x] Write StopwordImportSnapshot in import transaction and read snapshots for history/rollback preserving DTO and validation.
- [x] Write/read GlossaryMigrationReceipt atomically; update simulation metadata table creation.
- [x] Run focused domain tests GREEN, verify no requirement to keep local AuditLog.

### Task 3: UI and deployment choices

- [x] Write RED: config loading, unknown state and external-only explanation; Helm three combinations plus invalid combination and forbidden runtime.config key.
- [x] Add getAuditConfig and SecurityPanel mode/explanation, retain history/CSV/block controls and translate RU/EN, generate backend dictionaries through project generator.
- [x] Add auditDbEnabled Helm bool/default/config/schema checks and .env examples.
- [x] Run frontend tests/lint and Helm tests; no dependency installation or edits in shared node_modules.

### Task 4: Shared event routing and transport lifecycle

- [x] Write RED: three settings combinations, forbidden fourth, event sink counts and atomicity, auth parity, scope event routing, paused/resumed pending.
- [x] Add audit_modes resolver and Settings validation; build event independently of persisted log, route selected sinks in one transaction; returned id nullable, event_id distinct.
- [x] Enable mandatory auth audit and request context in all modes, preserve denial bounded lifecycle. Route scope audit through shared service.
- [x] Dispatcher/status use v2 id/timestamp. Preflight probes only selected sink writes plus correct sequence rights; DB-only validates denial driver profile.
- [x] Add protected no-store GET /audit/config; update operator CLI mode/status and dev migration.
- [x] Adapt old tests whose asserted behavior is intentionally changed, retaining failure coverage; run focused audit/auth/domain/process tests GREEN.

### Task 5: Integration, documentation and review

- [x] Update SECURITY.md in same changeset and AUDIT_DELIVERY/README with modes, migration, retention limits and rollback boundary.
- [x] Run focused suites, full backend suite (backend cwd), frontend tests/build, Ruff, Helm, project checks. Capture logs under this worktree tests/tmp. Record unavailable environments separately.
- [x] Fresh reviewer audits entire worktree diff against spec and review focus; fix Important/Critical with regression cases.
- [x] Local focused commit(s) after passing required checks. No push/merge/deploy.
- [x] Report branch/worktree, test evidence, target OpenSearch acceptance boundary and any remaining limitations.
