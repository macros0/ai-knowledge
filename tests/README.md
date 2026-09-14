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

Backend helper examples, run from the repository root:

```powershell
backend\.venv\Scripts\python.exe backend\test_scripts\probe_sources.py --help
backend\.venv\Scripts\python.exe -m test_scripts.benchmark_glossary --help
```

The `test_scripts` package form is available when the current directory is `backend`.
