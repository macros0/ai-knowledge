# Test Scripts Layout Design

## Goal

Separate maintained test, benchmark, probe, and acceptance tooling from operational backend scripts, and document one unambiguous naming and temporary-artifact policy.

## Current problem

The repository currently mixes three different categories:

1. Operational scripts in `backend/scripts/` that migrate, backfill, seed, reindex, export, or inspect application data.
2. Test and acceptance tools in the same directory, including corpus probes, benchmarks, Stage 8 validators, and reproducibility manifests.
3. Local pytest and measurement outputs in both dotted and non-dotted directories and beside source scripts.

The dotted names are not a second test-script convention. They are tool-generated or manually selected temporary directories, usually pytest `--basetemp` directories. They must not be treated as repository source.

## Target structure

```text
tests/
  README.md
  scripts/
    frontend/      # frontend-only test/check utilities
    stage8/        # Stage 8 contour launch/check wrappers
  artifacts/       # reproducible test outputs; ignored by Git
  tmp/             # disposable test runtime data; ignored by Git

backend/
  scripts/        # operational DB/filesystem scripts and migration helpers
  test_scripts/    # Python probes, benchmarks, validators, manifests
  tests/           # backend unit/integration test suites
```

Existing component test suites remain where their runtime and package imports are defined: `backend/tests`, `frontend/test`, and `doc-parser/tests`. This change concerns executable helper scripts and their generated artifacts, not test cases.

## Files to move

Move these maintained backend test tools from `backend/scripts/` to `backend/test_scripts/`:

- `bench_documents.py`
- `benchmark_glossary.py`
- `benchmark_glossary_exact.py`
- `glossary_probe_report.py`
- `probe_parallel_retrieval.py`
- `probe_sources.py`
- `probe_stage8_budget_tradeoff.py`
- `stage8_manifest.py`
- `stage8_sla_validator.py`
- `validate_stage8_labels.py`
- `glossary-probe-cases.json`

Keep `audit_glossary_identities.py` in `backend/scripts/`: the operational
`seed_glossary.py` uses it as a migration/data audit helper.

Move the tracked Stage 8 manifest snapshots to `tests/artifacts/stage8/`. New probe output must be written there and must not be stored beside source code.

Move `frontend/scripts/contrast-check.mjs` to `tests/scripts/frontend/contrast-check.mjs`. Keep `frontend/scripts/export-ui-keys.mjs` there because it is a source/build-generation utility, not a test script.

Move the Stage 8 wrappers `start-stage8-test.*`, `run-stage8-test.*`, `check-stage8-test.*`, and `stage8-test-profile.ps1` from `scripts/` to `tests/scripts/stage8/`. Keep common service lifecycle scripts (`start-all`, `stop-all`, `start-background`, database/Qdrant/Keycloak helpers) in `scripts/`.

Operational migrations, backfills, seeders, exports, rebuilds, and integrity/stuck-document checks stay in `backend/scripts/`.

## Compatibility and imports

The backend helpers remain executable from the repository root through their new paths and importable from the backend test suite as `test_scripts.<module>`. Their bootstrap path must resolve the `backend/` directory after the move. Internal helper imports must use the package-qualified path instead of relying on the old script directory being on `sys.path`.

Stage 8 wrappers must calculate the repository root from their deeper directory and pass the new backend helper paths to the Python runner. Documentation, AGENTS.md, tests, comments, and reproducibility manifests must reference only the new paths.

## Naming and artifact rules

- Repository-owned scripts never begin with `.`.
- Python modules use `snake_case.py` so they can be imported as packages.
- PowerShell, CMD, and Node executable names use lowercase `kebab-case`.
- Maintained cross-component test tools live only below `tests/scripts/<area>/`; backend Python tools live below `backend/test_scripts/` so their application imports remain package-safe.
- Repository-owned outputs live only below `tests/artifacts/`; disposable runtime data lives only below `tests/tmp/`.
- Both output directories are ignored by Git.
- `.pytest-*`, `.ruff_cache`, `.next`, and similar dotted names are tool-private caches or temporary directories; they are not source and must not be added to the repository.
- Non-dotted `pytest-*` directories are also temporary artifacts and must be ignored. Their presence does not make a script a maintained project file.

## Verification

1. Check that every moved file exists exactly once and no maintained test tool remains in the old directories.
2. Search the repository for old paths and update all source, documentation, test, and wrapper references.
3. Run the focused backend tests covering probes, benchmarks, Stage 8 validators/manifests, and glossary identity tooling.
4. Run frontend tests covering the contrast-check reference and the existing frontend test suite.
5. Run `git diff --check` and inspect `git status --short` to ensure unrelated dirty files and generated local artifacts were not modified or deleted.
