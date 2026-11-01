# Transactional work and coordinated runtime (R1–R4)

`WorkItem` is an internal execution ledger, separate from public bulk `Job`
approval/status records. The package starts no executor during import and is
not activated in a distributed application runtime yet. R2's closed future branch
integrates document admission and publication fencing; R3 separates runtime roles,
registers background handlers, R4 coordinates global capacity,
and O2 adds installation maintenance epochs. Existing replica/production gates
remain closed. `WorkClaim.maintenance_epoch` is zero until O2.

The closed API lifecycle owns request services only;
`python -m app.worker` owns an independent heartbeat, required-storage readiness,
consumers and a deduplicating periodic scheduler. Its internal health port8001
must not be exposed through the public Gateway. Currently registered handlers
cover documents, safe checkpoint retirement, duplicate/visibility projections,
portable exports and diagnostic bundles, bulk coordinators/items/reconciliation,
scope changes, auth/chat/export/trash retention, generation cleanup and artifact GC.
Canonical repair work allows only attachment-path and comment-thread operations.
It freezes base/canonical/original/vector identities and creates a fresh owned
generation per reclaimed fence. Provider calls run outside SQL; immutable batches
bound to that repair generation cache accepted embeddings by input/text hashes.
Reclaim transfers their bindings before abandoning the old candidate. Publication
checks the repair proof and acknowledges the work atomically. Processing-scope
repairs retain their existing unsupported status. Future repair CLIs enqueue work
instead of constructing providers/executing locally. A bounded reconciler
abandons terminal repair candidates through the coordinated document-intent lock,
so cancelled/failed attempts can be cleaned without changing the active version.
Index maintenance sweeps at most100 documents per claim and creates scoped
sparse/relations/chunks work for versioned active generations. Its versioned
cursor and child admission commit together. Each child rechecks the frozen
generation under the read lock; stale intents finish without modifying a newer
publication. Direct future whole-corpus backfills require scoped ownership.
Legacy NULL-generation documents require offline adoption before activation.
Reference/glossary translation work keeps the existing admin authorization and
result counters. API admits a public Job and hashed private work input; the UI
waits for that Job without holding the original HTTP request. Reference work
processes at most10 IDs per claim, glossary requests at most10 terms. Accepted
translation writes, audit and versioned cursor share one SQL transaction, with
the model call outside it. Existing source/target/reference revision and human
translation checks remain in force. A bounded reconciler closes failed public
jobs after worker failure. Global model admission covers these calls as described below.
Diagnostic maintenance scans at most100 bundle records per claim with a durable
cursor, retires expired portable bindings with audit, and reconciles terminal
bundle work. Existing read leases protect downloads. Portable terminal metadata
retains the existing seven-day period and is not pruned with pending receipts.
Export builders use private local `exports/attempts-v1` UUID directories. Kernel
locks protect live attempts and release on process death. Worker-local cleanup
pages100 directories, checks exact ownership/host/containment and rejects links
and unknown markers. It never retires blobs or modifies public completion. An
unavailable local cleanup is retried independently of the work outcome. This
root is per-instance local storage. Diagnostic parent/child scratch uses separate
ownership locks and cleanup, described below.
Legacy operator writers refuse the future runtime until the O2 maintenance
protocol supplies validated replacements. Commands use their selected runtime
settings, including explicit settings/env files. Supported repairs enqueue work;
read-only inspection remains available. See the complete CLI inventory in
`docs/REPLICA_STATE_INVENTORY.md`. Legacy canonical reconstruction on a viewer
GET is also disabled; verified corpus adoption is required before activation.
Deployment, provider, corpus-adoption and maintenance acceptance remain R5/O1–O3
activation requirements. This list does not declare distributed readiness.

`coordinator_session` requires a fresh transaction and locks global admission
before parent Work, then Job/document state. It cannot be nested inside a
Work-first execution context. `WorkDeferred` yields a coordinator's slot with a
bounded delay; cooperative waits do not consume the transient-failure attempt
budget, while every claim still gets a new fence. Bulk item receipts retain
input ordinals, so public progress can be reconstructed after a failed write.
Exports rebuild from frozen original bindings in unique per-attempt scratch;
archive bindings, public completion and work success commit together. Future
delete/restore enqueues latest-state visibility projection in its SQL transaction.
Diagnostic bundle admission is globally bounded and does not reserve an API-local
queue entry. A worker prepares a unique snapshot/part per attempt, then commits
verified ZIP bindings, public readiness, audit and Work success atomically.
Legacy bundle startup/recovery is disabled for the future schema. The worker
command owns audit preflight and its recorder lifecycle; cross-replica diagnostic
inbox and private instance paths are described below.
Scope work keeps its accepted input immutable across reclaim and trash/resume.
Transient errors pause the document without discarding its confirmation, including
when retries are exhausted before generation allocation. Explicit cancel-update
closes the scope receipt and restores the published snapshot. Reusing a current
published scope after its review changes requires verified publication proof.

Apply Alembic through `rpl03b_diagnostics` before using this build on an existing database.
The migrations preserve nonempty Job rows, create claim/reclaim indexes, and
refuses downgrade while **any** work history exists. Work dedupe tombstones must
not be removed to make a rollback appear safe. Restore a coherent backup instead.
Offline artifact migration now fingerprints this ledger as part of its source
revision; source/runtime schema must include the current head.
Private `WorkProgress` stores bounded versioned cursor JSON separately from the
immutable work input. Its input fingerprint and owner/fence are checked on every
read/write. Cursor acceptance and each SQL side effect share one transaction;
reclaim resumes the accepted cursor. Progress/history cannot be dropped to force
a downgrade. Export retention pages100 jobs per claim and preserves existing
materialized readers through the artifact read leases.

`enqueue_existing_document` locks global admission, pending work, then Document.
It commits the queued status, retained published diagnostics and frozen private
processing policy with the work intent. Upload also commits the original binding
and idempotency receipt with admission. Confirmed attachment selection and its
audit event share that transaction; a full queue leaves the review editable.
The document pending budget and model/rate capacity are global.
Bulk Job approval/cancellation preserves four-eyes and commits its private `bulk`
intent with the public transition and audit. The registered nonblocking bulk
handler tracks child completion through durable receipts.

`run_document_job(WorkClaim)` verifies the bound original hash, allocates a fresh
generation for each new claim/fence and reuses accepted portable checkpoints only
when source/parser/scope match. Recovery preserves accepted concept identities,
source graphs and table rows, and rebinds registered table references to the new
generation. `document_execution` guards SQL session entry/flush/DML/commit, including
nested savepoints. Generation writers require the matching claim for owned data.
Canonical publication and work success are one transaction. A document work must
not acknowledge success merely because the pipeline returned in a paused state.

Deletion invalidates work before the Document lock; cancellation can restore the
published diagnostics atomically without removing an unrelated worker's cache.
Inactive work generations keep exclusion identity after byte cleanup. Hard purge
removes private work inputs and keeps only IDs/fence in `deleted_work_generations`,
without an FK to the deleted document. These minimal tombstones cannot expire
until late external writes are provably impossible. The offline migration source
fingerprint includes both inputs and tombstones.

Publication records bounded `duplicate_refresh` intents instead of starting
post-success guarded transactions. Its handler coordinates checkpoint retirement
by expected generation. Distributed startup does not run
legacy global document/Job resets; a submitted review with accepted work cannot
be revoked by review-lease recovery. Existing combined behavior remains intact.

`enqueue/claim/renew/assert_owner/finish/cancel` take a caller-owned SQLAlchemy
Session and never commit it. Enqueue shares the owner's transaction and compares
canonical JSON on dedupe conflicts (JSON booleans and numbers are distinct).
PostgreSQL claims use `FOR UPDATE SKIP LOCKED` plus conditional UPDATE; DB UTC
time, owner ID, live lease and monotonic fence govern renew/completion. Reclaim
increments fence and attempt count. SQLite is only a single-writer dev/unit
fallback; it is not evidence for distributed contention or recovery.

Cancellation is durable and invalidates the publication guard immediately.
Queued cancellation is terminal; a running handler can acknowledge cancellation
while still owning its lease. Expired cancelled owners are retired in bounded
pages during polling. Unsupported kind/version remains queued, is annotated
`unsupported_payload`, and can be claimed by a subsequently compatible worker.
The annotation never prevents a compatible claim or deletes accepted work.

The explicit HandlerRegistry maps kind/version to trusted Python functions;
payload data cannot select arbitrary imports/callable names. Payloads are bounded
JSON; application-specific payload validation and authorization belong to each
handler/admission path. No public queue API or new permission is introduced here.

Worker executes a handler outside SQL, renews in short transactions, and
acknowledges only a still-live claim. A handler TimeoutError is a handler failure,
not a heartbeat timeout. After DB/lease loss, it leaves the attempt unacknowledged
and holds its local slot until the old handler returns. Completion-required global
model admission is separate, as described below. Generation fencing
excludes late document writes, but cannot cancel a provider's external call.
Shutdown stops claims/renewal without a late ACK callback.
Heartbeat row-lock waits are bounded on PostgreSQL. Only a `55P03` lock timeout
retries in a fresh transaction; the same owner/fence and DB-time lease check must
still pass. Other database errors propagate to the existing bounded coordinator
backoff. A completion marker authorizes only its original SQL transaction, never
a later transaction reusing the same Session. R3 supplies process grace/health,
lifecycle and handler registration.

Defaults: lease120s, heartbeat30s (at least three intervals per lease), poll1s,
max_attempts3, retry backoff5/30/120s. Max attempts counts total claims when handling
a classified transient failure; the third backoff entry is available with a
larger configured cap. Only explicit storage/model/transport-unavailable errors
retry. Unclassified handler errors become `handler_failed`; raw exception text,
payloads and credentials are never persisted/logged. Coordinator outages use
interruptible backoff capped at30s. These defaults need R5 workload measurements.

Run `tests/test_work_store.py`, `tests/test_work_worker.py` and
`tests/test_work_migration.py` for unit/schema checks. Actual PostgreSQL probes are
`tests/test_work_store_postgres.py`; pass a private
`OKF_ARTIFACT_PG_URL_FILE` pointing to an isolated loopback `replica_test_<runid>`
database and `OKF_REPLICA_PG_REQUIRED=1` to fail if the target is absent.
The harness owns a fresh random schema and only drops that schema. Multiprocess
tests use barriers/pipes and explicit DB lease expiry rather than timing guesses.

`test_work_recovery_postgres.py` additionally requires
`OKF_REPLICA_QDRANT_URL=http://127.0.0.1:25433` for the explicitly owned test server.
It owns random `replica_test_<uuid>` collections and shares a test filesystem root
between spawned processes. That root exercises adapter semantics; it does not
certify a Kubernetes RWX/CSI provider or production S3 durability.

R4 foundation uses caller-owned PostgreSQL transactions for resource slots,
rolling rate windows and configuration revisions. Slots have stable IDs,
owner/fence and DB-time expiry; a protected deadline can outlive the owner lease.
Provider executions additionally require completion: uncertain model calls stay
quarantined until a trusted completion or O2 reconciliation, even after expiry.
No client timeout proves a remote model execution ended. Future LLMClient calls
share `LLM_GLOBAL_MAX_CONCURRENCY` (default1), including the local-profile and
source-assessment paths. Local pools remain secondary limits. Normal terminal
replies and definite request rejection release capacity; ambiguous transport
errors and process death do not. Capacity admission refuses an open SQL session.

Future rate limiting uses hashed actor/action keys and weighted rolling events.
Bulk operation/document budgets commit together. Catalog writes bump their
configuration revision with the existing reference/audit transaction; stopword
cache hits compare that revision. Existing UI dictionary reads already use SQL.
Diagnostic instance coordination remains open; none of this enables distributed
deployment.
Checks: `test_replica_coordination.py`, `test_replica_model_budget.py`,
`test_replica_coordination_migration.py`, and explicit isolated PostgreSQL
`test_replica_coordination_postgres.py`. The migration refuses lossy rollback.

`ChatRun` binds a unique actor/client-attempt identity to one assistant message.
Its frozen input digest, owner/fence/DB lease and generic chat-capacity slot are
admitted with history in one caller-owned transaction. Retries never replay a
completed/interrupted model request. A request-local heartbeat renews ownership
and observes cancellation from another API. History/source/scenario writers
validate the private request token; late/expired owners cannot complete a turn.
The existing finish wrapper commits managed history and run completion together.
Legacy direct history admission refuses the future runtime. Private tokens are
not stream fields. A matching terminal owner can retain its own partial draft
only in an empty stopped/failed message. It cannot overwrite completed history.

Soft deletion cancels active requests. Retention follows resource→Run→history
locking and removes execution identities with their messages. A bounded25-row
worker reconciliation marks expired runs interrupted/stopped, with no model
replay or uncertain-provider slot release. Previews share global chat/model
capacity and preserve their no-history contract. `rpl03a_chat_runs` refuses to
discard accepted execution identities, including completed ones. Checks include
`test_replica_chat_runs.py`, `test_replica_chat_api.py`,
`test_replica_chat_migration.py` and explicit isolated PostgreSQL
`test_replica_chat_postgres.py`; direct API processes are not a multi-Pod proof.

Future auth sessions and user-block expiry use DB time. Identity/group/block
state is read again on each API request; common deployment role mapping is not
cached on the user session. PostgreSQL process checks cover changed groups,
block/unblock, missing roles and logout without restarting the other API.

The current future catalog cache epoch is `references`, bumped for new resources
and edits in the same SQL transaction. Old localization epoch rows are retained,
not reset/deleted. `/settings/revisions` exposes only known decimal-string
counters with private/no-store; legacy mode returns disabled without querying the
future tables. Browser metadata checks run about every30s while visible and on
foreground/navigation boundaries, coalesce requests and abort on cleanup.
UI dictionary failures retry without permanently caching a missing override.
Epoch changes refresh lookups and language options; controlled editor selections
are retained. Tag counts also use a monotonic30s cache TTL, checked on successful
polls (up to roughly60s between visible count refreshes while healthy).
All lookup reads bypass HTTP cache; old in-flight generations cannot replace new
ones. Backend glossary snapshots already check their own SQL revision; the new
two-process PostgreSQL probe verifies both warm caches after a remote alias edit.
This changes presentation cache freshness, not reference edit/CAS permissions.

Future durable audit senders run in API and worker entry points after mandatory
preflight. A dedicated PostgreSQL session holds the advisory lock and executes
all queue reads, attempts, retry updates and delivery markers. A standby sender
does not read the queue. Connection loss discards the physical connection; no
replacement connection can mark the previous write. A write/marker crash may
repeat the exact immutable payload and event ID. This is not an external ACK.
Stop does not release a blocked sender's connection while it can still finish;
a stopped sender never reacquires ownership. New events, eligibility, backoff
and local delivery markers use DB time in the future runtime.

The lock domain is the current database/schema, independent of local disk and
process role. Direct PostgreSQL or session pooling is required; transaction
pooling does not preserve this contract. Budget one checked-out DB connection
per active sender plus candidate connection activity on standby instances.
Preflight checks lock access; a busy peer is a valid standby, unavailable lock
transport is a startup failure. Legacy combined mode retains its local file lock.
The security outbox and restored/rollback holds already live in SQL. The separate
legacy diagnostic `control/deferred.jsonl` must be reconciled through O2 adoption;
the dispatcher alone never acknowledges those legacy control intents.

Future diagnostic control now reads the active capture from SQL across API/worker
instances. Start/stop and their audit commit together; a failed stop disables the
local selector and returns an error while retrying, without creating an accepted
file-only journal entry. Local selectors have a maximum ten-second monotonic
lease sampled before SQL; stalled refreshes cannot extend it. Frontend control
uses the same SQL state. Browser membership checks revalidate active state/DB
expiry after row locks, and join/report budgets share the rolling limiter.
Stopping a replica drains its recorder without ending the shared capture.
Events now live in immutable bounded SQL segments; final ZIP bundles remain
portable blob artifacts. A SQL repository serializes shared quotas and retention
without creating a recorder or local directory. Frontend inbox publication and
payload clearing share one transaction; replacement maintenance workers retry
accepted batches without the original API disk. Previously admitted events can
finish after capture stop; fresh stale writes are refused. Retention records
loss before retiring expired ordinary pending events. Shared counters are
allowlisted and distinguish unknown history, loss and unavailable frontend state.
Read views verify segment hashes and materialize private input files; supervised
children preserve the existing component/path validation and delete inputs on
release. Per-instance scratch has same-host ownership markers and separate OS
locks for its recorder, live bundle parent and child; a child watchdog survives
parent death. Cleanup retains busy/unknown/foreign/linked trees and validates
absolute containment before removing an inactive instance. Runtime shutdown does
not read SQL control. Offline filesystem export marks the missing shared SQL
events explicitly. O2 adoption of legacy deferred journals and R5/O3 deployment
and provider acceptance remain required before activation.
