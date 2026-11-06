# Owned frontend OIDC/CSRF probe — Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline; no new
> reviewers/subagents/worktree. Preserve the four unrelated user changes.

**Goal:** Supply bounded HTTP OIDC/CSRF actions for the remaining F2 producer.
**Architecture:** Separate stdlib TLS transport and explicit fixed OIDC flow;
reuse the existing private file primitive. Keep namespace admission, process
ownership, active frontend loss and qualification in a later native integration.
**Tech Stack:** Python stdlib; real TLS fixtures with test-only cryptography.
**Spec:** [Design](../specs/2026-11-05-frontend-oidc-probe-design.md).

## Global constraints

No Kubernetes changes or new live fixture before the pending Gateway approval.
No customer endpoints, ambient proxies, unverified TLS, root `.env`, credentials
in public receipts, automatic mutation retries or weakened simulation admission.
Production schedule stays off; all certification remains null.

## Review focus

Credentials must not leave the two pinned origins; untrusted/incorrect TLS must
fail before HTTP. Callback state/issuer/client mismatch must not create a valid
receipt. A CSRF denial must not be inferred from an anonymous session or an
unrelated 403. Loss of transport cannot permit another credential/logout POST.
Cookie persistence and later logout checks must not leak or accept linked files.

## Tasks

- [x] Add real loopback HTTPS wire tests in
  `backend/tests/test_replica_frontend_oidc_http.py`; watch RED for the missing
  component. Cover a full OIDC session/CSRF/logout flow, cookie flags/private
  persistence, re-opened terminal result without replay, TLS negative controls,
  wrong origins/forms/state/client/issuer, overlarge responses and mutations
  whose ACK is lost. Verify destination counters as well as returned failures.
- [x] Implement `backend/test_scripts/replica_frontend_oidc_http.py` to pass those
  tests. Separate fixed logical origins from the loopback socket, manually bound
  redirects, persist intents/results, and expose only small nonsecret outcomes.
  Run new tests plus existing frontend HTTP/private-journal and home access tests.
  Update `SECURITY.md` and this plan with precise component limits.
- [x] Run mandatory project checks and focused verification before a selective
  commit. Record RED→GREEN and artifact hashes in the persistent ledger.
  Do not mark the real CSRF/Gateway/SSO gate passed: actual native integration,
  current-source Linux verification and the fresh real-Keycloak/Gateway case
  follow after platform approval. Do not mint a trust issuer or sign old reports.

## Current evidence

The missing module produced 26 RED test failures. The implemented wire client
first passed 21 cases; five directory assertions incorrectly counted the shared
conftest's pre-existing `test.db` as a new probe file. Before/after directory
equality corrected that fixture assumption. The current Windows cohort passed
99/99: 26 new TLS/OIDC cases and 73 existing HTTP/private-journal/home access
cases, zero skips/failures/errors. Focused Ruff passed. The same
[99-case Linux cohort](../reports/2026-11-05-frontend-oidc-http-ci.json) also passed
with zero skips/failures/errors, exactly equal Windows case IDs, no OOM and all
2477 source hashes unchanged. The container used network-none, read-only root,
non-root1000, cap-drop ALL and2GiB/2CPU. Its separate CI-only capacity loan was
returned with original2/2/2/runtime/PVC/nine tables verified. Mandatory project
checks passed: audit0, frontend756 passes/one previous optional skip, Ruff0.

The user subsequently approved the Gateway package. Its
[native admission](../reports/2026-11-05-replica-gateway-platform.json) is closed:
27 explicit objects/18 established CRDs, exact controller image, UID1000,
read-only root/no effective capabilities, no restarts, Accepted GatewayClass.
Only the existing zero-valued startup probe field was omitted by API encoding;
the same intent/UID was observed without another apply. Controller is off,
its three application-client role counts were restored to2/2/2 and runtime/PVC/
nine canonical tables rechecked. The controller generated its own namespace's
OAuth2 HMAC Secret; only metadata is public. Actual Gateway/Keycloak data-plane
and frontend loss acceptance still require the future admitted native fixture.
