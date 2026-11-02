# Compact corpus runtime adoption — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline. Read the parent O2 requirements; prove each implementation with RED/GREEN. Final branch review remains separate.

**Goal:** Supply the missing offline SQL/CLI bridge from a nonempty compact corpus after verified S4 cutover to a held replicas-v1 installation, preserving NULL-generation documents and queued work.

**Architecture:** Keep fresh bootstrap unchanged. An explicit owned operator captures a bounded public snapshot of known current SQL schema, selected S4 migration and logical rows. Adoption locks the source, compares that snapshot, validates storage receipt/known queued work versions/all stop receipts and installs common identity plus held epoch1 atomically with audit. A retry must match the same operation, owner, source proof and target; a separate explicit provider/recovery/release coordinator remains required.

**Tech Stack:** Existing SQLAlchemy/Pydantic/Alembic head, MaintenanceOperator/advisory lock, bootstrap receipt/identity publisher. No new dependencies or startup bypass.

**Spec:** [Approved O2](2026-10-31-kubernetes-replicas-operations.md), [storage migration](2026-10-31-kubernetes-replicas-storage.md), [design](../specs/2026-10-31-kubernetes-replicas-storage-design.md).

## Global Constraints

- Only explicit offline operator transactions; no auto-migration, no schema stamp, no force adoption, activation, claim clearing or data deletion.
- Require exact current schema/known tables and columns, completed verified S4 cutover/backup evidence and selected descriptor. Existing replica identity/maintenance state is refused except same held retry.
- Snapshot hashes all logical application rows, including auth/reference configuration. Audit history uses captured integer upper bounds so the new adoption audit can append without weakening original-history verification.
- Preserve missing/NULL generation references and queued jobs; no generation synthesis, reindex, provider call or work execution. Unknown queued kind/version and uncertain work/chat/provider ownership refuse adoption.
- Runtime identity/settings/storage receipt and every writer stop/drain claim are bound to the operation proof; they are trusted coordinator inputs, not independently collected physical evidence.
- The CLI selects a fully specified literal runtime file: ambient variables/root `.env` and dotenv interpolation cannot redirect the operation. Unknown commit outcomes report an unknown hold status, not a confirmed fence.
- New identity stays held; normal fresh bootstrap remains strict. Backup restoration, real RWX/S3 qualification and physical writer proof still belong to actual deployment acceptance.
- Use only isolated synthetic fixtures. Working PC corpus and existing training namespaces/PVCs remain unchanged. Original4 dirty user files preserved; production schedule OFF.

## Review Focus

1. Identity publication fails after SQL commit: same held operation retries without a second audit or changing source rows.
2. A NULL/missing generation, legacy binding, reference configuration, old auth/audit row or queued payload changes after snapshot: refuse rather than silently adopting another corpus.
3. Unknown schema/extra columns, unverified S4 history, active or uncertain provider ownership: refuse, retaining data.
4. Another operation/owner/config or a released installation cannot reuse the source proof.
5. Audit/publisher failures cannot expose an unfenced new runtime, and diagnostic errors must not echo private Settings/input values.

## Task 1: Held adoption SQL contract

Create `backend/app/deployment/compact_adoption.py` and `backend/tests/test_replica_compact_adoption.py`.
`capture_compact_source(session, settings) -> dict` returns strict public version1 snapshot (installation/schema/store/migration/selection/plan/backup/logical SQL hashes, source counts and audit upper bounds).
`adopt_compact_runtime(session, settings, expected, storage_receipt, *, operation_id, intended_counts, writer_receipts) -> dict` returns the existing identity document, leaving epoch1 enabled and preserving source data. Use the retained MaintenanceOperator connection; PostgreSQL table locks and complete logical fingerprint check precede mutation. Same held retry verifies its exact audit/source/config and changes nothing.

- [x] Write realistic S4 cutover/NULL+missing-generation fixtures; tests for preservation, stale source/config/auth/audit, wrong owner/operation/receipt, unknown schemas/queues, running ownership, rollback/audit failure, old epoch refusal, same retry and fresh bootstrap remaining strict.
- [x] RED missing module/functions, then implement and GREEN alongside existing migration/bootstrap/maintenance tests; Ruff.

## Task 2: Explicit offline CLI and stronger verification

Create `backend/scripts/adopt_compact_runtime.py` and `backend/tests/test_replica_compact_adoption_cli.py`.
Require explicit runtime env, protected snapshot/receipt files, operation/owner, counts/stop receipts for apply and optional same-target operator DB file. Inspect reads SQL and publishes a create-only snapshot; adopt defaults to dry-run with no DB/file mutation. Apply commits held SQL before create-only identity publication; retry uses the same proof. Fixed public JSON/errors, bounded regular inputs, no root `.env`, no release/scaling.

- [x] CLI tests RED then GREEN, including publication failure/resume and private-input redaction; run relevant unchanged CLI/SQL cohorts.
- [x] Verify PostgreSQL locking/rollback on an isolated synthetic database/cluster; never use the working corpus or PG10 service. Record native proof separately from SQLite tests and actual Kubernetes/provider acceptance.
- [x] Update SECURITY/runbook/parent plan/matrix with exact completed scope and remaining physical acceptance; project check, secret/link/hash scan, commit only owned files.

2026-11-03 verification: [public SQL/CLI evidence](../reports/2026-11-03-replica-compact-adoption.json)
records153 passed/2 existing POSIX-platform skips in the relevant Windows cohort,
18 actual PostgreSQL passes (8 compact cases plus10 unchanged migration/fence
cases), and project checks with audit0/frontend756 passed. The new temporary
PostgreSQL17.11 cluster was stopped with its data/logs retained. S4 used an ordinary
local directory and checksum backup fixtures: no CSI or independent restore
qualification is claimed. Task3 remains open.

## Task 3: Remaining legacy intent handoff and actual deployment rehearsal

The initial SQL phase accepts preserved supported durable WorkItems. Legacy
queued/running public Jobs and processing/queued documents without durable work
are deliberately refused, rather than silently stranded after activation. This
refusal is not completion of legacy queue migration.

- [ ] Add explicit versioned recipes for legacy public queues/document intents,
  preserving approval decisions and immutable accepted inputs; unknown recipes
  remain held/refused. Verify interrupted handoff resumes without duplicate work
  or changing the original intent, and no work executes during adoption.
- [ ] Exercise a populated legacy compact source and NULL/missing generations
  through actual schema/S4/handoff/held-adoption/provider verification/release in
  a disposable target, with independently verified backup and source-off proof.
  Search/download/new publication and queued work require functional evidence.
  Unit/SQL phases alone do not close this O2 gate.

### Task 3a: Reviewed atomic legacy handoff

Implementation decision (2026-11-03, within approved O2): compose handoff into
the held adoption transaction. A separate converter would expose durable work
to a compact source before a fence exists; automatic startup recovery would
hide the input/approval review. Neither fits the approved safety boundary.

**Files:** create `backend/app/deployment/compact_handoff.py` and
`backend/tests/test_replica_compact_handoff.py`; extend compact SQL/CLI and their
native tests. No schema change, new dependency, queue thread or provider call.

`plan_compact_handoff(session, settings, source) -> dict` emits a strict bounded
version1 plan bound to the reviewed source SQL/selection hashes. Entries contain
only recipe/version, owner ID, accepted-input hash and deterministic new Work ID;
private params/policies stay in SQL. The planner compares the complete source
before/after inspection. All eligible orphan intents must appear; changed,
omitted, extra or unknown recipes refuse, never silently sample.

Known recipes reuse existing worker contracts: bulk delete/regenerate/resume;
S4-frozen export; reference/glossary translation; shared-protocol2 queued
diagnostic bundle with its original request/cutoff; queued unowned scope change with accepted plan;
interrupted document continuation with accepted portable checkpoint/generation
scope and frozen DocumentWorkInput. S4 already enriches export source_blob and
converts legacy checkpoints; handoff must verify those SQL bindings, not repeat
copying or alter accepted params. Draft review is never substituted for an
accepted scope. Unknown/owned/running/incomplete recipes remain refused until
explicit reconciliation; lease expiry is not permission to clear ownership.

Add optional `handoff` to `adopt_compact_runtime`. After existing table locks and
original source comparison, reproduce the whole plan from SQL and create only
its WorkItems/frozen inputs. Preserve every original row, including approval,
queued status, NULL generations and accepted payloads. Install identity, held
epoch1 and the one common audit in the same transaction; audit proof includes
the complete reviewed plan digest. A held retry excludes only deterministic new
rows from original-source hashing and separately verifies every new row's exact
spec, frozen policy, pristine queue state and admission timestamp. Never exclude
those IDs from initial source verification or allow a changed plan on retry.

Extend CLI `--handoff-plan`: inspect publishes a create-only plan beside its
source snapshot; adopt/dry-run read bounded strict unique-key JSON and check
source binding. No flag preserves existing orphan refusal. Publication failure
after acknowledged commit can resume with the exact same source/plan/settings.

- [x] RED latent omissions: pending diagnostic bundles, splitting/indexing docs,
  owned scope operations and queued work targeting awaiting-approval jobs.
- [x] RED absent planner/atomic handoff; implement the listed version1 queued recipes,
  complete-source/manifest and same-held retry verification; GREEN.
- [x] CLI RED/GREEN, including failure after SQL commit, missing/changed plan,
  bounded/duplicate-key inputs and no-execution/no-release assertions.
- [x] Native PostgreSQL atomic rollback/locking/retry with populated S4 fixture;
  relevant existing queue/scope/translation/migration regressions; project check.
- [ ] Complete legacy local diagnostic journal/input migration before allowing
  protocol1 pending bundles. S4 migrates ready archives, not recorder FS history;
  a request/cutoff alone cannot prove that history reached the shared SQL store.
  The current handoff refuses protocol1; this required migration stays open.
- [x] Update SECURITY/procedure/evidence with exact scope; retain actual
  source-off Kubernetes/provider/restore/release acceptance as Task3b; commit.

SQL/CLI handoff verification (2026-11-03):176 Windows passes, zero skips/failures,
including the existing bulk, scope, translation, export and S4 regressions;
24 native PostgreSQL passes, including6 handoff cases. Native FK enforcement
found and verified the fix to parent-before-input flush ordering, still inside
one atomic transaction. Root project checks passed (audit0/frontend756 passed).
The isolated cluster shut down normally after its initial30s stop-observation
timeout; the same postmaster completed its47.925s checkpoint, disappeared and
removed its PID file. Stop was observed, not repeated. Data/logs are retained.
See [bounded public evidence](../reports/2026-11-03-replica-compact-handoff.json).
The required legacy local diagnostic journal migration remains open above;
this is progress toward Task3, not completion of all legacy variants or O2.
