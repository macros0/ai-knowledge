# Test Scripts Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move maintained test tooling and its generated artifacts into explicit locations, update all consumers, and document the naming rules for dotted and non-dotted temporary files.

**Architecture:** Backend Python test tools live in the importable `backend/test_scripts` package. Cross-component wrappers live under `tests/scripts`, while operational scripts remain in their existing service directories. Test outputs move to `tests/artifacts` and disposable runtime data uses `tests/tmp`.

**Tech Stack:** Python package imports, PowerShell/CMD launchers, Node test utility, pytest, ripgrep, Git.

**Spec:** `docs/superpowers/specs/2026-09-14-test-scripts-layout-design.md`

## Global Constraints

- Repository-owned scripts never begin with `.`.
- Python modules use `snake_case.py`.
- PowerShell, CMD, and Node executable names use lowercase `kebab-case`.
- Existing dirty files and local generated data are preserved unless explicitly included in the move list.
- No commit, reset, cleanup deletion, service restart, or database change is part of this reorganization.

### Task 1: Create the target documentation and ignore policy

**Files:**
- Create: `tests/README.md`
- Modify: `.gitignore`

- [ ] **Step 1: Document the directory and naming rules**

Create `tests/README.md` describing the distinction between test suites, maintained helper scripts, reproducible artifacts, and tool-private caches. State directly that a leading dot means a hidden temporary/cache path, not a test-script type.

- [ ] **Step 2: Add ignore rules for owned test outputs and temporary runs**

Add `tests/artifacts/`, `tests/tmp/`, root `pytest-*`, and `backend/pytest-*` patterns to `.gitignore` while retaining the existing specific rules.

- [ ] **Step 3: Verify the policy files**

Run `git diff --check` and inspect only the new documentation and `.gitignore` diff.

### Task 2: Move backend test tools and preserve package imports

**Files:**
- Move: the ten backend helper modules and `glossary-probe-cases.json` listed in the spec from `backend/scripts/` to `backend/test_scripts/`
- Create: `backend/test_scripts/__init__.py`
- Modify: backend helper imports and bootstrap paths
- Modify: backend tests importing these helpers

- [ ] **Step 1: Move the explicitly classified backend test tools**

Move `bench_documents.py`, `benchmark_glossary.py`, `benchmark_glossary_exact.py`, `glossary_probe_report.py`, `probe_parallel_retrieval.py`, `probe_sources.py`, `probe_stage8_budget_tradeoff.py`, `stage8_manifest.py`, `stage8_sla_validator.py`, `validate_stage8_labels.py`, and `glossary-probe-cases.json`. Leave migrations, seeders, backfills, reindexers, exporters, and `audit_glossary_identities.py` in `backend/scripts/`.

- [ ] **Step 2: Update imports and direct-script path bootstrapping**

Use `from test_scripts.probe_sources import ...` for helper-to-helper imports and update backend test imports to `test_scripts.<module>`. Keep the moved modules' `backend/` bootstrap path based on `Path(__file__).resolve().parents[1]`.

- [ ] **Step 3: Update help text and default case paths**

Change examples and default paths so commands point to `backend/test_scripts/...` or the package module form `python -m test_scripts.<module>`.

- [ ] **Step 4: Run focused import and helper tests**

From `backend`, run the probe, glossary benchmark, Stage 8 validator/manifest, and identity-related test modules with the repository virtualenv and a writable workspace-local pytest basetemp.

### Task 3: Move Stage 8 and frontend test launchers

**Files:**
- Move: `scripts/start-stage8-test.*`, `scripts/run-stage8-test.*`, `scripts/check-stage8-test.*`, `tests/scripts/stage8/stage8-test-profile.ps1`
- Move: `tests/scripts/frontend/contrast-check.mjs`
- Modify: moved wrapper root/path calculations and all references

- [ ] **Step 1: Move wrappers to `tests/scripts/stage8/` and update repository-root resolution**

Make each PowerShell wrapper resolve the repository root three parent levels above its new directory. Point common service calls to root `scripts/` and Python helper calls to `backend/test_scripts/`.

- [ ] **Step 2: Update CMD usage text and frontend references**

Point CMD examples and the `globals.css` comment to the new locations. Keep `frontend/scripts/export-ui-keys.mjs` in its current directory.

- [ ] **Step 3: Run static wrapper checks**

Search for old Stage 8 and contrast-check paths and confirm no source or documentation reference remains except historical text explicitly retained in the design record.

### Task 4: Relocate reproducible artifacts and references

**Files:**
- Move: tracked `stage8-manifest*.json` files from `backend/scripts/` to `tests/artifacts/stage8/`
- Move: existing generated `probe-*.json` and `stage8-*.json` files when present
- Modify: docs, AGENTS.md, wrapper defaults, and manifest references

- [ ] **Step 1: Resolve the exact artifact set before moving**

List JSON files in `backend/scripts/`, select only probe/Stage 8 outputs and manifests, and preserve their contents and timestamps during the move.

- [ ] **Step 2: Update links and output defaults**

Change references to the new artifact directory while preserving historical artifact filenames and manifest payload data.

- [ ] **Step 3: Verify artifact links and Git status**

Search for old artifact paths, confirm each moved file exists once, and ensure unrelated dirty files are unchanged.

### Task 5: Final verification

**Files:**
- Verify: all changed paths and references

- [ ] **Step 1: Run backend focused tests**

Run the tests that import moved tools and the associated Stage 8/glossary acceptance tests using the workspace-local pytest basetemp.

- [ ] **Step 2: Run frontend tests and the contrast checker**

Run the existing frontend test command from `frontend` and execute `node tests/scripts/frontend/contrast-check.mjs` from the repository root if the checker has no external service requirement.

- [ ] **Step 3: Run structural checks**

Run `rg` for old paths, `git diff --check`, and a final `git status --short`. Report any pre-existing unrelated changes separately.
