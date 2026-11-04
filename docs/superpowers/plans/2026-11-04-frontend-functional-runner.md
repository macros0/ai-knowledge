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
- [x] Implement native transport/agent: scoped snapshot and backend preflight,
  exact-Pod forwards, one session/upload/export, SHA/search, bounded NDJSON,
  model-gated UID loss and read-only completion, session/logout and owned-handle
  closure. Keep cookies off parent stdout/journal; pin native process identities.
  HTTP component checkpoint: 14 real loopback tests cover shared simulation
  sessions, exclusive mutation intents, accepted-vs-visible upload receipts,
  publication/export polling, original/ZIP hashes, NDJSON delivery, logout and
  refusal of redirects/non-loopback/aliased-cookie inputs. Native ownership,
  process management, loss coordination and transport are implemented below;
  the fresh live rehearsal is a separate remaining verification task.
  [Component CI](../reports/2026-11-04-frontend-runner-protocol-ci.json): Windows
  60 journal/admission +14 HTTP tests; Linux 42 protocol tests, no skips. The
  same immutable source passed after correcting DATA_DIR/DIAGNOSTICS_DIR in the
  read-only CI harness. Its one temporary capacity loan was returned and verified.
- [x] Integrate explicit frontend stage/journal/apply flags into common runner;
  retain read-only defaults, reject offline/live mixing, leave real CSRF and
  Gateway/SSO pending. Never promote separate reports into terminal native proof.
- [ ] Run Windows and Linux checks, then a fresh native staged case on the
  existing lab with fresh namespace/claims/keys. Return every capacity loan and
  verify original runtime/PVC/nine-table hashes; publish exact bounded evidence,
  update SECURITY/runbooks and run project checks before committing.

## Native implementation checkpoint

The native coordinator, process manager and fixed SSH adapter are wired into
the common runner. Windows verification: 170 passed, one Linux-only pidfd check
skipped. The real VM103 pidfd smoke terminated only its own Python helper and
left none running. A real fixed-capsule/common-runner negative admission smoke
refused an absent test namespace, retained matching parent/native intents and
created no forwards or cookies. No application request or Pod mutation was made
by either smoke. Full current-source Linux CI and the fresh positive staged live
case remain unchecked above. Previous component and F2 reports stay frozen.

The explicit stage sequence is `preflight`, `session`, `upload`, `publish`,
`export`, `export-observe`, `stream`, `loss`, `loss-observe`, `replacement`,
`logout`, `final`. Select `--suite frontend --live`, a private `--manifest` and
`--frontend-journal`, and the pinned `--ssh-host`/`--known-hosts`. Pass exactly one
`--frontend-stage`; new mutating stages also need `--apply-frontend`. After an
uncertain response, use `--observe-frontend` with the same stage/journal. This
never resubmits a business POST or DELETE. Native terminal tuples must match the
execution, intent, stage, code and input binding before the parent advances.

The manifest selects `profile=basic`, `functional_recipe=owned-simulation-local-v1`, exact native
backend/frontend/model image IDs, namespace UID and installation label. Common
schema fields `database_url`, `qdrant_collection` and `bucket_prefix` are still
required; frontend-native transport does not use a credential-bearing parent
database URL. A syntactic marker `postgresql://unused/replica_test_<run_id>` is
sufficient there. Actual provider endpoints and scoped credentials are checked
inside the already running isolated backend; Secrets are hashed locally.
Provider Service names are discovered from their owned StatefulSet Pod selectors;
Helm release prefixes are supported. Preflight checks PostgreSQL verify-full,
empty corpus directories and an empty/absent run-scoped vector collection.

Even after `selected_frontend_case_passed=true`, overall qualification remains
false with `frontend.production_csrf` and `frontend.gateway_sso` pending. The
CLI therefore retains a nonzero incomplete-qualification exit status; inspect
the selected stage/checks when driving the training rehearsal. Production backup
scheduling remains off. This implementation adds no customer trust issuer or
retrospective signing of old reports.

## Ruling

The approved plan and autonomous instruction already authorize this staged
selected-fixture work. Fresh native receipts are needed; existing closed F2
evidence is a reference, not input accepted as a successful new run. The software
prerequisite can progress without choosing a customer Gateway/IdP or trusted
issuer. Those remain separate real-input gates.
