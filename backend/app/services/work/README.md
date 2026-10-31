# Transactional work and document execution foundation (R1/R2)

`WorkItem` is an internal execution ledger, separate from public bulk `Job`
approval/status records. The package starts no executor during import and is
not activated in a distributed application runtime yet. R2's closed future branch
integrates document admission and publication fencing; R3 separates runtime roles,
registers background handlers, R4 coordinates global capacity,
and O2 adds installation maintenance epochs. Existing replica/production gates
remain closed. `WorkClaim.maintenance_epoch` is zero until O2.

Apply Alembic through `rpl02a_document_work` before using this build on an existing database.
The migrations preserve nonempty Job rows, create claim/reclaim indexes, and
refuses downgrade while **any** work history exists. Work dedupe tombstones must
not be removed to make a rollback appear safe. Restore a coherent backup instead.
Offline artifact migration now fingerprints this ledger as part of its source
revision; source/runtime schema must include the current head.

`enqueue_existing_document` locks global admission, pending work, then Document.
It commits the queued status, retained published diagnostics and frozen private
processing policy with the work intent. Upload also commits the original binding
and idempotency receipt with admission. Confirmed attachment selection and its
audit event share that transaction; a full queue leaves the review editable.
The document pending budget is global; global model/rate capacity remains R4.
Bulk Job approval/cancellation preserves four-eyes and commits its private `bulk`
intent with the public transition and audit. R3 must register the nonblocking bulk
handler and child completion tracking before enabling this branch.

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
post-success guarded transactions. R3 must register that handler and coordinate
checkpoint retirement by expected generation. Distributed startup does not run
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
and holds its local slot until the old handler returns. This is not yet a global
LLM cap; R4 must supply that before distributed activation. Generation fencing
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
