# Replica maintenance and recovery

Status,2026-11-01: the SQL maintenance fence/operator and encrypted format3 archive
library are implemented. Coordinated Kubernetes drain, provider reconciliation,
actual provider exports/copy/restore and held restore release remain O2 work.
There is no completed distributed backup/restore command or certified recovery
procedure in this snapshot. The compact format2 path remains separate.

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
through the eventual coordinator. Transaction guard decisions are cached only
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

A complete backup must verify every component at a destination independent of
the source namespace/failure domain. Restore must use new storage targets, keep
jobs held, rotate/invalidate authentication, verify content/search/download with
the source unavailable and record measured consistent_at/RPO/RTO. Unknown writers,
provider requests, partial components or missing hashes must prevent completion.
Nightly scheduling still needs destination/access, encryption/key recovery,
timezone/window, retention and alert recipient inputs. No nightly CronJob is
enabled by this foundation.

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
Production scheduling remains explicitly disabled. The end-to-end acceptance must
still export a nonempty source, make its storage unavailable, restore to new
targets with jobs held, verify content/search/download and measure RTO/RPO.
