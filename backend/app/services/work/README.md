# Transactional work foundation (R1)

`WorkItem` is an internal execution ledger, separate from public bulk `Job`
approval/status records. The package starts no executor during import and is
not wired into application admission/startup yet. R2 integrates documents and
publication fencing; R3 separates runtime roles, R4 coordinates global capacity,
and O2 adds installation maintenance epochs. Existing replica/production gates
remain closed. `WorkClaim.maintenance_epoch` is zero until O2.

Apply Alembic `rpl02_work_items` before using this build on an existing database.
The migration preserves nonempty Job rows, creates claim/reclaim indexes, and
refuses downgrade while **any** work history exists. Work dedupe tombstones must
not be removed to make a rollback appear safe. Restore a coherent backup instead.
Offline artifact migration now fingerprints this ledger as part of its source
revision; source/runtime schema must include rpl02.

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
LLM cap or protection from late external writes; R2/R4 must supply those before
distributed activation. Shutdown stops claims/renewal without a late ACK callback.
R3 supplies process grace/health/lifecycle and real document handlers.

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
