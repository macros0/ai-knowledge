# Measurement contour — pre-release audit, 2026-09-27

The existing contour is defined by `tests/scripts/stage8/stage8-test-profile.ps1` and the Stage 8 environment report. It derives host/credentials from root `.env`, overrides database `okf_stage8_test`, collection `okf_knowledge_stage8_test`, data directory `tests/tmp/stage8-data`, profile `Измерительная база Stage 8`, glossary enabled. It is not a separate env file.

Read-only inventory: 9 documents, 66 chunks, 761 concepts. This DB predates canonical sources/generations: document_sources, document_generations, document_generation_states are absent; source_id/source_spans and parser/checkpoint fields are missing. Backfill dry-run cannot pass on this schema. No live schema/payload change performed.

Prepared compatibility SQL (separate schema file) adds 10 nullable columns and 3 tables already present in the baseline backend model. Validated on an owned schema-only PostgreSQL clone: PASS; no user rows copied. Full payload backup was rejected by automatic approval review; schema-only validation succeeded through an approved safer alternative. The clone was removed after the check.

After compatibility migration, existing records must remain unknown until canonical provenance is explicitly rebuilt in a separate authorized repair. No fabricated root bindings or hidden regenerate/reparse. Apply/data acceptance/new writer/restart/browser/CI/live decision remain NOT RUN; shared UI waits for both contours.
