# Frontend-split application acceptance — Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline. No new reviewer
> or subagent; preserve the existing worktree and four unrelated user changes.

**Goal:** Verify the selected two-frontend/one-combined-backend recipe, including
network boundaries, shared sessions, uploads, streaming and frontend loss.

**Architecture:** Keep compact unchanged. Give frontend-split explicit role
NetworkPolicies; frontend reaches only backend and DNS. Use a fresh synthetic
namespace with current immutable images and independently selected TLS PG/Q
services. Pin namespace/Pod/native identities; journal every mutation and never
replay an uncertain upload or frontend-loss action.

**Tech Stack:** Existing Helm chart, Kubernetes/Cilium on VM103, Next.js/FastAPI,
simulation auth, owned model stub, PostgreSQL17 and Qdrant1.19.

**Spec:** [F1/F2 plan](2026-10-31-kubernetes-replicas-frontend.md) and
[approved design](../specs/2026-10-31-kubernetes-replicas-storage-design.md).

## Global constraints

Only synthetic data, namespace resources and existing storage class. No new
operator/CRD, host firewall/NAS/VM changes, foreign services or source claims.
Read-only admission requires4GiB available RAM and disk10percent+PVC/scratch.
Any idle-role loan needs a new journal and original runtime/PVC/nine-table checks
before and after. Retain claims and failed attempts. Production scheduleOFF and
certificationnull; direct Pod forwards do not qualify customer Gateway/IdP.

## Tasks

- [x] Add actual rendered-policy regression tests that reject frontend storage
  or model access, broad same-release grants and external-egress inheritance;
  preserve compact policy bytes. Watch RED, implement role policies, then run
  frontend-split/Kubernetes/distributed chart tests with Helm.
- [x] Add bounded frontend-split inventory/probe support to the acceptance runner.
  Assert exactly one combined backend, two same-build frontend Pods, correct
  Service membership, diagnostic-only frontend Secret projection and no shared
  PVC. Wrong UID/release/role/template must fail before HTTP or mutation. Keep
  real Gateway/SSO requirements pending when running local simulation.
- [ ] Prepare/dry-run a fresh TLS/scoped-provider fixture with current images.
  Observe actual two frontend storage denials with backend positive controls.
  Check shared session, internal-path refusal, CSRF, synthetic upload/read/search,
  SSE framing/headers and a bounded interrupted stream. Normally delete only the
  selected owned frontend Pod, verify surviving requests/session and no automatic
  duplicate generation, then verify the replacement. Stop all new processes,
  retain claims, return every loan, publish explicit receipts and run project
  checks before committing.

## Ruling

F2 currently renders the compact same-release allowlist, granting frontend direct
PG/Q connectivity. Fix the selected frontend-split role boundary; do not redefine
the existing compact network contract. The original F2 design already specifies
frontend-to-backend diagnostics transport. Continue autonomously under that plan;
simulation and loopback forwards remain narrower than customer Gateway/SSO proof.

## Verified bounded checkpoint

The selected native simulation case is complete and closed; see
[report](../reports/2026-11-04-replica-frontend-split.json). Actual application
stream framing is NDJSON, not SSE: first events arrived at0.07s and results
at8.1s through both frontends, with no-store/X-Accel-Buffering:no. Active stream
loss interrupted after sources/model-active1; one model call completed with no
automatic repeat, survivor/replacement/backend kept data/session. Logout rejected
the old cookie for protected downloads on all three routes. Native SHA, one
publication and one upload audit were verified. All new processes/forwards are
off, three claims retained; both old2/2/2/runtime/PVC/nine-table loans returned.

The final native task remains unchecked for its full CSRF/common-runner/Gateway
scope: simulation explicitly bypasses production CSRF and the common frontend
probe currently performs only read-only topology admission. Do not relabel
separate unsigned native receipts as a complete automatic suite or certification.
The initial invalid harness profile, query-only raw-delete dry-run failure and
read-only receipt observers are preserved. Actual deletion used body UID
preconditions after first-frame/model-active proof. Product guards were unchanged.
Focused134 tests passed with0 skips. Production scheduleOFF/certificationnull.
