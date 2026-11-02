# Trusted replica evidence — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Follow RED/GREEN inline; final whole-branch review remains separate.

**Goal:** Verify bounded files and explicitly trusted harness provenance before the existing qualification policy sees evidence, and expose that read-only verification through the common runner.

**Architecture:** A separately selected expected binding and trust policy authorize Ed25519 public issuers for exact producer SHA/check names. A separately authorized terminal bundle signature binds the complete receipt set. A bundle only references regular, single-link JSON files below an explicit evidence root. Signed receipts bind actual artifact SHA, current binding and acceptance ID/run/namespace UID/installation scope. Artifacts contain strict bounded outcome summaries; missing/failed/skipped results cannot qualify. Signatures authenticate the configured harness, not independent truth of its observations.

**Tech Stack:** Existing Python/Pydantic/cryptography; no dependencies, network, signing service or deployment mutations.

**Spec:** [Approved O3](2026-10-31-kubernetes-replicas-operations.md), [design](../specs/2026-10-31-kubernetes-replicas-storage-design.md).

## Global Constraints

- User requested autonomous implementation; choosing Ed25519 over a shared signing secret is an implementation ruling, not a new request for production signing authority.
- No real issuer/private key is generated or configured. Tests use ephemeral issuers. Historical reports are never retroactively signed or promoted.
- Trust policy and expected binding are selected outside the evidence root, not embedded in the bundle. Trust policy authorizes exact producer SHA/check names and an enabled issuer.
- Receipt+artifact scope matches run ID, namespace UID, installation ID and binding digest. CLI additionally matches namespace/profile/runtime fingerprint from the protected manifest.
- Canonical JSON, duplicate-key refusal, bounded size/counts, link/hardlink/FIFO refusal and fixed redacted errors. No arbitrary paths/URLs/credentials enter public reports.
- Keep existing live/apply runner semantics unchanged. Offline verification cannot deploy, invoke probes, or grant production permission. Certificates remain null in the actual inventory.
- Production backup schedule stays OFF; existing local receiver and all storage/PVCs untouched.

## Review Focus

1. A valid signature from a different issuer/producer/check or namespace must not pass.
2. Artifact corruption/replacement, duplicate JSON keys and path aliasing must fail before policy.
3. A signed failed/skipped/incomplete artifact must not be made green by an outer status string.
4. Offline verification flags cannot combine with live/apply stages or shorten the required policy.
5. Public errors must not echo malformed private inputs; signature verification does not prove a dishonest trusted harness told the truth.

## Task 1: Bounded signed receipt ingestion

Create `backend/test_scripts/replica_evidence.py` and `backend/tests/test_replica_evidence.py`.
`verify_evidence(binding, scope, *, bundle_path, trust_path, evidence_root)` returns the existing certification report plus bounded provenance hashes. Trust and all models are strict. Each receipt uses domain-separated Ed25519 over canonical unsigned fields and references one hashed strict artifact. Artifact checks have unique bounded IDs/statuses; all must pass, with native reference hashes for live evidence and explicit verified restore for restore evidence. The loader derives policy status from artifact outcomes. No generic signing CLI.

- [x] Write tests for valid trusted complete evidence, absent required result, wrong/disabled issuer, unauthorized check/producer, altered signature/body/artifact, scope/binding change, failed/skipped/restore false, duplicates/unknown fields/oversize/path/FIFO/hardlink and redaction.
- [x] RED distinct basetemp: missing loader. Implement and GREEN new tests plus existing policy/safety cohort; Ruff.

## Task 2: Common runner read-only integration and documentation

Modify `backend/test_scripts/run_replica_acceptance.py`; add `backend/tests/test_replica_evidence_runner.py`. Require all four explicit flags `--qualification-bundle`, `--qualification-binding`, `--qualification-trust`, `--qualification-root`, `--suite all`, matching protected manifest scope/fingerprint, and no live/mutation flags. Verify all regular inputs before delegating; passing means only configured-harness offline qualification. Existing runner without these flags retains all pending gates.

- [x] Write CLI/integration tests; RED missing flags/function. Implement and GREEN alongside loader/policy/existing runner safety.
- [x] Add new cohorts to Kubernetes workflow contract checks. Update SECURITY, acceptance/runbooks/plans/matrix with exact trust limits and issuer rollout still pending.
- [x] Run project check, sanitized report/hash/link checks and commit only owned files. No actual profile certification or whole O3 completion.

## Recorded completion and remaining operational gate

Windows final cohort:186 passed/1 POSIX-only skip in262.04s; project check audit0,
frontend756+1skip, ESLint/Ruff passed. Meaningful closure and parent-alias RED
cases were observed before their fixes. [Validation](../reports/2026-11-02-replica-trusted-evidence.json).
No real issuer is configured; all profile certificates remain null.

Linux CI source handoff was rejected by automatic approval review before any
process/archive/container was created. Explicit source/destination authorization
is pending. Do not bypass/retry that rejection. The temporary idle capacity
reduction was completely reversed:2/2/2 restored,9 canonical tables/PVCs/identity
unchanged. The added workflow step has not run and the POSIX test is an open
Linux check. Production schedule remains OFF.

Follow-up after explicit user approval: [Linux validation](../reports/2026-11-02-replica-evidence-alerts-linux.json)
passed295 cases/0 skips/errors/failures, including native FIFO and symlink.
The initial read-only source copy's295 setup errors are retained; a fresh
writable test copy from the same authorized on-VM archive passed with no product
code change or second transfer.2395 frozen files stayed unchanged, target2/2/2
and9 canonical tables/PVCs/identity restored. Source-transfer authorization is
resolved for this snapshot; the selected POSIX checks are now verified. Full
backend CI, real issuers/producers and profile acceptance remain open.
