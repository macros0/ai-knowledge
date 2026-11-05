# Selected Synology target relocation implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline. No new
> subagents, reviewer or worktree; retain the four unrelated dirty paths.

**Goal:** Close existing D4 with an actual authenticated NAS-target4.0 restore.
**Architecture:** Add a literal NAS source/target proof to the existing RWX
restore entry point; reuse native import, held adoption and explicit release.
**Tech Stack:** Python/Pydantic, PostgreSQL17, Qdrant1.19, DS211 DSM6.2.4U8,
existing CSI4.13.6/NFS4.0 and2/2/2 training application.
**Spec:** [Design](../specs/2026-11-05-synology-target-relocation-design.md).

## Global constraints

Only synthetic retained PC backup and fresh target namespace/claim directories.
No platform/NAS changes, keys transferred to VM, source-directory writes or
relaxation of the strict4.1 recipe. Full final-source CI for the current provider
matrix and its two original capacity returns complete before new target startup.
NAS proof carries client-only provenance. ScheduleOFF/certification=null.

## Review focus

- Binding tamper, wrong recipe or alleged server identity refuses before mount/SQL.
- Source/ancestor/descendant export, inode alias, stacked mount or symlink refuses.
- Version/transport/security/address/UID drift cannot pass as the selected recipe.
- Artifact probe or SQL adoption may not use a different target proof.
- An unknown import/create/release intent is observed; never repeat a mutation.

## Task1: literal NAS relocation proof

Files: create `backend/app/deployment/nas_restore.py` and
`backend/tests/test_replica_nas_restore.py`; modify
`backend/app/deployment/rwx_restore.py` only for literal dispatch and a fixed
mount-version argument (default remains4.1). Existing official
`scripts/kubernetes/restore_identity.py` must continue to reobserve before/after
probe and in its transaction without a bypass flag.

Interfaces: `verify_nas_relocation(settings, plan, source_binding) -> dict`
authenticates the exact NAS scope and validates a real target mount. No caller-
supplied target observation or privileged server metadata. Dispatch solely by
the authenticated artifacts scope recipe. Proof has source scope, namespace,
NFS source and client inode/UID/GID/mode, wire identity, selected version and
`server_filesystem_identity_claimed=false`.

- [x] RED: actual published source-binding shape, valid disjoint4.0 target,
  tamper/unrecognized scope/UUID/handle/owner/reader/false-server-provenance
  negatives; old4.1 recipe must continue rejecting4.0. Unsafe target mount,
  source alias/containment, wrong namespace/address/transport/wire identity and
  changed proof before SQL must refuse.
- [x] Implement the separate bounded proof and narrow dispatch; GREEN new tests
  plus existing RWX/identity tests, required checks and a source-bound Linux cohort.
- [x] Commit source and record exact image/source verification for Task2.

Source commit bbced15 and a separately verified Docker image are ready:
`sha256:8210b868876c16fba1c41f7d2274b9cb64fd9e37d0235ccf6bf1c47397362486`.
All575 packaged Git files and117 unchanged packages were checked; only the two
NAS/RWX modules differ from the verified65d base. No native image import or
target startup is claimed at this checkpoint. Both prior matrix loans and the
prior fixture are now closed in the linked provider plan.

[Component evidence](../reports/2026-11-05-replica-nas-target-proof-ci.json):35 new
cases reproduced the missing NAS branch, while the legacy4.1 rejection passed.
All74 new/existing RWX/identity cases then passed identically on Windows/Linux,
0skips/failures/errors, source unchanged/noOOM. Required checks passed. The1GiB
focused CI reserved the remaining growth of the concurrent provider full-CI plus
1GiB for the host. Actual readonly preflight authenticated the retained copy and
verified source-off/existing CSI; no target or NAS write yet. This component
report is not native restore evidence or a complete final-source CI certificate.

## Task2: actual new-target import/adoption/runtime/closure

Use ignored durable `tests/tmp/synology_nas_target_*` helpers and protected
operation/input files. Retain immutable source journals/archive/binding. New
namespaced manifests reuse the admitted CSI/StorageClass; render and dry-run
first. Do not reuse a closed capacity loan. Any necessary new loan records exact
pre-state and closes with original counts/runtime/PVC/nine materialized tables.

- [x] Verify source-off and independent archive; prove the fresh/disjoint target
  mount with actual non-root UID1031/G100 before import or identity changes.
- [x] Import81 tables/28 sequences/four full points/eleven authenticated blobs
  with native intent/unknown-ACK handling; compare before adoption, jobs held.
- [x] Use unchanged official identity command and scoped operator projection;
  verify new identity/old-cookie rejection/explicit release/2/2/2 and old/new
  search/download/export/upload plus cold SHA.
- [x] Fence/stop target, close handles, retain all PVC/data and restore capacity.
  Publish a typed sanitized report, update D4/runbooks/support matrix and retain
  all customer/physical/trusted-issuer gates.

Actual Task2 functional evidence is now available in the
[NAS target report](../reports/2026-11-05-replica-synology-target-restore.json).
The new claim's client inode16371 differs from source16314; all81 tables,
28 sequences, four full points and eleven blobs matched before adoption.
The unchanged identity command revoked the old session; held/release checks,
2/2/2 readiness, all-four-endpoint document SHA/search, new ZIP integrity and
four-process cold reads of17 ready objects passed. The target is now held at
epoch3, all Pods terminal/CRI0, three PVCs retained, its NFS mount and owned
listeners gone. [Closure on06Nov](../reports/2026-11-06-replica-synology-target-closure.json)
confirms both current capacity returns: original2/2/2, fingerprints, three PVCs
and nine canonical tables per source preserved. D4 is closed for this selected
lab recipe. [Final f1b38e8 CI](../reports/2026-11-06-replica-s4-final-backend-ci.json)
passed7137+221unchanged optional skips; the original runtime binding remains bbced15.

The frozen original ZIP expired at2026-11-04T22:38:21.981982Z before this
rehearsal. Its authenticated bytes, native-import SHA and DOCX-member SHA passed;
the normally running worker then retired it once under the existing retention
rule. A separate test proved its HTTP409 rejection on all four endpoints.
No post-expiry HTTP200 is claimed, no clock/expiry was changed, and the original
failed HTTP200 assumption is retained. The first new fixture was legitimately
rejected as similar; a distinct synthetic topic with a new key passed with
deduplication unchanged and the rejected intent/bytes preserved.

Ruling, updated after D5 requirement audit: retain the same two fresh owned-idle
capacity loans through the single final backend CI and the original
[S4 legacy→RWX criterion](2026-10-31-kubernetes-replicas-storage.md), then restore
both original2/2/2 counts/runtime/PVC/materialized tables immediately. That
criterion was still unproven; portable NAS restore must not substitute for it.
This avoided another stop/start cycle; the loans remained explicitly open and
prevent D4 closure until returned. The immutable backend CI
uses ae8bffb (application bbced15); later report-only edits require separate
metadata checks and are not part of that snapshot.
