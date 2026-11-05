# Selected provider fault matrix implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline. No new
> reviewer/subagents/worktree; preserve the four unrelated dirty paths.

**Goal:** Close available current managed-S3 PG/Qdrant/model fault evidence.
**Architecture:** Reuse scoped PG recovery authority and add explicit staged
Qdrant/model outages, with pure checks over admitted native observations.
**Tech Stack:** Existing Python/Kubernetes/Cilium/PG17/Qdrant1.19/RustFS recipes.
**Spec:** [Selected provider faults](../specs/2026-11-05-replica-provider-faults-design.md).

## Global constraints

Only fresh synthetic namespaces, approved existing Cilium and cached verified
images. No old data/receipts/closed loans reused as a new run. No raw secrets in
outputs. No queue/lease/clock/fence repair to make a test pass. Unknown mutations
are observed, never replayed. New global infrastructure changes require separate
platform authorization; ordinary own namespaced fault policies stay in scope.
ProductionOFF, certification null, no customer/physical qualification inferred.

## Review focus

- Namespace/provider selection must not admit an arbitrary service or role.
- A timeout without actual four-Pod denial/positive controls is not an outage.
- Changed publication, hidden missing work or terminal failure is not a retry.
- A new work ID, stale fence or wrong original cannot count as recovery.
- An uncertain create/POST/DELETE must not trigger another mutation.

## Task1: pure policy and evidence guards

Create `backend/test_scripts/replica_provider_faults.py` and
`backend/tests/test_replica_provider_faults.py`; update `SECURITY.md`.

Interfaces: `fault_policy(value, provider, operation) -> dict` accepts only the
literal `owned-managed-s3-dependencies-v1`, basic/distributed/S3, explicit run/
namespace UID and DNS-label releases, Qdrant/model and a32hex operation.
`assert_network(rows, expected_pods, provider)` requires exact twoAPI/twoWorker
UID/role set, liveness200/no restart, targetFalse and all other servicesTrue.
`assert_outage(before, current, previous_work_ids, works, provider) -> dict`
requires unchanged publication and exactly one new queued typed transient work.
`assert_recovered(before, current, previous_work_ids, works, queued,
original_sha256) -> dict` requires same work/newer fence+attempt, exactly one
new active generation and unchanged original hash. Publication/work shapes are
bounded and contain no arbitrary payload or credentials.

- [x] RED: missing module and scope/network/publication/retry/recovery negatives.
- [x] Implement the fixed scoped policy and pure validators; no native mutations.
- [x] GREEN focused tests, mandatory project checks and selective commit.

## Task2: staged native outage coordinator

Create `scripts/kubernetes/replica_provider_outage.py` and
`backend/tests/test_replica_provider_outage.py`. Reuse protected journal helpers,
`Kubernetes`, scoped authority, bounded HTTP and existing corpus checks. Add
fixed readonly SQL/model/connectivity observation source; no payload-driven SQL.
Expose `preflight/baseline/inject/submit/observe/heal/verify/final` with explicit
provider and protected operation journal. Record source fingerprint, policy UID/
spec, admitted Pod identities, old work IDs, publication and original SHA before
dispatch. Final requires both provider cases and PG child report with matching
current source/runtime/namespace binding. It issues no release certificate.

- [ ] RED: changed namespace/provider/Pod/corpus; missing or duplicate work;
  unknown ACK observes only; wrong policy UID blocks healing; partial history
  cannot produce final proof; model uncertainty cannot be released by a flag.
- [ ] Implement literal action dispatch and exact unknown-ACK observation.
- [ ] GREEN targeted Windows/Linux tests and mandatory project checks; commit.

## Task3: fresh current managed-S3 rehearsal and publication

Use separate protected `tests/tmp` fixture/journals, current65d runtime source
equivalence and pinned provider/model images. Prepare all objects/dry-runs first;
take fresh idle loans only after resource/disk budget and recovery inputs exist.
Bootstrap retained96931d1 source; upload nonempty corpus/copy independently to PC;
perform actual same-schema upgrade to65d and obtain scoped PG fault authority.
Run current PG partition/stale return and Qdrant/model cases through their fixed
stages. Retain failures and exact intents; do not count a partial component as
the whole matrix. Fix observed defects with RED/GREEN and rebuilt source/image
proof if runtime changes. Full final-source Linux CI, matching focused tests,
project checks, current image/source and actual data must all agree.

- [ ] Actual scoped upgrade/copy/current PG child and Qdrant/model checks pass.
- [ ] Close handles/policies/fixtures, retain data and return all original
  counts/runtime/PVC/nine-table hashes; no open capacity debt.
- [ ] Publish new scoped report, reconcile matrix/runbooks and commit after
  project checks. Keep customer/physical/trusted-release gates explicit.


Task1 verified:62 tests first failed with the missing module, then all62 passed
on Windows (75.14s), including namespace/role/negative-positive network controls,
publication loss, duplicate/missing work, typed retry and same-work/newer-fence
recovery refusal. Mandatory project checks passed (audit0/frontend756 passes+
one existing optional skip/Ruff0). This is only the pure builder/checker; no
fault policy has been applied and native/current-provider acceptance is pending.
