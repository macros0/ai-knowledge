# Chat mail filter validation — 2026-09-27

Base: `047d383630ca5cb42b97153a9da9286f47a95af3`. Implementation in managed isolated checkout `chat-mail-filter`.

| Gate | Result | Evidence |
| --- | --- | --- |
| Real Qdrant 1.19.0 | PASS | 105 combinations: dense/BM25/hybrid, all/exclude/only, concept/chunk, tags/dev_tags, locale OR/AND; graph shares constraints; 40 higher mail sources cannot starve document retrieval |
| Real PostgreSQL 17 | PASS | Separate connections; observed actual Lock wait; shared read barrier; stale hydration/authorship rejected; writer succeeds during LLM callback; patch failure rolls back pointer/rows and preserves ready |
| Real metadata migration | PASS | Dry-run, bounded pages, repeated apply, vectors/IDs/unrelated payload unchanged, nonactive skip, SQL-to-Qdrant missing-point audit; startup restoration writes canonical scope v1 and is admitted by matching strict filter |
| Backfill unit tests | PASS | 24 tests; malformed doc_id cannot poison a page; count-by-point-type and retry controls |
| Frontend tests | PASS | 202 passed |
| Frontend lint | PASS | 0 errors, 36 existing warnings |
| Frontend production build | PASS | Temporary local Turbopack root includes shared dependency junction; product config restored |
| Backend full regression / coverage | PASS, final repeat RUNNING | Full rerun: 2256 passed, 21 skipped, 89.52% coverage (85% gate), 1709.94s. After the final startup-writer review fix, 39 affected tests and real recovery probe passed; another full run on the final code is in progress |
| Final independent review | PASS with verified fix | Fresh read-only gpt-6-astra review of 047d383..c0e7322; one Important startup backfill writer omission fixed in 9cf92fd, RED regression -> GREEN39 affected tests + real PostgreSQL/Qdrant startup recovery; no Critical/Minor, nothing declined |
| Linux CI actual release SHA | NOT RUN | Integration job added; existing backend/frontend/doc-parser/audit/docker jobs preserved |
| Live release | NOT RUN | Both contours remain unmodified; gates required before apply/UI release |

Benchmark: same disposable PostgreSQL/Qdrant corpus/config/index, baseline code from original base checkout. Ten warmups and 100 measurements per mode, fixed all/exclude/only alternation, no LLM. Fixed-candidate hydration overhead p95 3.059 ms; allowance 50.0 ms: PASS. All mode context length remained equal to baseline. SQL count is constant per batch, with extra canonical identity/tree reads.

Browser acceptance uses owned synthetic DB/collection and real product UI/API/retrieval. Only embedding/sparse query vectors and LLM are deterministic test boundaries. Request bodies and actual LLM arguments are captured only for synthetic text. Nine search/mode requests PASS; pending exclude → select only → repeat without glossary retains exclude/false; next/new-chat uses only; remount defaults all; RU/EN labels, EN empty, error followed by only, canonical chunk viewer PASS. Screenshot at 320 px: readable wrapping/select/keyboard controls. Tags, development, module, language filters and history/source links also PASS. Previous UI on port16301 sends no mail_mode and defaults to all; the compatible backend retains pending only. Index IDs/vectors/payload remained unchanged during this UI rollback check. Full backend downgrade was not performed.

Privacy: no real correspondence logged or sent to the synthetic LLM. Payload fields are derived filters, not ACL. Source view rights remain unchanged. No SECURITY.md property weakened. No API/database secrets included in reports.
