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
