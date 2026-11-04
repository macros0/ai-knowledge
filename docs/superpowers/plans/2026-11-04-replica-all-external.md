# All-external selected recipe implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline, with no new
> reviewer. Track the existing O1/O2/O3 requirements below with checkboxes.

**Goal:** Prove the selected application recipe with all three storage services
external to its namespace/lifecycle, including independent source-off recovery.

**Architecture:** A fresh application namespace has only2frontend/2API/2worker,
model stub and namespace-scoped configuration/policies; no storage workload/PVC.
A separate owned provider namespace supplies real pinned PG17/Qdrant1.19/RustFS1.0.4
services, each with a fresh retained512Mi PVC and explicit TLS/auth/ownership.
This is a training external-service contract, not customer or physical-HA approval.

**Tech Stack:** Existing kind-okf-policy on VM103, current verified65d6ee2 backend,
unchanged current frontend/stub, Helm and official bootstrap/maintenance/format3.

**Spec:** [approved replica/storage design](../specs/2026-10-31-kubernetes-replicas-storage-design.md),
[O1/O2/O3](2026-10-31-kubernetes-replicas-operations.md).

## Global constraints

Only new synthetic data; production scheduleOFF/certificationnull. No cluster
operator/CRD, storage-node, NAS, other VM/CT or existing claim change. Source
application and provider namespaces have independent exact UID/spec/Secret-hash
journals. Application storage ownership is external for every component; the
provider fixture is explicitly owned independently for this rehearsal. Only a
verified terminal source may be adopted after a lost local observation.

Reuse verified images/source bytes, never hot-edit containers. Frontend cannot
reach any external storage; explicit app-role/provider namespace selectors allow
only required ports. Unknown HTTP/create/scale/backup outcomes are inspected,
never replayed. No live work/lease/slot/clock edits or uncertain provider ACKs.

## Review focus

- External chart accidentally creates or acquires storage/PVC/credentials.
- Cross-namespace policy or TLS silently broadens to unrelated namespaces.
- Backup incorrectly treats external ownership as missing or as managed lifecycle.
- Restore reaches the source provider despite a source-unavailable claim.
- A capacity loan or running external fixture remains after closure.

## Tasks and evidence

- [x] Read-only host/namespace/image/source preflight; require actual free memory
  >=4GiB and disk10percent+1.5GiPVC+2Gi scratch before creating resources. If
  needed, take new reversible idle-role loans with fresh runtime/PVC/9table
  snapshots; keep all old providers unchanged and return every loan at closure.
- [x] Render separate provider/app inputs locally. App ownership all external;
  assert zero storage StatefulSets/PVC/credentials in application chart output.
  Pin all source/image/CA/namespace/role facts. Dry-run all exact objects before
  create. New negative guard tests must reject wrong namespace/Pod/spec/mount/
  provider ownership, secret roots and unknown admission outcomes.
- [x] Create providers separately, provision scoped application SQL/S3 rights,
  bootstrap fresh Alembic/common identity normally. Actual native scoped TLS,
  wrong-host/wrong-credential controls and frontend network denials for all3
  endpoints, with API positive controls. No admin credential in runtime.
- [x] Start2/2/2; shared session, idempotent upload, processing/search/download/ZIP
  and four native cold SHA/process/SQL/audit checks, zero duplicates/scratch.
- [x] Hold all application writers normally. Produce a real encrypted format3
  PG+Q+all accepted artifacts copy to the independent PC with all3 storage
  provider fields external and separate ownership/component receipts. Verify
  every authenticated object and source/claim identities, not just counts.
- [x] Prepare a fresh isolated target with fresh keys/identity and prove native
  source-endpoint denials while all3 source providers are still alive, with
  positive source controls. Then keep the source app stopped/held and physically
  stop all3 source provider Pods before loading/restoring data. Use official recovery;
  require source-unavailable scope before every restore/adoption step. Require cold hashes, held work nonexecution,
  scoped rights, explicit release, old session policy and actual fresh publication.
- [x] Close exact forwards/providers/Jobs, retain PVCs and verified encrypted copy;
  return all capacity loans and verify original runtime/PVC/9table hashes. Preserve
  failed attempts. Publish bounded evidence/matrix/runbooks/security, scan protected
  literals, and run mandatory node scripts/check-project.mjs before any commit.

Ruling: the approved design already selects per-component external endpoints and
permits downtime; two owned namespaces provide an actual independently managed
training service contract without requesting new cluster/platform authority.
The new provider fixture proves this selected recipe only. It cannot establish
customer provider permissions, separate physical domains, resilient certification,
production RPO/monitoring or real model/IdP behavior. If a software gap is observed,
use a meaningful RED-to-GREEN regression and the appropriate current-code CI;
no product change is made merely to relabel the fixture as certified.

Final checkpoint: the selected all-external S3 recipe is verified and closed.
See [immutable public report](../reports/2026-11-04-replica-all-external-runtime-recovery.json).
The application and independently owned provider namespaces used actual TLS/scoped
services, then PC format3 source-off recovery into three fresh managed512Mi claims.
All81 tables/28 sequences/two full points/six blobs matched before official held
adoption. Old/new HTTP and four native12-object SHA checks passed. Source held1
and target held3 are physically off; six claims/copy retained. Both earlier
capacity loans returned to2/2/2 with exact original runtime/PVC/nine-table hashes.
The target scratch budget was512Mi after measured source sizes70.6MB/2.88MB/0.31MB;
source preflight retained its2Gi scratch floor. No old cache/scratch/data deletion.
Product65d6ee2 was unchanged; prior immutable6710-case Linux CI remains applicable.
This closes the selected fixture, not overall O1/O2/O3 or customer qualification.
Production scheduleOFF/certificationnull.
