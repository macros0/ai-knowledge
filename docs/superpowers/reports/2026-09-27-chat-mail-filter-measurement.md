# Measurement contour — release acceptance, 2026-09-27

The existing contour is defined by `tests/scripts/stage8/stage8-test-profile.ps1` and the Stage 8 environment report. It derives host/credentials from root `.env`, overrides database `okf_stage8_test`, collection `okf_knowledge_stage8_test`, data directory `tests/tmp/stage8-data`, profile `Измерительная база Stage 8`, glossary enabled. It is not a separate env file.

Initial inventory: 9 documents, 66 chunks, 761 concepts, 827 Qdrant points. Database fingerprint `1ee1877dc19e9d65`. This DB predated canonical sources/generations: document_sources, document_generations, document_generation_states were absent; source_id/source_spans and parser/checkpoint fields were missing. Backfill dry-run could not pass on that old schema.

Prepared compatibility SQL (separate schema file) adds 10 nullable columns and 3 tables already present in the baseline backend model. Validated on an owned schema-only PostgreSQL clone: PASS; no user rows copied. Full payload backup was rejected by automatic approval review; schema-only validation succeeded through an approved safer alternative. The clone was removed after the check.

The compatibility DDL was applied transactionally to this confirmed test DB before the new runtime was started. The 10 columns are nullable; old records retained NULL source bindings and the three new canonical tables were initially empty. This is alignment with tables/columns already present in the baseline backend, separate from the two-field mail payload migration. Existing records remain unknown until canonical provenance is explicitly rebuilt in a separate repair. No fabricated root bindings or hidden regeneration/reparse occurred.

## Separate release and migration

Code artifact: `d1f8c61d52a31e147e9440baf0cd8e46be17dba9`. [Linux CI passed all six jobs on this exact SHA](https://github.com/macros0/ai-knowledge/actions/runs/36326405466); Windows2262 tests / coverage89.70%, frontend202 / lint / build and real-service integration gates passed as recorded in the validation report. Code was pushed directly to main under the user's explicit instruction.

Stage8 Environment overrides came from its existing profile (absolute data path resolved inside the primary checkout); the root env and credentials were not rewritten. The owned new backend reused port18000 while the old UI stayed on16300, with the future UI only on16302 for acceptance. Health displayed `Измерительная база Stage 8`. Normal Keycloak OIDC login succeeded. Premigration `only` under the owned paused-fixture tag returned the localized empty result.

At 15:13 UTC, all application Python writers and backend listener18000 were verified absent before the separate apply. The only running Docker container was Keycloak. No main-contour payload was copied to this collection. These helper operations each exited zero:

```powershell
backend/.venv/Scripts/python.exe tests/tmp/mail-release-ops.py measurement ddl
backend/.venv/Scripts/python.exe tests/tmp/mail-release-ops.py measurement dry --label pre-runtime
backend/.venv/Scripts/python.exe tests/tmp/mail-release-ops.py measurement snapshot --label before
backend/.venv/Scripts/python.exe tests/tmp/mail-release-ops.py measurement apply
backend/.venv/Scripts/python.exe tests/tmp/mail-release-ops.py measurement dry --label after
backend/.venv/Scripts/python.exe tests/tmp/mail-release-ops.py measurement snapshot --label after
```

The helper invokes the actual public backfill with this contour's Settings/SQL/Qdrant and batch256. Apply: 831 scanned, 827 active and updated, 4 owned candidate points skipped, unknown827 (`source_missing`), zero orphan/broken-tree/missing-active/failed/readback errors. Repeat: `would_change=0`, 827 unchanged. Mail-scope keyword and version integer schema/readback passed.

Before/after point count831 and hashes matched: IDs `d8c0ad85…da72`, vectors `14c4ca27…28d1`, unrelated payload `d3fc714b…2499`; chunks `24ba7c387f08007cba4fdb8706de94c5`; concepts `174df8ec5a91e9c9669260d0a038fed8`; generation snapshot `c48d71ee5f820c4cf695c21d1a233026`. SQL counts were unchanged by apply. After writer acceptance the original corpus, excluding only the two owned diagnostics, still matched the pre-release inventory exactly: original827 point IDs/vectors/unrelated payload, all9 documents/66 chunks/761 concepts and text. New canonical rows belong only to the synthetic documents.

## Writers, restart and UI

Independent owned EML/DOCX fixtures used distinct IDs and `mail-filter-acceptance-measurement`; real parser, Ollama/bge-m3, SQL, Qdrant, files and publication ran normally. Only synthetic concept generation was deterministic. A ready fixture prepared before runtime deployment had its scope fields removed to emulate the old payload contract. The drill is not presented as a real old user's pending candidate.

Ready publication, upload interrupted at chunk indexing followed by resume, public regenerate, canonical reindex and repair all passed. Every active point had the right scope/version1. Backend was restarted and verification repeated: PASS. The original nine table-classifier cache files were preserved/restored. No original document was regenerated/reparsed.

Nine dense/BM25/hybrid × all/exclude/only cases captured actual router LLM context with the synthetic-only LLM boundary and checked returned sources: zero forbidden markers. Live future UI via real OIDC and configured chat LLM confirmed only mail/attachment, exclude standalone document, all both, with correct citation IDs. Browser AX/screenshot receipts are in ignored primary `tests/tmp/mail-release/measurement-browser*`. Full U01–U04 matrix/network/retry/filter/history/viewer proof and UI rollback are recorded separately in the validation report.

Data/writer/browser gates: PASS. The shared new UI was released only after both contours passed, and the backend was restored to the main profile. Stage8 remains configured by its existing runner rather than a second permanently running backend. Both owned synthetic documents were moved through public soft_delete to ordinary recoverable trash; no purge was performed. Original-corpus aggregate checks passed again after cleanup; mail fields/indexes remain in place.

Completeness limitation: all827 original points are still unknown and therefore unavailable in strict modes. All mode remains available. This preserves the approved unknown policy; provenance repair is explicitly outside this release. A full backend downgrade was not performed; the tested rollback removes the new UI while retaining the compatible backend for pending strict requests.
