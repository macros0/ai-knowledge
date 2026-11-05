# Native real-OIDC frontend acceptance — Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline. No new
> reviewer/subagents/worktree; preserve the four unrelated dirty files.

**Goal:** Run the remaining real Keycloak/Gateway/CSRF F2 case through the common runner.
**Architecture:** A separate fixed OIDC recipe/capsule extends the existing
durable 12-stage parent protocol. Share guarded infrastructure primitives and
business checks while keeping simulation's constructor/admission defaults.
**Tech Stack:** Python stdlib TLS/native SSH, existing protected journals,
Gateway API1.6.2/kgateway2.4.5/Keycloak26.8.0, unchanged application images.
**Spec:** [Native OIDC design](../specs/2026-11-05-frontend-oidc-native-design.md).

## Global constraints

Use only owned synthetic fixtures. Existing Gateway package is already approved
and installed; no repeat approval, CRD/RBAC/operator/NAS/node/firewall change.
Controller currently0; start it only within a fresh journaled resource budget.
Keep all old closed reports/journals frozen and preserve all existing PVC/data.
Passwords/tokens/cookies/DSNs never enter parent/public transport or Git.
Production scheduling stays off, certification stays null, no new trust issuer.

## Review focus

TLS stream and ordinary requests must use the same pinned socket/logical host,
never ambient proxies or arbitrary redirects. Helper routes must select exact
own frontend UIDs, with no foreign endpoint or exposed backend/internal route.
Gateway/IdP/config drift must stop before credentials or DELETE. An uncertain
credential POST/label patch/DELETE must be observed and never resubmitted.
Real CSRF/SSO status requires the entire actual native recipe, not an input flag.

## Tasks

- [x] **TLS business and stream actions.** Extend
  `backend/test_scripts/replica_frontend_oidc_http.py` with a bounded response
  context; create `replica_frontend_oidc_business.py` using existing business
  methods with overridden HTTPS requests/session/stream/logout. Add real loopback
  TLS tests in `backend/tests/test_replica_frontend_oidc_business.py` and extend
  the OIDC component tests. RED first: first-frame timing/unbuffered stream,
  exact slot headers/CSRF/secure cookies, all three routes/SHA/ZIP, interrupted
  stream/no replay and refusal of caller Cookie/Host/slot overrides. GREEN new
  tests plus existing HTTP/OIDC tests. Update SECURITY before committing.
- [x] **Native OIDC admission and lifecycle.** Create
  `replica_frontend_oidc_native.py`, tests `test_replica_frontend_oidc_native.py`.
  Add explicit recipe/hooks to the base coordinator and a separately admitted
  gateway-forward method to `replica_frontend_process.py` with regression tests.
  Bind access/platform/application namespaces, operator/class/Gateway/proxy/IdP,
  TLS/realm Secret hashes, routes/slot endpoints and source images; verify actual
  backend OIDC/TLS/fresh corpus settings. Test foreign provider/namespace/image/
  endpoint/secret/config refusal before HTTP, lost ACK without replay, sources+
  model/PID loss gate, one tested label patch and delete, replacement read checks,
  logout and exact handle closure. Keep the simulation recipe strict. Stage
  source modules needed by the child as private SHA-verified immutable files.
- [x] **Common runner integration.** Add only declared OIDC modules and
  nonsecret safe fields in `replica_frontend_adapter.py`; select coordinator by
  exact recipe. Reuse parent stage/journal flags. Add adapter/CLI tests rejecting
  missing/mismatched capsule/recipe/UID metadata, passwords in payload and mixed
  R5/offline controls. Clear selected real CSRF/Gateway checks only after all12
  actual OIDC stages with full receipt evidence; keep simulation pending and
  profile certification independent. Run mandatory project checks before commit.
- [ ] **Current-source verification and fresh real case.** Run required Windows
  cohorts and immutable current-source Linux CI, then build a fresh owned
  Gateway/Keycloak/frontend-split fixture with new keys/claims/realm/identity.
  Use the pinned approved controller/class and image digests. Run all12 common
  stages, retaining unsuccessful observations and same-intent recovery. Close
  clients/forwards/fixture/controller, retain PVCs/diagnostics, return all loans
  with original runtime/PVC/nine-table hashes. Publish scoped reports and update
  matrix/runbooks without certifying physical/customer profiles or signing old
  reports. Project checks before the final selective commit.

## Shared interfaces

Task1 supplies OIDC TLS/business methods and the child's bounded stream reader;
Task2 must bind its origins/slot membership before those methods run. Task2's
fixed stages/receipts are Task3's only success source. Task3's literal module
list must include Task1/2 dependencies in import order. Task4 admits a new fixture
matching all those exact inputs, never a corrected/reopened old simulation run.

## Task1 verification

Initial14 RED cases covered the missing response context/business transport;
54-case regression cohort then passed. Two additional RED cases proved that
unlimited `read`/`readline` bypassed a caller-only limit; the response wrapper now
enforces its own cumulative1MiB/45s budget. Two RED protocol cases distinguished
malformed frames from transport interruption. A final RED progress-I/O case
exposed the context manager catching consumer errors as network failures;
network exception handling is now limited to actual connect/header/body calls.
The final59-case Windows cohort passed with zero skips/failures/errors, including
the prior HTTP/OIDC cases. Mandatory project checks passed (audit0,
frontend756 passes/one existing optional skip, Ruff0). No live workload or
resource loan was used in this task. Current-source Linux/full native verification
remains Task4; the earlier99-case Linux report stays bound to a3af1ac.

## Task2 verification

The native OIDC coordinator admits all three namespaces and the observed route
graph before Secret reads, validates the exact synthetic realm/probe import and
binds backend OIDC settings/client-secret hash before SQL. Both Gateway sockets,
anonymous redirect/hidden-route/TLS controls, verified TLS child loading, strict
sources/model/PID loss gate, UID-preconditioned deletion and a UID/resourceVersion
label patch are covered. Lost DELETE and patch acknowledgements are observed
without reissuing either mutation. All application/access/platform Node UIDs are
bound; helper slots admit only the recorded victim/replacement transition.

The combined Windows native/process/HTTP/OIDC/business cohort passed165 tests,
with one existing Linux pidfd skip (299.08s). Separate real Linux child-loader
tests collect three platform skips on Windows and remain an explicit Task4 Linux
gate. The first combined run's single failure was a test fixture assuming every
HTTPRoute had backendRefs; redirect routes correctly have none. The corrected
combined run passed. Common runner/capsule completion attestation is still Task3;
the native implementation does not clear release gates by itself. No VM resource
change, capacity loan or source transfer was used in Task2.

## Task3 verification

The separate literal OIDC capsule now contains the original five modules plus
OIDC wire/business/native modules in dependency order. Its bootstrap uses Python
isolated mode and materializes only the three declared child modules, requiring
exact source equality on observation. The transport adds only the seven typed
nonsecret OIDC identity fields. After completion it requests a read-only native
all-stage proof and compares the twelve actual receipt hashes, execution/code/
binding identities and current capsule to the parent's completed journal.

The common CLI requires that full proof to clear selected production_csrf and
gateway_sso checks; the recipe flag, missing proof, partial stages and mismatched
capsule/receipt/identity keep them open. Global qualification stays independent.
The common runner fingerprint includes all three OIDC sources. Adapter/native
attestation/runner/CLI tests passed67 cases (87.13s), and the separate common
acceptance safety/report cohort passed47 cases (59.08s). Mandatory project
checks passed (audit0, frontend756 passes/one existing skip, Ruff0). Actual
current-source Linux/full Gateway/Keycloak evidence remains Task4.

Task4 live correction: the first full Linux run for e5e7b39 passed6991 cases
(6770 passes/221 unchanged optional skips, coverage87.7057%), including all three
actual isolated TLS-child tests. A subsequent live fixture startup exposed that
kubectl does not read `--patch-file=-` from stdin. The controller patch was
rejected locally before application. An offline real-kubectl probe reproduced
that rejection and proved inline `-p` JSON works without API mutations. Native
replacement now uses that supported argument; a RED producer regression passed
after the correction, and a new Linux test executes the producer's captured
arguments with real `kubectl patch --local`. Windows correction cohort38 passes/
one Linux-only skip; required project checks passed. The e5e7b39 full-CI record
stays frozen; the corrected source requires its own full Linux run before the
actual OIDC producer is admitted.

Task4 ListenerSet correction: the actual v1 API reports reference resolution on
each named listener, with Accepted/Programmed at set level. Admission now checks
both levels, matching the listener name and generation, requiring HTTPRoute
support and Conflicted=False. Seven RED cases covered the real status shape and
missing/unresolved/conflicting/stale/foreign/unsupported listeners; the corrected
native/lifecycle/attestation cohort passed70 cases. The prepared85ff81e CI packet
was not transferred or started; the next full run must include this correction.

Task4 live verification on5d46ce8: full Linux CI collected/executed7000 cases,
6779 passed/221 unchanged optional skips,0 failures/errors,87.6989% coverage;
all prior/Windows IDs and exact tracked source hashes were retained. The first
real session attempt exposed a fixture-only ingress mismatch: actual Envoy
ports80/443 versus8080/8443. Both old forwards were closed and empty corpus/
sessions/model confirmed before a UID/spec-tested own NetworkPolicy correction.
Verified HTTPS discovery then returned200 with the exact issuer.

A separate execution passed preflight, real SSO/CSRF/session, upload, publish,
export, streams and the actual gated UID deletion. Its read-only loss observation
exposed API serialization of the now-empty first EndpointSlice as `null`.
Two regression tests reproduced that failure; admission now accepts null only
with an explicitly empty expected Pod set, retaining all ownership/live endpoint
checks. The partial execution and data remain evidence of an incomplete case;
it must not be rebound to a changed source capsule or counted as all12 passed.
Corrected-source verification and a fresh complete case remain required.
