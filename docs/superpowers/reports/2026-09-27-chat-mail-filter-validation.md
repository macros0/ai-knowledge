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
| Backend full regression / coverage | PASS | Final Windows run after the startup-writer fix: 2262 passed, 21 skipped, 269 warnings, 89.70% coverage (85% gate), 1681.37s. Earlier rerun: 2256 passed / 89.52%; both completed with exit0 |
| Final independent review | PASS with verified fix | Fresh read-only gpt-6-astra review of 047d383..c0e7322; one Important startup backfill writer omission fixed in 9cf92fd, RED regression -> GREEN39 affected tests + real PostgreSQL/Qdrant startup recovery; no Critical/Minor, nothing declined |
| Linux CI actual release SHA | PASS | [All six jobs on d1f8c61](https://github.com/macros0/ai-knowledge/actions/runs/36326405466); backend2259 passed / 24 skipped / 89.23% coverage; real PostgreSQL/Qdrant integration, frontend, doc-parser, dependency audit and compose build passed |
| Live release | PASS data/writers/browser | Separate main/measurement apply + readback + zero-change repeat, exact original-corpus preservation, owned new writers, restart and nine actual-context cases each passed; common UI waits both and main profile restored |

Benchmark: same disposable PostgreSQL/Qdrant corpus/config/index, baseline code from original base checkout. Ten warmups and 100 measurements per mode, fixed all/exclude/only alternation, no LLM. Fixed-candidate hydration overhead p95 3.059 ms; allowance 50.0 ms: PASS. All mode context length remained equal to baseline. SQL count is constant per batch, with extra canonical identity/tree reads.

Browser acceptance uses owned synthetic DB/collection and real product UI/API/retrieval. Only embedding/sparse query vectors and LLM are deterministic test boundaries. Request bodies and actual LLM arguments are captured only for synthetic text. Nine search/mode requests PASS; pending exclude → select only → repeat without glossary retains exclude/false; next/new-chat uses only; remount defaults all; RU/EN labels, EN empty, error followed by only, canonical chunk viewer PASS. Screenshot at 320 px: readable wrapping/select/keyboard controls. Tags, development, module, language filters and history/source links also PASS. Previous UI on port16301 sends no mail_mode and defaults to all; the compatible backend retains pending only. Index IDs/vectors/payload remained unchanged during this UI rollback check. Full backend downgrade was not performed.

Privacy: no real correspondence logged or sent to the synthetic LLM. Payload fields are derived filters, not ACL. Source view rights remain unchanged. No SECURITY.md property weakened. No API/database secrets included in reports.

## Live acceptance and integration

The code artifact is `d1f8c61d52a31e147e9440baf0cd8e46be17dba9`. Its twelve implementation/evidence commits were fast-forwarded and pushed directly to main as explicitly requested, with no remote feature branch or PR. Report-only completion changes do not alter the tested runtime code. Root env and the user's three dirty planning/spec files were preserved. Separate release reports contain the actual targets, dates, counts, unknown reasons, hash controls, command exits and scope limitations.

Main: 7589 original points were patched, unknown7364; measurement: 827 patched, all original827 unknown. Both had zero readback/failed/missing-active errors and `would_change=0` on the repeat. Four own nonactive ready points in each were deliberately skipped, then correctly admitted at publication. Compatibility DDL for old Stage8 was separately proved on a schema-only clone and applied before runtime deployment. Both original corpora were compared again after writer/restart acceptance, excluding only owned diagnostic documents: original IDs/vectors/unrelated payload/text/counts/generations preserved.

Real Ollama/bge-m3, parser workers, SQL/Qdrant, files and public writer methods were used on the owned live fixtures; only their concept generation boundary was deterministic. Real-router context probes substitute the synthetic LLM boundary and record marker booleans/source IDs; live browser requests use normal Keycloak and the configured real chat LLM, always under the owned tag. No authentication bypass or full user-content dump was used. The ready payload compatibility drill emulates old scope fields on an owned candidate; it is not claimed to be an actual user's pre-release candidate.

New backend with old UI was verified in each contour; strict premigration smoke returned empty safely. Live all/only/exclude browser checks passed independently on future UI port16302. After both passed, acceptance frontend ports were stopped, the main backend was restored and the new primary frontend released on16300. Standard-port all/exclude/only smoke passed; changing exclude to only while pending left the response/sources on the standalone document, and the next request used only with the mail/attachment sources. EN and RU controls both passed. Screenshot: ignored primary `tests/tmp/mail-release/released-ui.png`; 320px proof remains `tests/tmp/chat-mail-filter-320.png`.

Cleanup passed through public recoverable `Pipeline.soft_delete`: six owned main diagnostics (including four failed attempts caused by the first helper's missing Windows multiprocessing main guard) and two owned measurement fixtures were moved to ordinary trash. No purge was performed; files/vectors/source records remain recoverable. The helper guard was corrected before successful real parser acceptance; this was an operational harness error, not a product parser regression. Original-corpus aggregate checks passed again after cleanup. The final active corpus is the original one, with new mail-derived payload fields.

An existing LocaleContext hydration mismatch appeared during mixed old/new UI locale sessions (server EN/client RU); that file is identical to base047d383, React recovered and all mode/source checks passed. The final standard-port session aligned in EN and then selected RU normally. This pre-existing issue is not a filter bypass and was not hidden by a source/config change.

## Final review rulings

One fresh independent read-only reviewer examined immutable `047d383..c0e7322`. Critical0, Important1, Minor0; Declined-to-judge empty. The Important omission in startup `backfill_chunks` was fixed in `9cf92fd`, with RED→GREEN regression, 39 affected tests, actual missing-point startup recovery, final full Windows suite and Linux CI. No findings remain open.

Implementation rulings recorded in the execution ledger: use fail-closed unknown policy; canonical SQL ancestry rather than tags/filename; full tags/locale graph constraints are an expected bug fix; include skipped chunk identity/tree reads in constant-batch query accounting; external test doubles must store/return actual upsert IDs for publication readback; permanent candidate conflicts abandon their own ready candidate while transient patch errors retain ready; final independent review precedes operational release gates. No deferred Minor findings.

Rollback proof used an owned disposable synthetic stand: previous UI omits mail_mode and keeps all, pending only remains only on the compatible backend, and full index hashes remain unchanged. The full backend-downgrade branch was documented but not executed. No claim is made that an old backend can safely accept a new strict client.
