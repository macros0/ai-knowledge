# Replica storage packages and bootstrap

Implementation status, 2026-11-04: basic managed PostgreSQL/Qdrant/S3 templates and
external/mixed application wiring are implemented. Production activation still
requires the remaining O1 provider checks, O2 recovery and R5/O3 acceptance.
The separately admitted training RWX recipe now has a real NFS/CSI bootstrap and
two-Pod adapter proof. An existing verified RWX claim can be used by the application;
bounded RWX application/ENOSPC/restart checks passed; independent restore, large-write and cross-node gates remain open. Resilient is rejected.

## Selected basic recipes

The [application S3 full rehearsal](superpowers/reports/2026-11-04-replica-s3-application-full.json)
now tests the real API/worker path on65d6ee2 with an explicitly ephemeral128Mi
fixture. An unavailable artifact store returns503/artifact_unavailable at upload;
do not infer quota507 from opaque provider500. Typed BlobUnavailable permits
bounded retry of the same accepted work under a fresh fence. Missing/corrupt/
conflicting blobs and lost read leases still require reconciliation. During the
rehearsal old original/search/ZIP remained available; after75s independent space
recovery the same regeneration succeeded on attempt3/fence3. The known503 upload
was retried with its original key and created one document/work/audit. Uncertain
HTTP outcomes still require receipt/work inspection before replay.

Four processes verified all16 ready SHA and exact business effects; the fixture
is off/held3 with2PG/Q claims retained. Ready synthetic blobs were copied and
rehashed on the independent PC before stopping memory S3. This copy has no PG/Q
snapshots and is not coordinated backup/restore evidence. Both borrowed old
2/2/2 sets and their runtime/PVC/nine-table hashes were restored. PG/Q exhaustion,
production capacity/quota and other providers remain separate gates; certification
and production schedule are unchanged.

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

## NFS/RWX training bootstrap (2026-11-03)

The user separately admitted an isolated NFS platform on VM103. Official CSI4.13.6
and all five container images are pinned by digest; six cluster-scoped resources,
a separate namespace and a nondefault Retain StorageClass were created with UID
journals. NFSv4 is bound to the private kind bridge and exports only to the bound
node. Its new ext4 regular-file filesystem is limited to1GiB shared across test PV
directories. RPC bind/statd network services are masked; root_squash is enabled.

The [actual two-Pod provider proof](superpowers/reports/2026-11-03-replica-nfs-rwx-bootstrap.json)
passed immutable/idempotent creation, cross-Pod read/range, one-winner concurrent
creation and deletion of its two known synthetic objects. Failed initial offline
apt and successful same-operation dpkg recovery retain separate evidence. The
current backend image matches the full ccfcf9e CI and573 copied Git files.
RWX application/restore/full-storage/cross-node and external/mixed qualification
remain open. The earlier key-recovery and local-alert gates have separate completed
training reports; production scheduling staysOFF and certification staysnull.

## RWX application and bounded faults (2026-11-03)

The [actual current-image application receipt](superpowers/reports/2026-11-03-replica-rwx-application.json)
uses a fresh namespace/database/credentials/TLS and a new CSI claim. PostgreSQL and
Qdrant belong to the application infrastructure release; RWX belongs to the separate
platform lifecycle (`artifacts.provider=external`). This exercises that ownership
combination on our own lab; it is not customer external-service qualification.
Two frontends/APIs/workers passed shared sessions, cross-frontend upload idempotency,
publication/search/download and worker export. Server-local ordinary-UID allocation
filled the bounded filesystem: actual HTTP507/storage_full retained all earlier
objects and six business tables. Exact-inode cleanup and the same-key retry then
published the second document. Normal NFS restart retained both documents, the
old authenticated session and the export on all four HTTP endpoints. Four native
processes verified all11 blob hashes, two upload audits and three business WorkItems
completed once; scoped original scratch was empty. The target is stopped under
maintenance epoch1 with all three PVCs retained; earlier lab replicas were restored.

A separate initial large buffered NFS client write did **not** pass. At roughly54MiB
progress, all four NFSd threads waited in ext4 write/reclaim/COMMIT paths while the
filesystem still had free blocks. This is consistent with a same-kernel loopback
reclaim cycle, not a proven specific kernel bug. The exact filler was killed, the
new roles drained, four temporary server threads allowed COMMIT to finish, and the
known filler was removed only after process exit. The original four threads and
configuration were restored; cold verification preserved all earlier hashes. No
VM reboot, forced unmount, forced Pod deletion or foreign service stop was used.
The successful separate server-local ENOSPC test does not qualify large NFS writes.
Independent RWX recovery, sustained/outage/cross-node tests remain open. Production
scheduling staysOFF and no profile receives certification from these receipts.

The subsequent [independent RWX copy](superpowers/reports/2026-11-03-replica-rwx-independent-backup.json)
contains a native PG17 exported-snapshot dump, a native Qdrant snapshot and all11
ready blobs, encrypted as13 format3 objects on the separately authorized PC. All
objects were cold reopened, decrypted and authenticated;81 tables/28 sequences,
four full vectors and accepted blob hashes were bound to the frozen source. The
non-secret adjacent `source-binding-<backup_id>.json` must accompany the archive:
its NFS/PVC/PV coordinates are authenticated by the manifest's artifact storage
scope digest. The key stays in the protected PC vault. Application writers remained
stopped under epoch1; native snapshot/tunnel owners closed, both source storage
Pods stopped, PVCs retained, and prior2/2/2/runtime/PVCs/nine tables restored.

At this backup-only checkpoint restore had not run and identity rebind accepted
S3 only. Subsequent selected RWX target support and its recovery are recorded
below. Equal `/artifacts` paths cannot prove relocation: authenticated physical
source/actual target binding is required. Do not bypass the guard or relabel RWX as S3.
NFS server unavailability, large-write and cross-node gates remain separate.

## RWX source-off restore into real S3 (2026-11-03)

The [completed independent recovery](superpowers/reports/2026-11-03-replica-rwx-source-s3-restore.json)
used the RWX-source backup with all source providers unavailable, including the
actual stopped NFS server. New managed PostgreSQL/Qdrant and real RustFS used
three fresh claims, fresh TLS/scoped credentials and the unchanged restore CLI.
All81 tables/28 sequences, four full points and11 blobs matched before identity
adoption. Held2/explicit release, old-cookie rejection, old search/download/export
and new upload/export passed on2/2/2. Four current-image API/worker processes
verified all17 resulting objects and empty scratch; original business tasks
remained succeeded once at epoch0, new tasks succeeded once at epoch2.

Closure is verified: target roles/storage0, liveCRI0, held3, three PVCs retained;
source NFS restored to the same filesystem and172.18.0.1:2049/four threads; prior
lab2/2/2/PVCs/runtime/nine tables unchanged. Source app/PG/Qdrant stay off. This
qualifies only the bounded RWX-source→S3-target recovery recipe on the training
node. A fresh RWX target requires the separate authenticated relocation proof below.
Large-write, sustained outage, cross-node and customer provider qualification
remain open, with production schedulingOFF.

## Selected independent RWX-target recovery (2026-11-03)

The [completed current-image rehearsal](superpowers/reports/2026-11-03-replica-rwx-target-restore.json)
restored the PC archive into new PG/Qdrant and a disjoint NFS CSI claim/export/root.
All81 tables/28 sequences, four full points and11 blobs matched before held
identity adoption. The manifest-bound source and actual target mount were checked
by the new cf74f8a image, with full Linux6466 passes/221 unchanged optional skips.
The source claim stayed unmounted; the same admitted NFS server remained up.

Scoped credentials, actual Pod-IP password denial, session revocation, normal
held refusals and explicit release passed. A real controlled exit23 on a resumed
SQL transaction before identity output was followed by identical-identity resume
and109 unchanged catalog entries; the first adoption had already succeeded.
Old/new HTTP workflows passed across two frontend/two API endpoints; four native
API/worker processes checked17 cold hashes, empty original scratch and once-only
business jobs. Closure held epoch3, stopped all target Pods/CRI, retained three
PVCs and restored prior2/2/2/runtime/PVCs/nine tables without data mutations.
Other NFS servers, the earlier failed large buffered write, prolonged outage,
cross-node and physical failure domains remain unqualified. ScheduleOFF.

## Remote Synology component check (2026-11-03)

The supplied `10.10.1.5:/volume1/Vitaliy` accepts NFSv4.0; v4.1 returns
`Protocol not supported`. [Bounded current-image evidence](superpowers/reports/2026-11-03-replica-synology-nfs4-component.json)
passed256MiB RwxBlobStore write/fsync/atomic publish and a fresh process cold SHA,
range read and conflicting-put refusal. Client UID1000 sees owner65534; the
observed mapping is explicit and does not change product restore requirements.
This test used independent Docker processes, not Kubernetes/CSI or application
replicas. Existing share files were preserved despite permission to clear them.

The quota probe accepted1025MiB without storage-full error and stopped at its
hard cap. Thus the requested1GiB limit is not enforced on these writes; do not
continue filling the underlying NAS. Both probes removed their own files and
mounts, all owned containers stopped, and prior2/2/2 stayed Ready. Enforce a bounded
quota before NAS storage-full/retry acceptance. The user clarified that this
Synology offers a user quota: use a dedicated NAS account with its actual numeric
UID/GID and a1GiB volume1 limit. The original clientUID1000 probe does not test
another account’s quota. Folder quotas, where supported, require Btrfs. An
independent NFS VM is deferred while this user-quota path is prepared. CSI/application, outage, restore and profile gates
remain open; the earlier selected NFSv4.1 result remains a separate recipe.

The supplied NFS rule screenshot selects no squash mapping and asynchronous
writes. Its applied state was not read from DSM. Use a synchronous export for
subsequent preservation tests; the existing write/read proof does not establish
power-loss durability. See the report’s upstream Synology references.

The subsequent [correct-user/CSI run](superpowers/reports/2026-11-03-replica-synology-userquota-csi.json)
resolved `Vitaly` as UID1031/GID100 from the user's DSM task output. With matching
NFS client credentials, the bounded native write reached StorageFullError;
baseline integrity and same-object-ID retry after cleanup passed. A new
nondefault Retain class reused the admitted CSI4.13.6 driver and created one
fresh NAS claim. Two independent restricted Pods ran as1031:100, proved the same
actual NFSv4.0 root and passed256MiB write, cold SHA, range and conflict checks.
Only own test objects were removed. Both Jobs succeeded and all live containers/
target mounts are gone; namespace/class/empty PVC/PV are retained for further
selected runtime checks. The quota covers the NAS user on volume1, not each PVC.
Application/backup/restore/outage/cross-node and power-loss acceptance are open.


## Selected Synology mixed runtime (2026-11-04)

The [completed bounded rehearsal](superpowers/reports/2026-11-04-replica-synology-mixed-runtime.json)
uses managed PostgreSQL17/Qdrant and the external DS211 DSM6.2.4-25556 Update8
(user-confirmed inventory; asynchronous export disabled by the user), NFSv4.0 and
the existing CSI4.13.6. Client UID1031/GID100 belongs to this training installation;
resolve the actual account identifiers separately for another NAS. Its user quota
on volume1 was entered as1 GB in DSM; the exact byte boundary was not queried.
The PVC request is not a per-directory quota.

On this NAS, ACCESS reports no root write permission while `objects` and `tmp`
remain writable. Runtime readiness now checks root read/traverse and both actual
mutation directories. All four API/worker processes reported this exact condition
with successful readiness, cold blob SHA and ordinary HTTP operations. The change
passed6472 Linux tests with221 identical optional skips; all6687 previous case IDs
were retained and six regression cases added. No ACL relaxation was required.

Two frontend/API/worker replicas passed shared sessions, idempotent upload, search,
download and export. Bounded actual EDQUOT produced HTTP507/storage_full without
changing logical tables or existing reads; after exact-inode filler removal the
same idempotency key completed. Four processes verified all11 resulting objects
and one execution per document/export job. All trial roles/providers are stopped
under held epoch1, with three PVCs retained and owned forwards/mounts gone. The old
2/2/2 deployment, all borrowed providers,109 PG catalog entries, full Qdrant points,
nine canonical tables and eight live-bound S3 blob hashes were restored/checked.

The earlier failed cf74 trial remains recorded and retained separately. Subsequent
NAS-source backup and S3 recovery are recorded below. Prolonged outage and
cross-node gates remain open.
This bounded recipe does not certify physical failure or power-loss durability;
models/auth are stub/simulation. Production schedule remains off; certification null.


## Independent NAS corpus copy (2026-11-04)

The [Synology format3 copy](superpowers/reports/2026-11-04-replica-synology-independent-backup.json)
contains native PG17, native Qdrant1.19 and all11 accepted NAS objects in13
authenticated encrypted objects on the user-declared independent PC. Cold reopening,
decryption, MAC/SHA and the native Qsnapshot checksum passed. The master key stayed
on the PC. Keep the adjacent authenticated source-binding JSON with the archive.

The reader ran as1031:100 with a read-only NFSv4.0 bind, no service-account token,
no provider or database secrets, and a read-only container root. Its15 guard cases
passed after15 proper RED assertions. The binding records actual client mount and
inode provenance; it does not assert direct access to the NAS server filesystem.
The initial plan-header failure happened before any destination or sidecar existed;
all native owners closed. The one already-created Qsnapshot was reconciled and
reused without another POST. A new read-only PG snapshot captured the unchanged
held source. No encrypted stream was replayed.

All native handles and the reader are closed; source app/PG/Q are off under held1,
the source claim is unmounted, and three PVCs plus the completed reader/code objects
remain retained. The borrowed worker returned1→2, with old2/2/2, runtime/PVC and
nine canonical tables verified. NAS service stayed up. This is a backup checkpoint;
the subsequent independent S3 recovery is recorded below. The existing strict
NFSv4.1 target-relocation guard is unchanged. Production schedule stays off.

## Independent NAS-source recovery into S3 (2026-11-04)

The [completed recovery](superpowers/reports/2026-11-04-replica-synology-source-s3-restore.json)
used only the independent PC archive and fresh PostgreSQL17/Qdrant/RustFS with
three disjoint512Mi PVCs. Before identity adoption, all81 tables/28 sequences,
four full vector points and11 cold blob hashes matched. New scoped SQL/S3
principals passed10/9 denials; the unchanged official restore CLI revoked the old
session and kept jobs held. Normal Settings refused startup/write/claim/artifact
runtime before explicit release. No master key left the PC.

Source app/PG/Q stayed off, with no live CRI or source NFS mount. The NAS server
stayed up: a host TCP positive control succeeded, while all four ordinary target
API/worker processes failed to connect to its2049 endpoint. Target applications
had no NFS volumes. This proves source isolation from those processes, not a
physical NAS shutdown.

After release,2/2/2 verified both old documents/search/download/export, old-cookie
401, a new shared session and a new upload/export. The first new fixture was a
near-duplicate and correctly received409 without admission; it is retained. A
different fixture used a new key without relaxing duplicate detection. Four
processes cold-verified17 hashes, empty scratch and no legacy business-work replay.
The target is stopped under held3 with three PVCs retained. Earlier2/2/2,
runtime/PVC bindings and nine canonical tables were restored. Production schedule
is off; NAS-target relocation, prolonged outage, cross-node/physical failure and
real customer model/IdP acceptance remain separate gates.

## Bounded Synology network/I/O recovery (2026-11-04)

The [component drill](superpowers/reports/2026-11-04-replica-synology-bounded-outage.json)
passed on the current96931d1 image and a separate retained128Mi NAS claim. One
kernel-timed150-second nft element blocked only node-to-NAS TCP2049. Actual DROP
counters and in-flight read/write were observed through136.6s; the element was
absent at152.7s without renewal. The same process completed its old cold read
in182.9s and new fsynced/cold write in183.1s, with both1MiB hashes matching. Host
TCP positive controls kept succeeding; no NAS configuration or VM-host firewall
was changed. Exact table handle/spec were checked before removing only that table.

An import-only failed Job and the first delayed-control trial remain retained.
That first trial had150s of network blocking but started I/O120s late because the
nft1.1.3 JSON observer expected milliseconds rather than seconds. It was fully
closed before a fresh confirmed trial; no uncertain apply or timeout renewal.
The unchanged probe ConfigMap used an /app launch wrapper. Three Jobs are retained
(one failed before I/O, two successful); only the two owned blobs and their empty
directories were removed, all CRI/mounts are gone and the PVC remains.

The original1GiB memory floor initially refused before any resource creation. A
new temporary worker2→1 loan made room; frontend/API stayed2 and the old2/2/2,
runtime/PVC bindings and nine canonical tables were restored afterward. Original
NAS source and S3 restore target stayed off. This closes a bounded component
network/I/O recovery check. The full application case is recorded below; physical
node loss, longer outages and power-loss durability remain open.
Production backup schedule is off; no profile certificate is issued.

## Selected Synology active-worker outage (2026-11-04)

The [full application drill](superpowers/reports/2026-11-04-replica-synology-application-outage.json)
used the unchanged96931d1 backend, two frontend/API/worker replicas, managed PG/Q
and the owned DS211 NFSv4.0 claim. Before the approved150s node-to-NAS fault, native
SQL proved the new document's worker owned its first attempt and the delayed
synthetic model was active. Positive DROP counters and pending publication were
observed through136.5s. API liveness stayed200, readiness timed out during the
outage and all six application Pods kept their UIDs with zero restarts.

After natural kernel expiry, completion was observed by194.2s, including polling
and sequential RPC delays. The same candidate became the active generation with
problem=null and the same work ID succeeded on attempt1, releasing its owner.
Previous generation/concept hashes stayed unchanged. All four HTTP routes passed
the original session, both documents' SHA/search, old ZIP integrity, same-key
retry returning the same document and a fresh export. Four native API/worker
processes verified12 cold object hashes, empty scratch and two upload audits.
The model fixture alone was rebuilt from the current immutable CI source; no
application code, dependencies or real model service changed.

The exact owned nft table was removed, original OUTPUT rules preserved, forwards
closed, and the new app plus PG/Q stopped under held epoch1 with three PVCs
retained. The original NAS/S3 sources stayed frozen and unmounted. All temporary
capacity loans were returned; original2/2/2, runtime/PVC identity, nine canonical
tables, PG catalog, Qdrant data and eight S3 hashes were rechecked. This closes
the selected150s application/worker outage case. Cross-node, independent physical
domains, longer interruptions and customer profile qualification remain open;
this observed interval is not an RTO SLA. Production schedule remains off.

## Worker-node loss: DNS and dead PostgreSQL clients

Worker lease expiry cannot release SQL row locks held by a server backend whose
TCP client disappeared. The managed PostgreSQL chart therefore sets server-side
`tcp_keepalives_idle=30`, `tcp_keepalives_interval=10`, `tcp_keepalives_count=3`
and `tcp_user_timeout=60000` (milliseconds). These settings detect dead TCP
peers; they do not impose a statement or idle-transaction execution deadline.
They are Linux PostgreSQL recipe settings, not an application RTO guarantee.
Existing installations need a planned storage workload rollout to receive the
new command-line settings. External PostgreSQL owners must provide equivalent
bounded dead-client detection and verify the actual session values and failure
behavior. See [PostgreSQL TCP settings](https://www.postgresql.org/docs/17/runtime-config-connection.html#RUNTIME-CONFIG-CONNECTION-TCP).

A node-loss rehearsal must also verify that DNS replicas run in different
eligible node domains before the fault. Two DNS replicas scheduled together do
not preserve name resolution when that node is lost. Check actual placement,
service resolution from the surviving worker and the exact provider/claim
identities. A recovered node or an operator-terminated dead database backend
does not establish automatic worker takeover. Preserve unsuccessful trials and
repeat with a new document/work ID after the dependency changes.
