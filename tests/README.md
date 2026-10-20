# Test tooling

The repository has three separate kinds of test-related files:

- Test suites stay with their component: `backend/tests`, `frontend/test`, and `doc-parser/tests`.
- Maintained executable helpers live in `backend/test_scripts` for backend Python tools and `tests/scripts` for cross-component, frontend, and Stage 8 wrappers.
- Reproducible outputs belong in `tests/artifacts`; disposable test runtime data belongs in `tests/tmp`.

Naming is intentional:

- Project scripts never start with a dot.
- Python modules use `snake_case.py` so they can be imported.
- PowerShell, CMD, and Node tools use lowercase `kebab-case` names.
- `.pytest-*`, `.tmp-*`, `.ruff_cache`, `.next`, and similar dotted paths are hidden tool caches or temporary directories. They are not another kind of test script.
- Non-dotted `pytest-*` paths are temporary too and are ignored for the same reason.
- Temporary runtime directories such as `.runtime-cookie-check`, `.stage8-runtime-logs`, and the Stage 8 data directory belong below `tests/tmp`, never in the repository root.

When running pytest, never pass a basetemp in the repository root. The backend and doc-parser pytest configurations already place temporary data below `tests/tmp/pytest-runs/` and disable the default `.pytest_cache`. For parallel or isolated runs, use a unique child directory below `tests/tmp/pytest-runs/`. Save reports, manifests, and probe JSON below `tests/artifacts/<area>` rather than beside source code.

Retention policy:

- `tests/tmp/pytest-runs/` is disposable and is not a permanent test archive.
- Successful runs may be removed immediately after verification; failed runs are kept for at most 7 days.
- If a result is needed for a report or review, copy the small reproducible artifact to `tests/artifacts/<area>`; do not keep the whole pytest run directory.

Test maintenance:

- Age, a `legacy` name, or a platform/service-dependent skip does not make a test obsolete. Compatibility, migrations, and platform-specific checks remain supported contracts.
- Remove a redundant test only after identifying a retained test with the same setup and assertions. Compare coverage before and after; passing tests alone do not establish equivalent coverage.
- Backend collection excludes the old `backend/tests/tmp` artifact directory. New runs still belong under the root `tests/tmp/pytest-runs` directory.
- The shared backend fixture isolates local `REFERENCE_EDIT_LOCKS_ENABLED`, `AUDIT_STDOUT_MODE`, and `AUDIT_STDOUT_FORMAT` settings. Lock protocol and audit transport tests opt in explicitly with their own configuration; optional PostgreSQL/Docker test connection variables remain available.

The 2026-10-20 cleanup removed these nine redundant checks; all replacement tests are in the same file:

| File in `backend/tests` | Removed test | Retained coverage |
| --- | --- | --- |
| `test_admin_locales.py` | `test_etag_304` | `test_304_repeats_validator_headers` also checks ETag and Cache-Control |
| `test_admin_locales.py` | `test_create_duplicate_422` | `test_validation_422_carries_code` also checks the error code |
| `test_admin_locales.py` | `test_get_active_unknown_locale_404` | `test_unknown_locale_404_carries_code` |
| `test_admin_locales.py` | `test_import_preview_unknown_locale_404` | `test_ui_dictionary_unknown_locale_is_locale_code` |
| `test_admin_locales.py` | `test_history_unknown_locale_404` | `test_history_unknown_locale_carries_code` |
| `test_glossary_context.py` | `test_bare_domain_word_does_not_count_as_domain_match` | `test_domain_match_keeps_source_with_explicit_equivalent_alias` checks the identical unrelated block |
| `test_prompt_store.py` | `test_override_wins_over_canonical` | `test_override_deleted_falls_back` checks override precedence before deleting the file |
| `test_tag_registry_trash.py` | `TestTrashOnly.test_count_is_zero` | `test_count_returns_after_restore` checks zero before restoring |
| `test_tag_registry_trash.py` | `TestMixed.test_count_is_active_only` | `test_soft_delete_of_last_active_zeroes_count` checks one active holder, then zero |

Backend helper examples, run from the repository root:

```powershell
backend\.venv\Scripts\python.exe backend\test_scripts\probe_sources.py --help
backend\.venv\Scripts\python.exe -m test_scripts.benchmark_glossary --help
```

The `test_scripts` package form is available when the current directory is `backend`.
