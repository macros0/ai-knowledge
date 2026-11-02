# Local training backup alerts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver authenticated backup failure/age alerts to the user-approved local PC receiver and prove meaningful-change deduplication and recovery.

**Architecture:** Existing format3 cold health remains the source of backup state. A separate operator sender durably records pending events before HTTP, retries the same event after uncertainty, and records only matching receiver acknowledgements. A loopback-only receiver authenticates bounded events and commits an idempotent SQLite inbox before acknowledging.

**Tech Stack:** Existing Python3.12, Pydantic, filelock, httpx, HMAC-SHA256 and stdlib HTTP/SQLite; no added dependencies or services on the training VM.

**Spec:** [Approved O2 operations requirements](2026-10-31-kubernetes-replicas-operations.md), [design](../specs/2026-10-31-kubernetes-replicas-storage-design.md). User selected a protected key directory on this PC, confirmed there is no production installation, and explicitly approved a local alert receiver.

## Global Constraints

- Production backup scheduling stays OFF; no CronJob, automation or external recipient is created.
- Use only the real synthetic training archives and separate private PC configuration/state.
- Keep primary archive keys intact; notification signing uses a different32-byte key in the protected vault.
- Bind receiver only to127.0.0.1 on an unused port above15000; never kill an existing listener.
- No keys, credentials, raw paths/provider errors, document content or arbitrary exception values enter notification payloads/reports.
- Preserve source/target PVCs and original4 dirty user files; no working corpus/provider mutations.
- A local delivery drill is not production monitoring/SLA qualification.

## Review Focus

1. Receiver commits but the ACK is lost: sender retries the identical durable event; inbox remains one row.
2. Journal replacement/fsync has an uncertain outcome: an intent marker blocks further actions; no false delivered status.
3. A newly failing health check must not hide behind an older healthy archive/notification state.
4. Wrong signing key, changed body, reused ID with different body or expired signature is rejected without raw-input logging.
5. Increasing age alone must not produce repeated unchanged stale alerts; recovery after an alert produces one event.

## Task 1: Bounded event protocol and loopback receiver

Create `scripts/kubernetes/backup_alert_protocol.py` for strict event/ack models,
canonical bytes, bounded public health projection and domain-separated HMAC
timestamp signatures. Allowed health codes come only from the current cold-health
contract. Signature wall time is distinct from an explicitly simulated health
observation time. Receiver validates signatures before persistence, accepts only
the fixed POST path and <=8KiB bodies, and never logs request bodies/headers.

Create `scripts/kubernetes/replica_backup_alert_receiver.py` with a loopback-only
CLI, separate32-byte key-file input and protected SQLite inbox. Identical event
ID/body replay returns its persisted receipt; a changed body with the same ID
returns conflict. SQLite commit precedes ACK; GET health returns only readiness
and an event count. Fixed refusal responses contain no raw input.

- [ ] Add `backend/tests/test_replica_backup_alert_protocol.py` and `test_replica_backup_alert_receiver.py`: strict fields/types/bounds, wrong key/body/time/path, duplicate/conflicting IDs, actual HTTP ACK and committed inbox after a simulated lost response.
- [ ] RED: run both cohorts with a distinct basetemp; expect missing modules/functions.
- [ ] Implement protocol/receiver with no new dependencies; GREEN same cohorts and Ruff.

## Task 2: Durable change-only sender and explicit health CLI

Create `scripts/kubernetes/replica_backup_alerts.py`. Expose an explicit CLI for
bounded protected configuration (archive/key binding, local receiver/signing key,
delivery journal, optional backup-attempt journal) and a manual `--apply` action.
Dry-run assesses/project state but opens no network connection or delivery journal.
The existing health CLI and attempt-journal format remain unchanged.

Reuse the proven protected journal path/write-intent/fsync publisher from
`app.deployment.replica_backup_health`; validate the new delivery model at both
read/write boundaries without changing old attempt canonical bytes. Retain one
file lock across transition selection, pending persistence, POST and matching ACK
persistence. A pending event always finishes/reconciles first; never replace it
with a new health observation. Never automatically retry uncertain journal writes.

First healthy/pending observation is quiet. Initial alert or a changed alert
signature emits once; an alert-to-healthy transition emits one recovery. Ignore
clock-age increments in the change signature. Retry lost HTTP ACKs with the same
event ID/body and a new transport timestamp. Require matching event ID/body SHA
in the ACK; no redirect/proxy, wrong receipt or unknown response means undelivered.

- [ ] Add `backend/tests/test_replica_backup_alerts.py` and `test_replica_backup_alerts_cli.py`: quiet healthy/unchanged stale, failure and recovery transitions, current attempt failure, retained pending/ACK-loss retry, mismatched ACK, foreign scope, write-intent refusal, concurrent sender refusal, dry-run and safe CLI errors.
- [ ] RED missing sender; implement and GREEN alongside unchanged health tests.
- [ ] Run project check and update SECURITY/runbook with actual trust boundaries.

## Task 3: Real local drill and bounded evidence

- [ ] Create a separate notification signing key in the user-approved protected vault and an isolated private inbox/config/journal directory. Record ACLs, source/key identities and exact Hidden background receiver PID/start/argv; keys never enter argv or logs.
- [ ] Start one receiver on an unused loopback port; observe the same process before any recovery/restart.
- [ ] Cold-authenticate the real rpl04 archive using the separate vault key. A real-time healthy observation stays quiet; explicit observed age86401s sends one stale alert, a repeat remains quiet. Record clock simulation clearly.
- [ ] Use a disposable encrypted archive shadow or isolated attempt-journal failure to prove a real verification/attempt error notification without modifying the original archive/latest-attempt file. Restore healthy observation and prove one recovery.
- [ ] Verify inbox event IDs/content hashes, matching sender ACKs, no duplicate unchanged events, wrong-key rejection and original archive/key/source PVC preservation. Retain all private data locally; report only bounded IDs/codes/counts/SHA.
- [ ] Update the parent O2 plan/matrix with training-only scope; production scheduling and external monitoring remain disabled/unqualified.
- [ ] Commit implementation, tests and sanitized evidence; do not claim whole O2/O3 completion.
