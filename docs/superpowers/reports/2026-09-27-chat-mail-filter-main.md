# Main contour — pre-release audit, 2026-09-27

Runtime env: primary checkout root `.env`, user-confirmed main test database. Collection `okf_knowledge_base_v2`; profile `Основной контур`; database fingerprint `de138dcea7815d84`.

Dry-run only: 7589 scanned, 7587 canonical active, 139 mail, 86 document, 7364 unknown (7362 source_missing + 2 orphan), 7589 would_change; zero failed/readback batches; zero missing_active_points. SQL: 26 documents, 142 chunks, 7445 concepts, 37 source rows, 3 generation rows; 128 chunks / 7234 concepts without source_id. Two orphan points remain unknown; no destructive cleanup is part of mail migration.

Release completeness limit: strict modes include only proven scopes. Legacy records lacking source bindings remain available only in all. No source inference, hidden reparse or regenerate performed. Provenance passport checked read-only: the three documents with source trees use parser mail-sources-v22 and active canonical generations. One ordinary root, one mixed document with direct mail and a nested descendant attachment, and one root with metadata.mail=true. Chunk bindings match root/direct/nested source leaves. Every source row has matching parser version. Legacy rows without bindings are kept unknown; none are inferred from filenames/root/text.

Storage services started for read-only audit / disposable probes. Main app writers were not started, no main payload/schema changed. Backend/UI deploy, apply/readback/idempotency, new-writer/restart acceptance, CI and rollout decision: NOT RUN.
