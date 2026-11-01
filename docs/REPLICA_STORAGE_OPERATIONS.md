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
This closes scoped runtime credentials for this recipe. External/mixed/RWX,
coordinated running-app CA rotation and storage-full acceptance remain open.
