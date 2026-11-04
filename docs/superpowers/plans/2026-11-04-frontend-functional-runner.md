# Owned frontend functional runner — Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline, no new
> reviewer/subagents. Keep the current worktree and unrelated user changes.

**Goal:** Execute the selected frontend-split functional/loss case through the
common runner, with durable one-shot intents and actual native observations.

**Architecture:** A fixed staged run owns a fresh protected PC journal and a
separate 0700 native coordinator directory. Namespace/controller/Pod/image/config/
claim bindings guard every action. Cookies stay in the native directory. Native
terminal receipts are observed after uncertain transport; no unknown POST or
DELETE is replayed. Existing signed-evidence verification stays separate.

**Tech Stack:** Existing Python/Kubernetes/SSH transport, stdlib HTTP client,
PostgreSQL-backed single combined backend, immutable owned model stub.

**Spec:** [F1/F2](2026-10-31-kubernetes-replicas-frontend.md),
[selected rehearsal](../reports/2026-11-04-replica-frontend-split.json),
[trusted evidence contract](../../REPLICA_EVIDENCE_FORMAT.md).

## Global constraints

Only a deliberately selected isolated simulation/fake-embedding/local-artifact
frontend-split fixture. No customer endpoint, source data, host firewall, operator,
CRD or node change. Loss targets one bound frontend UID with DeleteOptions body
preconditions after first-frame/model-active proof. No query-only raw dry-run.
Do not sign old reports, accept arbitrary passed receipts, mint trust or infer
Gateway/IdP/production CSRF. Default frontend probe remains read-only.

## Tasks

- [x] Implement/test a fixed staged parent journal: preflight/session/upload/
  publish/stream/loss/loss-observe/replacement/logout/final. Bind code, manifest
  and exact installation. Persist intent before external side effects; terminal
  native proof may be adopted without replay. Reject changed scope/code, duplicate
  or out-of-order stages, aliased/uncertain files and public secret output.
  Export submission and read-only export observation are separate stages, too.
  This first checkpoint supplies the journal protocol; native actions and CLI
  wiring below remain unfinished and cannot be claimed as executable acceptance.
- [ ] Implement native transport/agent: scoped snapshot and backend preflight,
  exact-Pod forwards, one session/upload/export, SHA/search, bounded NDJSON,
  model-gated UID loss and read-only completion, session/logout and owned-handle
  closure. Keep cookies off parent stdout/journal; pin native process identities.
- [ ] Integrate explicit frontend stage/journal/apply flags into common runner;
  retain read-only defaults, reject offline/live mixing, leave real CSRF and
  Gateway/SSO pending. Never promote separate reports into terminal native proof.
- [ ] Run Windows and Linux checks, then a fresh native staged case on the
  existing lab with fresh namespace/claims/keys. Return every capacity loan and
  verify original runtime/PVC/nine-table hashes; publish exact bounded evidence,
  update SECURITY/runbooks and run project checks before committing.

## Ruling

The approved plan and autonomous instruction already authorize this staged
selected-fixture work. Fresh native receipts are needed; existing closed F2
evidence is a reference, not input accepted as a successful new run. The software
prerequisite can progress without choosing a customer Gateway/IdP or trusted
issuer. Those remain separate real-input gates.
