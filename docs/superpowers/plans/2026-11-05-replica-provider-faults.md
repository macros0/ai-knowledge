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

Progress reporting correction (05.11): the earlier85% estimate had no weighted
calculation and must not be used as a completion metric. Track the remaining
available lab deliverables explicitly, without adding customer infrastructure
qualification to the lab's denominator or treating those gates as passed:

- [x] D1: assemble the actual PG/Qdrant/model branches with a fresh read-only
  state check and source provenance; preserve the original10/14 parent history.
- [x] D2: full Linux backend CI of the final committed source, including the
  added recovery/matrix tests, complete collection and unchanged optional skips.
- [x] D3: stop the current fixture, close owned transports and return both
  original capacity loans, verifying counts/runtime/PVC/nine-table hashes.
- [ ] D4 (O2): execute the selected NAS-target NFSv4.0 relocation recipe.
- [ ] D5 (O3): reconcile the overall plan, support matrix and operator runbooks
  against actual evidence and retain the explicit customer/production gates.

These are the existing remaining deliverables, not a new feature programme.
Closed fault trials are not repeated just to obtain a simpler all-green history.

[D1 native matrix](../reports/2026-11-05-replica-provider-matrix.json) verifies
the exact PG physical/stale-token report, Qdrant same-work retry at fence2 and
parent-bound model physical recovery at fence4. Fresh read-only checks confirm
four preserved documents, epoch6 released,2/2/2, no CNP/model owner/running work
and unchanged resource/platform snapshots. Both historical producer hashes are
reconstructed from their unchanged isolated CI manifests; the only WorkerLoss
difference affects functional verification methods, with all other AST nodes
unchanged. The parent remains10/14, qualification=false/certification=null.
Matrix Windows29/29 cases and required project checks passed. D2/D3 are closed below.

[D2/D3 closure](../reports/2026-11-05-replica-provider-matrix-closure.json)
supersedes that pending checkpoint: immutable6b41f85 full Linux CI passed7317
cases=7096pass+221unchanged optional skips,0failures/errors,87.70% coverage.
Collection/prior IDs/Windows cohorts/added modules and all2522 source files were
verified. Fixture is off/held7, Pod+CRI0, three PVC retained; five handles, SSH and
all14 PG-supervisor child handles are gone. Both original loans returned2/2/2
with original runtime/PVCs/nine materialized tables, no data mutations.

Return uncovered expired three-day TLS leaves in both borrowed stacks. Six
mutable Secrets received locally signed same-CA/same-public-key/SAN leaf renewals;
CA roots, private keys, credentials, images and controller specs were retained.
Strict hostname/CA/new-DER probes passed from API on all six services. The
initial model-stub probe failed DNS due its existing isolation; no policy was
opened. API/worker/frontend scaling continued from acknowledged intents, without
replaying the timed-out first return. The later bbced15 NAS component is outside
the6b41f85 full CI scope; D4/D5 remain open.

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

- [x] Actual scoped upgrade/copy/current PG child and Qdrant/model checks pass.
  Scoped credentials, independent preimage copy (2 documents/12 encrypted
  objects), real same-schema upgrade to65d and current independent copy
  (3 documents/17 objects) are now verified on the corrected fresh fixture.
  Current PG, Qdrant retry and parent-bound physical model recovery are proven;
  the original automatic parent remains10/14 without fabricated completion.
- [x] Close handles/policies/fixtures, retain data and return all original
  counts/runtime/PVC/nine-table hashes; no open capacity debt.
- [x] Publish new scoped report, reconcile matrix/runbooks and commit after
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

### Task3 correction: claim work before Qdrant readiness is lost

The first native execution reached4/13: valid Qdrant denial and one accepted
regeneration, but work stayed queued/attempts0/fence0. It is not failed-attempt
acceptance. Exact update cancellation and UID-bound policy removal succeeded;
final observation confirmed original publication/corpus unchanged, work cancelled
without an attempt and CNP0. Core history remains4/13/unqualified.

**Files:** create `scripts/kubernetes/replica_provider_gate.py` and
`scripts/kubernetes/replica_provider_gate_native.py` with their focused tests
`backend/tests/test_replica_provider_gate.py`,
`backend/tests/test_replica_provider_gate_native.py` and
`backend/tests/test_replica_provider_gate_admission.py`; modify
`scripts/kubernetes/replica_provider_outage.py`,
`scripts/kubernetes/replica_provider_native.py`, corresponding tests and
`SECURITY.md`. Keep this plan's existing ledger; execute inline, no new agents.

**Interfaces:** fixed `GateActions.start/observe/release` use a180s scoped API
SQL transaction with rollback and exact native PID/start-time/Pod/container plus
backend PID/start-time binding. No arbitrary module/SQL/command inputs. A new
`qdrant.gate` precedes `qdrant.submit` and `qdrant.inject`; inject proves the
selected work has been claimed and blocked by that gate, then releases it after
actual four-Pod denial. Keep explicit apply and observe-only unknown outcomes.
Source SQL remains READ ONLY; expose database time/claim wait facts for real
lease checks, without modifying lease/fence/queue/clock. Core becomes14 stages;
old13-stage journals/reports remain immutable and cannot resume on changed code.

- [x] RED gate scope/unknown-start/expiry/changed-native-process/no-owned-waiter,
  stale work lease and no-claim refusal; no model release or hidden readiness
  override. Expected: new gate module/protocol assertions fail before implementation.
- [x] Implement the fixed gate actor/controller and ordered protocol. Gate rollback
  after explicit release must be proved; expired/EOF cannot stand in for it.
  Expected: focused tests pass, all negative controls still refuse.
- [x] Validate targeted Windows/Linux source and required project checks. Use new
  CI/capacity intents if needed, never reopen old operations. Commit scoped code.
- [ ] Prepare a fresh private manifest and export for the post-image PG document,
  preserving source baseline/actual PG report/current independent copy. Run the
  new14-stage native history. If model uncertainty is encountered, retain it and
  implement the already required exact-token physical recovery path; no flag may
  clear a slot. Complete final-source full Linux CI and the original closure/
  report/matrix tasks above. Keep production/provider/physical gates explicit.

Gate checkpoint:62 pure/protocol RED->GREEN; readiness/native-boundary12 initial
RED cases then74 gate/native-boundary/protocol cases passed. A real idle native
component `f6e3bda829354bd0a5557013d5427a6b` then acquired/released the own row
gate, verified rollback and absence of its parent/SQL backend, with exact corpus,
resource and model-slot hashes unchanged. It created no policy or document and
is explicitly not an active-work outage acceptance. Seven additional admission/
lease RED cases preceded integration; the combined202 Windows provider/gate
cases pass without skips/failures/errors. Native blocked-worker, controller-loss,
Linux CI and final live14-stage evidence remain separate gates.

Follow-up [gate component evidence](../reports/2026-11-05-replica-provider-active-gate-ci.json):
278 focused tests passed on Windows and Linux with identical case IDs, no skips,
failures or errors, exit0/noOOM and unchanged Linux sources. Both real idle gate
components closed: explicit release proved rollback and backend/process exit;
controller loss independently proved EOF cleanup without fabricating a release
receipt. Corpus/resource/slot hashes stayed unchanged. The CI-only capacity loan
returned2/2/2, the same PVC/runtime/nine canonical tables and four provider
processes. Only the owned frontend transport was refreshed afterward. Original
two matrix capacity loans remain active until complete trial closure. A fresh
14-stage history and final full backend CI are still required; the old4/13
unqualified history is retained. No provider qualification or production change.

### Task3 correction: fit full guards inside the unchanged fault window

The new14-stage attempt reached4/14 before gate release failed. Actual gate
backend start12:25:19Z and failure observation12:29:10Z exceed180s; selected
work completed once after expiry and cannot prove a failed Qdrant attempt.
Its exact CNP was removed; native parent/exec and SQL backend are gone, work
and model slots quiescent. Core history remains unchanged/unqualified. The
failed logout POST was then observed using the corrected real anonymous-user
plus protected-download401 contract, without reposting.

Ruling: batch fresh image-input/deployment reads and run every independent
runtime identity probe with bounded concurrency4, retaining all integrity
checks and exceptions. Do not extend timeouts, cache live state or relax guards.
Read-only component measurements retain the same complete binding: bind2.42s,
full guard9.70s (previous sample22.13s). These are not new fault acceptance.

Files: image-upgrade input guards, runtime identity probe, provider native HTTP
observer and corresponding tests. Eleven snapshot RED cases preceded batching;
an actual `cm/secret` journal-alias RED case preceded canonical lookup.
Anonymous logout and401-me with retained protected access reproduced RED before
HTTP correction. Five concurrency cases failed before implementation. Windows
changed suites:38 image/snapshot,63 native HTTP and5 concurrency cases pass.
Next: combined Windows/Linux checks, required project checks, scoped commit and
a new native history. Never resume the expired trial on changed source.

[Guard correction evidence](../reports/2026-11-05-replica-provider-batched-guard-ci.json):
all350 combined cases passed on both Windows and Linux with identical IDs,
0 skips/failures/errors, noOOM and unchanged source. Required project checks
passed. The new CI-only loan returned2/2/2/runtime/PVC/nine canonical tables and
all four provider processes unchanged; only the owned frontend transport was
refreshed. Both older matrix loans still await final matrix closure. The expired
trial remains closed-unqualified; the prepared next trial has not yet started.

### Task3 actual model quarantine and physical recovery

The next9ef5... history passed Qdrant gate/submit/deny/observe/heal/verify: the
same work recovered atattempt2/fence2 with original SHA/search/frozen ZIP intact.
It reached10/14 after one model-denied regeneration. The work remained running
inside model-budget waits; an unknown call retains its reservation, even past
DB-clock expiry. Both APIs independently refused new admission, model counters
remained5started/5finished/0active and publication stayed unchanged. Preserve
this expected quarantine branch; the old automatic queued/released-slot observer
does not qualify it. Keep the parent history unchanged at10/14.

Implement `replica_model_recovery.py` as a separate, source-bound producer for
the existing independently backed exact-token physical recovery. Admit only the
original history/work/namespace/policy and live slot; reobserve both API busy
checks. Reuse the audited worker-loss stop/reconcile/release/start primitives,
with truthful model fault reporting and no invented crash receipt. Minimal
verification-client/authentication/summary hooks preserve old worker-loss
behavior. Preserve the parent's synthetic cookie only in protected PC storage
before stopping APIs; verify continuity and old-cookie revocation after return.
Lost logout ACKs use observation only. No slot, queue or clock edits outside the
existing audited reconciliation API are allowed.

Windows118 and Linux118 recovery/worker/PG/authority cases passed identically,
0skips/failures/errors; source unchanged/noOOM. Required project checks passed.
This bounded CI used1GiB with a pre-start2GiB host floor (1GiB reserve), actual
MemAvailable2435624960B; no capacity loan was needed. Native physical recovery,
final source full backend CI and final matrix closure are still pending.

[Model physical recovery](../reports/2026-11-05-replica-model-quarantine-recovery.json)
now passed on the retained actual outage: exact slot11 was reconciled only after
all writers/provider were physically absent, with one audit under epoch6. The
same accepted work recovered from captured fence3 to4; all four documents,
original SHA/search/frozen ZIP and parent-session continuity survived. One logout
revoked the original cookie. CNP0 and2/2/2 were verified. The first verify command
refused a relative journal path in the report guard before HTTP/logout; an
absolute journal path completed the unchanged code/operation. Keep this CLI
path limitation documented; no evidence was rewritten to bypass it.

The historical parent remains10/14, rather than a false14/14 success. Final
provider-matrix evidence must explicitly combine its completed Qdrant receipts
with this exact parent-bound quarantine/recovery child. Full final-source backend
CI, fixture shutdown, handle closure and both original matrix capacity returns
remain required before completing Task3. Do not repeat closed native faults
merely to hide the expected quarantine branch or discard their history.
