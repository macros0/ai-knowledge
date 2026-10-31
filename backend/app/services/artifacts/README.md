# Portable artifacts — staged implementation

LocalBlobStore, RwxBlobStore and S3BlobStore share an immutable artifact contract.
Original admission, portable publication/checkpoints and durable file consumers
are a staged preview; full migration and distributed execution remain pending.

S2 admission preview is enabled only with ARTIFACT_UPLOADS_ENABLED=true in a
non-production combined process. Production activation is rejected until S3/S4
retention/migration is implemented. A verified original blob, document binding
and optional actor-scoped UploadReceipt are committed in one SQL transaction.
Idempotency-Key is 16–128 ASCII letters/digits/underscore/hyphen; it is hashed
in the database. Its fingerprint covers bytes, name, MIME type, tags, locale,
development and consent. An exact retry returns the original document; a changed
request conflicts (409), and a deleted/purged result is gone (410).

The combined queue is still submitted after that transaction. A queue error
leaves a paused document and its receipt; retry never starts a second worker.
Transactional enqueue belongs to R2. The browser retains each File attempt's
key and consent across uncertain retries within the mounted upload controller;
reload/unmount discards that client state.

Bound downloads authenticate/check document visibility before resolving metadata,
verify/materialize to private scratch, and keep the opened descriptor through
Range/HEAD streaming and disconnect cleanup. A missing or corrupt bound object
never falls back to legacy bytes. The combined pipeline can rebuild its local
original cache from the bound blob. Sources/attachments in v2 generations also
use bindings; source caches, review originals, table sidecars and ready job
archives now also restore from registered bytes.
Purge detaches bindings but never directly deletes
objects; receipt tombstones survive purge. Unreferenced objects retire through
GC; explicit corpus migration/runtime acceptance still keep preview disabled by
default and prohibited in production.

Lifecycle foundation (S3) adds durable read leases and deletion tombstones.
LeasedBlobStore is read-only: its open verifies against registered metadata,
renews a lease while the provider verifies/spools/reads, and passes loss guards
into local/S3 verification and range reads. Database errors or a conservative
monotonic deadline stop further byte delivery. An already materialized private
copy no longer reads the provider and does not keep that lease alive.

collect_blob only handles metadata-known, unbound objects older than its grace
(24h default). It checks ArtifactBinding, CheckpointBinding, SourceCacheHead and live
leases under the object row lock, commits deleting before provider deletion,
then acknowledges its exact claim token. Failed deletion can retry after claim
expiry. Metadata tombstones prevent identity reuse. It never removes bindings or
scans arbitrary provider keys. The opt-in worker below schedules it. Owner lifecycle code alone
detaches references under its document/job lock. Newly registered object creation
uses server time; upgrades preserve historical timestamps conservatively.

CheckpointBinding records generation/chunk/source signature and a verified blob.
Replacement requires an explicit previous object ID; abandoned or missing
generations reject writes. materialize_checkpoint reads with a lease on another
scratch root. StagingStore integrates this protocol; job-owner fencing remains R2 work.

Publication v2 is written by the ordinary pipeline, canonical repair and scope
worker when preview is enabled. Every durable generation file and the canonical
manifest are bound in the caller's SQL transaction before ready/publication.
JSON hashes use deterministic UTF8/key ordering, normalize JSON key wire forms,
reject ambiguous key aliases, and stream without building another full raw JSON
document. Concept identities use logical bundle paths; bundle-off mode remains
supported. The database publication_version marker survives cache/binding loss
and prevents interpreting v2 as a legacy filesystem publication. Migration
rpl01c_portable_publication adds/backfills it; apply migrations before running
this build against an existing database. Sealed candidates cannot be downgraded
by toggling the preview flag. Repeated sealing accepts identical refs only.

Before publication, leased provider bytes restore disposable local generation
caches. Each cache write rechecks current generation/hash/ref under the document
writer lock, so retired cleanup cannot be undone by late hydration. Publication
then verifies the complete cache and exact scoped bindings in its SQL transaction.
Provider loss/corruption leaves the previous active generation unchanged.
Authorized source/attachment downloads resolve registered ancestry and active
generation bindings before filesystem paths; missing v2 bytes/metadata never
fall back to an old local file. HTTP Range/disposition and private scratch cleanup
retain their previous contracts. Already-active publication replay is a no-op
and does not require provider access.

V1 remains restricted to legacy local combined execution/migration. Source caches,
table/export/diagnostic consumers and provider-only orphan discovery are described below.
Production/shared runtime gates remain closed. The S3 protocol double verifies
adapter behavior but does not certify a customer S3 service.

Staging preview uses one immutable JSON envelope per chunk containing raw input,
concepts, table-reference payload and generation provenance. Compact SQL metadata
and CheckpointBinding are replaced together using CAS under the document lock,
after the durable put. Replacements leave ordinary unreferenced objects for GC;
they never overwrite the previous snapshot or use mutable sidecar paths as truth.
Read leases protect materialization. Cache publication rechecks the current
document/generation/staging pointer under the document lock after provider I/O,
so removal cannot be undone by late hydration. A cold worker restores raw text,
concepts and hashed table-reference files before resume. Hot append reads only
its own chunk payload, retaining the bounded metadata preparation batches.
V2 finalization enumerates the committed checkpoint inventory, excluding stray
local chunk files. Partial recovery copies checkpoints to the new candidate;
removal detaches pointers and leaves byte deletion to GC/read-lease rules.

Migration rpl01d adds checkpoint_version (legacy=1, portable=2). Apply it before
running this build against an existing DB; create_all does not add columns.
Legacy checkpoints require explicit migration/regeneration before preview use.
Switching the flag off does not downgrade existing portable rows. Missing refs,
changed source identity or missing/corrupt bytes fail even if local files remain.
Downgrade refuses while portable rows exist, preserving their protocol marker.
This is checkpoint portability; distributed job-owner fencing remains R2.

SourceResultCache keeps its existing JSONL stage/identity contracts. SourceCacheHead
atomically selects a v2 manifest with bindings for every stage and retained
original. Missing/corrupt committed bytes are integrity failures, never cache
misses that regenerate accepted IDs. Document.source_cache_version survives
preview flag changes. Pending review originals use a separate versioned manifest
and revision pointer, sealed before acknowledgment. Replacement detaches the
previous reference group in the same transaction.

An artifact helper called inside session_scope joins that owner Session via a
ContextVar. TransactionLeasedBlobStore takes/renews/releases leases in that same
thread; the caller's document lock and committed bindings pin bytes until commit.
This avoids an independent SQLite writer during scope/repair operations. Provider
I/O can extend this transaction; distributed job fencing/lease budgets remain R2.
Generation hydration checks owner/phase/version/hash before provider access and
verifies all manifest members before publishing disposable cache files. Table
import, canonical repair, source selection and OKF export hydrate approved inputs.

Queued exports pin selected bound originals with their job/audit transaction.
Ready export parts and diagnostic ZIPs are immutable bound job results. Ready
state and refs commit together; deletion/expiry/rollback detach refs with audited
metadata even on a cold API. Downloads recheck readiness/ref after verified
private materialization, reserve local scratch capacity, and release handles,
private copies and quota on completion/disconnect. HTTP cleanup runs off the
event loop under cancellation shielding. Export ETags use the immutable digest,
so If-Range resumes survive private-cache/API replacement. Provider GC cannot invalidate the
already verified private copy. Diagnostic recorder spools remain reconstructible
local scratch; their distributed control/retention work remains R3/R4.

Apply rpl01e_source_caches and rpl01f_file_consumers before using this build on an
existing DB. They preserve legacy protocol1 rows and add source/review/diagnostic
markers. Export protocol2 is recorded in Job.result. Downgrade refuses portable
rows/results whose metadata would become unreadable. An environment flag toggle
does not migrate or downgrade known portable documents. Explicit legacy corpus
migration is S4; automatic GC is an independent opt-in preview described below.

GC discovery/worker (rpl01g_artifact_gc) uses bounded inventory pages, persistent
SQL cursors and a renewable token-checked run lease. The combined lifespan starts
and stops the worker only with ARTIFACT_GC_ENABLED=true; the default is false.
Production/distributed execution stays blocked until S4/R3/platform acceptance.
The default interval is 60s, page size 100, full-byte verification budget 2 GiB per
cycle, and grace 86400s (minimum 24h). Configuration names: ARTIFACT_GC_INTERVAL_SECONDS,
ARTIFACT_GC_BATCH_SIZE, ARTIFACT_GC_VERIFY_BYTES and ARTIFACT_GC_GRACE_SECONDS.
Oversized/budget-deferred candidates are counted; increase the budget deliberately
to collect larger artifacts. Local/RWX paging bounds memory but scans the flat
directory to select the next sorted page; S3 uses bounded continuation tokens.

Only exact objects/<32 lowercase hex> keys under the configured root/prefix are
eligible. Provider-only objects enter SQL quarantine at DB first-seen time, not
provider mtime. Changed inventory fingerprints restart quarantine. After grace,
inspection hashes actual bytes (ETag/provider metadata are not trusted), then
registers the orphan before ordinary deletion claims recheck all refs/leases.
Bound objects cannot be detached by GC. Source-cache heads protect their manifests
even if an associated binding is lost. Late binding wins or is rejected before
ACK; a claimed/deleted ID never becomes readable again. A late PUT recreating
the same deleted bytes can be quarantined/redeleted while preserving its tombstone.
Missing bytes/failed deletes retry without claiming success; cursors advance past
per-object processing failures. Invalid/unavailable inventory defers the cycle.
Stop/lease-loss guards run during hashing
and immediately before physical deletion. Logs contain counts and error classes.

Each store has a DB scope token/location hash and a conditional gc-owner.json
marker outside objects/. Empty new stores initialize automatically. Existing
unmarked stores require explicit adoption after verifying the matching database,
exclusive root/prefix and backup, with application writers stopped. A missing or
foreign marker, lost database identity or changed store location blocks collection;
neither automatic startup nor explicit adoption overwrites a foreign marker.
The DB identity and provider marker must be preserved together during backup/restore.

From backend/, review setup without mutation:
`python scripts/collect_artifacts.py --initialize --adopt-existing`.
After checking the target, append `--apply`; then enable ARTIFACT_GC_ENABLED.
`python scripts/collect_artifacts.py --apply` runs one bounded cycle when enabled.
Without --apply the command never opens the provider or changes the DB.
Apply rpl01g before enabling; downgrade refuses initialized ownership metadata.
GC never runs against a backup prefix/root. It handles completed current objects;
local tmp, incomplete S3 multipart uploads and noncurrent S3 versions require
separate scratch/provider lifecycle policies. Real CSI/S3 quota/failure acceptance,
global runtime scheduling and corpus migration remain open.

PostgreSQL probes live in tests/replica_postgres and require the private URL file
path in OKF_ARTIFACT_PG_URL_FILE. Only loopback replica_test_<run-id> databases are
accepted; each probe creates/removes its own fresh artifact_<uuid> schema. Without
that explicit file they skip and must not be counted as completed live acceptance.

Object IDs are server-generated 32-character lowercase hex strings. Put streams
to a temporary file, fsyncs it and publishes through an exclusive hard link;
existing IDs are never replaced. Filesystems without atomic hard-link support
fail explicitly. Same bytes may retry the same ID; different bytes conflict.
Do not substitute rename-over-existing as a fallback.

The root and its contents must be owned by the service. Existing shared link
guards reject symlinks/reparse points inside it; operator-owned ancestors are
canonicalized. This is not isolation against another process with the same OS
permissions that can rewrite the root during operations. Real filesystem/CSI
guarantees require platform acceptance.

Open verifies the full SHA256/size on the same handle before exposing a bounded
range; this prioritizes integrity and costs a full pass before streaming. GC
claims/read leases are implemented; distributed job fencing remains R2 work.
Materialization rechecks provider bytes and cleans parser scratch on exit.
The returned path is named source; parser callers must retain document format
metadata when integrating it. No original filenames enter artifact keys.

Storage I/O errors are classified without exposing paths; cleanup failure does
not replace a primary error. Cleanup can leave an orphan if OS permissions stop
unlink, to be handled by later maintenance. Caller exceptions inside the open
context are preserved, rather than misclassified as storage errors.

RWX requires an existing mount root and uses the same exclusive hard-link layout.
An existing directory does not prove a shared mount: real CSI/node restart and
concurrency acceptance remains mandatory.

S3 uses explicitly supplied endpoint/bucket/prefix, credentials, region and CA;
there is no ambient AWS credential discovery. HTTPS verification is mandatory;
plain HTTP requires an explicit development opt-in. The SDK before-send hook
rejects changes of authority, including redirects. Single PUT and multipart
completion use If-None-Match: *; unsupported conditional writes fail. Conflicting
retries verify the complete existing object. Upload/download spool to bounded
local scratch (2 GiB default), and reads verify full SHA256/size before exposing
the requested range. ETag and metadata are not integrity evidence. Failed
multipart writes attempt abort; operators still need orphan-upload lifecycle
cleanup. A missing bucket is an error, not successful deletion.

The shared contract suite uses an in-memory S3 fixture plus actual botocore
request-model/hook tests. These do not certify a customer S3 service. Provider
quota/access/timeout/restart/conditional-write acceptance remains open.
