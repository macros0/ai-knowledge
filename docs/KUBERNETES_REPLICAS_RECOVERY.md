# Replica maintenance and recovery

Status,2026-11-01: the SQL maintenance fence/operator foundation is implemented.
Coordinated Kubernetes drain, provider reconciliation, backup format3, independent
copy/verify/restore, held restore release and nightly scheduling remain O2 work.
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
and is historical evidence; this maintenance code has not been rolled into that
running lab application.

## Remaining recovery acceptance

A complete backup must verify every component at a destination independent of
the source namespace/failure domain. Restore must use new storage targets, keep
jobs held, rotate/invalidate authentication, verify content/search/download with
the source unavailable and record measured consistent_at/RPO/RTO. Unknown writers,
provider requests, partial components or missing hashes must prevent completion.
Nightly scheduling still needs destination/access, encryption/key recovery,
timezone/window, retention and alert recipient inputs. No nightly CronJob is
enabled by this foundation.
