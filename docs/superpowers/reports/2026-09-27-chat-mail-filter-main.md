# Main contour — release acceptance, 2026-09-27

Runtime env: primary checkout root `.env`, user-confirmed main test database. Collection `okf_knowledge_base_v2`; profile `Основной контур`; database fingerprint `de138dcea7815d84`.

Initial inventory: 7589 points, 7587 canonical active, 139 mail, 86 document, 7364 unknown (7362 source_missing + 2 orphan), 7589 would_change; zero failed/readback batches; zero missing_active_points. SQL: 26 documents, 142 chunks, 7445 concepts, 37 source rows, 3 generation rows; 128 chunks / 7234 concepts without source_id. Two orphan points remain unknown; no destructive cleanup is part of mail migration.

Release completeness limit: strict modes include only proven scopes. Legacy records lacking source bindings remain available only in all. No source inference, hidden reparse or regenerate performed. Provenance passport checked read-only: the three documents with source trees use parser mail-sources-v22 and active canonical generations. One ordinary root, one mixed document with direct mail and a nested descendant attachment, and one root with metadata.mail=true. Chunk bindings match root/direct/nested source leaves. Every source row has matching parser version. Legacy rows without bindings are kept unknown; none are inferred from filenames/root/text.

Backend/frontend code artifact: `d1f8c61d52a31e147e9440baf0cd8e46be17dba9`, integrated and pushed directly to `main` as requested; no remote feature branch or PR. [All six Linux CI jobs passed on this exact artifact](https://github.com/macros0/ai-knowledge/actions/runs/36326405466). Windows final full suite: 2262 passed, 21 skipped, 89.70% coverage. Frontend: 202 passed, lint zero errors / 36 existing warnings, production build passed. Independent review and PostgreSQL/Qdrant/latency gates are in the validation report.

## Deployment and maintenance window

New backend was started from the primary `backend` with the root runtime env, while the previous UI (`047d383`) remained on port16300. Health and dependency checks passed. Normal Keycloak OIDC login as the documented test account succeeded; authentication, client and redirect URIs were not changed. Future UI was available only for acceptance on port16302. Explicit `only` with the owned paused-fixture tag returned the localized empty response before migration.

At 15:06–15:07 UTC, the exact owned backend parent/child processes were stopped, listener18000 was absent, and process inventory found no remaining Python application writers. Docker inventory contained only the existing Keycloak container. Upload/resume/regenerate/repair/purge/startup backfill could not run during apply. The paused owned candidate was not resumed until after the repeat dry-run.

The local operations helper loads the same primary Settings/engine/VectorStore and calls the public `run_backfill(..., batch_size=256)`; it does not fabricate a DB or target. Exit codes were zero for:

```powershell
backend/.venv/Scripts/python.exe tests/tmp/mail-release-ops.py main dry --label pre-runtime
backend/.venv/Scripts/python.exe tests/tmp/mail-release-ops.py main snapshot --label before
backend/.venv/Scripts/python.exe tests/tmp/mail-release-ops.py main apply
backend/.venv/Scripts/python.exe tests/tmp/mail-release-ops.py main dry --label after
backend/.venv/Scripts/python.exe tests/tmp/mail-release-ops.py main snapshot --label after
```

Apply: 7593 scanned, 7587 canonical active, 4 skipped nonactive, 7589 updated, zero failed/readback/missing-active errors. Repeat: `would_change=0`, 7589 unchanged. The four skipped points belong exclusively to the owned ready-candidate drill. Schema keyword/integer verification and bounded patch readback passed.

Before/after apply point count7593 and all hashes agreed: IDs `001ffa8c…557457`; vectors `313d5aa9…943e2`; unrelated payload `a93b9cef…a2e1d`; canonical chunk content `a8b467c861dd02f9b0519c6b7fb4f061`; concept content `5c63802647cd1857bbe1f3d575d7f1c5`; generation-state snapshot `3c0db8072e4bcc3cf38c991d88dbc8b5`. All SQL counts agreed. The initial corpus was also compared again after writer acceptance, excluding only the six owned diagnostic documents: original 7589 point IDs/vectors/unrelated payload, original text, counts and generations all remained identical to the pre-release inventory.

## Writer and browser acceptance

Real parser, actual Ollama/bge-m3 embeddings, PostgreSQL, Qdrant, filesystem and publication were exercised on an owned EML with a DOCX attachment and a standalone DOCX. Only concept generation for these synthetic fixtures used a deterministic LLM boundary. A ready candidate was prepared before first runtime deployment; its two scope fields were removed on its owned candidate points to emulate the pre-upgrade payload contract. This was a controlled compatibility drill, not recovery of a real user's old candidate.

Resume of that ready candidate, interrupted chunk indexing followed by resume, public regenerate for both documents, canonical reindex and canonical repair all passed. Every resulting active concept/chunk had the expected `mail`/`document` and integer version1. Verification passed after backend restart. The original four table-classifier cache files were preserved around public regenerate and restored; no existing document was regenerated or reparsed.

Nine real-router cases (dense/BM25/hybrid × all/exclude/only) captured actual LLM arguments and returned sources under the owned tag: zero forbidden markers; only includes mail and its attachment, exclude only the standalone document, all both. The capture substitutes only the synthetic LLM boundary and is a direct maintenance router probe; the live UI uses ordinary OIDC and the configured real chat LLM. Browser live all/exclude/only passed after restart, with citation links pointing only to the selected owned sources. Full request-body/pending/retry/RU/EN/viewer/filter/history/320px acceptance and compatible-UI rollback were separately proved on the disposable synthetic integration stand described in the validation report.

Evidence remains ignored under primary `tests/tmp/mail-release/`: target reports, before/after snapshots, original-corpus preservation, writer/context receipts, health and browser screenshots. Actual strings/credentials from the original corpus are absent from the committed reports.

## Release decision

Data, writer and browser gates: PASS; UI released. The measurement contour passed independently before the common new UI replaced the old UI on port16300. The primary backend was restored to `Основной контур`; temporary acceptance UI/backend routes were removed. Final all/exclude/only and pending-snapshot smoke passed on16300 in EN/RU; screenshot `tests/tmp/mail-release/released-ui.png`. All six owned diagnostics were moved to recoverable trash through public soft_delete, with no purge. Original-corpus hashes passed again afterwards. The compatible-backend UI rollback drill preserved pending strict modes and all index data; full backend downgrade was not performed.

Completeness limitation remains material: 7364 original unknown points are excluded from strict modes. This is the approved fail-closed policy, not evidence that the legacy corpus has acquired provenance. Correcting those bindings is a separate repair task.
