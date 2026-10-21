# Source result location implementation plan

Approved scope: the six-step design in this chat, authorized for autonomous execution.

Goal: preserve the location of each retrieved excerpt across all search branches, answer modes, selection and history, then verify and display it in canonical document text.

## Tasks

- [x] 1. Add a bounded versioned location reference and a shared batch resolver. Distinguish raw chunk coordinates, generated concept evidence, projection coordinates and table references. Tests cover IT0004, dense paraphrases, duplicate text, stale generations, nested sources and compacted tables.
- [x] 2. Attach references to chat/search output after final block filtering. Preserve them through JSON history and selected-source responses. Keep retrieval rankings and context admission unchanged.
- [x] 3. Carry references in source links and resolve them through the authenticated document API. Recover legacy snippets only within their known source; never guess the first duplicate. Show stale/missing locations explicitly.
- [x] 4. Highlight exact visible text ranges, retaining canonical Unicode coordinates through Markdown and large-table splitting. Distinguish found passages, concept evidence and chunk fallback in all locales.
- [x] 5. Run focused and project checks, inspect the live IT0004 case and dense/source variants in an isolated test stack, review the complete diff, document results.

## Constraints and decisions

- No LLM calls, corpus regeneration or index migration for navigation.
- Reuse existing source/table provenance; offsets in a generated concept are never raw document offsets.
- Coordinate ranges use Python Unicode code points and hashes of canonical source text.
- A stale version does not silently select text in a different generation.
- Old history without generation identity can recover only unambiguous literal evidence; otherwise show chunk/concept evidence with its actual basis.
- User authorized autonomous execution of the in-chat design. Implement inline without new review/approval pauses; preserve unrelated main-checkout changes.

## Progress

- Baseline: source-location and retrieval snapshot backend tests: 56 passed; frontend source/link/view tests: 10 passed.
- Working tree: isolated codex/source-result-location based on 5bf552871a98d8b6a8889d1850f2990cd987f739. Shared runtime dependencies only; no shared .env or data directory.
- Ruling: references carry canonical source spans + chunk hash and generation ID, rather than offsets into generated concept text. Resolve legacy snippets on demand only with known source identity; tables keep their existing typed refs.
- Ruling: location and displayed chunk can race across two requests. Both responses now carry a hash/generation and the viewer refuses mismatched snapshots; single-chunk views use a typed source-text endpoint.
- Ruling: reuse micromark-util-decode-string 2.0.1 (already in the runtime graph) as a declared direct dependency to map Markdown entities/escapes linearly; avoid a recursive custom entity decoder.
- Independent review: fixed missing-source legacy recovery, transformed Markdown text, compacted-table suffix spaces, empty-span overhead, and nested connection checkout during concept fallback. Added reproductions before each repair.
- Focused verification: 167 related backend tests and 22 cross-mode/history/API tests passed before review repairs; 88 source/API tests passed after the initial repairs. Final checks and UI acceptance are recorded in the acceptance report when complete.
- Complete: final source/API regression set 89 passed; full backend 4780 passed, 105 skipped, zero failures across all389 test files; frontend692 passed,1 skipped; npm audit0 vulnerabilities, ESLint/Ruff and production build passed. Independent final review clean; real PDF new/legacy history and stale-location UI acceptance passed. Details: `docs/SOURCE_RESULT_LOCATION_ACCEPTANCE_2026-10-22.md`.
- Integration: retain the finished isolated branch for review; no main merge or remote publication was part of the agreed implementation scope. Temporary acceptance servers stopped; original dirty main files preserved.
