# Selected NFS RWX Target Recovery Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline. The user has already requested autonomous execution of the approved O2 work.

**Goal:** Recover into a demonstrably different selected NFS RWX root while preserving restore fencing and resume safety.

**Architecture:** Authenticate source coordinates through the verified format3 plan,
observe the actual mounted target in the operator process, and bind its stable
physical identity into the restore storage receipt. Retain the existing S3 path.

**Tech Stack:** Python, PostgreSQL/SQLAlchemy, Linux mountinfo/stat, existing Kubernetes NFS CSI recipe.

**Spec:** [RWX target design](../specs/2026-11-03-replica-rwx-target-recovery-design.md).

## Global constraints

- Selected `authorized-nfs-csi-4.13.6-training-v1`, external RWX, NFSv4.1/TCP/sec=sys only.
- Verified PC archive/binding; master key never transferred to VM.
- Existing S3 guards and transactional identity/auth/maintenance behavior remain.
- Ordinary runtime has no operator credential; no automatic release/start.
- No source writes, foreign workloads, new platform installation, profile certification or production schedule.

## Review focus

1. A bind/stacked/local mount or source alias must refuse before artifact probe writes.
2. A tampered binding or valid binding from another source must not authorize adoption.
3. Same-operation resume with a different physical target must retain held identity/auth state.
4. A failed proof or active provider owner must not partially revoke auth or change epochs.
5. Existing S3 restore and guarded operator credential matching must remain unchanged.

### Task 1: Physical proof and transactional adoption

**Files:** create `backend/app/deployment/rwx_restore.py` and
`backend/tests/test_replica_rwx_restore.py`; modify
`scripts/kubernetes/restore_identity.py` and
`backend/tests/test_replica_restore_identity.py`.

**Interfaces:** `verify_rwx_relocation(settings, plan, source_binding: dict) -> dict`
returns the stable observed target binding; it owns real mount/root observation.
`rebind_target(..., rwx_source_binding=None)` revalidates it and binds its digest.
CLI adds `--rwx-source-binding` as a bounded projected non-secret file.

- [x] Write RED cases for the five review focus items and the exact supported mount layout.
- [x] Run focused tests; confirm failures are missing proof/support rather than fixture errors.
- [x] Implement strict binding/mount observation and pre-probe/transactional proof, preserving S3 behavior.
- [x] Run focused GREEN plus restore/bootstrap/backup/maintenance contracts; native NFS mount proof.
- [x] Document CLI/operator boundary, run mandatory checks, scan public artifacts and commit only owned files (implementation cf74f8a; subsequent evidence update below).

### Task 2: Current Linux image and independent RWX-target rehearsal

**Files:** existing operational helpers under ignored `tests/tmp`; dated public
receipt, recovery/storage runbooks and acceptance matrix.

**Interfaces:** Task1 proof consumes the authenticated source binding already
kept with backup b620bc6fc5284e6bbd2311e5acec427e. Reuse the fresh empty retained
`rwxt-artifacts` claim after binding/PV/Pod/native-container guards are rechecked.

- [x] Transfer minimal secret-free patch to the already authorized VM; run appropriate native/full current CI and build exact image.
- [x] Import backup bytes only into fresh stores, verify all SQL/sequences/points/blobs before identity changes.
- [x] Adopt held identity with real mount proof, verify normal runtime refusal before explicit release.
- [x] Verify2/2/2 old/new session, search/download/export and once-only business tasks; original source claim stays unmounted.
- [x] Close owned resources under maintenance, retain all PVCs, restore any borrowed capacity; publish bounded evidence including failures.

Completed [bounded current-image evidence](../reports/2026-11-03-replica-rwx-target-restore.json):
full Linux6466 passes/221 identical optional skips/zero failures; fresh native
81-table/28-sequence/four-point/11-blob import; held identity/explicit release;
controlled exit23 before output on a resumed already-adopted SQL identity, then
same-identity resume with109 unchanged catalog entries. Four HTTP endpoints and
four API/worker processes passed old/new workflows and17 cold hashes. Target is
off/held3, three PVCs retained, prior2/2/2 and nine tables restored. The same NFS
server stayed up while the source claim was unmounted. Separate-kernel large
writes/outages and broader profile certification are outside this completed plan.

Self-review: both tasks retain the spec boundaries; Task1 owns all five refusal
classes, Task2 supplies actual Linux/provider/current-image evidence. Source NFS
server-off is deliberately not claimed for the same-server RWX target recipe.
