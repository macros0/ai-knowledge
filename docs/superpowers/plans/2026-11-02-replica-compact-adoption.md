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

- [x] Add explicit versioned recipes for legacy public queues/document intents,
  preserving approval decisions and immutable accepted inputs; unknown recipes
  remain held/refused. Verify interrupted handoff resumes without duplicate work
  or changing the original intent, and no work executes during adoption.
- [x] Exercise a populated legacy compact source and NULL/missing generations
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
- [x] Complete legacy local diagnostic journal/input migration before allowing
  protocol1 pending bundles. S4 migrates ready archives, not recorder FS history;
  a request/cutoff alone cannot prove that history reached the shared SQL store.
  Task3a.1 below adds that verified conversion; unconverted protocol1 still refuses.
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
The subsequent journal/input migration is recorded below. This SQL handoff
milestone alone does not establish that conversion or complete Task3/O2.

### Task 3a.1: Legacy diagnostic journal and pending bundle inputs

Implementation decision (2026-11-03): extend the existing verified S4 copy and
atomic cutover. A separate unfenced importer would duplicate ownership/source
comparison and backup logic. Merely setting artifact_version2 or importing live
segments with fresh timestamps would lose pending-request cutoff/TTL semantics.
Use S4's private plan, source inventory, target read-back and one cutover instead.

**Files:** create `app/services/artifacts/diagnostic_migration.py` for read-only
legacy inventory/SQL import; `app/services/diagnostics/frozen_bundle.py` for the
version2 durable completion contract; modify S4 compiler/coordinator, diagnostic
queue/runtime registry and compact handoff. Add isolated unit/native tests and
update the existing security/procedure/evidence documentation.

1. Inspect backend local event/control/expiry files and an optional explicitly
   selected frontend spool. Canonical bounded regular files only; no links,
   truncated/changed input or unknown paths/protocols. Record the complete input
   inventory and relevant diagnostic SQL revision in the private S4 plan. Active
   captures/unreconciled stop journals must be stopped/reconciled with existing
   audited source recovery before planning; expiry alone grants no takeover.
2. Preserve every retained event byte in bounded immutable SQL segments at
   cutover, retaining source times/expiry and honest unknown recorder coverage.
   No TTL refresh, pruning or silent sampling. Raw source blobs participate in
   the existing S4 target-copy verification and backup source digest. Extra or
   changed files/SQL after planning prevent completion.
3. Prepare each eligible queued local diagnostic bundle from the old retained
   inputs and its original request/cutoff, using existing safe supervised snapshot
   and fixed-entry ZIP builder. Preserve partial/gap metadata. The prepared bytes
   are frozen input, not a ready/downloadable job: keep its original status,
   owner, approval and request. Copy/verify them with S4 and bind a strict receipt
   under a separate frozen-input logical name; flip storage protocol only in the
   atomic verified cutover. No queue handler/model call or work admission occurs.
4. Add diagnostic_bundle payload version2 for frozen inputs. The matching new
   worker checks the exact receipt/binding and complete archive SHA/size under
   ownership, then publishes the ready binding/audit/Work success atomically.
   It must never fall back to collecting current events. Existing version1 stays
   compatible; compact handoff emits version2 only for a verified frozen receipt.
5. Migrate an existing frozen binding/receipt and pending work digest consistently
   on later supported S4 transfers. Retain original source files. Source-off cold
   worker execution must prove the original before-cutoff events survive and
   after-cutoff events stay excluded. Unknown/owned operations stay refused.

- [x] RED old local journal/queued bundle remains nonportable; implement S4
  inventory/target-copy/atomic SQL conversion and GREEN source-change/rollback tests.
- [x] RED missing version2 worker/handoff, then GREEN cold-source execution,
  immutable input validation, old version compatibility and lost-owner refusal.
- [x] Native PostgreSQL real Alembic/copy/cutover/held handoff/release/work proof;
  relevant S4/diagnostic/queue regressions, project check, private-value/link/hash scan.
- [x] Update SECURITY/runbook/evidence and commit owned files. Actual populated
  source-off Kubernetes/provider/independent restore acceptance remains Task3b.

Verification:174 distinct Windows cases passed (22 new migration/worker cases
and152 existing S4/diagnostic/compact regressions);6 native PostgreSQL cases passed
with the real Alembic chain. The PostgreSQL suite includes actual conflicting
diagnostic UPDATE/55P03, complete SQL rollback/same-plan retry, cold backend and
frontend input, stale-worker fencing and second-store migration. Project checks
passed with audit0/frontend756 passes plus1 existing skip/Ruff. The owned private
PostgreSQL cluster stopped normally; source data/logs remain retained. See
[bounded evidence](../reports/2026-11-03-replica-legacy-diagnostics.json).

### Task 3b: Populated compact source-off rehearsal

Operational decision: create a new synthetic compact source and a separate new
recovery target. Do not repurpose the retained source7/target8 installations as a
compact source: they already have replica identities. The predecessor's recorded
`21f5a6d` image predates rpl05 and is a candidate for the real rpl04 compact source;
its availability, image digest and actual schema/code must first be verified.
No new upload of an older source snapshot is implied by permission for 5f5c43d.

- [x] Complete full current backend CI (all collected nodes, no fail-fast),
  combined coverage gate and source-byte comparison. Keep real/native/provider
  checks distinct from optional unit-suite platform/provider skips. Build the
  current runtime from the exact approved source only after those results.

  Verified `5f5c43d`:6610 exact collected/executed node IDs,6390 passes,
  220 explicit skips (203 PostgreSQL opt-in,12 platform,5 real ENOSPC opt-in),
  zero failures/errors and87.62% coverage against85%. All2418 Git source files
  remained unchanged. The new runtime image was built from canonical Git bytes;
  its572 copied files, real rpl05 Alembic head and build fingerprint were checked.
  The temporary CI capacity operation has restored the prior2/2/2 roles and
  verified the same PVCs/runtime and nine unchanged canonical tables. See
  [full Linux CI and image evidence](../reports/2026-11-03-replica-full-linux-backend-ci.json).

- [x] Reserve two fresh owned namespace identities, operation journals, volumes
  and scoped credentials. Prove capacity without touching foreign workloads.
  Restore any temporary capacity operation's original counts and verify its
  runtime/PVC/canonical data before starting a separate rehearsal operation.
- [x] Verify the old source image and official rpl04 schema. Populate only
  synthetic accepted legacy files/SQL/Qdrant data. Include a completed legacy
  NULL-generation document, an interrupted accepted checkpoint, a pristine queued
  export, a stopped diagnostic capture with a pending protocol1 archive and an
  awaiting-approval job. Fixture creation must be explicit and recorded; never
  alter an installed source identity or running ownership to fit a test.
- [x] Establish a functional compact baseline (search and original download
  hashes), then stop every source writer physically. Record UID/spec/process/
  provider observations, not expiry-based declarations. Freeze the complete
  source inventory and accepted queued inputs after that stop.
- [x] Produce an independently encrypted PC backup before schema mutation.
  Reuse the authenticated backup codec and native provider primitives, but do not
  fabricate a replica identity to pass `replica_backup.sql_source`: that recipe
  deliberately requires an installed rpl05 runtime. The compact recipe must bind
  its own original installation/schema/source inventory and be restored and
  verified before it can authorize conversion.
- [x] Restore that copy into the fresh target with application roles at zero
  and the original source unavailable. Verify full SQL logical rows, original
  files, Qdrant data and diagnostic/control inputs before migration. Legacy FS
  metadata needs explicit nanosecond mtime preservation: queued exports bind
  `mtime_ns`, and baseline/capture retention must not be renewed by copying.
  A tar float timestamp alone is insufficient proof. Never overwrite the source.
- [x] Run the official Alembic chain from the actual restored schema to head;
  no stamp/create_all workaround. Run S4 plan/copy/target read-back/cutover with
  the real independent backup, physical writer proof and frozen diagnostic
  inputs. Preserve NULL-generation originals and accepted checkpoint semantics.
- [x] Execute compact inspect/handoff/adopt against the actual target PostgreSQL
  and selected provider. Check held epoch1, pristine versioned queued WorkItems,
  preserved approvals/auth/reference/audit state, same-operation retry and zero
  execution before release. Publish only the matching common runtime identity.
- [x] Verify provider transport/permissions and the exact current image before
  explicit release. Start 2 frontend/2 API/2 worker instances; prove original
  search/download hashes, original diagnostic cutoff, queued export/resume,
  awaiting-approval nonexecution and one new publication. Compare generations
  before execution separately from legitimate queued-work changes afterwards.
- [x] Preserve source/backup/target evidence and restore intended topology.
  Publish bounded observations and explicit scope; do not qualify RWX/CSI,
  external providers, physical HA or an issuer from this one managed/stub recipe.

The Linux CI operation is separate from this rehearsal. Passed temporary unit
inputs may use pytest failed-only retention on bounded tmpfs; failed inputs,
JUnit and coverage stay on the VM. Raw CI payload export to the PC remains
prohibited. Resource exhaustion or an unobserved terminal outcome fails the CI
gate and must not be reported as a successful skip.

2026-11-03 actual rehearsal: [bounded evidence](../reports/2026-11-03-replica-compact-independent-restore.json). Independent source-off restore, official schema/S4 actual exit23/resume, held atomic three-intent handoff/adoption/retry and2/2/2 functional acceptance passed. The accepted checkpoint text and original diagnostic cutoff/13 events/expiry survived; awaiting-approval remained unexecuted and a new DOCX was published/searchable/downloaded by SHA. Image-default7-Zip mismatch was reconciled by an audited held rebind. Actual prefix-scoped S3 readiness refusal led to b5a9775 RED/GREEN fix,36 Windows and190 Linux passes without skips, a verified new image and another held upgrade/release. The new target is gracefully stopped at held epoch3 with all four PVCs retained. The prior training installation has returned to2/2/2 with the same runtime/PVCs and nine unchanged canonical tables. The single final branch review found five Important issues; the consolidated RED/GREEN fix pass is complete. Full ccfcf9e CI executed6652 exact cases:6431 passes/221 explicit optional skips/zero failures/errors and87.67% coverage; all2429 Git files stayed unchanged. The exact573-file runtime was built and imported, and the separate final-CI capacity operation restored2/2/2 with the same runtime/PVCs and nine unchanged canonical tables. Cancelled/failed earlier runs retain their original outcomes. Current application/provider qualification and profile certification remain separate gates.
