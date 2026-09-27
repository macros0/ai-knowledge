# Chat mail filter baseline — 2026-09-27

Base SHA: `047d383630ca5cb42b97153a9da9286f47a95af3`.
Isolated checkout: `C:/Users/alexey/.codex/worktrees/chat-mail-filter/ai-knowledge`.
Primary checkout has only existing plan/spec edits; they are preserved.

Baseline selected regression command from plan (14 test modules): **227 passed, 39 warnings**, 183.78 s. Warnings: existing Starlette/httpx deprecation and Qdrant compatibility/local-index warnings. Fixtures use isolated SQLite and mocks/local Qdrant; this result does not prove real PostgreSQL/Qdrant integration.

Writers inventory: pipeline, canonical_reindex, canonical_repair; seed_backup_fixture has no provenance and must write unknown. Direct metadata patches migrate_payload and vector_store preserve unrelated payload. Full upserts reside in vector_store.

Expected compatibility difference: graph expansion will apply tags/locale using the full common filter; previously it retained only exclusions. Unconstrained all ranking remains unchanged.

User identifies primary test contour by root `.env`. Stage 8 contour is documented in `2026-09-12-glossary-stage8-test-environment.md` and configured by `tests/scripts/stage8/stage8-test-profile.ps1`; verify live settings before apply.

Task 0 real-service baseline now recorded in the validation report: same owned PG/Qdrant corpus/config/index, original base code; 10 warmups/100 measurements per mode, fixed-candidate hydration comparison. Disposable probes passed; unconstrained all context size agrees with baseline. No user corpus baseline generation.
