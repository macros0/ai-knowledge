# Replica maintenance and recovery

Status,2026-11-01: the owned managed PostgreSQL/Qdrant/S3 lab has passed an actual
encrypted backup to the user's separate PC and restore into new storage with the
source switched off. PostgreSQL snapshot export, physical writer drain, all-object
hash verification, held identity adoption and explicit release were exercised.
This is a single-node synthetic lab result, not customer/provider certification.
External/mixed/RWX recipes, uncertain provider reconciliation, installed-schema
upgrades and production scheduling remain O2 work. The compact format2 path remains separate.

## Transaction boundary

Fresh bootstrap installs `replica_maintenance_state` beside the runtime identity.
API/worker startup binds the process once to its current epoch. A PostgreSQL
transaction takes a shared lock on that singleton before work/control row locks.
Maintenance entry takes an exclusive lock, waits for existing guarded transactions
and increments the epoch in the same transaction as its security audit event.
Normal session scopes, ORM writes and text execution are guarded, including the
audit dispatcher's retained-connection sessions and denial audit sessions.
Work claims carry the epoch and cannot renew/publish/finish under a retired one.

After maintenance ends, the previous processes stay fenced. Revalidating the
same Settings/process cannot adopt the new epoch; restart the application roles
through the coordinator. Transaction guard decisions are cached only
while the exact PostgreSQL transaction retains its shared row lock. SQLite is a
single-process development/test fallback, with no distributed guarantee.

## Offline operator

`MaintenanceOperator` owns one direct PostgreSQL connection and a session advisory
lock for the whole operation, with separately committed phase transactions. Each
phase checks the same physical session/lock; a lost connection cannot reconnect
and acknowledge through another session. Operator row-lock waits are bounded to5s.
The connection is physically discarded on release. Direct PostgreSQL/session
pooling is required. The explicit SQLite operator option is for unit tests only.

The exact retained operator transaction can bypass live configuration/epoch
admission while updating an old installation. It receives explicit offline
settings for transactional audit; there is no environment or application startup
bypass. Normal sessions on other connections remain fenced. The operation also
owns `artifact_storage_control.maintenance_token`, excluding a simultaneous S4
storage migration. Verified historical cutover receipts do not permanently block
later maintenance. A different/unknown active operation is never taken over by
elapsed time or automatically undone after an exception.

The singleton retains intended frontend/API/worker counts and writer receipts.
Exit requires explicit stopped/drained receipts for API, worker, audit dispatcher,
diagnostic children, provider requests and maintenance jobs. It also refuses any
owned LLM capacity slot, including an expired uncertain request; entry/exit never
clears such a slot. Receipts at this library boundary are trusted operator
attestations. Actual Kubernetes/process/provider evidence collection is still
required before a coordinator can use them as a recovery guarantee. SQL fencing
alone does not stop an already-issued external request or local child process.

## Schema upgrade hold

The new head is `rpl05_maintenance_epoch`, following `rpl04_runtime_identity`.
An uninstalled fresh target remains empty until explicit bootstrap installs epoch0.
Upgrading an installed replica schema preserves queued work, installs a held
epoch1 state (`operation_id` all zeroes, owner `schema-migration`), reserves the
storage operation token and retires the installed old schema revision. Existing
active storage maintenance blocks the upgrade. Downgrade refuses installed
identities or nonzero maintenance history.

Existing writers must be stopped before schema upgrade. A complete adoption,
new image/common identity installation and verified release procedure is still
pending; do not apply this migration to a running installation as an online
upgrade. The existing owned runtime smoke report used the previous rpl04 image
and is historical evidence; this maintenance code was not rolled into that lab
application before its disposable namespace was removed.

## Remaining recovery acceptance

Each supported provider recipe must verify every component at a destination
independent of its source failure domain. The managed-S3 lab result below covers
one such recipe. Unknown writers, provider requests, partial components or missing
hashes prevent completion. Additional provider recipes, installed-schema adoption
and the remaining runtime fault matrix are open. Production scheduling is disabled
by user instruction; its future activation requires independent key recovery,
timezone/window, retention and alert recipient configuration. No nightly CronJob is enabled.

## Encrypted archive contract

`app.deployment.replica_backup` defines the format3 plan/manifest, filesystem
archive writer and cold verifier. PostgreSQL and Qdrant require one export each;
the complete accepted artifact inventory carries its source sizes/SHA-256.
Counts, exact image digests, schema, model/vector identity, per-component storage
scopes/receipts, the maintenance epoch and recovery point are authenticated with
the complete inventory. Metadata has a fixed field allowlist; raw connection
strings, source payloads, passwords and key material are not manifest fields.

Only encrypted bytes enter the archive. Each object has a random128-bit file salt
and a separate HKDF-derived AES256-GCM key. Up to4MiB plaintext frames use distinct
96-bit counter nonces; authenticated additional data binds the plan/object,
component, frame order and final marker. The manifest has a separately derived
HMAC-SHA256 key. Root backup keys are supplied separately from the archive and
are never persisted by this library. The implementation uses the existing pinned
cryptography50.0.2 dependency, not a new cipher implementation. References:
[AESGCM](https://cryptography.io/en/50.0.0/hazmat/primitives/aead/),
[HKDF](https://cryptography.io/en/50.0.0/hazmat/primitives/key-derivation-functions/).

The writer requires both a live/frozen source guard and a live destination binding
guard. Different failure-domain names alone do not prove independence: the
coordinator must validate the actual configured mount/host against operator-owned
platform evidence. A local spool cannot acquire independent-destination status
by omitting that guard. The present writer supports a filesystem destination with
regular files, create-only hardlinks, atomic manifest replacement and file locking;
NAS capabilities/durability still require actual qualification. S3 archive delivery
is a separate adapter and is not implemented by pointing this writer at a spool.

Initial/incomplete manifests precede object publication. Every completion reopens
and authenticates all destination objects, checks both plaintext and ciphertext
hashes/sizes, revalidates source/destination and only then publishes complete.
Missing/corrupt parts, a wrong key, changed source, changed inventory and oversized
accepted artifacts fail closed. An existing complete manifest is never replaced.
Memory is bounded by frame size; manifests are limited to64MiB/100000 objects and
individual plaintext objects to1PiB. These are explicit archive limits, not tested
production capacity claims. Retention does not run in this library.

Resume requires the identical authenticated plan and still-valid frozen source
proof. It verifies completed objects and can reconstruct the receipt for a crash
between create-only encrypted object publication and manifest publication. It
does not pretend that a lost PostgreSQL snapshot or unverified provider state can
be resumed. The recovery point age is measured from consistent_at, irrespective
of when copying finishes. Complete here establishes an archive copy against its
coordinator-supplied scope; it does not certify application recovery by itself.
`BackupReader` authenticates the whole archive before producing any plaintext,
then reauthenticates copied frames and verifies complete object receipts. Its
final verification detects archive changes. A provider coordinator must keep new
targets isolated and jobs held on any decoding/import failure; decoding by itself
does not create a database, restore a collection, or release workloads.

The user selected this physically separate Windows PC for the owned lab copy.
Production scheduling remains explicitly disabled.

## Exercised managed-S3 recovery (2026-11-01)

`scripts/kubernetes/replica_backup.py` is an explicit **owned-kind-lab** coordinator.
It validates the recorded namespace UID, SQL installation/schema/storage scope,
isolated loopback forwards and the approved separate-PC destination before entry.
The SQL maintenance fence precedes normal scale-down. Completed Jobs are allowed;
unknown/terminating writer Pods, unsuspended CronJobs, unfinished Jobs, unreachable
nodes and owned LLM slots prohibit a verified drain. No force deletion or lease
expiry substitutes for stopping a provider request. Intended role counts are
restored with new processes after a successful verified backup. On failure the
source stays held; an incomplete archive is retained and never accepted.

One retained repeatable-read, read-only PostgreSQL connection exports the snapshot
and obtains the database `consistent_at`. The selected server's `pg_dump` uses its
Pod-local Unix socket with that exact snapshot; SSH/Kubernetes stream the dump to
the PC. No credential enters argv. This avoids kubectl terminating its entire TCP
forward when an individual PostgreSQL client resets a stream. Dump exit status is
checked before completion. A Qdrant snapshot and every SQL-ready artifact's bytes,
size and SHA-256 complete the encrypted archive. No lost snapshot is silently resumed.

`scripts/kubernetes/replica_restore.py` authenticates the complete archive before
import, requires new namespace/installation/PVCs and empty PG/Qdrant/S3 instances,
and checks the source has no running workloads. Application roles stay at zero.
PostgreSQL imports in one transaction; Qdrant imports the saved snapshot; S3 uses
create-only writes and cold reads of every object. Counts and the work ledger are
checked before reporting `providers-restored-held`. This command never activates jobs.

`scripts/kubernetes/restore_identity.py` runs as an offline Job in the exact saved
backend image, mounted outside `app/` so the application build fingerprint remains
the saved one. It uses new target credentials, APP_SECRET_KEY and CA. On its exact
owned operator connection it replaces source installation/storage bindings, computes
the common fingerprint on Linux, increments the maintenance epoch, invalidates
auth/OIDC sessions and holds pending audit events atomically. It refuses uncertain
LLM owners and keeps maintenance enabled. SQL-commit/file-publication recovery only
accepts the identical new identity, operation and bindings. Publish the resulting
immutable identity ConfigMap, verify the held queue and target endpoints, then use
the exact restore operator to leave maintenance and start the intended workloads.
The owned rehearsal driver performs those explicit final operations; automated
production deployment/activation is not provided by these lab entry points.

Observed result: source `replica-test-r5o2r20261101` was fully stopped; target
`replica-test-r5o2t20261101` used three different PVCs and installation IDs. The
independent format3 archive held 17 encrypted objects (1,330,928 plaintext bytes):
one PG dump, one Qdrant snapshot and 15 artifacts. Restore verified 3 documents,
3 concepts, 3 chunks, 6 vector points and all 15 artifact hashes. All three originals
passed search/download SHA-256 checks with the source unavailable. The restored
work ledger was unchanged, execution before release was zero, and an actual write
guard rejected access before release. One old auth session was invalidated.
After release, 2 API + 2 worker + 2 frontend were ready with zero restarts; a new
document finished without a problem and passed search/download in 26.688s.

`consistent_at=2026-11-01T13:50:43.734141Z`; observed recovery-point age at the
outage was 236.334s. Observed recovery elapsed time was 973.464s, including operator
implementation/verification during the rehearsal. These measurements are not a
production RPO/RTO guarantee. The public receipt is
[`2026-11-01-replica-managed-s3-recovery.json`](superpowers/reports/2026-11-01-replica-managed-s3-recovery.json)
(local original: `tests/tmp/replica-recovery-lab/recovery-report.json`); archives and keys stay in
separate protected directories on the approved PC. Secret material is not committed.

## Provider acknowledgement and same-schema image update foundation

`app.services.work.provider_reconciliation` is an offline acknowledgement boundary
for the **owned model-stub Pod-stop recipe only**. A coordinator must capture the
original LLM reservations before the fault, enter an owned `recovery` epoch, stop
all writers/provider processes and verify namespace/Pod/node/service endpoint facts.
Its mandatory live guard returns the same typed proof before and after SQL changes.
Proof/lease digests bind the exact operation, installation, epoch, namespace UID,
retired Pod UIDs and provider image. Unknown writers, unreachable nodes, provider
Pods/endpoints or changed lease owners/fences prohibit acknowledgement. Expiry
alone never releases capacity. Slot changes and `replica_model_reconcile` audit
are atomic; failed audit/last guard rolls them back. Repeated acknowledgement
requires identical cleared leases and durable audit. Maintenance remains closed.
External providers need their own implemented termination/cancellation recipe.

`app.deployment.runtime_upgrade.rebind_runtime` and
`backend/scripts/upgrade_replica_runtime.py` provide an offline **same-schema**
identity update. Supply explicit runtime env file, expected old public identity,
operation/owner IDs and `--apply`. Run in the selected new image with real target
settings/trust and private scratch identity output. The owned held transaction
compares old SQL identity/installation/storage/schema/receipt, requires no owned
LLM slots and commits the new Linux fingerprint with its upgrade audit. SQL-commit/
file-publication retry only accepts that identical audited identity/epoch. Publish
the new immutable identity ConfigMap, verify image/config bindings, explicitly
leave maintenance and restart intended roles. This command does not migrate
schemas, adopt unknown data or activate workloads. Physical proof collection,
live image rollout and in-flight provider-loss rehearsal remain pending; these
unit checks do not qualify a physical provider.
