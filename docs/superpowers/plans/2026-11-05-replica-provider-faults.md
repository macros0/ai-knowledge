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

Create `scripts/kubernetes/replica_provider_outage.py`,
`scripts/kubernetes/replica_provider_native.py` and matching
`backend/tests/test_replica_provider_outage.py`/`test_replica_provider_native.py`.
Separate native scope/SQL/HTTP from stage/policy journal mechanics. Reuse protected journal helpers,
`Kubernetes`, scoped authority, bounded HTTP and existing corpus checks. Add
fixed readonly SQL/model/connectivity observation source; no payload-driven SQL.
Expose `preflight/baseline/inject/submit/observe/heal/verify/final` with explicit
provider and protected operation journal. Record source fingerprint, policy UID/
spec, admitted Pod identities, old work IDs, publication and original SHA before
dispatch. Final requires both provider cases and PG child report with matching
current source/runtime/namespace binding. It issues no release certificate.

- [x] RED: changed namespace/provider/Pod/corpus; missing or duplicate work;
  unknown ACK observes only; wrong policy UID blocks healing; partial history
  cannot produce final proof; model uncertainty cannot be released by a flag.
- [x] Implement literal action dispatch and exact unknown-ACK observation.
- [x] GREEN targeted Windows/Linux tests and mandatory project checks; commit.

## Task3: fresh current managed-S3 rehearsal and publication

Use separate protected `tests/tmp` fixture/journals, current65d runtime source
equivalence and pinned provider/model images. Prepare all objects/dry-runs first;
take fresh idle loans only after resource/disk budget and recovery inputs exist.
Bootstrap retained96931d1 source, upload a nonempty corpus, complete the existing
real scoped-credential coordinator, and copy independently to PC. Its verified
credential journal is the genuine prior source journal for ImageUpgrade; do not
invent bootstrap/upgrade provenance. Then perform actual same-schema upgrade
to65d and make a second independent copy with
current fingerprint/images, and obtain scoped PG fault authority. Pre-upgrade
copy remains retained; no recovery-point equality check is relaxed.
Run current PG partition/stale return and Qdrant/model cases through their fixed
stages. Retain failures and exact intents; do not count a partial component as
the whole matrix. Fix observed defects with RED/GREEN and rebuilt source/image
proof if runtime changes. Full final-source Linux CI, matching focused tests,
project checks, current image/source and actual data must all agree.

- [ ] Actual scoped upgrade/copy/current PG child and Qdrant/model checks pass.
  Scoped credentials, independent preimage copy (2 documents/12 encrypted
  objects), real same-schema upgrade to65d and current independent copy
  (3 documents/17 objects) are now verified on the corrected fresh fixture.
  Current PG partition is proven; stale return and Qdrant/model remain pending.
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

Task2 protocol/policy checkpoint:20 missing-module stage tests and14 selected
policy tests failed before implementation; the combined31 cases then passed.
The fixed13-stage history binds code/inputs and chains receipt hashes. Pending
actions only observe, even with apply=True. Policy create/lost-ACK adoption and
UID-preconditioned raw DELETE/absence observation are tested with actual call
counters. This earlier checkpoint did not yet contain native admission.

Task2 native adapter now provides the fixed CLI, source/backup/PG-child binding,
Pod/container/image and Node/PV/claim identities, readonly SQL and fixed Pod-local
HTTP intents. Unknown active controllers, changed runtime modes and aliased
receipts fail closed. Final logout requires apply and rechecks the last verified
publication/work/corpus/maintenance snapshot. Windows140 tests passed with
zero skips/failures/errors; project checks passed (audit0/frontend756+one prior
optional skip/Ruff0). A real read-only PostgreSQL component found SQLSTATE25001
from setting isolation after business-session guards; a new connection now sets
REPEATABLE READ, READ ONLY first. Actual observation then passed on22 synthetic
documents,52 bound resources/oneNode/threePVs and positive connectivity on all
four API/worker Pods. Targeted Linux CI then passed the same140 cases,0 skips/
failures/errors,exit0/noOOM with source unchanged. Both new capacity operations
returned original2/2/2/runtime/PVC/nine canonical tables. Primary verification
waited read-only for background work to drain; no scaling was repeated.
[Component evidence](../reports/2026-11-05-replica-provider-native-component-ci.json)
is explicitly not a new provider fault acceptance or full backend CI.
The Task3 fresh current-provider rehearsal remains in progress.

The initial137-case Windows/Linux source checkpoint is retained separately in
the final component report. Three additional RED cases exposed final-target
drift; they pass only after the final snapshot comparison, alongside a valid
unchanged-snapshot control. The follow-up CI transfers only the two changed
files and uses new capacity operations; no closed journal is reopened.

Task3 checker correction: the actual bulk-export API uses positive SQL Integer
job IDs, not32hex UUIDs. Both native admission and Pod-local HTTP now require a
bounded integer and construct its numeric download URL. Real local HTTP checks
cover original/download, BM25 and ZIP bytes; boolean/string/zero/negative/overflow
IDs are refused. Seven expected RED failures exposed the mismatch; the first
fix left one URL-concatenation failure, then all153 focused Windows cases passed
with0 skips/failures/errors. Required project checks passed (audit0/frontend756+
one existing optional skip/Ruff0). This changes the checker only; no runtime
image change or Linux/current-provider acceptance is inferred from these tests.

Task3 uncertain PG-return branch: the real old process returned and completed
other jobs while the original model slot remained reserved. A heartbeat
OperationalError can exit before the LostOwnership metrics branch, so waiting
only for a lease-loss counter incorrectly prevented existing physical recovery.
The observer now requires a captured-original-owner/fence SQL publication probe
with unconditional rollback, unchanged publication and the same returned
process. Both API admission probes must still report busy; exact slot identity
and provider counters remain unchanged. Zero lease-loss delta is reported as
observed, never fabricated. Five expected RED cases preceded the correction;
68 Windows PG/WorkerLoss/authority cases pass with0 skips/failures/errors.
The same in-progress fault uses an explicit observer-code handoff with parent
driver/source hashes; no reinjection or rebinding of the old driver. This is the
uncertain-call branch: physical writer/provider stop and audited exact-token
reconciliation are still mandatory, not a confirmed-reply recovery claim.

That physical recovery and functional PG verification subsequently passed with
three prior documents preserved and the same interrupted work published at
fence3. Consumer admission exposed a separate producer inconsistency: the
physical branch omitted normalized final fields emitted by the confirmed
branch. Six expected RED tests reproduce this. The physical producer now
re-observes policy absence, exact normal worker UID/spec/count, actual runtime
identity and quiescent slots before emitting canonical fields. All74 focused
PG/WorkerLoss/authority cases pass without skips/errors/failures. Keep the first
report and generate a separate newly verified report; do not manually patch an
old receipt or weaken the consumer. Current Qdrant/model acceptance is pending.
