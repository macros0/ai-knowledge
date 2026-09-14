# Glossary exact identities acceptance — 2026-09-14

> Current status: acceptance is OPEN. The independent review in
> `2026-09-14-glossary-independent-review.md` reproduced nine unmet requirements.
> Previous green suites below are historical evidence, not full acceptance.

> Historical focused results in this file were superseded by the follow-up
> implementation pass. The defects listed there are now covered by regression
> tests and fixes. The user confirmed there is no production database; both
> existing PostgreSQL test glossaries were reset and adapted with backups.
> Manual browser smoke remains outside this run.

## Verified behavior

- Glossary names and explicit aliases share one exact identity namespace. A name/name, name/alias, or alias/alias collision returns a structured conflict before commit.
- Infotype recognition is query-only. A rule is a user-owned range plus literal prefixes; it accepts exactly four ASCII digits with identifier boundaries. `IT0003`, `ИТ 0003`, and other forms are accepted only when the corresponding prefixes are configured. `инфотип 3`, `IT00037`, and `XIT0003Y` are rejected.
- Rule or alias edits increment glossary revision and are visible in the next query. No embedding, sparse index, Qdrant payload, document, or chunk write is part of these mutations. The active expansion path has no built-in SAP variant list; all infotype prefixes come from saved rules.
- Retrieval exact filtering checks each concept/chunk title and content with complete-form boundaries before merge. Similar identifiers do not become exact hits.
- SAP transactions, programs, objects, tables, abbreviations, and business terms use only saved exact names/aliases; no structural variants are generated for these kinds.
- Merge preview returns detached source/target/merged values plus a digest and glossary revision. Commit moves aliases/translations and identity keys atomically, records an idempotency receipt and an audit event containing both original cards, and fails on stale versions or unresolved identity conflicts.

## Tests

Focused backend results recorded during implementation:

- normalization/rules/identity: 37 passed
- registry/aliases/exact concurrency: 25 passed across the focused suites; opt-in PostgreSQL lock/rollback suite — `2 passed` on a dedicated `test_glossary_migrated` database
- expansion: 33 passed
- context/search: 24 passed
- API: 17 passed, including read-only merge preview, audited commit, and `503 glossary_migration_required` gate
- merge: 3 passed
- seed: 6 passed
- offline identity migration: 4 passed, including `--prepare`, expected-revision gate, atomic `--apply`, and explicit source→target execution
- frontend glossary and conflict contracts: 29 passed; Next production build completed; ESLint reported 0 errors
- backend i18n contract: 2 passed
- unready snapshot/migration gate and exact-only SAP kinds: 5 snapshot/i18n tests plus 43 exact-only alias/normalization tests passed

Final focused rerun after the last API, migration-readiness, SAP-kind, and merge-locale changes: `117 passed` in the combined backend glossary/audit/error-code suite; full frontend suite — `138 passed`; ESLint — `0 errors`; Next production build — successful; full `ruff check .` — successful. The fresh full backend suite then completed with **1284 passed, 2 skipped** and 113 warnings. The disposable PostgreSQL concurrency suite completed with **2 passed**.

The UI conflict path is covered by frontend contract tests and a successful
production build. A manual browser smoke was not repeated in this final pass, so
browser-level preview/commit/readback remains an explicit follow-up check.

Per-stage code review was recorded in the implementation plan. It covered the identity/rule layer, query expansion and exact retrieval, merge/API transaction boundaries, UI and localization contracts, and the offline migration/readiness path. No blocking finding was left for the completed stages.

`ruff check .` passed. `git diff --check` passed. `ui_keys.json` parses successfully. On a disposable PostgreSQL database, the writer serialization and rollback tests passed. Existing-term databases initialize with `identities_ready=false`; fresh databases initialize ready. The offline migration CLI has explicit `--prepare`/`--apply` boundaries; default audit/planning remains non-mutating.

## Limits of this acceptance

The test corpus is isolated and does not establish global recall or ranking quality for every Qdrant collection. Browser preview, commit endpoint, and post-commit UI readback were not manually repeated in the final pass; the code-level UI contracts and production build passed. Production metadata migration is not applicable because the user confirmed there is no production database. The existing Stage 8 readiness acceptance remains a separate task.
