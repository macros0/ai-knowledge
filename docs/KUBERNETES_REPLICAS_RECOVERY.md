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

## Training key recovery on the separate PC

The user selected a separate protected directory on this PC for the training
backup key: `tests/tmp/replica-key-vault`. The directory is Git-ignored, disables
inherited ACL access, and permits only the current user, SYSTEM and Administrators.
Its `training-format3.key` is a create-only32-byte backup of the existing training
archive key; the primary file remains unchanged. Never print, commit or send key
contents, and do not reuse this archive key for notification signing.

The [actual key-recovery receipt](superpowers/reports/2026-11-02-replica-backup-key-recovery.json)
records a separate Python process using only that vault copy. Its audited-open
negative control refused the original key directory, then all12 authenticated
objects of the real rpl04 backup decoded to count/hash sinks (1,260,646 bytes).
No plaintext files or key values/digests were emitted; a wrong key was refused.
The original source remained physically off with PVCs retained. This is a Python
file-access guard, not OS sandbox isolation. A second full provider restoration
with the copied key was not repeated; the same archive's actual restoration is
recorded separately. The vault is a different directory on the archive PC, not
an independent physical key-escrow device. No production installation exists.

The existing health CLI accepts the separate vault key without any source service:

```powershell
backend/.venv/Scripts/python.exe scripts/kubernetes/replica_backup_health.py `
  --archive tests/tmp/replica-recovery-backups/predecessor-r4o2s20261102-format3 `
  --key-file tests/tmp/replica-key-vault/training-format3.key `
  --installation-id 77777777777777777777777777777777 `
  --destination-store-id separate-pc-training-backup
```

## Local training notification receiver

The user-approved receiver is running on `127.0.0.1:16491`, with readiness at
`GET /health` and authenticated events at `POST /v1/backup-alerts`. It accepts
only loopback traffic. The protected vault contains a separate32-byte
`training-backup-alerts.key`; do not reuse the archive encryption key. The inbox,
sender journal and explicit configuration are in the Git-ignored protected
`tests/tmp/replica-backup-alerts-private` directory. No sender schedule is enabled.

For a manual check, first preview the public health projection; add `--apply`
only to deliver to the configured receiver:

```powershell
backend/.venv/Scripts/python.exe scripts/kubernetes/replica_backup_alerts.py `
  --config tests/tmp/replica-backup-alerts-private/config.json
```

Configuration has strict `scope=owned-lab`, explicit archive/key/installation/
destination bindings, loopback receiver URL, signing key, delivery journal and
optional attempt journal. Keep it private; the CLI does not load the root `.env`.
Dry-run does not read the signing key or open a delivery journal/network connection.
An alert still exits2 after successful delivery; healthy exits0 and pending health
exits1. Exit2 can also mean pending delivery or refusal: inspect the public JSON.
Delivery does not certify restoration or a24-hour RPO.

The [actual local drill](superpowers/reports/2026-11-02-replica-local-backup-alerts.json)
produced exactly four durable inbox rows: one stale alert at explicitly simulated
age86401s, one incomplete encrypted-copy verification/attempt error, one changed
alert proving an older good archive cannot hide the latest failed copy attempt,
and one recovery after a complete verified copy. Repeated unchanged checks were
quiet. The failed journal belongs to an isolated local candidate-copy drill;
this is not an actual missed nightly provider backup. Original13 archive files,
primary key and source/target PVCs remained intact; the source stayed off.

Sender persists one pending ID/body before HTTP and requires a matching signed
ACK. Receiver commits its idempotent SQLite inbox before ACK; tests exercise a
lost ACK, identical replay, conflicting ID, wrong signature and corrupt receipt.
Increasing age alone sends no repeat. An older pending event must finish first;
`recheck_required` means assess again afterwards. Never delete an uncertain
`.writing` marker or replace a pending event to force progress. Observe the same
recorded PID/start/argv and inbox before any receiver recovery; do not start a
second instance on an occupied port. The receiver CLI for an explicitly prepared
private inbox is `scripts/kubernetes/replica_backup_alert_receiver.py --key-file
<protected-signing-key> --inbox <private-inbox>` (default loopback port16491).

The receiver shares the archive PC's failure domain and is a training service,
not independently available production monitoring. The107 Windows tests passed
with one health symlink-permission skip. The
[Linux follow-up](superpowers/reports/2026-11-02-replica-evidence-alerts-linux.json)
passed295 notification, verifier/policy and runner cases without skips, including
native symlink and FIFO checks. The original Windows alert receipt called its
health skip FIFO; that historical label is corrected by this Linux receipt.
Production scheduling remains disabled, and no external recipient was selected.
See the [completed local-alert plan](superpowers/plans/2026-11-02-replica-local-backup-alerts.md).

## Local backup health and attempt status

`scripts/kubernetes/replica_backup_health.py` performs a read-only cold check of
an explicitly selected format3 archive. It authenticates the manifest and every
object with the separately supplied key, checks the installation/destination
binding and measures age from `source.consistent_at`. Neither file modification
time, successful Job status nor copy completion time resets the recovery point.
Age86400 seconds remains within the target; age86401 emits
`backup_recovery_point_stale`. Missing, incomplete, corrupt, foreign or future
copies emit `backup_unverifiable`. Paths, key bytes and raw exception messages
are absent from the JSON result. The command does not contact source services.

```powershell
backend/.venv/Scripts/python.exe scripts/kubernetes/replica_backup_health.py `
  --archive <last-successful-archive> --key-file <separate-key-file> `
  --installation-id <source-installation-id> --destination-store-id <archive-store-id> `
  --attempt-journal <protected-config-directory>/backup-attempt.json
```

Pass the same `--attempt-journal` to `replica_backup.py` on every subsequent
backup of this installation. The optional journal must reside in the existing
protected config directory, outside the archive. A file lock and atomic durable
replacement record `running` before provider work, then the exact verified backup
ID on `complete`, or only the fixed `backup_failed` code on failure. It contains
no DSNs, provider exception text, credentials or source data. A concurrent,
foreign or unresolved running attempt cannot be overwritten; the exact active
attempt owns its completion. A `.writing` marker precedes every status write and
remains if write/rename/directory-sync fails. While it exists, readers refuse the
status even when a complete-looking JSON is visible. A crash leaves uncertainty
visible. Inspect the
same backup process, maintenance operation and archive before resolving that
status and its write marker; never delete either just to retry or release a source.

A failed last attempt emits `backup_last_attempt_failed`, even when the older
selected archive still verifies. A running attempt yields `pending`; after the
explicit timeout (default3600 seconds) it emits `backup_attempt_overdue`.
A completed newer backup ID that differs from the selected archive emits
`backup_selection_stale`, so choosing an older good file cannot hide a missing
latest copy. Invalid/unreadable/foreign attempt status emits
`backup_attempt_unverifiable`. Without a journal the check covers only the
selected archive, not the outcome of other attempts.

Exit codes:0 means a fresh authenticated copy with no supplied-attempt problem,
1 means a pending attempt,2 means an alert or unavailable health check. A local
supervisor can consume the JSON `alerts` and these exit codes. This implementation
does not send messages or install a scheduler. Production schedule staysOFF;
the monitoring/notification recipient, delivery test and scheduled execution
remain deployment gates. `--now` is available only as an explicit offline-drill
observation time; operational checks should use the actual clock.

Every result retains `restore_verified=false` and `rpo_assured=false`. Cold copy
verification alone cannot certify restoration, independent key recovery,
destination durability, notification delivery or a production RPO.

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

After an update, the source configuration is resolved from the actual API and
worker Deployment envFrom references. Both must select the same immutable
ConfigMap; a retained legacy `runtime` object is not authoritative. Ambiguous
references and model/auth/audit env overrides are refused, except the exact owned
`http://model-stub:8000/v1` literal used by the synthetic fixture. Live role probes
verify the common identity and quiescent stub; audit settings come from that
validated process. Its fingerprint must match SQL before maintenance, again on
the retained operator transaction before entry, and in the exported frozen
snapshot. The backup receipt records the selected ConfigMap and fingerprint.

The follow-up current-runtime run passed at epoch7 with the scoped application
credentials and the versioned runtime configuration. At consistent_at
2026-11-01T20:07:11.917915Z the source had18 documents,18 concepts,18 chunks,
3748 work rows and90 ready blobs. PostgreSQL, Qdrant and those blobs produced92
encrypted objects (2,586,300 plaintext bytes) on the user-confirmed independent PC.
Cold verification passed; normal roles returned Ready2/2/2 with zero restarts.
All18 prior document states/generations/source hashes/delete visibility and active
search/downloads matched. Four API/worker processes proved their scoped SQL user,
released epoch7 and the enter/leave audit pair; the common fingerprint stayed
unchanged. [Public result](superpowers/reports/2026-11-01-replica-current-runtime-backup.json).
This is a verified new recovery point, not a restore rehearsal of this archive;
its receipt explicitly retains `restore_verified=false`. The earlier independent
restore rehearsal below remains separate versioned evidence. Production scheduling
and retention deletion remain disabled.

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

## Database adoption after the rpl04 → rpl05 schema change

`app.deployment.runtime_upgrade.adopt_schema_upgrade` provides the offline SQL
step for this specific existing-installation transition. It is distinct from the
same-schema `rebind_runtime` step and is never called at application startup.
The actual Alembic migration must have installed `rpl05_maintenance_epoch`,
retired the old schema identity, and left its exact initial fence: epoch1,
operation `00000000000000000000000000000000`, owner `schema-migration`.
Other schema transitions require their own implementation and evidence.

Before calling it, the offline coordinator must authenticate the independent
backup, preserve the saved source identity, verify normal stops/drains for every
writer, verify the retained stores/corpus and supply explicit intended role
counts. Use one `MaintenanceOperator` with the same operation/owner/settings and
call inside its transaction:

```python
document = adopt_schema_upgrade(
    session, settings, saved_source_identity,
    operation_id=operation_id,
    previous_revision="rpl04_runtime_identity",
    intended_counts={"frontend": 2, "api": 2, "worker": 2},
    writer_receipts=verified_writer_receipts,
)
```

The transaction checks the exact installed schema, source identity/storage and
the migration-created fence. Running/leased work, running chats, owned or
unreconciled model slots and unknown storage-migration history refuse adoption.
It atomically claims epoch2 for this operator, retains document/queue/auth rows,
rebinds the runtime fingerprint, and records maintenance/runtime audit events.
The maintenance event's `replica_schema_upgrade` target contains a hash of the
source identity, transition, operation/owner, counts and stop receipts. Retries
must match that proof and its committed audit in the DB and/or durable outbox;
they do not create a second event or advance the epoch again. Audit failure
rolls back both the claim and the new identity.

The returned document is still held. Publish it only to the stopped matching
roles, verify provider/corpus/legacy-generation invariants, then use a separate
explicit release. This primitive supplies no stop evidence, clears no uncertain
owner and starts no workload. It does not adopt a compact installation or
legacy documents with missing generation identities. A downgrade after installed
maintenance history is refused; use the coherent verified recovery procedure.
The SQL transition is covered by its
[schema-adoption receipt](superpowers/reports/2026-11-02-replica-schema-adoption.json).
The complete Kubernetes schema-update coordinator and other legacy-corpus
adoption recipes remain separate O2 gates.

### Pre-maintenance predecessor archives

The rpl04 source uses its existing S4 `artifact_storage_control.maintenance_token`
to block ordinary SQL sessions; it has no maintenance epoch. The new format3
`PreMaintenanceSourceState` therefore records strict `epoch=0` and the explicit
`owned-kind-normal-stop-rpl04-v1` protocol. It requires the same nonzero operation
in its artifact-fence proof, namespace UID, source/runtime scope hash, pinned
controller generation/spec and physical CRI observation hashes, complete stopped
writer receipts and bounded observation times. Other schemas and fabricated
positive epochs cannot use this predecessor path. The original `SourceState`
serialization and authentication bytes are unchanged.

The owned-source helpers in `scripts/kubernetes/replica_predecessor_backup.py`
check the exact old database/schema/identity/S3 scope, absence of epoch schema,
and absence of running/leased work, running chats, owned/unreconciled model slots
or uncertain artifact deletion. `hold_predecessor_source` explicitly commits and
audits the real S4 token under one owned operator connection; a repeat must find
the same token/audit. Backup guards must require that token throughout export.
Physical guards additionally re-read Kubernetes/node/CRI facts and reject changed
controller specs/generations or storage Pod bindings, active/unknown processes,
autoscalers, schedules or unreachable nodes. A saved proof or scale0 alone does
not authorize a copy.

`app.deployment.predecessor_upgrade.migrate_fenced_predecessor` handles the
already-authenticated snapshot's SQL transition. It requires a real non-autocommit
PostgreSQL transaction, the retained operator connection, matching source fence
audit/token, schema, fingerprint, logical store and metadata counts. In-place
updates retain the source DB/namespace; restore uses a new isolated DB/namespace.
The old token is cleared only inside the same transaction that executes the
actual rpl05 DDL and installs the fresh held epoch1/zero-token fence. No other
connection observes a writable gap. Failure after DDL rolls back both token
clearance and the schema changes. A retry accepts only that exact fresh migrated
state. The operation never releases maintenance or starts jobs.

After verified provider imports and that forward migration,
`app.deployment.predecessor_restore.rebind_predecessor_target` validates the new
target's initial fence, source fingerprint/counts/owned S3 bucket-prefix and
absence of uncertain work. It changes the target installation/identity at held
epoch2, revokes source sessions/login flows, holds old pending audit deliveries,
and records the restore audit atomically. Same-operation retries preserve the
identity and verify the restored counts. Failure leaves the target stopped.

The protocol/SQL building blocks have
[focused evidence](superpowers/reports/2026-11-02-replica-predecessor-protocol.json).
The existing modern backup command continues to require rpl05. The separate
owned-kind predecessor exporter is available through:

```text
python scripts/kubernetes/replica_predecessor_backup.py --help
```

Its required inputs are `--config`, `--namespace-uid`, `--fingerprint`,
`--evidence-file`, `--archive`, `--key-file`, `--pg-dump`, `--report` and
`--operation-journal`. The protected configuration selects only the existing
synthetic kind lab, dedicated PostgreSQL17/Qdrant/S3 scopes and loopback TLS
transports. The journal must be new and in the protected CA/config directory;
the archive must be new in the user-approved independent PC destination, with
its32-byte key stored separately. The report goes in the repository reports
directory. The exact old runtime must have immutable settings and a quiescent
2/2/2 topology before the command installs the owned audited S4 token and
normally stops all four application/model deployments.

The exporter retains the PostgreSQL advisory-lock connection and an actual
exported repeatable-read snapshot transaction throughout copying. Native
`pg_dump` reads that snapshot through the selected PostgreSQL Pod's local Unix
socket; Qdrant supplies a real snapshot and each accepted S3 object must match
its size/SHA. Before every publication boundary the guard rechecks current
S4 token/audit/old identity, the original transaction/driver/backend, and fresh
Kubernetes/node/CRI stop facts. Reusing the same connection/PID after a commit
or rollback does not preserve snapshot authority. All bytes enter an encrypted
create-only archive, followed by independent cold authentication of every object.

Success deliberately leaves the old source stopped and S4-held; it does not
release, change schema or start jobs. Failure also attempts no release. An
existing journal prohibits repeating the command: observe the same operation
and process first. A lost snapshot cannot be recovered by reopening a database
connection, deleting the journal or clearing the token. Provider error details
stay in the protected config directory; console output uses fixed classes and
source locations. A failed PG port-forward can require an exact owned transport
replacement between operations, after proving the old process terminal and
that no retained SQL operator/snapshot exists.

The actual old-image run produced an
[independent predecessor archive](superpowers/reports/2026-11-02-replica-predecessor-backup.json)
on the separate PC:2 documents,2 concepts,2 chunks,369 work rows,6 audit events,
10 blobs and12 verified encrypted objects. A separate guarded lab action then
normally stopped its three storage StatefulSets, verified all seven controllers
at0/no live source CRI processes on the same Ready node, and retained all three
PVCs. The subsequent
[provider import](superpowers/reports/2026-11-02-replica-predecessor-provider-import.json)
restored that archive into new PostgreSQL/Qdrant/S3 instances and disjoint PVCs:
2 documents,369 work rows,4 points and10 verified blob SHA. The old source stayed
physically off. Target schema/identity/token remain the authenticated rpl04/S4
source values and all application/model deployments remained0 at that import
checkpoint. The later
[schema-changing recovery](superpowers/reports/2026-11-02-replica-predecessor-schema-recovery.json)
completed the actual forward migration, new scoped identity/authentication cleanup,
explicit release and functional acceptance for this selected synthetic recipe.
The [export/source-off validation](superpowers/reports/2026-11-02-replica-predecessor-export-validation.json)
records the separate109-pass Windows and157-pass Linux cohorts, including8
native PostgreSQL cases and post-source-off cold archive verification.
Existing modern archives and their saved-image recovery path remain supported.

### Importing the predecessor providers

The separate `scripts/kubernetes/replica_predecessor_restore.py` command requires
`--config`, `--namespace-uid`, `--source-config`, `--source-namespace-uid`,
`--source-operation-journal`, `--archive`, `--key-file`, `--report` and
`--operation-journal`. It accepts only a fully authenticated predecessor archive
and exact completed source backup provenance. The target must be a fresh empty
owned namespace/database/Qdrant/S3 instance with new, disjoint PVCs and stopped
application/model deployments. Both journals remain in their protected config/CA
directory; an existing target operation or report prohibits blind replay.

One retained target PostgreSQL advisory lock coordinates the import. Native
`pg_restore` runs in a single transaction, followed by the actual Qdrant snapshot
and conditional S3 copies with cold size/SHA reads. Every provider-write stage
rechecks the original source's same Ready node UID, zero controllers/endpoints/
Pod/CRI writers, both PVC bindings and pinned target controller/storage-Pod
snapshots. Read-only SQL checks require the actual rpl04 head, absent maintenance
table/work epoch column, unchanged source fingerprint/control/S4 token/unique
epoch0 audit, exact metadata totals, accepted object references and bounded work/
corpus hashes. The complete verified S3 TLS descriptor is preserved; HTTP, wrong
CA or unknown descriptor fields are refused.

The command performs no migration, identity rebinding, authentication revocation
or activation. Successful import leaves the original source installation ID and
fingerprint in SQL with its genuine S4 token; source auth sessions also remain
until the explicit held target-rebinding step. Do not rewrite those fields or
clear the token to start an application. A failed/uncertain import remains
stopped; observe the same protected operation rather than repeating a fresh
import against a partially populated target. The modern rpl05 import command
and saved-image restore implementation retain their original contracts.

### Verified predecessor replacement recovery

The completed training recipe uses a new namespace and disjoint storage. The
original rpl04 source remains physically off with all PVCs retained. It does not
perform an in-place migration of that retained namespace. Reauthenticate the
independent archive and its completed source provenance before provider import;
never replace the original token/identity to make a current image start.

Build the exact current committed backend, verify its source hashes and Alembic
head inside the image, then resolve its Docker/CRI/rootfs/config mapping. Keep
ordinary roles0 while binding immutable runtime/identity/scoped Secret objects.
An offline-only Job uses `OperatorSettings` with the future scoped application
credentials and a separately target-bound administrator URL projected0440.
`migrate_fenced_predecessor(..., restore=True)` executes the real rpl05 migration
and transfers the genuine S4 fence atomically to held migration epoch1. Provision
the dedicated scoped SQL/S3 principals and verify their actual TLS/denied rights;
then `rebind_predecessor_target` changes installation/config/storage at held epoch2,
revokes source sessions/login flows and holds pending audit deliveries. Root
credentials are never mounted in ordinary frontend/API/worker deployments.

Before release, compare all9 canonical-table hashes, normalized old work/progress
hashes (the added epoch column must be0),10 restored object size/SHA and4 complete
Qdrant points with active-generation references. Verify0 source sessions and a
real denied held write. The old-image direct transactional guard was separately
tested read-only against the actual target with matching genuine identity/storage
values, isolating its exact-schema refusal. This test is not normal old startup.
After an explicit same-operation release, start2/2/2 and verify live image IDs,
common identity, scoped SQL/TLS, original search/download SHA, fresh publication,
shared sessions and frontend storage isolation. The selected run passed7 smoke
checks and retained both originals after2 new clean uploads. It does not inherit
the previous image's complete R5 fault qualification or prove other providers,
compact/NULL-generation adoption, physical HA or a production RPO/RTO SLA.

An uncertain Job/staging outcome requires observation of the same UID/operation.
The recorded staging retry reused its already-created identity ConfigMap only
after exact UID/data and unchanged Deployment checks; it repeated no schema or
identity SQL and added no maintenance epoch. Preserve source PVCs and failed
diagnostic artifacts. Production backup scheduling remains disabled.

### Explicit offline CLI

The current-version `backend/scripts/upgrade_replica_runtime.py` invokes this
step only when all three schema inputs are supplied together:

```text
python backend/scripts/upgrade_replica_runtime.py
  --runtime-env-file /run/upgrade/runtime.env
  --expected-identity /run/upgrade/source-identity.json
  --operation-id <owned-operation-id> --owner-id <owned-operator-id>
  --adopt-schema-from rpl04_runtime_identity
  --writer-receipts /run/upgrade/writer-receipts.json
  --intended-counts /run/upgrade/intended-counts.json
  --operator-db-file /run/operator-db/url
  --apply
```

`writer-receipts.json` contains every `REQUIRED_WRITERS` key with its verified
`stopped` or `drained` value; `intended-counts.json` contains exactly `frontend`,
`api` and `worker` integer counts. Both and the saved source identity must be
regular JSON-object files of at most8192 bytes. Projected symlinks are supported;
FIFOs are refused without waiting. Missing, partial or unknown-transition inputs
fail before database configuration. Omitting `--apply` performs input planning
without configuring SQL or publishing an identity.

Keep runtime env/Settings on the scoped application principals. The optional
operator URL is a separate read-only Secret projection, at most16KiB, whose
driver/named host/database/effective port/every query option must match the
application URL with explicit TLS verify-full and the same root certificate.
Only the username/password may differ. It configures only the offline SQL engine
and is absent from the signed runtime settings and public output. The default
without this projection preserves the existing bootstrap connection path.

The CLI commits the held claim/identity/audit before create-only atomic identity
publication. If publication fails or its reply is lost, inspect the same operation
and repeat with identical source/settings/receipts. A successful repeat publishes
the same document without another epoch or audit event. A different existing
output document is refused, never overwritten. The public success receipt keeps
`jobs_held=true`; neither mode performs Alembic migration, release or scaling.
Without the schema inputs, the default remains the existing same-schema rebind.
See [CLI evidence](superpowers/reports/2026-11-02-replica-schema-upgrade-cli.json).

The standalone recovery tool keeps its own saved-image-compatible transport
implementation: older archived images do not contain the new current-version
operator module. Its file/projection contract remains covered separately. The
native CLI SQL tests use an isolated loopback PostgreSQL17 server; they do not
qualify Kubernetes operator projections or an end-to-end schema-update rollout.

## Owned live same-schema image rollout (2026-11-01)

`scripts/kubernetes/replica_upgrade.py` coordinates the owned-kind lab recipe.
Explicit protected `--config`, recorded `--namespace-uid`, selected `--image` and
public `--report` inputs are required. It verifies ready roles/installed identity/
quiescence before fencing, records the source plan privately and normally stops
all roles plus the test provider. With the exact held operator it hashes corpus,
artifact bindings and the work ledger. An immutable next runtime ConfigMap changes
the image reference; a bootstrap-policy Job in the selected new image computes
and audits the new Linux identity. API/worker image, runtime and identity references
are staged together while replicas stay0. A separate owned offline release Job
validates that identity against SQL and rechecks every held data hash before
leaving maintenance. Only then are the intended counts restored and identities
compared across the restarted API/worker processes.

`--resume /protected/<operation>-upgrade-journal.json` resumes the same held
operation, never inventing a new epoch after failure. Namespace/installation/image,
owner/token, unchanged data and provider state are rechecked. Only an owned
terminal failed operator Job may be removed for retry; active/unknown processes
are not force-deleted. Immutable resources must match their exact prior data,
and deployment replacement retains Kubernetes resourceVersion/UID checks. The
initial Job's incorrect role label was denied PostgreSQL access by NetworkPolicy;
roles remained0 and epoch4 stayed held. After fixing the label to the existing
`okf-role=bootstrap` contract, the same operation resumed successfully.

Actual update on `replica-test-r5o2t20261101` preserved installation5 and exact
schema `rpl05_maintenance_epoch`, and changed the API/worker image from the saved
b570457 build to `okf-backend:replicas-40b7972-559d6cb1e83e`. The new runtime image
digest was `sha256:43015b36d8bc0d70355d96fbd5c8eaa25e6bd93f0bcf9204af241d71146b03c8`.
The held corpus/work hashes matched; no queued application work executed before release. All
2 frontend/2 API/2 worker became ready with zero restarts and the common new
fingerprint. All8 saved documents remained present:7 active originals passed
search/download SHA-256 checks and the trashed document stayed hidden. A fresh
document completed without a problem and passed search/download in25.5s.
See the [public result](superpowers/reports/2026-11-01-replica-same-schema-upgrade.json).
This proves one same-schema synthetic managed-S3 rollout and its held Job-failure
resume. Schema-version adoption, other providers and unknown-provider physical
stop/reconciliation still require their own evidence. Production scheduling is off.

## Owned in-flight worker-loss recipe

Run `scripts/kubernetes/replica_worker_loss.py` with `PYTHONPATH=backend` and the
backend virtualenv. Every phase requires explicit protected `--config`, recorded
`--namespace-uid`, installed `--expected-fingerprint`, separate protected `--journal`
and `--apply`. The journal must share the protected config directory; it contains
original reservation tokens and must not be published. The phases are:

1. `inject`: require idle 2/2/2 roles and the owned instrumented provider, save
   existing document hashes/generations and exact node/Pod/container identities,
   enable the bounded synthetic delay and upload one isolated DOCX. Capture the
   original work/reservation before stopping the two exact worker containers via
   CRI. Verify the provider is still executing afterward. Normally delete only
   the bound old worker Pods; no force deletion or node shutdown is used.
2. `observe`: wait on the same original reservation. After the provider finishes
   and the DB clock passes lease expiry, require unchanged owner/fence, no new
   provider call, replacement workers and immediate busy admission on both APIs.
3. `recover`: enter an owned recovery epoch, normally stop roles/provider and
   collect fresh controller/Pod/Job/endpoint/node plus CRI observations. Confirm
   every writer node is the same and Ready, no orphan or unknown namespace process
   remains and the provider Service has zero endpoints. Recheck before and after
   exact-token SQL acknowledgement with atomic proof-digest audit. Stay held.
4. `release`: recheck the physical stop, then run a bootstrap-policy release Job
   in the same image with the current identity. It checks held corpus/work hashes
   before leaving maintenance. Only after its receipt, restore the success fixture
   and intended role counts. Rebind only the recorded frontend tunnel if needed.
5. `verify --report <public.json>`: require automatic completion with a newer work
   fence and exactly one active publication; verify original SHA/search and every
   pre-existing document's content, generation, source hash and delete visibility.

An observation timeout does not mean the process or provider stopped. Inspect the
same live handle/journal, never repeat `inject` against an existing journal. A
held recovery may resume with the same operation/owner and fresh physical guards;
do not acknowledge through an old lost PostgreSQL connection. Any unknown node,
writer, changed reservation or failed audit keeps maintenance/reservation held.
This recipe is for the owned single-node stub lab, with production scheduling off.
Real/external providers and node/host loss require separate qualified recipes.

Actual rehearsal on2026-11-01 passed on the owned managed-S3 target: both worker
containers were lost with provider active1; provider completion and reservation
expiry still left the original slot occupied and both APIs busy. Under the same
recovery operation at epoch5, physical Kubernetes/CRI stop guards authorized one
audited completion. A nullable empty EndpointSlice initially stopped the observer
without releasing anything; the same operation resumed after its tested fix.
The release Job verified held data; the interrupted document automatically reached
done/problemNone with fence1→3 and one publication. Search/original SHA passed,
all9 previous documents retained content/generations/bytes/delete visibility and
all2/2/2 roles became ready0restarts. See the
[public result](superpowers/reports/2026-11-01-replica-active-worker-loss.json).

## Owned PostgreSQL partition and stale return

`scripts/kubernetes/replica_postgres_partition.py` uses the same explicit protected
config, namespace UID, installed fingerprint and private-journal arguments as the
worker-loss recipe. It requires the owned Cilium cluster and unchanged worker
image/configuration. The temporary startup wrapper changes only runtime's UUID
source to the Pod UID, so the exact SQL owner can be located. Full worker startup,
queue/handler/heartbeat, fencing, health and capacity code remain unchanged.

`inject` starts one synthetic DOCX, captures its original owner/fence/reservation,
and selects only that Pod with a namespaced Cilium egress deny to the managed
PostgreSQL endpoint on TCP5432. Positive controls must still reach Qdrant/model
and the peer worker must still reach PostgreSQL. The original container must stay
live without restart and become unready. If observing the policy times out, use
`confirm` to read the same Pod/container/policy; never inject again. The report
records provider activity both before the patch and at the actual network sample.
A late network sample does not prove the provider was still executing at denial;
the partitioned document must still be unfinished and unpublished.

`observe` requires the healthy peer to reclaim and prepare a newer generation
while the original request reservation remains occupied. `heal` removes only the
bound policy UID/spec. The returning old container must retain its identity.
There are two valid completion paths:

- If the original provider client remains alive and confirms a terminal reply,
  normal exact-token completion may free capacity after reconnection. `finish`
  verifies the newer work/generation succeeded alone, the old generation is
  abandoned, model peak stayed1 and no slot remains owned. It probes the actual
  publication entry guard in the original Pod with the captured old owner/fence
  and a future client lease; refusal plus rollback and unchanged data are required.
  Continued claims/completions prove the original process resumed. A zero
  `lease_losses` counter is allowed: a disconnected heartbeat may have exited
  through OperationalError instead of that counter's LostOwnership branch.
- If completion remains uncertain, keep the original reservation, verify busy
  admission and use the owned physical-stop `recover`/`release` recipe. Reconnecting
  alone, elapsed time and a completed server counter never authorize clearing it.

On the confirmed path, `restore` first requires quiescence, then restores the
recorded ordinary worker entrypoint and success fixture through normal rollouts.
`verify --report <public.json>` checks common runtime identity, unchanged maintenance
epoch, the full previous corpus's content/generation/source/delete state and the
recovered document's single publication, search and original SHA. No maintenance
epoch or offline reconciliation is invented for a normally acknowledged reply.

The actual2026-11-01 run passed the confirmed-reply path. Cilium identity/policy
propagation exceeded the initial network-observation window; read-only `confirm`
verified the same still-running fault without injecting again. At that first
confirmed network sample the provider had already finished, but document work and
publication remained unfinished. The peer reclaimed fence1→2. After reconnect,
the unchanged old container resumed useful work, the captured old-token guard
probe refused publication and rolled back, and only generation2 became active.
The process lease-loss counter was0, so refusal is established by the explicit
guard probe and persisted ownership/publication state, not that counter. Normal
confirmed provider completion freed capacity without operator reconciliation;
observed model peak remained1. The ordinary startup and success fixture were
restored; all10 previous documents and the new document passed preservation,
search/source SHA and delete-visibility checks with2/2/2 Ready0restarts. See the
[public result](superpowers/reports/2026-11-01-replica-postgres-partition-stale-return.json).
