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
- [ ] **Native OIDC admission and lifecycle.** Create
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
- [ ] **Common runner integration.** Add only declared OIDC modules and
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
