# Replica storage packages and bootstrap

Implementation status, 2026-11-01: basic managed PostgreSQL/Qdrant/S3 templates and
external/mixed application wiring are implemented. Production activation still
requires the remaining O1 provider checks, O2 recovery and R5/O3 acceptance.
Managed RWX has no selected platform recipe or installation permission yet; an
existing verified RWX claim can be used by the application. Resilient is rejected.

## Selected basic recipes

The infrastructure chart is `deploy/helm/ai-knowledge-storage`. All components
default to external and create no storage resources. Managed components have one
StatefulSet and one dedicated RWO claim each. They do not share PostgreSQL/Qdrant
data directories between replicas and provide no storage HA.

| Component | Recipe | Pinned image content | License |
|---|---|---|---|
| PostgreSQL | postgres-17-single-v1 | postgres:17@sha256:67f41722b7a8cbdb868a44a4995c846eddfdc2973bccb291ce937dce88ad5675 | PostgreSQL |
| Qdrant | qdrant-1.19-single-v1 | qdrant/qdrant:v1.19.0@sha256:057ee3a8da769fe7310dd3537b4dc7583bf87a95ce8ac43c0af5a46bc580d1fc | Apache-2.0 |
| S3 | rustfs-1.0.4-single-v1, Linux/amd64 | rustfs/rustfs:1.0.4@sha256:e287e99762a4a3e7b706526e01f2ff755f67db165e9be9f6b907adc6fb6d6ee1 | Apache-2.0 |

Registry mirrors may retain the exact content digest; changing recipe/version or
digest requires a new selection/contract acceptance. Registry CAs belong to the
platform; pull credentials are existing Secret references. Sources:
[PostgreSQL license](https://www.postgresql.org/about/licence/),
[Qdrant v1.19.0 license](https://github.com/qdrant/qdrant/blob/v1.19.0/LICENSE),
[RustFS installation/license](https://docs.rustfs.com/en/installation),
[RustFS container mode](https://docs.rustfs.com/en/installation/container).

## Ownership and credentials

Distributed application releases create no PostgreSQL/Qdrant StatefulSets, storage
Services or storage PVCs. Each provider is selected independently. Resolved URLs
come from runtime configuration/Secret; hostnames do not infer Compose ownership.
The infrastructure release and application release share a namespace in this basic
recipe. Their NetworkPolicies match their explicit release names. External
services require explicit application egress rules. No cluster operator/CRD or
storage-node change is installed by either chart.

PVC creation is an explicit `component.provision=true` operation. Supply an unused
claim name, StorageClass, capacity, installation ID and ownership. Existing claims
are referenced with provision=false; provision refuses foreign live PVC adoption.
Created claims have `helm.sh/resource-policy: keep`; uninstalling the app does not
remove the independent infrastructure release. Deleting a namespace can still
delete claims: backups must be independent of this namespace/failure domain.

Credentials are never Helm values/rendered literals. Infrastructure Secrets use
POSTGRES_PASSWORD, QDRANT_API_KEY and S3_ROOT_ACCESS_KEY/S3_ROOT_SECRET_KEY. Runtime
credentials are supplied separately. Production must provision a restricted DB
role and scoped S3 application account/bucket before application bootstrap; root
credentials used by the disposable lab are not a production IAM recipe. Automated
role/bucket/IAM provisioning remains an O1 requirement. Direct PostgreSQL/session
pooling is required for audit ownership; transaction pooling is unsupported.

Optional TLS server Secrets use tls.crt/tls.key. PostgreSQL, Qdrant and RustFS have
recipe-specific TLS settings; RustFS keys are projected as rustfs_cert.pem and
rustfs_key.pem. The console has no exposed Service. Supply an aggregate public CA
bundle through the application trust ConfigMap. API/worker bind CA bytes into the
common identity and mount a per-Pod file snapshot. Rotation requires new common
identity and a coordinated restart, not a silent mounted-file change. External
production PostgreSQL requires sslmode=verify-full and Qdrant requires HTTPS.
The gRPC client uses the same snapshot through
`GRPC_DEFAULT_SSL_ROOTS_FILE_PATH`; HTTP clients use the explicit CA/SSL settings.
URL validation alone does not establish a working TLS connection.

## Fresh installation

Fill a private storage-selection document using
`deploy/kubernetes/replicas/storage-selection.schema.json`. It records each
provider/recipe/digest/license, allowed scope, disk/failure model, backup recipe
and maintenance owner. Recipe names are declared recovery interfaces; coordinated
backup/restore implementation and qualification remain O2/O3.

Keep application workloads disabled. Prepare storage, credentials, bucket/scoped
application account and runtime settings. Run inside the exact backend image:

```sh
python scripts/bootstrap_replica_runtime.py --runtime-env-file /private/runtime.env --selection /private/storage-selection.json
python scripts/bootstrap_replica_runtime.py --runtime-env-file /private/runtime.env --selection /private/storage-selection.json --phase migrate --apply
python scripts/bootstrap_replica_runtime.py --runtime-env-file /private/runtime.env --selection /private/storage-selection.json --phase bootstrap --apply
```

The first command is a plan with zero provider/DB mutations. Migrate accepts only
a fresh target, uses the complete Alembic chain and never loads API settings with
a missing identity. Bootstrap rejects application data and edited reference seeds,
checks the artifact adapter using fresh probe IDs, and prepares/verifies only an
empty Qdrant collection. No working corpus is adopted by this path. A mismatched
existing installation requires O2 maintenance/adoption. Migration/initialization
must run with all application writers stopped.

The common HMAC covers normalized settings including credentials, namespace,
backend image reference, application/parser source, Python/package versions and
custom CA bytes. Bootstrap commits the SQL/storage identity before create-only
atomic document publication. A retry after file-publication failure reconstructs
the same accepted identity. Competing initializers serialize on the control row;
a bounded lock timeout can be retried in a fresh transaction. It never replaces a
different accepted identity or document. Mount the resulting identity.json in a
read-only operator-owned ConfigMap for API/worker. API/worker cannot bootstrap it.

## Evidence and remaining gates

The owned single-node kind lab ran the selected Qdrant and RustFS images with
non-root restricted Pods and separate local-path RWO PVCs. Actual S3 conditional
publication/cold/range reads/deletion and wrong-credential refusals passed.
Qdrant schema initialization and populated-collection refusal passed. The full
PostgreSQL migration chain and competing fresh initialization/retry passed in a
disposable schema. This is basic functional evidence, not storage failover or
customer qualification. The same lab subsequently passed real TLS probes for
PostgreSQL, Qdrant REST/gRPC and RustFS. PostgreSQL probes use direct Pod TCP,
verify-full and SCRAM: rejected passwords, hostname mismatches and the retired CA
are checked for their specific protocol failures, not just connection errors.
All three storage Pods restarted onto a second CA with their original PVCs; an
8192-byte pre-restart S3 object retained its full SHA-256. Qdrant's Python gRPC
client verified the second CA through the explicit shared root file. This is
storage-only CA rotation, not coordinated rotation of a running application.
Independent recovery, application rotation, disk-full, RWX/provider and
multi-node failure gates remain open. Local-path requested size
does not establish an enforced per-volume disk quota; do not fill the host disk
to simulate ENOSPC. Resources remain provisional until R5/O3 measurements.

## Scoped credentials in the selected owned lab

`scripts/kubernetes/replica_credentials.py` implements an explicit PG17/RustFS1.0.4
recipe for a dedicated owned instance/database and a dedicated installation bucket.
It is not a generic customer IAM administrator. Private configuration and journal
must stay in the same protected directory; never commit either or pass passwords
in command arguments. Each phase requires the recorded namespace UID, original
runtime fingerprint and `--apply`:

```text
prepare → provision → rights → secrets → fence → compute → stage → release → start → verify
```

Use `--config <private-config.json> --namespace-uid <recorded-uid>
--expected-fingerprint <original-fingerprint> --journal <private-journal.json>`.
`verify` additionally requires `--report docs/superpowers/reports/<result>.json`.
After a timeout inspect the same operation/Job; do not prepare a second operation
or delete its Job to retry. A failed or uncertain offline phase leaves application
roles stopped. After successful release, `start` resumes counts0→2 idempotently.

The SQL role gets ordinary table DML, sequence USAGE and no DDL/TRUNCATE, superuser,
role creation or memberships. Three authority singleton tables allow SELECT and
UPDATE(id) only after verifying CHECK(id=1): PostgreSQL requires UPDATE on a column
for row locks. Audit history is append-only; migration history is read-only. There
are no automatic grants on future tables: a schema update must supply reviewed
grants. Database PUBLIC grants are changed only in the dedicated selected instance.
See [PostgreSQL row-lock privileges](https://www.postgresql.org/docs/17/ddl-priv.html).

S3 object mutation is confined to the application prefix; bucket metadata/listing
is confined to its dedicated bucket. Native RustFS returns a filtered ListBuckets
result for this account. A separate operator-only bucket is used as a positive
control: its discovery/list/read/write/delete must be refused. This does not
certify prefix-only metadata isolation in a shared bucket. The pinned native
Admin API uses verified TLS/SigV4 and JSON, following
[RustFS1.0.4 user management](https://github.com/rustfs/rustfs/blob/1.0.4/rustfs/src/admin/handlers/user.rs)
and [policy handling](https://github.com/rustfs/rustfs/blob/1.0.4/rustfs/src/admin/handlers/policies.rs).

Immutable runtime Secret preserves the signing/model/vector/diagnostic keys and
replaces only DATABASE_URL and the S3 access/secret pair. A separate operator
Secret contains only the database URL and is mounted read-only by offline Jobs,
never normal API/worker Pods. Jobs hash the new application settings while using
the separate operator connection to update the accepted identity and release
maintenance. Installation, schema, images, storage receipt and held corpus/work
hashes stay unchanged. The protected config retains operator access for explicit
maintenance; do not replace it with the application URL.

The actual2026-11-01 transition passed at epoch6: 4 API/worker processes proved
their scoped credentials over TLS, 10 SQL and9 S3 forbidden operations were denied,
the retired identity was rejected and transition audits were present. All17 prior
documents retained content/generation/source/delete state; a fresh DOCX completed
with one publication, no problem and matching search/download SHA. Shared auth
worked across both APIs. Final2/2/2 Ready0restarts, production scheduleOFF.
[Public result](superpowers/reports/2026-11-01-replica-scoped-credentials.json).
This closes scoped runtime credentials for this recipe. External/mixed/RWX and
storage-full acceptance remain open; the coordinated CA follow-up is below.

## Coordinated CA replacement in the owned lab

`scripts/kubernetes/replica_ca_rotation.py` replaces the root and three storage
certificates in the same synthetic installation. It requires the protected
operator config/journal, recorded namespace UID, original runtime fingerprint,
`--apply`, and an authenticated independent backup younger than24h with matching
installation, schema and images. The current app remains on its scoped PG/S3
credentials; the separate operator DB Secret is available only to offline
compute/release Jobs. Normal API/worker Pods never receive it.

```text
prepare → fence → storage → check-storage → compute → tls → stage → release → start → verify → activate
```

`prepare` additionally requires `--archive`, `--key-file` and
`--credential-journal` from the completed scoped-credential operation. It creates
independent private lab key material and immutable new roots/TLS resources;
existing trust, keys and application settings are retained. This is the explicit
short-lived lab PKI recipe, not customer certificate lifecycle automation.

`fence` records a held maintenance epoch, stops frontend/API/workers/model stub,
requires zero uncertain model reservations and snapshots the corpus and full
Qdrant payloads/vectors. `storage` stops each owned StatefulSet normally, changes
only its TLS Secret reference and restarts it with the original PVC/spec/image.
No claim is removed or recreated. After these Pod replacements, rebind the
recorded owned storage forwards; a libpq connection close may itself terminate
kubectl's PG forward, so inspect/rebind that exact helper before another PC check.
Do not restart a maintenance operation or Job to repair a forwarding process.

`check-storage` verifies the selected immutable material, all three claim UIDs and
specs, new-root TLS, held SQL corpus hashes, every ready S3 object's bytes and
complete Qdrant vectors/payloads. `compute` performs that check again, then
audits/rebinds the common runtime identity under the same held operation.
`tls` runs native PG/REST/gRPC/S3 clients in a scoped diagnostic Job: the new CA
must work, the retired CA and wrong hostname must fail for certificate-specific
reasons. That Job has no operator DB mount. `stage` changes only the stopped
API/worker trust and identity bindings. `release` repeats the storage checks and
requires all native negative receipts before the explicit audited release.

`start` returns the recorded app counts. `verify --report
docs/superpowers/reports/<result>.json` checks every original document's content,
generation, original SHA and trash state, search/download, all four scoped role
processes, a retained login session, transition audits and one new publication.
Its PG privilege probe is identified as prior evidence; S3 deny probes are fresh.
Only then does `activate` atomically select the new CA file in the protected PC
operator config. Old CA/key files and independent backups remain retained.
On uncertain outcomes inspect the same journal/Job and leave maintenance held;
no timeout, TLS failure or missing receipt automatically starts the application.

The first owned TLS diagnostic exposed an unset optional gRPC port in the probe;
the application resolves that setting from the REST port plus1. `retry-tls` is an
explicit correction path for precisely that terminal `_InactiveRpcError` and
absent setting. It retains the original failed Job/Pod and records its UID and
complete spec digest. Every drain observation revalidates the failed condition,
zero active Pods, backoffLimit0 and terminated containers on Ready nodes. Changed
inputs, an active process or an unreachable node refuse the correction. The next
diagnostic gets a different name; the failed check never becomes a PASS, and no
other failed Job is ignored. Ordinary observation timeouts still observe the same
Job; they do not select this correction path.

The actual2026-11-01 coordinated transition passed at epoch8. All18 prior
documents retained their content/generation/source SHA/trash state;90 ready blobs
and all36 original Qdrant payload/vector records matched while held. All3 PVC UIDs
and specs and the application/storage images were preserved. Native new-root
connections and8 specific old-root/hostname refusals passed across PG/REST/gRPC/S3.
The retained pre-stop session still resolved to the same authenticated admin;
four running API/worker processes proved the new common identity with unchanged
scoped credentials. Fresh S3 denial probes passed. One new DOCX finished with no
problem, one publication and matching search/download SHA. Final2/2/2 Ready,
zero app container restarts. The old failed diagnostic remains explicitly failed.
The protected operator config now selects the new CA; production scheduleOFF.
[Public result](superpowers/reports/2026-11-01-replica-coordinated-ca-rotation.json).
This closes only coordinated CA rotation for the selected owned managed-S3 recipe;
storage-full, RWX/external/mixed providers and node/host failover are still open.

## Image update after scoped credentials

Use `scripts/kubernetes/replica_image_upgrade.py` for the owned same-schema
backend-image rehearsal after credentials/CA activation. The earlier
`replica_upgrade.py` recipe refuses a scoped SQL principal before fencing.
This recipe changes the backend image and immutable runtime ConfigMap together;
frontend/model images, scoped runtime Secret, session/signing keys, trust,
storage controllers and all three PVCs remain bound to their captured identities.
Privileged SQL access is projected only into offline bootstrap Jobs as the
read-only `/run/operator-db/url` file. Runtime identity is computed from the
application's scoped settings, using the candidate image inside Linux.

```text
prepare → fence → compute → stage → release → start → verify
```

Every command names the explicit owned `--config`, `--namespace-uid`,
`--expected-fingerprint` and protected `--journal` beside the config file.
`prepare` additionally requires `--image`, `--archive`, separate `--key-file`,
the verified `--credential-journal` and latest verified `--source-journal`.
The authenticated backup must match the current installation/fingerprint/images,
be younger than24h and reside in an independent failure domain. A new backup
after CA acceptance may supply that source journal through the backup command's
`--source-journal`; only the recorded exact terminal diagnostic is recognised.
All other unknown writers, failures and uncertain model reservations still refuse.

`fence` records the operation before normal role shutdown. `compute` and
`release` use the same journal and existing Jobs when observing uncertain outcomes;
never delete them or start another operation merely after an observation timeout.
`stage` accepts only stopped unchanged source templates or the exact already
staged candidate; partial image/config/identity combinations are refused.
The held SQL corpus, every ready object's size/SHA and full Qdrant vector/payload
inventory are checked before release. `start` requires the successful release
receipt and intended role counts. `verify --report docs/superpowers/reports/<name>.json`
checks the existing authenticated session, all four scoped API/worker processes,
transition audits, old identity rejection, original corpus/trash/search/download
and one new clean publication. Prior privilege-denial evidence is identified as
reused; it is not presented as a new privilege-probe run.

This remains a synthetic managed-S3, single-node lab recipe. Schema-changing
upgrades/adoption, rollback/restore, other storage profiles and final combined CI
remain separate gates. Production backup scheduling stays disabled.

The actual2026-11-02 run preserved19 prior documents, their generations/source SHA
and trash state, the existing authenticated session, all storage claims and scoped
credentials. One new document completed cleanly with exactly one publication;
final2/2/2 Ready had zero restarts. Two PC PostgreSQL-forward closures after durable
phase completion were handled by rebinding only that recorded forward and
reverifying the same operation, not by repeating its fence or compute Job.
[Image-update result](superpowers/reports/2026-11-02-replica-scoped-image-upgrade.json),
[independent-copy result](superpowers/reports/2026-11-02-replica-current-runtime-backup.json).

## Bounded native S3 ENOSPC contract

`scripts/kubernetes/replica_storage_full.py` uses the separate owned
`replica-test-full20261102` namespace. Its one restricted Pod has the pinned
RustFS1.0.4 server and a probe using the deployed backend image. The S3 adapter
component is unchanged from that image's source revision. All writable volumes
are bounded memory filesystems; data is128Mi, scratch32Mi, logs/temp16Mi each.
There are no PVCs, host mounts/namespaces, SA tokens or exposed Services. Network
policy denies ingress/egress; the TLS client talks only to127.0.0.1 in its Pod.
The client mounts the public root, not the server's TLS private key. See the
[Kubernetes memory-volume contract](https://kubernetes.io/docs/concepts/storage/volumes/#emptydir).

```text
prepare → seed → arm → fill → check-single → check-multipart → recover → verify → report → stop
```

Every phase requires `--apply --journal <protected-directory>/storage-full-journal.json`.
The recipe only accepts the existing protected owned-lab directory. `report`
additionally requires `--report docs/superpowers/reports/<result>.json`.
It never reads the working app's env/config or connects to its SQL/model/storage.
`arm` persists the unique filler inode/device. `fill` checks the exact writable
tmpfs/mount capacity before writing at most its bounded capacity and requires a
real errno28 with zero remaining bytes. It cannot fill a disk-backed directory.
`recover` unlinks only the same regular single-link inode. Each native phase has
an operation/action/recipe-hash receipt. A completed receipt is observed on retry;
an in-progress marker without completion refuses execution until the same process
and outcome have been inspected. Never remove markers merely to repeat a fault.

The actual2026-11-02 contract passed: native ENOSPC at128Mi, single conditional
PutObject and multipart UploadPart/Abort refused with500 InternalError, original
8192-byte object still readable with matching SHA/range. RustFS's message contained
no ENOSPC/errno28 signal, so the adapter correctly returned sanitized
BlobUnavailable; converting all InternalError responses into StorageFullError
would be incorrect. After freeing only the filler, the same single/multipart IDs
published successfully, conditional replay verified their SHA/ranges, and native
multipart inventory was empty. No provider errors or clients were mocked.
[Public result](superpowers/reports/2026-11-02-replica-bounded-s3-enospc.json).

This closes the bounded native S3 server/adapter contract only. Physical/PVC quota
and PG/Qdrant/RWX/external/mixed full-storage checks, plus API/worker publication
under full storage, remain required. No production or HA qualification is issued.

## Scoped fault recovery after an image update

The owned post-image managed-S3 recipe accepts `--source-journal`, `--archive`
and `--key-file` together in `replica_worker_loss.py` and
`replica_postgres_partition.py`. The source must be the verified image-operation
journal for this namespace/installation/current fingerprint. Its existing exact
immutable Secret/config/trust/PVC/controller guards and retained failed-diagnostic
verifier are reused. The authenticated independent backup must match the current
fingerprint and be no older than24h when a new fault is admitted. Resume retains
the originally admitted provenance/time, so elapsed time alone cannot strand an
already stopped installation. Changed journals or backup bindings refuse resume.
This adapter covers the current owned post-image recipe, not other providers.

Run the explicit `preflight` phase before `inject`, using the same protected inputs
and a new fault journal name. Preflight reads/validates inputs and never writes a
fault journal or injects a failure. An unconfigured legacy command refuses scoped
runtime credentials before starting its fault probe. It never grants DDL/control
privileges to the application role.

```text
replica_worker_loss.py preflight --apply --config <protected-config>
  --namespace-uid <uid> --expected-fingerprint <current-fingerprint>
  --journal <protected-directory>/scoped-worker-loss.json
  --source-journal <protected-directory>/image-upgrade-journal.json
  --archive <matching-independent-copy> --key-file <separate-key-file>
```

Use the same arguments for subsequent phases. Worker loss follows
`inject → observe → recover → release → verify`. PostgreSQL partition retains
its separate confirmed-reply flow `inject → confirm → observe → heal → finish →
restore → verify`; uncertain provider completion still requires physical stop
proof and the audited recovery path. Never repeat `inject` to recover a response.
Privileged release SQL is read from the operator Secret only through the0440
read-only `/run/operator-db/url` projection in an offline bootstrap Job. Normal
Settings/envFrom keep the scoped application credentials and common identity.
Existing release Jobs are checked through the strict credential-job observer,
including command/environment and Secret/config bindings; they are not recreated.

Release normally continues to application start. If its response is lost after
release or during restart, inspect the same journal and use the explicit `start`
phase when it records `released`. Start requires the exact release receipt,
controller UIDs and original templates (or the stopped owned30-second model
fixture). It restores that provider template, scales only still-zero roles and
leaves already intended counts unchanged. Before recording `running`, it verifies
the Ready runtime fingerprint, the exact released operation/epoch and its audit.
A changed count/template/identity or a later maintenance operation refuses ACK.
This is restart recovery, not evidence of physical-node or external-provider HA.

## One current-version runtime acceptance run

The common `backend/test_scripts/run_replica_acceptance.py` entrypoint supports
`--owned-runtime-stage` with a protected `--runtime-journal`. This is an explicit
owned basic managed-S3/model-stub/fake-embedding recipe. Default `--live` smoke
behaviour stays unchanged. The manifest keeps its existing isolated namespace/DB/
bucket-prefix fields and adds `runtime_fingerprint` plus `owned_runtime` with only
four path references: `config`, `source_journal`, `archive`, `key_file`. Namespace
UID, installation, release names, fixed kind context and loopback frontend must
match the protected config. The key remains outside the archive.

```text
python backend/test_scripts/run_replica_acceptance.py --suite runtime --live
  --manifest <protected-manifest> --runtime-journal <protected-directory>/runtime.json
  --owned-runtime-stage preflight
```

Use the same files and recipe version for every stage. Mutating stages additionally
require `--apply-owned-runtime`. The ordered stages are:

1. `preflight`, `smoke`, `idle`, `after-idle`, `active`.
2. `worker.inject`, `worker.observe`, `worker.recover`, `worker.release`, `worker.verify`.
3. `partition.inject`, `partition.confirm`, `partition.observe`, `partition.heal`,
   `partition.finish`, `partition.restore`, `partition.verify`.
4. `load.prepare`, `load.single`, `load.dual`, `load.verify`, `final`.

Smoke validates actual roles/identity/session/network/document flow and resources.
Idle scaling/Pod replacement is separate from the following document roundtrip,
allowing the recorded frontend transport to be refreshed between them. Likewise,
refresh that transport after worker release/restart and before verification. The
recorded PostgreSQL transport may need refresh before worker recovery or partition
reconciliation; never refresh it while an operator still owns a physical SQL
connection. This runner does not open host ports or replace unknown listeners.

The parent journal fixes one execution ID, recipe/input hashes and original
nonempty corpus. Generated child paths and their `runtime_run_id` prevent adoption
of old standalone reports/journals. Parent intent is durable before mutation.
Repeated completed stages return their existing outcome. An uncertain active
injection refuses replay. Inspect the same child/Job; `--resume-owned-stage` can
continue only recognized observable/idempotent child phases. A confirmed late
provider reply and an uncertain quarantined reply retain their different recovery
paths; the latter still needs physical proof and audited reconciliation. Known
maintenance increments are verified as a chain, not treated as a reason to adopt
an unrelated epoch.

Each invocation writes one final JSON object to stdout; native member progress is
on stderr. Exit0 means the requested suite has all required checks passed. Exit1
with `failed=0` and remaining `skipped_required` means the staged run is incomplete,
not qualified. A partial run never receives PASS. Runtime completion alone keeps
`--suite all` unqualified while contracts/storage/operations or other required
suites remain pending. Reports contain hashes/timings; detailed private outcomes
stay in the protected journal. Changed code/inputs require a new run, preserving
the previous journal rather than rewriting its identity or importing its results.
Production backup scheduling staysOFF and this is not customer/model/HA sizing.

The2026-11-02 current basic managed-S3 execution completed all22 stages with
0 failures and0 required skips. It retained80 baseline documents and verified
the legitimate epoch15→16→17 recovery chain, final2/2/2 replicas, model capacity,
and source search/download. See the
[common runtime receipt](superpowers/reports/2026-11-02-replica-common-runtime.json).
Older failed parent journals remain historical failures. Other suites/profiles
and final-version CI remain separate gates. The latest20-document restore drill
subsequently passed as recorded below; it does not transfer this22-stage runtime
qualification to the new target.
Worker memory lifetime peak reached92.24% of its768MiB limit in the two-worker
trial with no OOM; these bounds are lab measurements, not production sizing.

## Restore with scoped runtime credentials

When the fresh target uses scoped PG/S3 application principals, mount its
administrative SQL URL separately as a read-only0440 Secret projection at
`/run/operator-db/url`. Pass `--operator-db-file /run/operator-db/url` to the
offline `restore_identity.py` Job. Keep envFrom/Settings on the scoped runtime
Secret so the resulting signed identity belongs to the settings ordinary roles
will actually use. The operator URL must retain the exact target driver, host,
database, effective port, verify-full/root certificate and other query options;
only its SQL username/password may differ. The file is bounded to16KiB and must
resolve to regular material; supported projected-Secret symlinks are allowed.

The option changes only the offline engine connection. It does not grant an
application bypass or release restored work. Authenticate/import the backup into
new disjoint stores with the source unavailable, create the scoped principals,
verify imports and held jobs, rebind identity, then explicitly release the same
restore operation after its checks. Keep failed targets stopped and preserve
source PVCs.

The2026-11-02 [current-copy recovery receipt](superpowers/reports/2026-11-02-replica-current-copy-recovery.json)
records an actual restore of backup6bfd7725f526474990e1e706eb4201a4 from the
independent PC into three new disjoint PVCs in `replica-test-r5o2u20261102`.
Source application/model/storage Pods were stopped throughout import and
functional checks; all96 source documents and its three PVCs were retained.
Before explicit release, all20 document rows,100 blob hashes and40 vector points
were verified, restored jobs remained unexecuted, ordinary writes were refused,
and25 old sessions were invalidated. Fresh scoped PG/S3 principals passed native
positive/negative controls; only the offline Job received the operator projection.
After release,19 visible original documents passed targeted search/download SHA,
one original remained hidden in trash, two new documents completed, and seven
runtime smoke checks passed with2/2/2 Ready and no restarts. The new target has
not repeated the full22-stage runtime fault suite. Timings include operator
implementation and manual verification; they are not production RTO sizing.
The later [predecessor replacement recovery](superpowers/reports/2026-11-02-replica-predecessor-schema-recovery.json)
also verified a real rpl04→rpl05 transition in a new target, new scoped identity,
source auth cleanup, explicit release and original/fresh functional checks.
Compact/NULL-generation and other adoption/update recipes, key recovery, alert
delivery and the other providers remain open. Production backup scheduling staysOFF.
